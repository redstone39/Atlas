from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas_production.infrastructure.conversation_review_source import (
    ConversationReviewSource,
    ConversationReviewSourceError,
    ConversationReviewTranscriptV1,
)
from atlas_production.modules.context_engineering.public import (
    TurnInputProjectionAuditReader,
    TurnInputProjectionV1,
)
from atlas_production.modules.conversation_review.public import (
    ConversationLearningCaseProposalV1,
    ConversationReviewOwner,
)
from atlas_production.modules.learner.public import (
    LearnerNode,
    LearnerRunClaimV1,
    LearnerSourceIdentityV1,
    learner_case_digest,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalog,
    PromptSkillCatalogV1,
    PromptSkillExactReader,
    PromptSkillInstructionsV1,
    PromptSkillRefV1,
)
from atlas_production.modules.result_governance.public import (
    GovernedAnswerDraftV2,
    ResultGovernanceDraftOwnerV2,
)
from atlas_production.modules.turn_experience.public import (
    TurnExperienceStore,
    TurnExperienceV1,
)
from atlas_production.modules.turn_runtime.public import (
    ExecutionSnapshotV1,
    TerminalOutcomeV1,
    TurnRuntimeOwner,
)

SkillIssueType = Literal[
    "wrong_skill_selected",
    "selected_skill_underperformed",
    "missing_suitable_skill",
    "not_skill_related",
    "indeterminate",
]


class LearnerSourceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LearnerExactSkillV1(_StrictModel):
    ref: PromptSkillRefV1
    instructions: PromptSkillInstructionsV1

    @model_validator(mode="after")
    def require_exact_ref(self) -> "LearnerExactSkillV1":
        if (
            self.instructions.name != self.ref.name
            or self.instructions.revision != self.ref.revision
            or self.instructions.content_digest != self.ref.content_digest
        ):
            raise ValueError("skill instructions do not bind exact ref")
        return self


class LearnerExactCatalogV1(_StrictModel):
    catalog: PromptSkillCatalogV1
    skills: list[LearnerExactSkillV1]

    @model_validator(mode="after")
    def require_complete_catalog(self) -> "LearnerExactCatalogV1":
        expected = [candidate.ref for candidate in self.catalog.skills]
        if [skill.ref for skill in self.skills] != expected:
            raise ValueError("exact instructions must cover catalog in order")
        if any(ref.category != self.catalog.ref.category for ref in expected):
            raise ValueError("catalog contains a skill from another category")
        return self


class LearnerExecutionPacketV1(_StrictModel):
    input_projection: TurnInputProjectionV1
    runtime_snapshot: ExecutionSnapshotV1
    terminal_outcome: TerminalOutcomeV1
    governed_answer: GovernedAnswerDraftV2 | None = None
    turn_experience: TurnExperienceV1 | None = None
    exact_catalogs: list[LearnerExactCatalogV1] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def require_execution_identity(self) -> "LearnerExecutionPacketV1":
        execution_id = self.runtime_snapshot.execution_id
        if (
            self.input_projection.execution_id != execution_id
            or self.terminal_outcome.execution_id != execution_id
        ):
            raise ValueError("learner execution packet identity mismatch")
        completed = self.terminal_outcome.outcome == "completed"
        if completed != (self.governed_answer is not None):
            raise ValueError("completed execution requires governed Answer")
        if completed != (self.turn_experience is not None):
            raise ValueError("completed execution requires closed Turn Experience")
        if self.governed_answer is not None and self.governed_answer.execution_id != execution_id:
            raise ValueError("governed Answer execution identity mismatch")
        if self.turn_experience is not None:
            if (
                self.turn_experience.execution_id != execution_id
                or self.turn_experience.turn_id != self.runtime_snapshot.turn_id
            ):
                raise ValueError("Turn Experience identity mismatch")
        if [item.catalog.ref for item in self.exact_catalogs] != self.runtime_snapshot.prompt_skill_catalogs:
            raise ValueError("exact catalogs must match execution-pinned catalogs")
        return self


