from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier, Event, current_thread

import pytest
from sqlalchemy import delete, event, select, update

from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnAcceptanceResourceRow,
    AtlasTurnBudgetCounterRow,
    AtlasTurnExecutionLeaseRow,
    AtlasTurnExecutionRow,
    AtlasTurnRuntimeEventRow,
    AtlasTurnReleaseIntentRow,
    AtlasTurnTerminalIntentRow,
    AtlasTurnTerminalOutcomeRow,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
    TurnRuntimeBudgetExceeded,
    TurnRuntimeCurrentnessConflict,
    TurnRuntimeLeaseConflict,
    TurnRuntimeReplayConflict,
    TurnRuntimeTerminalConflict,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.turn_execution_orchestrator import (
    _next_reasoning_trace,
)
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    AllocateExecutionV1,
    BeginResultGovernanceV1,
    BeginToolInvocationV1,
    BindContextV1,
    ClaimSchemaRetryV1,
    CommitTerminalV1,
    CompleteReleaseIntentV1,
    CompleteToolInvocationV1,
    ExecutionState,
    FailCarrierExecutionV1,
    FinalizeExpiredExecutionV1,
    LeasePolicyV1,
    PrepareTerminalV1,
    ReasoningEvaluationV1,
    ReasoningCorrectionV2,
    ReasoningPlanItemV2,
    ReasoningPlanV2,
    RecordReasoningProgressV1,
    RenewExecutionLeaseV1,
    RequestModelActionV1,
    RoutePolicyV1,
    StageAcceptanceResourceV1,
    TurnRouteSnapshotV2,
)


PREFIX = "atr020-runtime-owner-"


def route_snapshot() -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id="test-route",
        route_revision=1,
        runtime_policy_revision=1,
        tokenizer_profile="cl100k_base",
        context_window_tokens=128000,
        max_input_tokens_per_invocation=112000,
        max_output_tokens_per_invocation=16000,
        max_tool_result_tokens_per_execution=16000,
        max_total_tokens_per_conversation=256000,
    )


def _cleanup(runtime: PostgresRuntime) -> None:
    pattern = f"{PREFIX}%"
    with runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasTurnReleaseIntentRow).where(
                AtlasTurnReleaseIntentRow.execution_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasTurnTerminalOutcomeRow).where(
                AtlasTurnTerminalOutcomeRow.execution_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasTurnTerminalIntentRow).where(
                AtlasTurnTerminalIntentRow.execution_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasTurnAcceptanceResourceRow).where(
                AtlasTurnAcceptanceResourceRow.execution_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasTurnExecutionRow).where(
                AtlasTurnExecutionRow.execution_id.like(pattern)
            )
        )


@pytest.fixture(autouse=True)
def clean_runtime_rows(postgres_runtime: PostgresRuntime):
    _cleanup(postgres_runtime)
    yield
    _cleanup(postgres_runtime)


def _owner(runtime: PostgresRuntime) -> PostgresTurnRuntimeOwner:
    return PostgresTurnRuntimeOwner(runtime.session_factory)


def _allocate(
    owner: PostgresTurnRuntimeOwner,
    suffix: str,
    *,
    max_tools: int = 2,
    max_catalog_pages: int = 2,
    max_retrieval_repairs: int = 3,
    max_selected_anchor_pages_per_round: int = 7,
    max_schema_retries: int = 1,
    reasoning_mode: str = "standard",
) -> object:
    execution_id = f"{PREFIX}{suffix}"
    return owner.allocate(
        AllocateExecutionV1(
            execution_id=execution_id,
            turn_id=f"turn-{execution_id}",
            conversation_id=f"conversation-{suffix}",
            actor_id="actor-1",
            holder_id="holder-1",
            route_policy=RoutePolicyV1(
                max_tool_invocations=max_tools,
                max_catalog_pages=max_catalog_pages,
                max_search_rounds=2,
                max_model_visible_items_per_turn=2,
                max_retrieval_repairs=max_retrieval_repairs,
                max_selected_anchor_pages_per_round=(
                    max_selected_anchor_pages_per_round
                ),
                max_provider_invocations=(
                    max_tools + (4 if reasoning_mode == "deep" else 0) + 6
                ),
                max_reasoning_revision_cycles=(1 if reasoning_mode == "deep" else 0),
                max_schema_retries_per_turn=max_schema_retries,
                context_token_budget=20,
                tool_token_budget=20,
                tool_execution_timeout_seconds=30,
                deadline_seconds=120,
            ),
            route=route_snapshot(),
            lease_policy=LeasePolicyV1(ttl_seconds=30),
            idempotency_key=f"allocate-{suffix}",
            operation="create_turn",
            retry_of_turn_id=None,
            input_digest="0" * 64,
            response_language="zh-TW",
            reasoning_mode=reasoning_mode,
            applied_guidance_revision=0,
            applied_guidance_digest=None,
        )
    )


