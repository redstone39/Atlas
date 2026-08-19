from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.turn_experience_recorder import (
    TurnExperienceRecorder,
    TurnExperienceRecordingError,
    project_turn_experience,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogRefV1,
    PromptSkillRefV1,
)
from atlas_production.modules.result_governance.public import (
    GovernedAnswerDraftV2,
    GovernedAnswerSegmentV2,
    PostHocAnswerAssessmentV2,
)
from atlas_production.modules.retrieval.public import DeclaredEvidenceMappingV1
from atlas_production.modules.turn_experience.public import MaterializeTurnExperienceV1
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionPromptSkillSelectionTraceV1,
    ExecutionSnapshotV1,
    ProcessScoreV1,
    PromptSkillSelectionTraceV1,
    ProvisionalEvidenceCheckV1,
    ReasoningCorrectionV2,
    ReasoningEvaluationV1,
    ReasoningLimitFinalizationV2,
    ReasoningPlanItemV2,
    ReasoningPlanV2,
    ReasoningTraceV4,
    RoutePolicyV1,
    TerminalOutcomeV1,
    TurnRouteSnapshotV2,
)


NOW = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
CATALOG = PromptSkillCatalogRefV1(
    category="planner", catalog_revision=7, catalog_digest="7" * 64
)
UNDERSTANDING_CATALOG = PromptSkillCatalogRefV1(
    category="understanding", catalog_revision=1, catalog_digest="1" * 64
)
ANSWER_CATALOG = PromptSkillCatalogRefV1(
    category="answer", catalog_revision=1, catalog_digest="2" * 64
)
SCORE = ProcessScoreV1(
    plan_coverage=2,
    evidence_handling=2,
    conflict_handling=1,
    gap_resolution=1,
    revision_completion=2,
    total=8,
)


def _trace(
    *,
    verdict: str = "accept",
    consistency: str = "aligned",
    rich: bool = False,
) -> ReasoningTraceV4:
    unavailable = verdict == "unavailable"
    evaluations = [
        ReasoningEvaluationV1(
            cycle=1,
            verdict=("research_then_revise" if rich else verdict),
            finding_codes=["missing_context"] if verdict != "accept" or rich else [],
            summary="must never persist evaluator prose",
            score=None if unavailable else SCORE,
            unavailable_reason="provider_unavailable" if unavailable else None,
        )
    ]
    plans = [
        ReasoningPlanV2(
            generation=1,
            next_objective="must never persist objective",
            completion_condition="must never persist completion condition",
            items=[
                ReasoningPlanItemV2(
                    item_id="secret-plan-item", summary="must never persist plan summary", status="completed"
                )
            ],
        )
    ]
    corrections = []
    selections = [
        PromptSkillSelectionTraceV1(
            node="deep_initial_planner",
            plan_generation=1,
            status="selected",
            selected_skills=[
                PromptSkillRefV1(
                    category="planner",
                    name="source-review",
                    revision=3,
                    content_digest="8" * 64,
                )
            ],
        )
    ]
    checks = [
        ProvisionalEvidenceCheckV1(
            ordinal=1,
            candidate_kind="normal",
            linked_evaluation_cycle=1,
            consistency=consistency,
            reason_code=("aligned" if consistency == "aligned" else "declared_evidence_insufficient"),
            candidate_disposition="accepted" if consistency == "aligned" else "degraded",
            answer_digest="a" * 64,
            declared_subset_digest="b" * 64,
            assessment_input_digest="c" * 64,
            assessment_output_digest="d" * 64,
        )
    ]
    if rich:
        plans.append(
            ReasoningPlanV2(
                generation=2,
                parent_generation=1,
                next_objective="replanned secret objective",
                completion_condition="replanned secret completion",
                items=[
                    ReasoningPlanItemV2(
                        item_id="secret-plan-item", summary="replanned secret", status="completed"
                    )
                ],
            )
        )
        evaluations.append(
            ReasoningEvaluationV1(
                cycle=2,
                verdict="accept",
                summary="accepted summary must not persist",
                score=ProcessScoreV1(
                    plan_coverage=2,
                    evidence_handling=2,
                    conflict_handling=2,
                    gap_resolution=2,
                    revision_completion=2,
                    total=10,
                ),
            )
        )
        corrections.append(
            ReasoningCorrectionV2(
                cycle=1,
                kind="research_then_revise",
                triggering_evaluation=1,
                plan_generation=2,
                tool_invocation_start=2,
                tool_invocation_end=3,
                result_evaluation=2,
                addressed_finding_codes=["missing_context"],
                summary="must never persist correction prose",
            )
        )
        selections.append(
            PromptSkillSelectionTraceV1(
                node="deep_replanner",
                plan_generation=2,
                status="baseline_fallback",
                fallback_code="selector_unavailable",
            )
        )
        checks[0] = checks[0].model_copy(
            update={"candidate_disposition": "revised", "consistency": "insufficient"}
        )
        checks.append(
            ProvisionalEvidenceCheckV1(
                ordinal=2,
                candidate_kind="normal",
                linked_evaluation_cycle=2,
                consistency="aligned",
                reason_code="aligned",
                candidate_disposition="accepted",
                answer_digest="e" * 64,
                declared_subset_digest="f" * 64,
                assessment_input_digest="1" * 64,
                assessment_output_digest="2" * 64,
            )
        )
    return ReasoningTraceV4(
        prompt_skill_catalog=CATALOG,
        skill_selections=selections,
        trace_revision=2,
        trace_digest="3" * 64,
        parent_trace_digest="4" * 64,
        status="degraded" if unavailable else "completed",
        plans=plans,
        evaluations=evaluations,
        corrections=corrections,
        provisional_evidence_checks=checks,
        termination_reason="evaluator_unavailable" if unavailable else "completed",
    )


