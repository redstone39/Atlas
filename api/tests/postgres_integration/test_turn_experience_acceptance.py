from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete

from atlas_production.infrastructure import composition
from atlas_production.infrastructure.persistence.base import OrmBase
from atlas_production.infrastructure.postgres_owner.turn_experience import (
    PostgresTurnExperienceStore,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.turn_execution_orchestrator import (
    _next_reasoning_trace,
)
from atlas_production.infrastructure.turn_experience_reconciler import (
    TurnExperienceReconciler,
)
from atlas_production.infrastructure.turn_experience_recorder import (
    TurnExperienceRecorder,
)
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    AllocateExecutionV1,
    BeginResultGovernanceV1,
    BindContextV1,
    CommitTerminalV1,
    ExecutionPromptSkillSelectionTraceV1,
    FailCarrierExecutionV1,
    LeasePolicyV1,
    PrepareTerminalV1,
    PromptSkillSelectionTraceV1,
    ReasoningCorrectionV2,
    ReasoningEvaluationV1,
    ReasoningPlanItemV2,
    ReasoningPlanV2,
    RecordExecutionPromptSkillSelectionV1,
    RecordReasoningProgressV1,
    RequestModelActionV1,
    RoutePolicyV1,
)
from tests.test_turn_terminal_drafts import _v2_command
from tests.turn_runtime_fixtures import route_snapshot

PREFIX = "atr020-turn-experience-acceptance-"


def _cleanup(runtime: PostgresRuntime) -> None:
    pattern = f"{PREFIX}%"
    with runtime.session_factory() as session, session.begin():
        for table in reversed(OrmBase.metadata.sorted_tables):
            execution_id = table.columns.get("execution_id")
            if execution_id is not None:
                session.execute(delete(table).where(execution_id.like(pattern)))


@pytest.fixture(autouse=True)
def clean_acceptance_rows(postgres_runtime: PostgresRuntime):
    _cleanup(postgres_runtime)
    yield
    _cleanup(postgres_runtime)


def _stop_background(composed: composition.ApiComposition) -> None:
    composed.turn_execution_carrier.shutdown()
    composed.turn_experience_reconciler.stop()
    composed.turn_resource_release_reconciler.stop()
    composed.turn_lease_failure_sweeper.stop()


def _allocate(
    owner: PostgresTurnRuntimeOwner,
    execution_id: str,
    *,
    reasoning_mode: str = "standard",
    operation: str = "create_turn",
    retry_of_turn_id: str | None = None,
):
    catalogs = [
        PromptSkillCatalogRefV1(
            category="understanding",
            catalog_revision=1,
            catalog_digest="1" * 64,
        ),
        *(
            [
                PromptSkillCatalogRefV1(
                    category="planner",
                    catalog_revision=1,
                    catalog_digest="0" * 64,
                )
            ]
            if reasoning_mode == "deep"
            else []
        ),
        PromptSkillCatalogRefV1(
            category="answer",
            catalog_revision=1,
            catalog_digest="2" * 64,
        ),
    ]
    allocated = owner.allocate(
        AllocateExecutionV1(
            execution_id=execution_id,
            turn_id=f"turn-{execution_id}",
            conversation_id="conversation-experience-acceptance",
            actor_id="actor-experience-acceptance",
            holder_id="holder-experience-acceptance",
            route_policy=RoutePolicyV1(),
            route=route_snapshot(),
            lease_policy=LeasePolicyV1(),
            idempotency_key=f"allocate-{execution_id}",
            operation=operation,
            retry_of_turn_id=retry_of_turn_id,
            input_digest="0" * 64,
            response_language="zh-TW",
            reasoning_mode=reasoning_mode,
            prompt_skill_catalogs=catalogs,
            applied_guidance_revision=0,
            applied_guidance_digest=None,
        )
    )
    accepted = owner.accept(
        AcceptExecutionV1(
            execution_id=execution_id,
            expected_version=allocated.version,
            fencing_token=allocated.lease.fencing_token,
            grant_ref=f"grant-{execution_id}",
            catalog_ref=f"catalog-{execution_id}",
        )
    )
    accepted = owner.record_prompt_skill_selection(
        RecordExecutionPromptSkillSelectionV1(
            execution_id=execution_id,
            expected_version=accepted.version,
            fencing_token=accepted.lease.fencing_token,
            selection=ExecutionPromptSkillSelectionTraceV1(
                category="understanding",
                node="resolver",
                status="not_applicable",
            ),
        )
    )
    return owner.bind_context(
        BindContextV1(
            execution_id=execution_id,
            expected_version=accepted.version,
            fencing_token=accepted.lease.fencing_token,
            context_pack_ref=f"context-{execution_id}",
        )
    )