class LearnerCapabilityManifestV1(_StrictModel):
    node: LearnerNode
    selected_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list)
    candidate_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list)
    allowed_issue_types: list[SkillIssueType] = Field(min_length=1)

    @model_validator(mode="after")
    def require_manifest_shape(self) -> "LearnerCapabilityManifestV1":
        if len(set(self.allowed_issue_types)) != len(self.allowed_issue_types):
            raise ValueError("capability issue types must be ordered and unique")
        for refs in (self.selected_skill_refs, self.candidate_skill_refs):
            identities = [
                (ref.category, ref.name, ref.revision, ref.content_digest) for ref in refs
            ]
            if len(set(identities)) != len(identities):
                raise ValueError("capability skill refs must be ordered and unique")
        if "indeterminate" not in self.allowed_issue_types:
            raise ValueError("capability manifest must always allow indeterminate")
        return self


class LearnerCasePacketV1(_StrictModel):
    source: LearnerSourceIdentityV1
    case: ConversationLearningCaseProposalV1
    transcript: ConversationReviewTranscriptV1
    executions: list[LearnerExecutionPacketV1] = Field(min_length=1)
    planner_applicability: Literal["applicable", "unavailable", "not_applicable"]
    capability_manifests: list[LearnerCapabilityManifestV1] = Field(
        min_length=3, max_length=3
    )
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_packet_shape(self) -> "LearnerCasePacketV1":
        if [manifest.node for manifest in self.capability_manifests] != [
            "understanding",
            "planner",
            "answer",
        ]:
            raise ValueError("capability manifests must be in layer order")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("packet evidence ids must be ordered and unique")
        involved = set(self.source.involved_turn_ids)
        packet_turns = {item.runtime_snapshot.turn_id for item in self.executions}
        if packet_turns != involved:
            raise ValueError("packet executions must cover exactly involved turns")
        return self

    def manifest_for(self, node: LearnerNode) -> LearnerCapabilityManifestV1:
        return next(item for item in self.capability_manifests if item.node == node)


def _skill_identity(ref: PromptSkillRefV1) -> tuple[str, str, int, str]:
    return (ref.category, ref.name, ref.revision, ref.content_digest)


def _is_prompt_skill_integrity_error(exc: BaseException) -> bool:
    return getattr(exc, "error_code", None) in {
        "prompt_skill_integrity_error",
        "prompt_skill_not_found",
    }


def _require_selected_skill_membership(
    runtime_snapshot: ExecutionSnapshotV1,
    exact_catalogs: list[LearnerExactCatalogV1],
) -> None:
    catalog_members = {
        exact.catalog.ref.category: {
            _skill_identity(skill.ref) for skill in exact.skills
        }
        for exact in exact_catalogs
    }
    selected = [
        skill
        for trace in runtime_snapshot.prompt_skill_selections
        if trace.status == "selected"
        for skill in trace.selected_skills
    ]
    if runtime_snapshot.reasoning_trace is not None:
        selected.extend(
            skill
            for trace in runtime_snapshot.reasoning_trace.skill_selections
            if trace.status == "selected"
            for skill in trace.selected_skills
        )
    if any(
        _skill_identity(ref) not in catalog_members.get(ref.category, set())
        for ref in selected
    ):
        raise LearnerSourceError(
            "learner_skill_catalog_integrity_error",
            retryable=False,
        )