def _snapshot(*, mode: str = "standard", trace: ReasoningTraceV4 | None = None) -> ExecutionSnapshotV1:
    return ExecutionSnapshotV1(
        execution_id="execution-1",
        turn_id="turn-1",
        conversation_id="conversation-1",
        actor_id="actor-1",
        state="terminal_completed",
        version=8,
        policy=RoutePolicyV1(),
        route=TurnRouteSnapshotV2(
            route_id="route-1",
            route_revision=2,
            runtime_policy_revision=3,
            tokenizer_profile="tokenizer-1",
            context_window_tokens=8192,
            max_input_tokens_per_invocation=4096,
            max_output_tokens_per_invocation=2048,
            max_tool_result_tokens_per_execution=4096,
            max_total_tokens_per_conversation=16384,
        ),
        input_digest="5" * 64,
        response_language="zh-TW",
        reasoning_mode=mode,
        reasoning_trace=trace,
        prompt_skill_catalogs=(
            [UNDERSTANDING_CATALOG, CATALOG, ANSWER_CATALOG]
            if mode == "deep"
            else [UNDERSTANDING_CATALOG, ANSWER_CATALOG]
        ),
        prompt_skill_selections=[
            ExecutionPromptSkillSelectionTraceV1(
                category="understanding",
                node="resolver",
                status="not_applicable",
            ),
            ExecutionPromptSkillSelectionTraceV1(
                category="answer",
                node="answer_candidate",
                candidate_ordinal=1,
                candidate_kind="normal",
                status="not_applicable",
            ),
        ],
        applied_guidance_revision=4,
        applied_guidance_digest="6" * 64,
        lease=ExecutionLeaseV1(
            execution_id="execution-1",
            holder_id="holder-1",
            lease_version=1,
            fencing_token=1,
            acquired_at=NOW,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        ),
        budget=BudgetSnapshotV1(
            tool_invocations=3,
            catalog_pages=1,
            document_candidates=2,
            search_rounds=1,
            model_visible_items=2,
            provider_invocations=4,
            context_tokens=1200,
            tool_tokens=500,
            retrieval_repairs=1,
            schema_retries=0,
        ),
        terminal_commit_intent_ref="terminal-intent-1",
        deadline_at=NOW + timedelta(minutes=1),
        created_at=NOW,
        updated_at=NOW,
    )