def _record_rich_deep_trace(owner: PostgresTurnRuntimeOwner, snapshot):
    first_plan = ReasoningPlanV2(
        generation=1,
        next_objective="Inspect the evidence gap.",
        completion_condition="The gap is resolved.",
        items=[ReasoningPlanItemV2(item_id="plan-1", summary="Inspect evidence.")],
    )
    first_evaluation = ReasoningEvaluationV1(
        cycle=1,
        verdict="research_then_revise",
        finding_codes=["coverage_gap"],
        summary="More evidence is required.",
        score={
            "plan_coverage": 1,
            "evidence_handling": 1,
            "conflict_handling": 1,
            "gap_resolution": 0,
            "revision_completion": 1,
            "total": 4,
        },
    )
    initial_trace = _next_reasoning_trace(
        None,
        status="running",
        plans=[first_plan],
        evaluations=[first_evaluation],
        corrections=[],
        prompt_skill_catalog=snapshot.prompt_skill_catalogs[1],
        appended_skill_selection=PromptSkillSelectionTraceV1(
            node="deep_initial_planner",
            plan_generation=1,
            status="not_applicable",
        ),
    )
    started = owner.record_reasoning_progress(
        RecordReasoningProgressV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            trace=initial_trace,
            phase="evaluating",
            progress_status="completed",
            cycle=1,
            message_code="reasoning.evaluation_completed",
            message_params={"cycle": 1},
        )
    )
    second_plan = ReasoningPlanV2(
        generation=2,
        parent_generation=1,
        next_objective="Revise with the new evidence.",
        completion_condition="The revision is accepted.",
        items=[
            ReasoningPlanItemV2(
                item_id="plan-1",
                summary="Inspect evidence.",
                status="completed",
            )
        ],
    )
    second_evaluation = ReasoningEvaluationV1(
        cycle=2,
        verdict="accept",
        summary="The revision closes the gap.",
        score={
            "plan_coverage": 2,
            "evidence_handling": 2,
            "conflict_handling": 2,
            "gap_resolution": 2,
            "revision_completion": 2,
            "total": 10,
        },
    )
    rich_trace = _next_reasoning_trace(
        initial_trace,
        status="completed",
        plans=[first_plan, second_plan],
        evaluations=[first_evaluation, second_evaluation],
        corrections=[
            ReasoningCorrectionV2(
                cycle=1,
                kind="research_then_revise",
                triggering_evaluation=1,
                plan_generation=2,
                tool_invocation_start=1,
                tool_invocation_end=2,
                result_evaluation=2,
                addressed_finding_codes=["coverage_gap"],
                summary="Research completed.",
            )
        ],
        appended_skill_selection=PromptSkillSelectionTraceV1(
            node="deep_replanner",
            plan_generation=2,
            status="not_applicable",
        ),
        termination_reason="completed",
    )
    return owner.record_reasoning_progress(
        RecordReasoningProgressV1(
            execution_id=snapshot.execution_id,
            expected_version=started.version,
            fencing_token=snapshot.lease.fencing_token,
            trace=rich_trace,
            phase="evaluating",
            progress_status="completed",
            cycle=2,
            message_code="reasoning.evaluation_completed",
            message_params={"cycle": 2},
        )
    )


def _complete(owner, governance, snapshot):
    draft_ref = f"answer-draft-{snapshot.execution_id}"
    governance.materialize_v2(
        _v2_command().model_copy(
            update={
                "draft_ref": draft_ref,
                "execution_id": snapshot.execution_id,
                "idempotency_key": f"governance-{snapshot.execution_id}",
            }
        )
    )
    requested = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            context_tokens=1,
        )
    )
    governing = owner.begin_governance(
        BeginResultGovernanceV1(
            execution_id=snapshot.execution_id,
            expected_version=requested.version,
            fencing_token=snapshot.lease.fencing_token,
            finalize_action_digest="f" * 64,
        )
    )
    prepared = owner.prepare_terminal(
        PrepareTerminalV1(
            execution_id=snapshot.execution_id,
            expected_version=governing.version,
            fencing_token=snapshot.lease.fencing_token,
            evidence_pack_ref=f"evidence-pack-{snapshot.execution_id}",
            governed_answer_draft_ref=draft_ref,
            citation_binding_draft_ref=f"citation-draft-{snapshot.execution_id}",
            audit_draft_ref=f"audit-draft-{snapshot.execution_id}",
        )
    )
    return owner.commit_terminal(
        CommitTerminalV1(
            execution_id=snapshot.execution_id,
            expected_version=prepared.version,
            fencing_token=snapshot.lease.fencing_token,
            terminal_commit_intent_ref=prepared.terminal_commit_intent_ref,
        )
    )


