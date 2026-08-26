from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from atlas_production.infrastructure.conversation_review_source import (
    ConversationReviewTranscriptSegmentV1,
    ConversationReviewTranscriptTurnV1,
    ConversationReviewTranscriptV1,
)
from atlas_production.modules.consolidator.public import (
    ConsolidatedExperienceV1,
    ConsolidationV1,
)
from atlas_production.modules.conversation_review.public import (
    REVIEW_PROMPT_REVISION,
    SEMANTIC_QUIET_PERIOD,
    ConversationLearningCaseProposalV1,
    ConversationReviewProposalV1,
    ConversationReviewSnapshotTurnV1,
    ConversationReviewSnapshotV1,
    conversation_review_ref,
    conversation_review_snapshot_digest,
)
from atlas_production.modules.learner.public import (
    LearnerExperiencePayloadV1,
    LearnerExperienceSynthesisV1,
    LearnerLayerDiagnosisV1,
    LearnerSourceIdentityV1,
)
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.skill_designer.public import (
    SkillCandidateDraftV1,
    SkillCandidateEvidenceRefV1,
    add_draft_key,
)
PUBLIC_SECRET_VALUE = "public-synthetic-sensitive-value"

PUBLIC_NOW = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)
PUBLIC_DIGEST_A = "a" * 64
PUBLIC_DIGEST_B = "b" * 64


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synthetic_snapshot_turn(position: int) -> ConversationReviewSnapshotTurnV1:
    suffix = str(position)
    return ConversationReviewSnapshotTurnV1(
        position=position,
        turn_id=f"public-synthetic-turn-{suffix}",
        execution_id=f"public-synthetic-execution-{suffix}",
        input_projection_ref=f"public-synthetic-input-{suffix}",
        user_text_digest=PUBLIC_DIGEST_A if position == 1 else PUBLIC_DIGEST_B,
        terminal_status="completed",
        terminal_scan_sequence=position,
        terminal_commit_intent_ref=f"public-synthetic-intent-{suffix}",
        terminal_committed_at=PUBLIC_NOW + timedelta(minutes=position),
        governed_answer_draft_ref=f"public-synthetic-answer-{suffix}",
        governed_answer_digest=PUBLIC_DIGEST_B if position == 1 else PUBLIC_DIGEST_A,
    )


def synthetic_review_snapshot(
    *, conversation_suffix: str = "1"
) -> ConversationReviewSnapshotV1:
    turns = [synthetic_snapshot_turn(1), synthetic_snapshot_turn(2)]
    latest_activity = PUBLIC_NOW + timedelta(minutes=2)
    conversation_id = f"public-synthetic-conversation-{conversation_suffix}"
    digest = conversation_review_snapshot_digest(
        conversation_id=conversation_id,
        conversation_updated_at=latest_activity,
        expected_next_ordinal=3,
        latest_semantic_activity_at=latest_activity,
        turns=turns,
    )
    return ConversationReviewSnapshotV1(
        review_ref=conversation_review_ref(
            conversation_id=conversation_id,
            snapshot_digest=digest,
            review_prompt_revision=REVIEW_PROMPT_REVISION,
        ),
        conversation_id=conversation_id,
        conversation_updated_at=latest_activity,
        expected_next_ordinal=3,
        latest_semantic_activity_at=latest_activity,
        eligible_at=latest_activity + SEMANTIC_QUIET_PERIOD,
        snapshot_digest=digest,
        turns=turns,
    )


def synthetic_review_transcript(
    *, conversation_suffix: str = "1"
) -> ConversationReviewTranscriptV1:
    identity_suffix = "" if conversation_suffix == "1" else f"-{conversation_suffix}"
    snapshot = synthetic_review_snapshot(conversation_suffix=conversation_suffix)
    return ConversationReviewTranscriptV1(
        review_ref=snapshot.review_ref,
        conversation_id=snapshot.conversation_id,
        snapshot_digest=snapshot.snapshot_digest,
        turns=[
            ConversationReviewTranscriptTurnV1(
                position=1,
                turn_id=f"public-synthetic-turn{identity_suffix}-1",
                execution_id=f"public-synthetic-execution{identity_suffix}-1",
                original_user_text=(
                    "public-synthetic user asks for a bounded planning comparison "
                    f"with token=「{PUBLIC_SECRET_VALUE}」"
                ),
                final_governed_assistant_segments=[
                    ConversationReviewTranscriptSegmentV1(
                        segment_id=f"public-synthetic-segment{identity_suffix}-1",
                        text=(
                            "public-synthetic assistant gives an incomplete comparison"
                        ),
                    )
                ],
                terminal_status="completed",
            ),
            ConversationReviewTranscriptTurnV1(
                position=2,
                turn_id=f"public-synthetic-turn{identity_suffix}-2",
                execution_id=f"public-synthetic-execution{identity_suffix}-2",
                original_user_text=(
                    "public-synthetic user requests the missing tradeoff analysis"
                ),
                final_governed_assistant_segments=[
                    ConversationReviewTranscriptSegmentV1(
                        segment_id=f"public-synthetic-segment{identity_suffix}-2",
                        text="public-synthetic assistant supplies the missing tradeoffs",
                    )
                ],
                terminal_status="completed",
            ),
        ],
    )