def test_reasoning_revision_started_trace_and_event_commit_atomically(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(
        owner,
        _allocate(owner, "reasoning-revision-started", reasoning_mode="deep"),
    )
    evaluation = ReasoningEvaluationV1(
        cycle=1,
        verdict="revise_only",
        finding_codes=["coverage_gap"],
        summary="A revision is required.",
        score={
            "plan_coverage": 1,
            "evidence_handling": 1,
            "conflict_handling": 1,
            "gap_resolution": 1,
            "revision_completion": 0,
            "total": 4,
        },
    )
    trace = _next_reasoning_trace(
        None,
        status="running",
        plans=[ReasoningPlanV2(
            generation=1,
            next_objective="Review evidence.",
            completion_condition="Evidence reviewed.",
            items=[ReasoningPlanItemV2(item_id="plan-1", summary="Review evidence.")],
        )],
        evaluations=[evaluation],
        corrections=[],
    )

    progressed = owner.record_reasoning_progress(
        RecordReasoningProgressV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            trace=trace,
            phase="revising",
            progress_status="started",
            cycle=1,
            message_code="reasoning.revision_requested",
            message_params={"cycle": 1},
        )
    )

    with postgres_runtime.session_factory() as session:
        row = session.get(AtlasTurnExecutionRow, snapshot.execution_id)
        event = session.scalar(
            select(AtlasTurnRuntimeEventRow).where(
                AtlasTurnRuntimeEventRow.execution_id == snapshot.execution_id,
                AtlasTurnRuntimeEventRow.sequence == progressed.version,
            )
        )
    assert row is not None and event is not None
    assert row.reasoning_trace == trace.model_dump(mode="json")
    assert row.reasoning_trace["corrections"] == []
    assert event.reasoning_phase == "revising"
    assert event.progress_status == "started"
    assert event.cycle == 1


def test_reasoning_trace_v2_generations_and_standard_null_persist_atomically(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    standard = _allocate(owner, "reasoning-standard-null")
    snapshot = _accept_and_bind(
        owner,
        _allocate(owner, "reasoning-v2-generations", reasoning_mode="deep"),
    )
    first = ReasoningPlanV2(
        generation=1,
        next_objective="Review the available evidence.",
        completion_condition="The evidence gap is identified.",
        items=[ReasoningPlanItemV2(item_id="plan-1", summary="Review evidence.")],
    )
    second = ReasoningPlanV2(
        generation=2,
        parent_generation=1,
        next_objective="Research the identified evidence gap.",
        completion_condition="The gap is resolved or disclosed.",
        items=[
            ReasoningPlanItemV2(
                item_id="plan-1",
                summary="Review evidence.",
                status="completed",
            )
        ],
    )
    evaluations = [
        ReasoningEvaluationV1(
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
        ),
        ReasoningEvaluationV1(
            cycle=2,
            verdict="accept",
            summary="The revised candidate closes the gap.",
            score={
                "plan_coverage": 2,
                "evidence_handling": 2,
                "conflict_handling": 1,
                "gap_resolution": 2,
                "revision_completion": 2,
                "total": 9,
            },
        ),
    ]
    trace = _next_reasoning_trace(
        None,
        status="completed",
        plans=[first, second],
        evaluations=evaluations,
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
                summary="Researched the missing evidence and revised the candidate.",
            )
        ],
        termination_reason="completed",
    )

    progressed = owner.record_reasoning_progress(
        RecordReasoningProgressV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            trace=trace,
            phase="evaluating",
            progress_status="completed",
            cycle=2,
            message_code="reasoning.evaluation_completed",
            message_params={"cycle": 2},
        )
    )

    with postgres_runtime.session_factory() as session:
        standard_row = session.get(AtlasTurnExecutionRow, standard.execution_id)
        row = session.get(AtlasTurnExecutionRow, snapshot.execution_id)
        event = session.scalar(
            select(AtlasTurnRuntimeEventRow).where(
                AtlasTurnRuntimeEventRow.execution_id == snapshot.execution_id,
                AtlasTurnRuntimeEventRow.sequence == progressed.version,
            )
        )
    expected = trace.model_dump(mode="json")
    assert standard_row is not None and standard_row.reasoning_trace is None
    assert row is not None and row.reasoning_trace == expected
    assert event is not None
    assert (event.reasoning_phase, event.progress_status, event.cycle) == (
        "evaluating",
        "completed",
        2,
    )
    assert [plan["generation"] for plan in row.reasoning_trace["plans"]] == [1, 2]
    assert len(
        json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ) <= 32768