class LearnerSource:
    def __init__(
        self,
        *,
        learners,
        reviews: ConversationReviewOwner,
        review_source: ConversationReviewSource,
        input_reader: TurnInputProjectionAuditReader,
        runtime: TurnRuntimeOwner,
        governance: ResultGovernanceDraftOwnerV2,
        turn_experiences: TurnExperienceStore,
        skill_catalog: PromptSkillCatalog,
        skill_exact_reader: PromptSkillExactReader,
    ) -> None:
        self._learners = learners
        self._reviews = reviews
        self._review_source = review_source
        self._input_reader = input_reader
        self._runtime = runtime
        self._governance = governance
        self._turn_experiences = turn_experiences
        self._skill_catalog = skill_catalog
        self._skill_exact_reader = skill_exact_reader

    def assemble(self, claim: LearnerRunClaimV1) -> LearnerCasePacketV1:
        run = self._learners.read_run(claim.run_ref)
        if run is None or run.source.experience_ref != claim.experience_ref:
            raise LearnerSourceError("learner_source_identity_mismatch", retryable=False)
        source = run.source
        review = self._reviews.read(source.review_ref)
        if review is None or review.status != "completed":
            raise LearnerSourceError("learner_source_unavailable", retryable=True)
        if (
            review.review_digest != source.review_digest
            or review.snapshot.snapshot_digest != source.snapshot_digest
        ):
            raise LearnerSourceError("learner_source_digest_mismatch", retryable=False)
        if source.case_ordinal > len(review.cases):
            raise LearnerSourceError("learner_source_identity_mismatch", retryable=False)
        case = review.cases[source.case_ordinal - 1]
        digest = learner_case_digest(
            review_ref=source.review_ref,
            review_digest=source.review_digest,
            case_ordinal=source.case_ordinal,
            case=case,
        )
        if (
            digest != source.case_digest
            or case.title != source.case_title
            or case.involved_turn_ids != source.involved_turn_ids
            or case.primary_assistant_turn_id != source.primary_assistant_turn_id
        ):
            raise LearnerSourceError("learner_source_identity_mismatch", retryable=False)
        try:
            transcript = self._review_source.rehydrate(review.snapshot)
        except ConversationReviewSourceError as exc:
            raise LearnerSourceError(
                "learner_source_digest_mismatch", retryable=False
            ) from exc

        transcript_turns = {turn.turn_id: turn for turn in transcript.turns}
        snapshot_turns = {turn.turn_id: turn for turn in review.snapshot.turns}
        executions: list[LearnerExecutionPacketV1] = []
        evidence_ids: list[str] = []
        for turn_id in source.involved_turn_ids:
            if turn_id not in transcript_turns or turn_id not in snapshot_turns:
                raise LearnerSourceError("learner_source_identity_mismatch", retryable=False)
            snapshot_turn = snapshot_turns[turn_id]
            runtime_snapshot = self._runtime.snapshot(snapshot_turn.execution_id)
            if (
                runtime_snapshot.execution_id != snapshot_turn.execution_id
                or runtime_snapshot.turn_id != turn_id
                or runtime_snapshot.conversation_id != review.snapshot.conversation_id
            ):
                raise LearnerSourceError("learner_source_identity_mismatch", retryable=False)
            projection = self._input_reader.get_input_projection(snapshot_turn.execution_id)
            outcome = self._runtime.terminal_outcome(snapshot_turn.execution_id)
            if projection is None or outcome is None:
                raise LearnerSourceError("learner_source_unavailable", retryable=True)
            governed = None
            turn_experience = None
            if outcome.outcome == "completed":
                if outcome.governed_answer_draft_ref is None:
                    raise LearnerSourceError("learner_source_digest_mismatch", retryable=False)
                governed = self._governance.read_v2(outcome.governed_answer_draft_ref)
                if governed is None or governed.execution_id != snapshot_turn.execution_id:
                    raise LearnerSourceError("learner_source_digest_mismatch", retryable=False)
                turn_experience = self._turn_experiences.read_for_execution(
                    snapshot_turn.execution_id, "turn-experience-v1"
                )
                if turn_experience is None:
                    raise LearnerSourceError(
                        "learner_turn_experience_unavailable", retryable=True
                    )
            exact_catalogs = [self._read_catalog(ref) for ref in runtime_snapshot.prompt_skill_catalogs]
            _require_selected_skill_membership(runtime_snapshot, exact_catalogs)
            try:
                packet = LearnerExecutionPacketV1(
                    input_projection=projection,
                    runtime_snapshot=runtime_snapshot,
                    terminal_outcome=outcome,
                    governed_answer=governed,
                    turn_experience=turn_experience,
                    exact_catalogs=exact_catalogs,
                )
            except ValueError as exc:
                raise LearnerSourceError(
                    "learner_source_identity_mismatch", retryable=False
                ) from exc
            executions.append(packet)
            evidence_ids.extend(
                [
                    f"turn:{turn_id}:user",
                    f"turn:{turn_id}:understanding",
                    f"turn:{turn_id}:answer",
                ]
            )
            if runtime_snapshot.reasoning_trace is not None:
                for plan in runtime_snapshot.reasoning_trace.plans:
                    evidence_ids.append(f"turn:{turn_id}:planner:{plan.generation}")
            for catalog in exact_catalogs:
                for skill in catalog.skills:
                    evidence_ids.append(
                        f"turn:{turn_id}:skill:{skill.ref.category}:{skill.ref.name}:{skill.ref.revision}"
                    )
            if turn_experience is not None:
                evidence_ids.append(f"turn:{turn_id}:closed-facts")

        planner_applicability = self._planner_applicability(executions)
        manifests = [
            self._manifest("understanding", executions),
            self._manifest("planner", executions),
            self._manifest("answer", executions),
        ]
        return LearnerCasePacketV1(
            source=source,
            case=case,
            transcript=transcript,
            executions=executions,
            planner_applicability=planner_applicability,
            capability_manifests=manifests,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
        )

    def _read_catalog(self, ref) -> LearnerExactCatalogV1:
        try:
            catalog = self._skill_catalog.read_catalog(ref)
        except Exception as exc:
            if _is_prompt_skill_integrity_error(exc):
                raise LearnerSourceError(
                    "learner_skill_catalog_integrity_error",
                    retryable=False,
                ) from exc
            raise
        if catalog.ref != ref:
            raise LearnerSourceError(
                "learner_skill_catalog_integrity_error", retryable=False
            )
        exact: list[LearnerExactSkillV1] = []
        for candidate in catalog.skills:
            try:
                instructions = self._skill_exact_reader.read_instructions(candidate.ref)
                exact.append(LearnerExactSkillV1(ref=candidate.ref, instructions=instructions))
            except Exception as exc:
                if _is_prompt_skill_integrity_error(exc):
                    raise LearnerSourceError(
                        "learner_skill_catalog_integrity_error",
                        retryable=False,
                    ) from exc
                raise
        try:
            return LearnerExactCatalogV1(catalog=catalog, skills=exact)
        except ValueError as exc:
            raise LearnerSourceError(
                "learner_skill_catalog_integrity_error", retryable=False
            ) from exc

    @staticmethod
    def _planner_applicability(
        executions: list[LearnerExecutionPacketV1],
    ) -> Literal["applicable", "unavailable", "not_applicable"]:
        if all(item.runtime_snapshot.reasoning_mode == "standard" for item in executions):
            return "not_applicable"
        deep = [item for item in executions if item.runtime_snapshot.reasoning_mode == "deep"]
        if any(item.runtime_snapshot.reasoning_trace is None for item in deep):
            return "unavailable"
        return "applicable"

    @staticmethod
    def _manifest(
        node: LearnerNode, executions: list[LearnerExecutionPacketV1]
    ) -> LearnerCapabilityManifestV1:
        category = node
        candidates: list[PromptSkillRefV1] = []
        selected: list[PromptSkillRefV1] = []
        for item in executions:
            catalog = next(
                (
                    exact
                    for exact in item.exact_catalogs
                    if exact.catalog.ref.category == category
                ),
                None,
            )
            if catalog is not None:
                candidates.extend(skill.ref for skill in catalog.skills)
            if node in {"understanding", "answer"}:
                selected.extend(
                    skill
                    for trace in item.runtime_snapshot.prompt_skill_selections
                    if trace.category == category and trace.status == "selected"
                    for skill in trace.selected_skills
                )
            elif item.runtime_snapshot.reasoning_trace is not None:
                selected.extend(
                    skill
                    for trace in item.runtime_snapshot.reasoning_trace.skill_selections
                    if trace.status == "selected"
                    for skill in trace.selected_skills
                )
        candidates = list(
            {
                (ref.category, ref.name, ref.revision, ref.content_digest): ref
                for ref in candidates
            }.values()
        )
        selected = list(
            {
                (ref.category, ref.name, ref.revision, ref.content_digest): ref
                for ref in selected
            }.values()
        )
        allowed: list[SkillIssueType] = []
        if selected:
            allowed.extend(["wrong_skill_selected", "selected_skill_underperformed"])
        if candidates:
            allowed.append("missing_suitable_skill")
        allowed.extend(["not_skill_related", "indeterminate"])
        return LearnerCapabilityManifestV1(
            node=node,
            selected_skill_refs=selected,
            candidate_skill_refs=candidates,
            allowed_issue_types=allowed,
        )


__all__ = [
    "LearnerCapabilityManifestV1",
    "LearnerCasePacketV1",
    "LearnerExactCatalogV1",
    "LearnerExactSkillV1",
    "LearnerExecutionPacketV1",
    "LearnerSource",
    "LearnerSourceError",
]