def _outcome(*, completed: bool = True) -> TerminalOutcomeV1:
    if not completed:
        return TerminalOutcomeV1(
            execution_id="execution-1",
            scan_sequence=1,
            outcome="failed",
            failure_code="provider_failed",
            committed_at=NOW,
        )
    return TerminalOutcomeV1(
        execution_id="execution-1",
        scan_sequence=1,
        outcome="completed",
        terminal_commit_intent_ref="terminal-intent-1",
        evidence_pack_ref="evidence-pack-1",
        governed_answer_draft_ref="governed-draft-1",
        citation_binding_draft_ref="citation-draft-1",
        audit_draft_ref="audit-draft-1",
        committed_at=NOW,
    )


def _governed() -> GovernedAnswerDraftV2:
    return GovernedAnswerDraftV2(
        draft_ref="governed-draft-1",
        execution_id="execution-1",
        retrieval_status="evidence_found",
        evidence_review_status="questionable",
        evidence_review_reason_codes=["declared_evidence_not_aligned", "answer_item_failed"],
        declared_evidence_mappings=[
            DeclaredEvidenceMappingV1(
                position=1,
                handle="evidence-handle-1",
                resolution_status="resolved",
                subset_position=1,
                reason_code="resolved",
            ),
            DeclaredEvidenceMappingV1(
                position=2,
                handle="evidence-handle-2",
                resolution_status="unresolved",
                reason_code="unknown_or_out_of_execution",
            ),
        ],
        assessment_state="completed",
        assessment_reason_code="completed",
        assessment_version="provisional-declared-evidence-v1",
        assessment_consistency="insufficient",
        assessment_answer_digest="9" * 64,
        assessment_declared_subset_digest="a" * 64,
        assessment_visual_image_digests=["b" * 64],
        assessment_input_digest="c" * 64,
        assessment_output_digest="d" * 64,
        assessment_results=[
            PostHocAnswerAssessmentV2(id="segment-secret", status="failure")
        ],
        segments=[
            GovernedAnswerSegmentV2(
                segment_id="segment-secret", text="answer content must never persist"
            )
        ],
        digest="e" * 64,
        created_at=NOW,
    )


def test_standard_projection_is_refs_only_and_has_null_deep_trace() -> None:
    command = project_turn_experience(_snapshot(), _outcome(), _governed())

    assert command.experience_ref == "turn-experience:execution-1:v1"
    assert command.deep_trace is None
    assert command.usage.tool_invocations == 3
    assert command.governance.declared_evidence_count == 2
    assert command.governance.resolved_evidence_count == 1
    assert command.governance.assessment_failure_count == 1
    encoded = json.dumps(command.model_dump(mode="json"), sort_keys=True)
    for prohibited in (
        "answer content must never persist",
        "segment-secret",
        "summary",
        "feedback",
        "raw_prompt",
        "segments",
    ):
        assert prohibited not in encoded


def test_deep_projection_preserves_nullable_trace_without_fabrication() -> None:
    command = project_turn_experience(
        _snapshot(mode="deep", trace=None), _outcome(), _governed()
    )

    assert command.reasoning_mode == "deep"
    assert command.deep_trace is None


@pytest.mark.parametrize(
    ("verdict", "unavailable_reason"),
    [
        ("accept", None),
        ("revise_only", None),
        ("research_then_revise", None),
        ("unavailable", "provider_unavailable"),
    ],
)
def test_deep_projection_preserves_each_evaluation_verdict(
    verdict: str, unavailable_reason: str | None
) -> None:
    command = project_turn_experience(
        _snapshot(mode="deep", trace=_trace(verdict=verdict)),
        _outcome(),
        _governed(),
    )

    assert command.deep_trace is not None
    projected = command.deep_trace.evaluations[0]
    assert projected.verdict == verdict
    assert projected.unavailable_reason == unavailable_reason


@pytest.mark.parametrize(
    "consistency",
    ["aligned", "conflict", "insufficient", "not_applicable", "unavailable"],
)
def test_deep_projection_preserves_each_gate_consistency(consistency: str) -> None:
    command = project_turn_experience(
        _snapshot(mode="deep", trace=_trace(consistency=consistency)),
        _outcome(),
        _governed(),
    )

    assert command.deep_trace is not None
    assert command.deep_trace.evidence_checks[0].consistency == consistency