def _accept_and_bind(owner: PostgresTurnRuntimeOwner, snapshot: object):
    accepted = owner.accept(
        AcceptExecutionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            grant_ref="grant-1",
            catalog_ref="catalog-1",
        )
    )
    return owner.bind_context(
        BindContextV1(
            execution_id=accepted.execution_id,
            expected_version=accepted.version,
            fencing_token=accepted.lease.fencing_token,
            context_pack_ref="context-1",
        )
    )


def _tool_cycle(
    owner: PostgresTurnRuntimeOwner,
    snapshot: object,
    ordinal: int,
    *,
    context_tokens: int = 2,
):
    requested = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            context_tokens=context_tokens,
        )
    )
    started = owner.begin_tool(
        BeginToolInvocationV1(
            execution_id=requested.execution_id,
            expected_version=requested.version,
            fencing_token=requested.lease.fencing_token,
            tool_invocation_id=f"tool-{requested.execution_id}-{ordinal}",
            invocation_ordinal=ordinal,
            tool_name="search",
            schema_version="v1",
            arguments_digest=f"{ordinal}" * 64,
            reserve_catalog_pages=1 if ordinal == 1 else 0,
            reserve_document_candidates=1,
            reserve_search_rounds=1 if ordinal == 1 else 0,
            reserve_model_visible_items=1,
            reserve_tool_tokens=2,
        )
    )
    return owner.complete_tool(
        CompleteToolInvocationV1(
            execution_id=started.execution_id,
            expected_version=started.version,
            fencing_token=started.lease.fencing_token,
            tool_invocation_id=f"tool-{started.execution_id}-{ordinal}",
            invocation_ordinal=ordinal,
            result_ref=f"result-{ordinal}",
            result_digest=f"{ordinal + 2}" * 64,
            document_candidate_handles=["document-1", "document-1"],
            model_visible_item_identities=["evidence-1", "evidence-1"],
            catalog_pages=1 if ordinal == 1 else 0,
            search_rounds=1 if ordinal == 1 else 0,
            tool_tokens=2,
        )
    )


def _prepare(owner: PostgresTurnRuntimeOwner, snapshot: object):
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
            execution_id=requested.execution_id,
            expected_version=requested.version,
            fencing_token=requested.lease.fencing_token,
            finalize_action_digest="f" * 64,
        )
    )
    return owner.prepare_terminal(
        PrepareTerminalV1(
            execution_id=governing.execution_id,
            expected_version=governing.version,
            fencing_token=governing.lease.fencing_token,
            evidence_pack_ref="evidence-pack-1",
            governed_answer_draft_ref="answer-draft-1",
            citation_binding_draft_ref="citation-draft-1",
            audit_draft_ref="audit-draft-1",
        )
    )


def test_allocation_exact_replay_conflict_and_competing_cas(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    first = _allocate(owner, "allocation")
    replay = _allocate(owner, "allocation")
    assert replay.execution_id == first.execution_id
    assert replay.version == first.version == 1
    conflicting = AllocateExecutionV1(
        execution_id=first.execution_id,
        turn_id=first.turn_id,
        conversation_id=first.conversation_id,
        actor_id=first.actor_id,
        holder_id="other-holder",
        route_policy=RoutePolicyV1(
            max_tool_invocations=2,
            max_provider_invocations=8,
            max_reasoning_revision_cycles=0,
        ),
        route=route_snapshot(),
        lease_policy=LeasePolicyV1(ttl_seconds=30),
        idempotency_key="allocate-allocation",
        operation="create_turn",
        retry_of_turn_id=None,
        input_digest="1" * 64,
        response_language="zh-TW",
        applied_guidance_revision=0,
        applied_guidance_digest=None,
    )
    with pytest.raises(TurnRuntimeReplayConflict):
        owner.allocate(conflicting)

    command = AcceptExecutionV1(
        execution_id=first.execution_id,
        expected_version=first.version,
        fencing_token=first.lease.fencing_token,
        grant_ref="grant-1",
        catalog_ref="catalog-1",
    )
    barrier = Barrier(2)

    def compete() -> str:
        barrier.wait()
        try:
            owner.accept(command)
            return "accepted"
        except TurnRuntimeCurrentnessConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: compete(), range(2))) == ["accepted", "stale"]