def synthetic_learning_case(
    ordinal: int = 1,
) -> ConversationLearningCaseProposalV1:
    return ConversationLearningCaseProposalV1(
        case_ordinal=ordinal,
        title=f"public-synthetic-case-{ordinal}",
        learning_evidence="public-synthetic-evidence",
        generalization_hypothesis="public-synthetic-hypothesis",
        investigation_question="public-synthetic-question",
        selection_rationale="public-synthetic-rationale",
        involved_turn_ids=["public-synthetic-turn-1", "public-synthetic-turn-2"],
        primary_assistant_turn_id="public-synthetic-turn-1",
    )


def synthetic_review_proposal(case_count: int = 1) -> ConversationReviewProposalV1:
    return ConversationReviewProposalV1(
        cases=[synthetic_learning_case(index) for index in range(1, case_count + 1)]
    )


def synthetic_catalog_refs() -> list[PromptSkillCatalogRefV1]:
    return [
        PromptSkillCatalogRefV1(
            category=category,
            catalog_revision=index,
            catalog_digest=str(index) * 64,
        )
        for index, category in enumerate(
            ("understanding", "planner", "answer"),
            start=1,
        )
    ]


def synthetic_learner_payload(
    source: LearnerSourceIdentityV1,
    *, invocation_suffix: str,
) -> LearnerExperiencePayloadV1:
    layers = [
        LearnerLayerDiagnosisV1(
            node="understanding",
            applicability="applicable",
            verdict="pass",
            relation="none",
            expected_behavior="Identify the requested comparison.",
            observed_behavior="The request was identified.",
            supporting_observations=["public-synthetic-observation-understanding"],
            evidence_ids=["public-synthetic-turn-1"],
        ),
        LearnerLayerDiagnosisV1(
            node="planner",
            applicability="applicable",
            verdict="fail",
            relation="origin",
            expected_behavior="Group evidence by claim before drafting.",
            observed_behavior="Evidence was grouped only by source.",
            divergence="Claim-level structure was absent.",
            propagation_effect="The final comparison obscured tradeoffs.",
            supporting_observations=["public-synthetic-observation-planner"],
            evidence_ids=["public-synthetic-turn-2"],
        ),
        LearnerLayerDiagnosisV1(
            node="answer",
            applicability="applicable",
            verdict="pass",
            relation="corrected",
            expected_behavior="State supported tradeoffs.",
            observed_behavior="The answer stated supported tradeoffs.",
            supporting_observations=["public-synthetic-observation-answer"],
            evidence_ids=["public-synthetic-turn-2"],
        ),
    ]
    synthesis = LearnerExperienceSynthesisV1(
        outcome="supported",
        scenario_context="A public synthetic multi-source comparison.",
        user_goal="Compare options using traceable evidence.",
        explicit_requirements=["Keep each claim tied to evidence."],
        explicit_constraints=["Use only the supplied public synthetic facts."],
        expected_behavior="Structure evidence by claim before drafting.",
        observed_behavior="Evidence was structured by source.",
        user_impact="Tradeoffs were harder to verify.",
        correction_signal="Reorganize the evidence around claims.",
        failure_statement="The plan omitted claim-level evidence structure.",
        problem_pattern="Source-first grouping can obscure cross-source tradeoffs.",
        trigger_conditions=["Several sources support overlapping claims."],
        desired_behavior="Create a claim-to-evidence map first.",
        prohibited_behavior="Do not group solely by source.",
        rationale="Claim-first grouping keeps comparisons auditable.",
        applicability_boundaries=["Use when more than one source is available."],
        target_nodes=["planner"],
        behavior_kinds=["evidence-organization"],
        evidence_sufficiency="complete",
        supporting_observations=["The correction improved the final comparison."],
    )
    return LearnerExperiencePayloadV1(
        source=source,
        layers=layers,
        origin_status="confirmed",
        origin_node="planner",
        synthesis=synthesis,
        route_id="public-synthetic-learner-route",
        route_revision=1,
        runtime_policy_revision=1,
        model_invocation_refs=[f"public-synthetic-model-invocation-{invocation_suffix}"],
        audit_lineage=[f"public-synthetic-audit-{invocation_suffix}"],
    )