def test_deep_research_correction_keeps_ordered_closed_facts_without_prose() -> None:
    command = project_turn_experience(
        _snapshot(mode="deep", trace=_trace(rich=True)), _outcome(), _governed()
    )

    assert command.deep_trace is not None
    assert [item.generation for item in command.deep_trace.plans] == [1, 2]
    assert [item.cycle for item in command.deep_trace.evaluations] == [1, 2]
    assert command.deep_trace.corrections[0].tool_invocation_start == 2
    assert command.deep_trace.corrections[0].addressed_finding_codes == ["missing_context"]
    assert [item.ordinal for item in command.deep_trace.evidence_checks] == [1, 2]
    encoded = json.dumps(command.deep_trace.model_dump(mode="json"), sort_keys=True)
    for prohibited in (
        "secret-plan-item",
        "must never persist",
        "replanned secret",
        "next_objective",
        "completion_condition",
        "summary",
    ):
        assert prohibited not in encoded


def test_correction_limit_projection_keeps_only_closed_trigger() -> None:
    source = _trace(rich=True)
    trace = ReasoningTraceV4.model_validate(
        {
            **source.model_dump(),
            "termination_reason": "correction_limit_reached",
            "limit_finalization": ReasoningLimitFinalizationV2(
                triggering_evaluation=2,
                summary="limit finalization prose must never persist",
            ),
        }
    )
    command = project_turn_experience(
        _snapshot(mode="deep", trace=trace), _outcome(), _governed()
    )

    assert command.deep_trace is not None
    assert command.deep_trace.termination_reason == "correction_limit_reached"
    assert command.deep_trace.limit_finalization_triggering_evaluation == 2
    assert "limit finalization prose" not in json.dumps(
        command.deep_trace.model_dump(mode="json")
    )


@pytest.mark.parametrize("field", ["raw_prompt", "segments", "evidence_content", "summary", "feedback"])
def test_materialize_contract_rejects_non_allowlisted_content_fields(field: str) -> None:
    payload = project_turn_experience(_snapshot(), _outcome(), _governed()).model_dump()
    payload[field] = "forbidden"
    with pytest.raises(ValidationError):
        MaterializeTurnExperienceV1.model_validate(payload)


class _Runtime:
    def __init__(self, outcome: TerminalOutcomeV1 | None = None) -> None:
        self.outcome = outcome or _outcome()

    def snapshot(self, execution_id: str) -> ExecutionSnapshotV1:
        assert execution_id == "execution-1"
        return _snapshot()

    def terminal_outcome(self, execution_id: str) -> TerminalOutcomeV1:
        assert execution_id == "execution-1"
        return self.outcome


class _Governance:
    def read_v2(self, draft_ref: str) -> GovernedAnswerDraftV2:
        assert draft_ref == "governed-draft-1"
        return _governed()


class _Store:
    def __init__(self) -> None:
        self.commands: list[MaterializeTurnExperienceV1] = []

    def materialize(self, command: MaterializeTurnExperienceV1):
        self.commands.append(command)
        return command


def test_recorder_reads_exact_sources_and_materializes_once() -> None:
    store = _Store()
    result = TurnExperienceRecorder(_Runtime(), _Governance(), store).record_execution(
        "execution-1"
    )

    assert result is store.commands[0]
    assert len(store.commands) == 1


def test_recorder_rejects_failed_terminal_without_store_write() -> None:
    store = _Store()
    with pytest.raises(TurnExperienceRecordingError, match="failed terminal"):
        TurnExperienceRecorder(_Runtime(_outcome(completed=False)), _Governance(), store).record_execution(
            "execution-1"
        )
    assert store.commands == []


def test_projection_rejects_cross_source_and_catalog_mismatches() -> None:
    governed = _governed().model_copy(update={"execution_id": "another-execution"})
    with pytest.raises(TurnExperienceRecordingError, match="cross execution"):
        project_turn_experience(_snapshot(), _outcome(), governed)

    wrong_catalog = PromptSkillCatalogRefV1(
        category="planner", catalog_revision=8, catalog_digest="f" * 64
    )
    snapshot = _snapshot(mode="deep", trace=_trace()).model_copy(
        update={
            "prompt_skill_catalogs": [
                UNDERSTANDING_CATALOG,
                wrong_catalog,
                ANSWER_CATALOG,
            ]
        }
    )
    with pytest.raises(TurnExperienceRecordingError, match="catalog"):
        project_turn_experience(snapshot, _outcome(), _governed())