def test_schema_retry_claim_is_turn_scoped_idempotent_and_bounded(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(owner, _allocate(owner, "schema-retry-bounded"))
    assert snapshot.policy.max_schema_retries_per_turn == 1
    assert snapshot.budget.schema_retries == 0

    command = ClaimSchemaRetryV1(
        execution_id=snapshot.execution_id,
        fencing_token=snapshot.lease.fencing_token,
        claim_key="context-resolver-repair-1",
        origin_error_code="provider_output_decode_error",
    )
    claimed = owner.claim_schema_retry(command)
    assert claimed.budget.schema_retries == 1
    assert owner.claim_schema_retry(command).budget.schema_retries == 1

    with pytest.raises(TurnRuntimeBudgetExceeded):
        owner.claim_schema_retry(
            command.model_copy(
                update={
                    "claim_key": "deep-plan-repair-1",
                    "origin_error_code": "deep_reasoning_plan_invalid",
                }
            )
        )
    assert owner.snapshot(snapshot.execution_id).budget.schema_retries == 1


def test_contract_repair_admission_is_execution_fixed_durable_and_bounded(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(
        owner,
        _allocate(
            owner,
            "retrieval-repair-bounded",
            max_retrieval_repairs=2,
        ),
    )
    assert snapshot.policy.max_retrieval_repairs == 2
    assert snapshot.policy.max_selected_anchor_pages_per_round == 7
    assert snapshot.policy.tool_execution_timeout_seconds == 30
    assert snapshot.budget.retrieval_repairs == 0

    initial = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            context_tokens=1,
        )
    )
    first = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=initial.version,
            fencing_token=initial.lease.fencing_token,
            context_tokens=1,
            contract_repair=True,
        )
    )
    second = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=first.version,
            fencing_token=first.lease.fencing_token,
            context_tokens=1,
            contract_repair=True,
        )
    )

    assert first.budget.retrieval_repairs == 1
    assert second.budget.retrieval_repairs == 2
    assert owner.snapshot(snapshot.execution_id).budget.retrieval_repairs == 2
    with pytest.raises(TurnRuntimeBudgetExceeded):
        owner.request_model_action(
            RequestModelActionV1(
                execution_id=snapshot.execution_id,
                expected_version=second.version,
                fencing_token=second.lease.fencing_token,
                context_tokens=1,
                contract_repair=True,
            )
        )
    reloaded = owner.snapshot(snapshot.execution_id)
    assert reloaded.version == second.version
    assert reloaded.budget.retrieval_repairs == 2


def test_competing_schema_retry_claims_cannot_overspend(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(owner, _allocate(owner, "schema-retry-competing"))
    barrier = Barrier(2)

    def compete(ordinal: int) -> str:
        barrier.wait()
        try:
            owner.claim_schema_retry(
                ClaimSchemaRetryV1(
                    execution_id=snapshot.execution_id,
                    fencing_token=snapshot.lease.fencing_token,
                    claim_key=f"repair-{ordinal}",
                    origin_error_code="provider_output_schema_error",
                )
            )
            return "claimed"
        except TurnRuntimeBudgetExceeded:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, (1, 2))) == ["claimed", "exhausted"]
    with postgres_runtime.session_factory() as session:
        budget = session.get(AtlasTurnBudgetCounterRow, snapshot.execution_id)
        assert budget is not None and budget.schema_retries == 1