def synthetic_consolidated_experience(
    experience_refs: list[str],
) -> ConsolidatedExperienceV1:
    return ConsolidatedExperienceV1(
        behavior="Build a claim-to-evidence map before comparative drafting.",
        applicability="Multi-source comparisons with overlapping claims.",
        supporting_experience_refs=experience_refs,
        counterexample_experience_refs=[],
        unresolved_issue="Validate the behavior when only one source is available.",
    )


def synthetic_candidate_draft(
    consolidation: ConsolidationV1,
    *,
    catalog_refs: list[PromptSkillCatalogRefV1] | None = None,
) -> SkillCandidateDraftV1:
    topic = "public synthetic evidence structure"
    goal = "organize comparative evidence by claim"
    source = (
        "---\n"
        "name: structure-research\n"
        "description: Organize comparative evidence by claim.\n"
        "---\n"
        "Build a claim-to-evidence map before drafting the comparison.\n"
    )
    return SkillCandidateDraftV1(
        disposition="add",
        category="planner",
        target_name="structure-research",
        topic=topic,
        goal=goal,
        draft_key=add_draft_key(category="planner", topic=topic, goal=goal),
        source_evidence=[
            SkillCandidateEvidenceRefV1(
                consolidation_ref=consolidation.consolidation_ref,
                consolidation_digest=consolidation.digest,
                generalized_experience_ordinal=1,
            )
        ],
        observed_catalog_refs=catalog_refs or synthetic_catalog_refs(),
        matched_skill_refs=[],
        skill_source=source,
        skill_source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        rationale="The public synthetic evidence supports a reusable planning behavior.",
        risk="Apply only to comparative tasks with multiple sources.",
    )


PUBLIC_RESEARCH_ID = "public-synthetic-research-1"
PUBLIC_RESEARCH_EXECUTION_ID = "public-synthetic-research-execution-1"
PUBLIC_RESEARCH_SCOPE_REF = "public-synthetic-scope-ref-1"
PUBLIC_RESEARCH_PACKET_REF = "public-synthetic-packet-ref-1"
PUBLIC_RESEARCH_EVIDENCE_ID = "public-synthetic-evidence-1"
PUBLIC_RESEARCH_EVIDENCE_HANDLE = "public-synthetic-evidence-handle-1"
PUBLIC_RESEARCH_QUESTION = "Compare the public synthetic evidence."
PUBLIC_RESEARCH_SCOPE = {
    "mode": "selected",
    "refs": [{"kind": "project", "id": "public-synthetic-project-1"}],
}


def synthetic_research_packet_payload() -> dict[str, object]:
    return {
        "research_id": PUBLIC_RESEARCH_ID,
        "execution_id": PUBLIC_RESEARCH_EXECUTION_ID,
        "question_ref": "public-synthetic-question-ref-1",
        "scope_ref": PUBLIC_RESEARCH_SCOPE_REF,
        "scope_digest": canonical_digest(PUBLIC_RESEARCH_SCOPE),
        "findings": [
            {
                "finding_id": "public-synthetic-finding-1",
                "text": "The public synthetic evidence supports the comparison.",
                "evidence_ids": [PUBLIC_RESEARCH_EVIDENCE_ID],
                "evidence_assessment": "aligned",
            }
        ],
        "unresolved_questions": [
            "Which additional public synthetic source should be evaluated?"
        ],
        "research_limits": [
            {
                "code": "public-synthetic-source-limit",
                "detail": "Only one public synthetic source was available.",
            }
        ],
        "evidence": [
            {
                "evidence_id": PUBLIC_RESEARCH_EVIDENCE_ID,
                "kind": "text",
                "title": "Public synthetic evidence",
                "page": 1,
                "locator": "public-synthetic://evidence/1",
                "available_representations": ["text"],
                "lineage_digest": canonical_digest(
                    {
                        "handle": PUBLIC_RESEARCH_EVIDENCE_HANDLE,
                        "project_id": "public-synthetic-project-1",
                    }
                ),
            }
        ],
    }