class _FailFirstStore:
    def __init__(self, store) -> None:
        self.store = store
        self.calls = 0

    def materialize(self, command):
        self.calls += 1
        raise RuntimeError("injected first write failure")


def test_production_composition_records_and_recovers_complete_experience_journey(
    postgres_runtime: PostgresRuntime,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        composition,
        "_active_artifact_filesystem",
        lambda runtime: object(),
    )
    prefix = f"{PREFIX}{uuid4().hex}-"
    first = composition.build_api_composition(postgres_runtime)
    first.turn_experience_reconciler.stop()
    first.turn_resource_release_reconciler.stop()
    first.turn_lease_failure_sweeper.stop()
    try:
        reconciler = first.turn_experience_reconciler
        recorder = reconciler._recorder
        runtime = reconciler._runtime
        governance = recorder._governance
        store = recorder._store
        orchestrator = first.turn_execution_carrier._orchestrator

        assert isinstance(reconciler, TurnExperienceReconciler)
        assert isinstance(runtime, PostgresTurnRuntimeOwner)
        assert isinstance(recorder, TurnExperienceRecorder)
        assert isinstance(store, PostgresTurnExperienceStore)
        assert orchestrator._experience_recorder is recorder
        assert recorder._runtime is runtime

        standard_id = f"{prefix}standard"
        deep_id = f"{prefix}deep"
        failed_id = f"{prefix}failed"
        recovery_id = f"{prefix}recovery"
        retry_source_id = f"{prefix}retry-source"
        retry_id = f"{prefix}retry"

        _complete(runtime, governance, _allocate(runtime, standard_id))
        standard = recorder.record_execution(standard_id)
        assert standard.deep_trace is None
        assert recorder.record_execution(standard_id) == standard

        deep_snapshot = _record_rich_deep_trace(
            runtime,
            _allocate(runtime, deep_id, reasoning_mode="deep"),
        )
        _complete(runtime, governance, deep_snapshot)
        deep = recorder.record_execution(deep_id)
        assert deep.deep_trace is not None
        assert deep.deep_trace.corrections[0].kind == "research_then_revise"
        assert [item.cycle for item in deep.deep_trace.evaluations] == [1, 2]

        failed_snapshot = _allocate(runtime, failed_id)
        runtime.fail_carrier(
            FailCarrierExecutionV1(
                execution_id=failed_id,
                expected_version=failed_snapshot.version,
                holder_id=failed_snapshot.lease.holder_id,
                expected_lease_version=failed_snapshot.lease.lease_version,
                fencing_token=failed_snapshot.lease.fencing_token,
                failure_code="provider_failed",
                detected_by="carrier",
            )
        )

        _complete(runtime, governance, _allocate(runtime, recovery_id))
        original_store = recorder._store
        failing_store = _FailFirstStore(original_store)
        recorder._store = failing_store
        with pytest.raises(RuntimeError, match="injected first write failure"):
            recorder.record_execution(recovery_id)
        recorder._store = original_store
        assert runtime.terminal_outcome(recovery_id).outcome == "completed"
        assert store.read_for_execution(recovery_id, "turn-experience-v1") is None

        source = _allocate(runtime, retry_source_id)
        _complete(runtime, governance, source)
        recorder.record_execution(retry_source_id)
        retry = _allocate(
            runtime,
            retry_id,
            operation="retry_turn",
            retry_of_turn_id=source.turn_id,
        )
        _complete(runtime, governance, retry)
        recorder.record_execution(retry_id)
    finally:
        _stop_background(first)

    restarted = composition.build_api_composition(postgres_runtime)
    try:
        recorder = restarted.turn_experience_reconciler._recorder
        store = recorder._store
        recovered = store.read_for_execution(recovery_id, "turn-experience-v1")
        assert recovered is not None
        assert recovered.execution_id == recovery_id
        assert store.read_for_execution(failed_id, "turn-experience-v1") is None
        assert store.read_for_execution(retry_source_id, "turn-experience-v1") is not None
        assert store.read_for_execution(retry_id, "turn-experience-v1") is not None

        rows = store.list_after(None, 100)
        selected = [row for row in rows if row.execution_id.startswith(prefix)]
        assert {row.execution_id for row in selected} == {
            standard_id,
            deep_id,
            recovery_id,
            retry_source_id,
            retry_id,
        }
        assert [(row.scan_sequence, row.execution_id) for row in selected] == sorted(
            (row.scan_sequence, row.execution_id) for row in selected
        )
    finally:
        _stop_background(restarted)