def test_two_stage_schema_retry_claims_persist_and_third_is_denied(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(
        owner,
        _allocate(owner, "schema-retry-two-stages", max_schema_retries=2),
    )

    first = owner.claim_schema_retry(
        ClaimSchemaRetryV1(
            execution_id=snapshot.execution_id,
            fencing_token=snapshot.lease.fencing_token,
            claim_key="context-summary-repair-1",
            origin_error_code="summary_output_too_large",
        )
    )
    assert first.budget.schema_retries == 1

    reloaded_owner = _owner(postgres_runtime)
    reloaded = reloaded_owner.snapshot(snapshot.execution_id)
    assert reloaded.budget.schema_retries == 1
    second = reloaded_owner.claim_schema_retry(
        ClaimSchemaRetryV1(
            execution_id=snapshot.execution_id,
            fencing_token=reloaded.lease.fencing_token,
            claim_key="deep-plan-repair-1",
            origin_error_code="deep_reasoning_plan_invalid",
        )
    )
    assert second.budget.schema_retries == 2

    with pytest.raises(TurnRuntimeBudgetExceeded):
        reloaded_owner.claim_schema_retry(
            ClaimSchemaRetryV1(
                execution_id=snapshot.execution_id,
                fencing_token=second.lease.fencing_token,
                claim_key="provisional-evidence-repair-1",
                origin_error_code="provisional_evidence_item_count_invalid",
            )
        )
    assert reloaded_owner.snapshot(snapshot.execution_id).budget.schema_retries == 2


def test_schema_retry_claim_serializes_with_terminal_transition(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(owner, _allocate(owner, "schema-retry-terminal-race"))
    command = ClaimSchemaRetryV1(
        execution_id=snapshot.execution_id,
        fencing_token=snapshot.lease.fencing_token,
        claim_key="answer-repair-1",
        origin_error_code="provider_output_schema_error",
    )
    claim_locked = Event()
    release_claim = Event()
    terminal_started = Event()
    completion_order: list[str] = []

    def pause_after_execution_lock(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            current_thread().name.startswith("schema-claim")
            and "atlas_turn_executions" in statement
            and "FOR UPDATE" in statement
        ):
            claim_locked.set()
            assert release_claim.wait(timeout=5)

    event.listen(
        postgres_runtime.engine,
        "after_cursor_execute",
        pause_after_execution_lock,
    )
    try:
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="schema-claim"
        ) as pool:
            def claim_retry():
                result = owner.claim_schema_retry(command)
                completion_order.append("claim")
                return result

            claim_future = pool.submit(claim_retry)
            assert claim_locked.wait(timeout=5)

            def terminal_transition() -> None:
                terminal_started.set()
                with postgres_runtime.session_factory() as session, session.begin():
                    session.execute(
                        update(AtlasTurnExecutionRow)
                        .where(
                            AtlasTurnExecutionRow.execution_id
                            == snapshot.execution_id
                        )
                        .values(
                            state=ExecutionState.TERMINAL_FAILED.value,
                            terminal_failure_code="test_terminal_transition",
                        )
                    )
                completion_order.append("terminal")

            terminal_future = pool.submit(terminal_transition)
            assert terminal_started.wait(timeout=5)
            release_claim.set()
            assert claim_future.result(timeout=5).budget.schema_retries == 1
            terminal_future.result(timeout=5)
    finally:
        release_claim.set()
        event.remove(
            postgres_runtime.engine,
            "after_cursor_execute",
            pause_after_execution_lock,
        )

    assert completion_order == ["claim", "terminal"]
    with pytest.raises(TurnRuntimeCurrentnessConflict):
        owner.claim_schema_retry(command)


def test_lease_renewal_is_fenced_and_expiry_is_terminal_no_takeover(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _allocate(owner, "lease")
    for changes in (
        {"holder_id": "wrong"},
        {"fencing_token": snapshot.lease.fencing_token + 1},
        {"expected_lease_version": snapshot.lease.lease_version + 1},
    ):
        payload = {
            "execution_id": snapshot.execution_id,
            "expected_lease_version": snapshot.lease.lease_version,
            "fencing_token": snapshot.lease.fencing_token,
            "holder_id": snapshot.lease.holder_id,
            **changes,
        }
        with pytest.raises(TurnRuntimeLeaseConflict):
            owner.renew_lease(RenewExecutionLeaseV1(**payload))

    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            update(AtlasTurnExecutionLeaseRow)
            .where(AtlasTurnExecutionLeaseRow.execution_id == snapshot.execution_id)
            .values(expires_at=AtlasTurnExecutionLeaseRow.heartbeat_at + timedelta(microseconds=1))
        )
    with pytest.raises(TurnRuntimeLeaseConflict):
        owner.renew_lease(
            RenewExecutionLeaseV1(
                execution_id=snapshot.execution_id,
                expected_lease_version=1,
                fencing_token=1,
                holder_id="holder-1",
            )
        )
    failed = owner.finalize_expired(
        FinalizeExpiredExecutionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            expected_lease_version=1,
            failure_code="lease_expired",
            detected_by="lease_sweep",
        )
    )
    assert failed.state == ExecutionState.TERMINAL_FAILED
    assert failed.lease.holder_id == "holder-1"


def test_pre_acceptance_saga_survives_failure_before_refs_are_bound(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _allocate(owner, "acceptance-saga")
    for resource_owner, release_kind in (
        ("authorization", "release_turn_grant"),
        ("retrieval", "release_knowledge_catalog"),
        ("context_engineering", "release_context_pack"),
    ):
        owner.stage_acceptance_resource(
            StageAcceptanceResourceV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                resource_owner=resource_owner,
                release_kind=release_kind,
            )
        )

    failed = owner.fail_carrier(
        FailCarrierExecutionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            holder_id=snapshot.lease.holder_id,
            expected_lease_version=snapshot.lease.lease_version,
            fencing_token=snapshot.lease.fencing_token,
            failure_code="contract_violation",
            detected_by="runtime_validator",
        )
    )
    assert failed.state is ExecutionState.TERMINAL_FAILED
    intents = owner.pending_release_intents(limit=10)
    assert {(item.resource_owner, item.resource_ref) for item in intents} == {
        ("authorization", f"execution-resource:authorization:{snapshot.execution_id}"),
        ("retrieval", f"execution-resource:retrieval:{snapshot.execution_id}"),
        (
            "context_engineering",
            f"execution-resource:context_engineering:{snapshot.execution_id}",
        ),
    }


def test_expired_sweep_cas_loser_does_not_overwrite_terminal_outcome(
    postgres_runtime: PostgresRuntime,
) -> None:
    primary = _owner(postgres_runtime)
    snapshot = _allocate(primary, "sweep-race")
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            update(AtlasTurnExecutionLeaseRow)
            .where(AtlasTurnExecutionLeaseRow.execution_id == snapshot.execution_id)
            .values(
                expires_at=AtlasTurnExecutionLeaseRow.heartbeat_at
                + timedelta(microseconds=1)
            )
        )

    class RacingOwner(PostgresTurnRuntimeOwner):
        def finalize_expired(self, command: FinalizeExpiredExecutionV1):
            primary.finalize_expired(
                command.model_copy(
                    update={
                        "failure_code": "execution_carrier_lost",
                        "detected_by": "startup_sweep",
                    }
                )
            )
            return super().finalize_expired(command)

    assert RacingOwner(postgres_runtime.session_factory).fail_expired_leases(limit=1) == []
    with postgres_runtime.session_factory() as session:
        outcome = session.get(AtlasTurnTerminalOutcomeRow, snapshot.execution_id)
        assert outcome is not None
        assert outcome.failure_code == "execution_carrier_lost"


def test_dedup_budgets_terminal_rollback_single_outcome_and_release_saga(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(owner, _allocate(owner, "journey"))
    after_first = _tool_cycle(owner, snapshot, 1)
    after_second = _tool_cycle(owner, after_first, 2)
    assert after_second.budget.tool_invocations == 2
    assert after_second.budget.document_candidates == 1
    assert after_second.budget.model_visible_items == 1
    assert after_second.budget.catalog_pages == 1
    assert after_second.budget.search_rounds == 1
    prepared = _prepare(owner, after_second)
    with pytest.raises(TurnRuntimeTerminalConflict):
        owner.commit_terminal(
            CommitTerminalV1(
                execution_id=prepared.execution_id,
                expected_version=prepared.version,
                fencing_token=prepared.lease.fencing_token,
                terminal_commit_intent_ref="wrong-intent",
            )
        )
    with postgres_runtime.session_factory() as session:
        current = session.get(AtlasTurnExecutionRow, prepared.execution_id)
        assert current is not None
        assert current.state == ExecutionState.MATERIALIZING_TERMINAL.value
        assert current.version == prepared.version
    completed = owner.commit_terminal(
        CommitTerminalV1(
            execution_id=prepared.execution_id,
            expected_version=prepared.version,
            fencing_token=prepared.lease.fencing_token,
            terminal_commit_intent_ref=prepared.terminal_commit_intent_ref,
        )
    )
    assert completed.state == ExecutionState.TERMINAL_COMPLETED
    assert [event.sequence for event in owner.events(completed.execution_id)] == sorted(
        event.sequence for event in owner.events(completed.execution_id)
    )
    claimed = [
        intent
        for intent in owner.pending_release_intents(limit=50)
        if intent.execution_id == completed.execution_id
    ]
    # Only leased acceptance resources are released. Immutable evidence and
    # terminal drafts remain durable projection/audit inputs.
    assert len(claimed) == 3
    assert {item.resource_owner for item in claimed} == {
        "authorization",
        "retrieval",
        "context_engineering",
    }
    assert all(intent.next_attempt_at is not None for intent in claimed)
    # Active claims are not immediately double-claimed; their due timestamp
    # makes them recoverable after a reconciler carrier loss.
    assert not [
        intent
        for intent in owner.pending_release_intents(limit=50)
        if intent.execution_id == completed.execution_id
    ]
    released = owner.complete_release_intent(
        CompleteReleaseIntentV1(
            release_intent_id=claimed[0].release_intent_id,
            expected_status="releasing",
            outcome="released",
            failure_code=None,
        )
    )
    assert released.status == "released"


def test_context_budget_is_per_provider_invocation_while_usage_remains_cumulative(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(owner, _allocate(owner, "per-invocation-context"))

    after_first = _tool_cycle(owner, snapshot, 1, context_tokens=12)
    after_second = _tool_cycle(owner, after_first, 2, context_tokens=12)

    assert after_second.budget.context_tokens == 24
    assert after_second.budget.context_tokens > after_second.policy.context_token_budget

    with pytest.raises(
        TurnRuntimeBudgetExceeded,
        match="per-invocation context token budget exceeded",
    ):
        owner.request_model_action(
            RequestModelActionV1(
                execution_id=after_second.execution_id,
                expected_version=after_second.version,
                fencing_token=after_second.lease.fencing_token,
                context_tokens=21,
            )
        )


def test_candidate_reservation_is_per_call_accounting_not_a_turn_ceiling(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    ready = _accept_and_bind(owner, _allocate(owner, "reservation", max_tools=1))
    requested = owner.request_model_action(
        RequestModelActionV1(
            execution_id=ready.execution_id,
            expected_version=ready.version,
            fencing_token=ready.lease.fencing_token,
            context_tokens=1,
        )
    )
    started = owner.begin_tool(BeginToolInvocationV1(
        execution_id=requested.execution_id,
        expected_version=requested.version,
        fencing_token=requested.lease.fencing_token,
        tool_invocation_id="tool-three-candidates",
        invocation_ordinal=1,
        tool_name="find_knowledge_documents",
        schema_version="find-knowledge-documents-v1",
        arguments_digest="a" * 64,
        reserve_catalog_pages=1,
        reserve_document_candidates=3,
        reserve_search_rounds=0,
        reserve_model_visible_items=0,
        reserve_tool_tokens=1,
    ))
    completed = owner.complete_tool(
        CompleteToolInvocationV1(
            execution_id=started.execution_id,
            expected_version=started.version,
            fencing_token=started.lease.fencing_token,
            tool_invocation_id="tool-three-candidates",
            invocation_ordinal=1,
            result_ref="result-three-candidates",
            result_digest="b" * 64,
            document_candidate_handles=["document-1", "document-2", "document-3"],
            model_visible_item_identities=[],
            catalog_pages=1,
            search_rounds=0,
            tool_tokens=1,
        )
    )
    assert completed.budget.document_candidates == 3

    second_ready = _accept_and_bind(
        owner,
        _allocate(owner, "reservation-overflow", max_tools=1),
    )
    second_requested = owner.request_model_action(
        RequestModelActionV1(
            execution_id=second_ready.execution_id,
            expected_version=second_ready.version,
            fencing_token=second_ready.lease.fencing_token,
            context_tokens=1,
        )
    )
    bounded = owner.begin_tool(
        BeginToolInvocationV1(
            execution_id=second_requested.execution_id,
            expected_version=second_requested.version,
            fencing_token=second_requested.lease.fencing_token,
            tool_invocation_id="tool-bounded-reservation",
            invocation_ordinal=1,
            tool_name="find_knowledge_documents",
            schema_version="find-knowledge-documents-v1",
            arguments_digest="c" * 64,
            reserve_catalog_pages=1,
            reserve_document_candidates=0,
            reserve_search_rounds=0,
            reserve_model_visible_items=0,
            reserve_tool_tokens=1,
        )
    )
    with pytest.raises(TurnRuntimeBudgetExceeded):
        owner.complete_tool(
            CompleteToolInvocationV1(
                execution_id=bounded.execution_id,
                expected_version=bounded.version,
                fencing_token=bounded.lease.fencing_token,
                tool_invocation_id="tool-bounded-reservation",
                invocation_ordinal=1,
                result_ref="result-over-reservation",
                result_digest="d" * 64,
                document_candidate_handles=["new-document"],
                model_visible_item_identities=[],
                catalog_pages=1,
                search_rounds=0,
                tool_tokens=1,
            )
        )
    snapshot = owner.snapshot(bounded.execution_id)
    assert snapshot.state == ExecutionState.TOOL_PENDING
    assert snapshot.budget.document_candidates == 0


def test_tool_token_threshold_blocks_only_the_request_after_overshoot(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(
        owner,
        _allocate(owner, "tool-token-next-request", max_tools=3),
    )
    for ordinal, actual_tokens in ((1, 19), (2, 5)):
        requested = owner.request_model_action(
            RequestModelActionV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                context_tokens=1,
            )
        )
        started = owner.begin_tool(
            BeginToolInvocationV1(
                execution_id=requested.execution_id,
                expected_version=requested.version,
                fencing_token=requested.lease.fencing_token,
                tool_invocation_id=f"tool-token-{ordinal}",
                invocation_ordinal=ordinal,
                tool_name="search_knowledge",
                schema_version="search-knowledge-v1",
                arguments_digest=f"{ordinal}" * 64,
                reserve_catalog_pages=0,
                reserve_document_candidates=0,
                reserve_search_rounds=0,
                reserve_model_visible_items=0,
                reserve_tool_tokens=20,
            )
        )
        snapshot = owner.complete_tool(
            CompleteToolInvocationV1(
                execution_id=started.execution_id,
                expected_version=started.version,
                fencing_token=started.lease.fencing_token,
                tool_invocation_id=f"tool-token-{ordinal}",
                invocation_ordinal=ordinal,
                result_ref=f"result-tool-token-{ordinal}",
                result_digest=f"{ordinal + 4}" * 64,
                document_candidate_handles=[],
                model_visible_item_identities=[],
                catalog_pages=0,
                search_rounds=0,
                tool_tokens=actual_tokens,
            )
        )

    assert snapshot.budget.tool_tokens == 24
    requested = owner.request_model_action(
        RequestModelActionV1(
            execution_id=snapshot.execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            context_tokens=1,
        )
    )
    with pytest.raises(TurnRuntimeBudgetExceeded):
        owner.begin_tool(
            BeginToolInvocationV1(
                execution_id=requested.execution_id,
                expected_version=requested.version,
                fencing_token=requested.lease.fencing_token,
                tool_invocation_id="tool-token-3",
                invocation_ordinal=3,
                tool_name="search_knowledge",
                schema_version="search-knowledge-v1",
                arguments_digest="3" * 64,
                reserve_catalog_pages=0,
                reserve_document_candidates=0,
                reserve_search_rounds=0,
                reserve_model_visible_items=0,
                reserve_tool_tokens=20,
            )
        )


def test_candidate_counter_can_exceed_twenty_without_closing_discovery(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    snapshot = _accept_and_bind(
        owner,
        _allocate(
            owner,
            "candidate-accounting",
            max_tools=3,
            max_catalog_pages=4,
        ),
    )
    for ordinal in range(1, 4):
        requested = owner.request_model_action(
            RequestModelActionV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                context_tokens=1,
            )
        )
        started = owner.begin_tool(
            BeginToolInvocationV1(
                execution_id=requested.execution_id,
                expected_version=requested.version,
                fencing_token=requested.lease.fencing_token,
                tool_invocation_id=f"tool-candidate-page-{ordinal}",
                invocation_ordinal=ordinal,
                tool_name="find_knowledge_documents",
                schema_version="find-knowledge-documents-v1",
                arguments_digest=f"{ordinal}" * 64,
                reserve_catalog_pages=1,
                reserve_document_candidates=10,
                reserve_search_rounds=0,
                reserve_model_visible_items=0,
                reserve_tool_tokens=1,
            )
        )
        snapshot = owner.complete_tool(
            CompleteToolInvocationV1(
                execution_id=started.execution_id,
                expected_version=started.version,
                fencing_token=started.lease.fencing_token,
                tool_invocation_id=f"tool-candidate-page-{ordinal}",
                invocation_ordinal=ordinal,
                result_ref=f"result-candidate-page-{ordinal}",
                result_digest=f"{ordinal + 3}" * 64,
                document_candidate_handles=[
                    f"document-{ordinal}-{index}" for index in range(10)
                ],
                model_visible_item_identities=[],
                catalog_pages=1,
                search_rounds=0,
                tool_tokens=1,
            )
        )

    assert snapshot.budget.catalog_pages == 3
    assert snapshot.budget.document_candidates == 30


def test_completed_and_fenced_failure_have_one_terminal_winner(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = _owner(postgres_runtime)
    prepared = _prepare(
        owner,
        _tool_cycle(owner, _accept_and_bind(owner, _allocate(owner, "race", max_tools=1)), 1),
    )
    barrier = Barrier(2)

    def complete() -> str:
        barrier.wait()
        try:
            owner.commit_terminal(
                CommitTerminalV1(
                    execution_id=prepared.execution_id,
                    expected_version=prepared.version,
                    fencing_token=prepared.lease.fencing_token,
                    terminal_commit_intent_ref=prepared.terminal_commit_intent_ref,
                )
            )
            return "completed"
        except TurnRuntimeTerminalConflict:
            return "lost"

    def fail() -> str:
        barrier.wait()
        try:
            owner.fail_carrier(
                FailCarrierExecutionV1(
                    execution_id=prepared.execution_id,
                    expected_version=prepared.version,
                    holder_id=prepared.lease.holder_id,
                    expected_lease_version=prepared.lease.lease_version,
                    fencing_token=prepared.lease.fencing_token,
                    failure_code="provider_failed",
                    detected_by="carrier",
                )
            )
            return "failed"
        except TurnRuntimeTerminalConflict:
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = {pool.submit(complete), pool.submit(fail)}
        outcomes = sorted(future.result() for future in results)
    assert outcomes in (["completed", "lost"], ["failed", "lost"])
    with postgres_runtime.session_factory() as session:
        rows = session.scalars(
            select(AtlasTurnTerminalOutcomeRow).where(
                AtlasTurnTerminalOutcomeRow.execution_id == prepared.execution_id
            )
        ).all()
        assert len(rows) == 1
