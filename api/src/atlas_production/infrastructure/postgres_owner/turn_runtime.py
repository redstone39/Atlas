from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, exists, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnBudgetCounterRow,
    AtlasTurnAcceptanceResourceRow,
    AtlasTurnDocumentCandidateLedgerRow,
    AtlasTurnExecutionLeaseRow,
    AtlasTurnExecutionRow,
    AtlasTurnReleaseIntentRow,
    AtlasTurnRuntimeEventRow,
    AtlasTurnRuntimeIdempotencyRow,
    AtlasTurnStepLedgerRow,
    AtlasTurnTerminalIntentRow,
    AtlasTurnTerminalOutcomeRow,
    AtlasTurnToolLedgerRow,
    AtlasTurnModelVisibleItemLedgerRow,
)
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    ActivateResearchExecutionV1,
    AllocateExecutionV1,
    BeginResultGovernanceV1,
    BeginToolInvocationV1,
    BindContextV1,
    BudgetSnapshotV1,
    ClaimSchemaRetryV1,
    CommitTerminalV1,
    CompleteReleaseIntentV1,
    CompleteToolInvocationV1,
    ExecutionLeaseV1,
    ExecutionPromptSkillSelectionTraceV1,
    ExecutionSnapshotV1,
    ExecutionState,
    FailCarrierExecutionV1,
    FinalizeExpiredExecutionV1,
    PrepareTerminalV1,
    ReasoningTraceV4,
    RecordExecutionPromptSkillSelectionV1,
    RecordReasoningProgressV1,
    ReleaseIntentV1,
    RenewExecutionLeaseV1,
    ReserveAcceptanceModelActionV1,
    RequestModelActionV1,
    RoutePolicyV1,
    RuntimeEventV1,
    StageAcceptanceResourceV1,
    TerminalCompletionCursorV1,
    TerminalOutcomeV1,
    TurnRuntimeBudgetExceeded,
    TurnRuntimeCurrentnessConflict,
    TurnRuntimeError,
    TurnRuntimeLeaseConflict,
    TurnRuntimeReplayConflict,
    TurnRuntimeTerminalConflict,
    TurnRouteSnapshotV2,
    VisionRouteSnapshotV1,
)


SessionFactory = Callable[[], Session]
_TERMINAL = (ExecutionState.TERMINAL_COMPLETED.value, ExecutionState.TERMINAL_FAILED.value)
_MAX_READ_LIMIT = 500


def _bounded_limit(limit: int) -> int:
    if limit < 1 or limit > _MAX_READ_LIMIT:
        raise ValueError("turn-runtime limit must be between 1 and 500")
    return limit

def _completed_scan_limit(limit: int) -> int:
    if limit < 1 or limit > 100:
        raise ValueError("completed terminal scan limit must be between 1 and 100")
    return limit


def _digest_model(command: object) -> str:
    payload = command.model_dump(mode="json")  # type: ignore[attr-defined]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _terminal_digest(command: PrepareTerminalV1) -> str:
    materialized = {
        "audit_draft_ref": command.audit_draft_ref,
        "citation_binding_draft_ref": command.citation_binding_draft_ref,
        "evidence_pack_ref": command.evidence_pack_ref,
        "execution_id": command.execution_id,
        "governed_answer_draft_ref": command.governed_answer_draft_ref,
        "research_packet_ref": command.research_packet_ref,
        "research_packet_digest": command.research_packet_digest,
        "result_kind": command.result_kind,
    }
    return hashlib.sha256(
        json.dumps(materialized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _stable_id(kind: str, *parts: str) -> str:
    return f"{kind}-{uuid5(NAMESPACE_URL, ':'.join((kind, *parts)))}"


def _lease_model(row: AtlasTurnExecutionLeaseRow) -> ExecutionLeaseV1:
    return ExecutionLeaseV1(
        execution_id=row.execution_id,
        holder_id=row.holder_id,
        lease_version=row.lease_version,
        fencing_token=row.fencing_token,
        acquired_at=row.acquired_at,
        heartbeat_at=row.heartbeat_at,
        expires_at=row.expires_at,
    )


def _budget_model(row: AtlasTurnBudgetCounterRow) -> BudgetSnapshotV1:
    return BudgetSnapshotV1(
        tool_invocations=row.tool_invocations,
        catalog_pages=row.catalog_pages,
        document_candidates=row.document_candidates,
        search_rounds=row.search_rounds,
        model_visible_items=row.model_visible_items,
        provider_invocations=row.provider_invocations,
        context_tokens=row.context_tokens,
        tool_tokens=row.tool_tokens,
        retrieval_repairs=row.retrieval_repairs,
        schema_retries=row.schema_retries,
    )


def _policy_model(row: AtlasTurnExecutionRow) -> RoutePolicyV1:
    return RoutePolicyV1(
        max_tool_invocations=row.max_tool_invocations,
        max_catalog_pages=row.max_catalog_pages,
        max_search_rounds=row.max_search_rounds,
        max_model_visible_items_per_turn=row.max_model_visible_items_per_turn,
        max_retrieval_repairs=row.max_retrieval_repairs,
        max_selected_anchor_pages_per_round=(
            row.max_selected_anchor_pages_per_round
        ),
        max_provider_invocations=row.max_provider_invocations,
        max_reasoning_revision_cycles=row.max_reasoning_revision_cycles,
        max_schema_retries_per_turn=row.max_schema_retries_per_turn,
        context_token_budget=row.context_token_budget,
        tool_token_budget=row.tool_token_budget,
        tool_execution_timeout_seconds=row.tool_execution_timeout_seconds,
        deadline_seconds=row.deadline_seconds,
    )
def _route_model(row: AtlasTurnExecutionRow) -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id=row.route_id,
        route_revision=row.route_revision,
        runtime_policy_revision=row.runtime_policy_revision,
        tokenizer_profile=row.tokenizer_profile,
        context_window_tokens=row.context_window_tokens,
        max_input_tokens_per_invocation=row.max_input_tokens_per_invocation,
        max_output_tokens_per_invocation=row.max_output_tokens_per_invocation,
        max_tool_result_tokens_per_execution=row.max_tool_result_tokens_per_execution,
        max_total_tokens_per_conversation=row.max_total_tokens_per_conversation,
        vision_route=_vision_route_model(row),
    )
def _vision_route_model(
    row: AtlasTurnExecutionRow,
) -> VisionRouteSnapshotV1 | None:
    if row.vision_route_id is None:
        return None
    return VisionRouteSnapshotV1(
        route_id=row.vision_route_id,
        route_revision=row.vision_route_revision,
        runtime_policy_revision=row.vision_runtime_policy_revision,
        tokenizer_profile=row.vision_tokenizer_profile,
        context_window_tokens=row.vision_context_window_tokens,
        max_input_tokens_per_invocation=(
            row.vision_max_input_tokens_per_invocation
        ),
        max_output_tokens_per_invocation=(
            row.vision_max_output_tokens_per_invocation
        ),
        max_tool_result_tokens_per_execution=(
            row.vision_max_tool_result_tokens_per_execution
        ),
        max_total_tokens_per_conversation=(
            row.vision_max_total_tokens_per_conversation
        ),
    )




def _release_model(row: AtlasTurnReleaseIntentRow) -> ReleaseIntentV1:
    return ReleaseIntentV1(
        release_intent_id=row.release_intent_id,
        execution_id=row.execution_id,
        resource_owner=row.resource_owner,
        resource_ref=row.resource_ref,
        release_kind=row.release_kind,
        status=row.status,
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
    )


def _event_model(row: AtlasTurnRuntimeEventRow) -> RuntimeEventV1:
    return RuntimeEventV1(
        event_id=row.event_id,
        execution_id=row.execution_id,
        sequence=row.sequence,
        event_type=row.event_type,
        state=row.state,
        invocation_ordinal=row.invocation_ordinal,
        result_ref=row.result_ref,
        failure_code=row.failure_code,
        reasoning_phase=row.reasoning_phase,
        progress_status=row.progress_status,
        cycle=row.cycle,
        message_code=row.message_code,
        message_params=row.message_params,
        created_at=row.created_at,
    )


class PostgresTurnRuntimeOwner:
    """Short, owner-local PostgreSQL transactions for one turn execution."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def snapshot(self, execution_id: str) -> ExecutionSnapshotV1:
        if not execution_id:
            raise ValueError("execution_id must be non-empty")
        with self._session_factory() as session:
            return self._snapshot(session, execution_id)

    def find_execution(self, execution_id: str) -> ExecutionSnapshotV1 | None:
        if not execution_id:
            raise ValueError("execution_id must be non-empty")
        with self._session_factory() as session:
            if session.get(AtlasTurnExecutionRow, execution_id) is None:
                return None
            return self._snapshot(session, execution_id)

    def terminal_outcome(self, execution_id: str) -> TerminalOutcomeV1 | None:
        if not execution_id:
            raise ValueError("execution_id must be non-empty")
        with self._session_factory() as session:
            outcome = session.get(AtlasTurnTerminalOutcomeRow, execution_id)
            if outcome is None:
                return None
            execution = session.get(AtlasTurnExecutionRow, execution_id)
            if execution is None:
                raise TurnRuntimeTerminalConflict("terminal execution does not exist")
            if outcome.outcome == "failed":
                return TerminalOutcomeV1(
                    execution_id=outcome.execution_id,
                    scan_sequence=outcome.scan_sequence,
                    outcome="failed",
                    result_kind=execution.result_kind,
                    failure_code=outcome.failure_code,
                    committed_at=outcome.committed_at,
                )
            intent = session.get(
                AtlasTurnTerminalIntentRow, outcome.terminal_intent_ref
            )
            if intent is None or intent.execution_id != execution_id:
                raise TurnRuntimeTerminalConflict(
                    "completed terminal outcome has no immutable intent refs"
                )
            return TerminalOutcomeV1(
                scan_sequence=outcome.scan_sequence,
                execution_id=outcome.execution_id,
                outcome="completed",
                terminal_commit_intent_ref=outcome.terminal_intent_ref,
                result_kind=intent.result_kind,
                evidence_pack_ref=intent.evidence_pack_ref,
                governed_answer_draft_ref=intent.governed_answer_draft_ref,
                citation_binding_draft_ref=intent.citation_binding_draft_ref,
                audit_draft_ref=intent.audit_draft_ref,
                research_packet_ref=intent.research_packet_ref,
                research_packet_digest=intent.research_packet_digest,
                committed_at=outcome.committed_at,
            )

    def completed_terminal_outcomes(
        self,
        *,
        after: TerminalCompletionCursorV1 | None,
        limit: int,
    ) -> list[TerminalOutcomeV1]:
        statement = (
            select(AtlasTurnTerminalOutcomeRow, AtlasTurnTerminalIntentRow)
            .join(
                AtlasTurnTerminalIntentRow,
                AtlasTurnTerminalIntentRow.terminal_intent_ref
                == AtlasTurnTerminalOutcomeRow.terminal_intent_ref,
            )
            .where(AtlasTurnTerminalOutcomeRow.outcome == "completed")
            .where(AtlasTurnTerminalIntentRow.result_kind == "conversation_answer")
        )
        if after is not None:
            statement = statement.where(
                tuple_(
                    AtlasTurnTerminalOutcomeRow.scan_sequence,
                    AtlasTurnTerminalOutcomeRow.execution_id,
                )
                > (after.scan_sequence, after.execution_id)
            )
        statement = statement.order_by(
            AtlasTurnTerminalOutcomeRow.scan_sequence,
            AtlasTurnTerminalOutcomeRow.execution_id,
        ).limit(_completed_scan_limit(limit))
        with self._session_factory() as session, session.begin():
            session.execute(
                text("LOCK TABLE atlas_turn_terminal_outcomes IN SHARE MODE")
            )
            rows = session.execute(statement).all()
            outcomes: list[TerminalOutcomeV1] = []
            for outcome, intent in rows:
                if intent.execution_id != outcome.execution_id:
                    raise TurnRuntimeTerminalConflict(
                        "completed terminal scan found mismatched immutable intent"
                    )
                outcomes.append(
                    TerminalOutcomeV1(
                        execution_id=outcome.execution_id,
                        scan_sequence=outcome.scan_sequence,
                        outcome="completed",
                        result_kind=intent.result_kind,
                        terminal_commit_intent_ref=outcome.terminal_intent_ref,
                        evidence_pack_ref=intent.evidence_pack_ref,
                        governed_answer_draft_ref=intent.governed_answer_draft_ref,
                        citation_binding_draft_ref=intent.citation_binding_draft_ref,
                        audit_draft_ref=intent.audit_draft_ref,
                        research_packet_ref=intent.research_packet_ref,
                        research_packet_digest=intent.research_packet_digest,
                        committed_at=outcome.committed_at,
                    )
                )
            return outcomes

    @staticmethod
    def _active_lease_clause(execution_id: str, fencing_token: int):
        return exists(
            select(1).where(
                AtlasTurnExecutionLeaseRow.execution_id == execution_id,
                AtlasTurnExecutionLeaseRow.fencing_token == fencing_token,
                AtlasTurnExecutionLeaseRow.expires_at > func.clock_timestamp(),
            )
        )

    @staticmethod
    def _snapshot(session: Session, execution_id: str) -> ExecutionSnapshotV1:
        execution = session.get(AtlasTurnExecutionRow, execution_id)
        lease = session.get(AtlasTurnExecutionLeaseRow, execution_id)
        budget = session.get(AtlasTurnBudgetCounterRow, execution_id)
        if execution is None or lease is None or budget is None:
            raise TurnRuntimeCurrentnessConflict("execution snapshot does not exist")
        return ExecutionSnapshotV1(
            execution_id=execution.execution_id,
            turn_id=execution.turn_id,
            conversation_id=execution.conversation_id,
            research_id=execution.research_id,
            actor_id=execution.actor_id,
            operation=execution.operation,
            result_kind=execution.result_kind,
            state=execution.state,
            version=execution.version,
            policy=_policy_model(execution),
            route=_route_model(execution),
            input_digest=execution.input_digest,
            response_language=execution.response_language,
            reasoning_mode=execution.reasoning_mode,
            reasoning_trace=(
                None
                if execution.reasoning_trace is None
                else ReasoningTraceV4.model_validate(execution.reasoning_trace)
            ),
            prompt_skill_catalogs=[
                PromptSkillCatalogRefV1.model_validate(item)
                for item in execution.prompt_skill_catalogs
            ],
            prompt_skill_selections=[
                ExecutionPromptSkillSelectionTraceV1.model_validate(item)
                for item in execution.prompt_skill_selections
            ],
            applied_guidance_revision=execution.applied_guidance_revision,
            applied_guidance_digest=execution.applied_guidance_digest,
            lease=_lease_model(lease),
            budget=_budget_model(budget),
            grant_ref=execution.grant_ref,
            catalog_ref=execution.catalog_ref,
            context_pack_ref=execution.context_pack_ref,
            terminal_commit_intent_ref=execution.terminal_commit_intent_ref,
            terminal_failure_code=execution.terminal_failure_code,
            deadline_at=execution.deadline_at,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
        )

    @staticmethod
    def _append_event(
        session: Session,
        *,
        execution_id: str,
        sequence: int,
        event_type: str,
        state: str,
        invocation_ordinal: int | None = None,
        result_ref: str | None = None,
        failure_code: str | None = None,
        reasoning_phase: str | None = None,
        progress_status: str | None = None,
        cycle: int | None = None,
        message_code: str | None = None,
        message_params: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        session.add(
            AtlasTurnRuntimeEventRow(
                event_id=str(uuid4()),
                execution_id=execution_id,
                sequence=sequence,
                event_type=event_type,
                state=state,
                invocation_ordinal=invocation_ordinal,
                result_ref=result_ref,
                failure_code=failure_code,
                reasoning_phase=reasoning_phase,
                progress_status=progress_status,
                cycle=cycle,
                message_code=message_code,
                message_params=message_params or {},
                created_at=func.clock_timestamp(),
            )
        )

    def _cas_execution(
        self,
        session: Session,
        *,
        execution_id: str,
        expected_version: int,
        fencing_token: int,
        from_states: Iterable[str],
        to_state: str,
        values: dict[str, object] | None = None,
        require_live_deadline: bool = True,
    ) -> AtlasTurnExecutionRow:
        predicates = [
            AtlasTurnExecutionRow.execution_id == execution_id,
            AtlasTurnExecutionRow.version == expected_version,
            AtlasTurnExecutionRow.state.in_(tuple(from_states)),
            self._active_lease_clause(execution_id, fencing_token),
        ]
        if require_live_deadline:
            predicates.append(AtlasTurnExecutionRow.deadline_at > func.clock_timestamp())
        changed = session.scalar(
            update(AtlasTurnExecutionRow)
            .where(*predicates)
            .values(
                state=to_state,
                version=AtlasTurnExecutionRow.version + 1,
                updated_at=func.clock_timestamp(),
                **(values or {}),
            )
            .returning(AtlasTurnExecutionRow)
        )
        if changed is None:
            raise TurnRuntimeCurrentnessConflict(
                "execution state, version, fence, lease, or deadline is stale"
            )
        return changed

    @staticmethod
    def _add_release_intent(
        session: Session,
        *,
        execution_id: str,
        resource_owner: str,
        resource_ref: str,
        release_kind: str,
    ) -> None:
        intent_id = _stable_id("release", execution_id, resource_owner, resource_ref, release_kind)
        session.execute(
            insert(AtlasTurnReleaseIntentRow)
            .values(
                release_intent_id=intent_id,
                execution_id=execution_id,
                resource_owner=resource_owner,
                resource_ref=resource_ref,
                release_kind=release_kind,
                idempotency_key=intent_id,
                status="pending",
                attempt_count=0,
                next_attempt_at=None,
                failure_code=None,
                created_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
            .on_conflict_do_nothing()
        )

    def _add_bound_release_intents(
        self, session: Session, execution: AtlasTurnExecutionRow
    ) -> None:
        staged_owners = set(
            session.scalars(
                select(AtlasTurnAcceptanceResourceRow.resource_owner).where(
                    AtlasTurnAcceptanceResourceRow.execution_id == execution.execution_id
                )
            )
        )
        resources = (
            ("authorization", execution.grant_ref, "release_turn_grant"),
            ("retrieval", execution.catalog_ref, "release_knowledge_catalog"),
            ("context_engineering", execution.context_pack_ref, "release_context_pack"),
        )
        for owner, reference, kind in resources:
            if reference and owner not in staged_owners:
                self._add_release_intent(
                    session,
                    execution_id=execution.execution_id,
                    resource_owner=owner,
                    resource_ref=reference,
                    release_kind=kind,
                )

    def _add_staged_release_intents(self, session: Session, execution_id: str) -> None:
        rows = session.scalars(
            select(AtlasTurnAcceptanceResourceRow).where(
                AtlasTurnAcceptanceResourceRow.execution_id == execution_id
            )
        )
        for row in rows:
            self._add_release_intent(
                session,
                execution_id=execution_id,
                resource_owner=row.resource_owner,
                resource_ref=f"execution-resource:{row.resource_owner}:{execution_id}",
                release_kind=row.release_kind,
            )

    def _add_prepared_release_intents(self, session: Session, execution_id: str) -> None:
        # Evidence packs and terminal drafts are immutable durable terminal
        # records, not leased resources. They remain readable for projection,
        # audit, and request-time visibility after either terminal outcome.
        del session, execution_id

    def _record_stale_materialized_refs(
        self,
        execution_id: str,
        resources: Iterable[tuple[str, str, str]],
        *,
        accepted_refs: tuple[str | None, ...],
    ) -> None:
        with self._session_factory() as session, session.begin():
            if session.get(AtlasTurnExecutionRow, execution_id) is None:
                return
            for (owner, reference, kind), accepted in zip(resources, accepted_refs, strict=True):
                if reference != accepted:
                    self._add_release_intent(
                        session,
                        execution_id=execution_id,
                        resource_owner=owner,
                        resource_ref=reference,
                        release_kind=kind,
                    )

    def allocate(self, command: AllocateExecutionV1) -> ExecutionSnapshotV1:
        scope = f"execution:{command.execution_id}"
        digest = _digest_model(command)
        key = (scope, "allocate", command.idempotency_key)
        with self._session_factory() as session, session.begin():
            replay = session.get(AtlasTurnRuntimeIdempotencyRow, key)
            if replay is not None:
                if replay.request_digest != digest:
                    raise TurnRuntimeReplayConflict(
                        "allocation replay payload conflicts with the original"
                    )
                return self._snapshot(session, replay.result_execution_id)

            policy = command.route_policy
            lease_policy = command.lease_policy
            route_values = command.route.model_dump(exclude={"vision_route"})
            vision_route = command.route.vision_route
            vision_route_values = (
                {}
                if vision_route is None
                else {
                    f"vision_{key}": value
                    for key, value in vision_route.model_dump().items()
                }
            )
            inserted_id = session.scalar(
                insert(AtlasTurnExecutionRow)
                .values(
                    execution_id=command.execution_id,
                    turn_id=command.turn_id,
                    conversation_id=command.conversation_id,
                    research_id=command.research_id,
                    actor_id=command.actor_id,
                    operation=command.operation,
                    result_kind=command.result_kind,
                    input_digest=command.input_digest,
                    response_language=command.response_language,
                    reasoning_mode=command.reasoning_mode,
                    prompt_skill_catalogs=[
                        item.model_dump(mode="json")
                        for item in command.prompt_skill_catalogs
                    ],
                    reasoning_trace=None,
                    prompt_skill_selections=[],
                    applied_guidance_revision=command.applied_guidance_revision,
                    applied_guidance_digest=command.applied_guidance_digest,
                    state=ExecutionState.ALLOCATED.value,
                    version=1,
                    **policy.model_dump(),
                    **route_values,
                    **vision_route_values,
                    **lease_policy.model_dump(),
                    grant_ref=None,
                    catalog_ref=None,
                    context_pack_ref=None,
                    terminal_commit_intent_ref=None,
                    terminal_failure_code=None,
                    deadline_at=func.clock_timestamp()
                    + func.make_interval(
                        0, 0, 0, 0, 0, 0, policy.deadline_seconds
                    ),
                    created_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing()
                .returning(AtlasTurnExecutionRow.execution_id)
            )
            if inserted_id is None:
                replay = session.get(AtlasTurnRuntimeIdempotencyRow, key)
                if replay is not None and replay.request_digest == digest:
                    return self._snapshot(session, replay.result_execution_id)
                raise TurnRuntimeReplayConflict(
                    "execution, turn, or allocation replay identity already exists"
                )

            now = func.clock_timestamp()
            session.add_all(
                [
                    AtlasTurnExecutionLeaseRow(
                        execution_id=command.execution_id,
                        holder_id=command.holder_id,
                        lease_version=1,
                        fencing_token=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        expires_at=now
                        + func.make_interval(
                            0, 0, 0, 0, 0, 0, lease_policy.ttl_seconds
                        ),
                    ),
                    AtlasTurnBudgetCounterRow(
                        execution_id=command.execution_id,
                        tool_invocations=0,
                        catalog_pages=0,
                        document_candidates=0,
                        search_rounds=0,
                        model_visible_items=0,
                        provider_invocations=0,
                        context_tokens=0,
                        tool_tokens=0,
                        retrieval_repairs=0,
                        schema_retries=0,
                    ),
                    AtlasTurnRuntimeIdempotencyRow(
                        scope_ref=scope,
                        operation="allocate",
                        idempotency_key=command.idempotency_key,
                        request_digest=digest,
                        result_execution_id=command.execution_id,
                        result_version=1,
                        created_at=now,
                    ),
                ]
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=1,
                event_type="execution_allocated",
                state=ExecutionState.ALLOCATED.value,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def record_prompt_skill_selection(
        self,
        command: RecordExecutionPromptSkillSelectionV1,
    ) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            current = session.get(AtlasTurnExecutionRow, command.execution_id)
            if current is None:
                raise TurnRuntimeCurrentnessConflict("execution does not exist")
            existing = [
                ExecutionPromptSkillSelectionTraceV1.model_validate(item)
                for item in current.prompt_skill_selections
            ]
            selection = command.selection
            if selection.node == "resolver":
                if current.state != ExecutionState.ACCEPTED.value or existing:
                    raise TurnRuntimeCurrentnessConflict(
                        "Resolver selection requires accepted execution before context bind"
                    )
            else:
                if current.state not in {
                    ExecutionState.CONTEXT_READY.value,
                    ExecutionState.AWAITING_MODEL_ACTION.value,
                    ExecutionState.TOOL_COMPLETED.value,
                }:
                    raise TurnRuntimeCurrentnessConflict(
                        "Answer selection requires context-ready execution"
                    )
                if not existing or existing[0].node != "resolver":
                    raise TurnRuntimeReplayConflict(
                        "Answer selection requires the Resolver selection"
                    )
                expected_ordinal = len(existing)
                if selection.candidate_ordinal != expected_ordinal:
                    raise TurnRuntimeReplayConflict(
                        "Answer candidate selection ordinal is not contiguous"
                    )
            appended = [
                *existing,
                selection,
            ]
            if len(appended) > 6:
                raise TurnRuntimeReplayConflict(
                    "execution prompt skill selection limit exceeded"
                )
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(current.state,),
                to_state=current.state,
                values={
                    "prompt_skill_selections": [
                        item.model_dump(mode="json") for item in appended
                    ]
                },
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="prompt_skill_selection_recorded",
                state=changed.state,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def record_reasoning_progress(
        self, command: RecordReasoningProgressV1
    ) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            current = session.get(AtlasTurnExecutionRow, command.execution_id)
            if current is None or current.reasoning_mode != "deep":
                raise TurnRuntimeCurrentnessConflict(
                    "reasoning progress requires a deep execution"
                )
            planner_catalog = PromptSkillCatalogRefV1.model_validate(
                current.prompt_skill_catalogs[1]
            )
            if command.trace.prompt_skill_catalog != planner_catalog:
                raise TurnRuntimeReplayConflict(
                    "reasoning trace planner catalog does not match execution pin"
                )
            if current.reasoning_trace is not None:
                prior = ReasoningTraceV4.model_validate(current.reasoning_trace)
                if (
                    command.trace.trace_revision != prior.trace_revision + 1
                    or command.trace.parent_trace_digest != prior.trace_digest
                ):
                    raise TurnRuntimeReplayConflict(
                        "reasoning trace lineage is not contiguous"
                    )
            elif (
                command.trace.trace_revision != 1
                or command.trace.parent_trace_digest is not None
            ):
                raise TurnRuntimeReplayConflict(
                    "initial reasoning trace lineage is invalid"
                )
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(current.state,),
                to_state=current.state,
                values={"reasoning_trace": command.trace.model_dump(mode="json")},
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="reasoning_progressed",
                state=changed.state,
                reasoning_phase=command.phase,
                progress_status=command.progress_status,
                cycle=command.cycle,
                message_code=command.message_code,
                message_params=command.message_params,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def accept(self, command: AcceptExecutionV1) -> ExecutionSnapshotV1:
        try:
            with self._session_factory() as session, session.begin():
                changed = self._cas_execution(
                    session,
                    execution_id=command.execution_id,
                    expected_version=command.expected_version,
                    fencing_token=command.fencing_token,
                    from_states=(ExecutionState.ALLOCATED.value,),
                    to_state=ExecutionState.ACCEPTED.value,
                    values={"grant_ref": command.grant_ref, "catalog_ref": command.catalog_ref},
                )
                self._append_event(
                    session,
                    execution_id=command.execution_id,
                    sequence=changed.version,
                    event_type="execution_accepted",
                    state=changed.state,
                )
                session.flush()
                return self._snapshot(session, command.execution_id)
        except TurnRuntimeCurrentnessConflict:
            with self._session_factory() as session:
                current = session.get(AtlasTurnExecutionRow, command.execution_id)
                accepted = (
                    current.grant_ref if current else None,
                    current.catalog_ref if current else None,
                )
            self._record_stale_materialized_refs(
                command.execution_id,
                (
                    ("authorization", command.grant_ref, "release_turn_grant"),
                    ("retrieval", command.catalog_ref, "release_knowledge_catalog"),
                ),
                accepted_refs=accepted,
            )
            raise

    def stage_acceptance_resource(self, command: StageAcceptanceResourceV1) -> None:
        """Durably record cleanup authority before the next owner call."""

        with self._session_factory() as session, session.begin():
            live = session.scalar(
                select(AtlasTurnExecutionRow.execution_id).where(
                    AtlasTurnExecutionRow.execution_id == command.execution_id,
                    AtlasTurnExecutionRow.version == command.expected_version,
                    AtlasTurnExecutionRow.state.not_in(_TERMINAL),
                    AtlasTurnExecutionRow.deadline_at > func.clock_timestamp(),
                    exists(
                        select(1).where(
                            AtlasTurnExecutionLeaseRow.execution_id == command.execution_id,
                            AtlasTurnExecutionLeaseRow.fencing_token == command.fencing_token,
                            AtlasTurnExecutionLeaseRow.expires_at > func.clock_timestamp(),
                        )
                    ),
                )
            )
            if live is None:
                raise TurnRuntimeCurrentnessConflict(
                    "acceptance resource stage is stale"
                )
            inserted = session.scalar(
                insert(AtlasTurnAcceptanceResourceRow)
                .values(
                    execution_id=command.execution_id,
                    resource_owner=command.resource_owner,
                    release_kind=command.release_kind,
                    staged_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing()
                .returning(AtlasTurnAcceptanceResourceRow.execution_id)
            )
            if inserted is None:
                existing = session.get(
                    AtlasTurnAcceptanceResourceRow,
                    (command.execution_id, command.resource_owner),
                )
                if existing is None or existing.release_kind != command.release_kind:
                    raise TurnRuntimeReplayConflict(
                        "acceptance resource replay payload changed"
                    )

    def bind_context(self, command: BindContextV1) -> ExecutionSnapshotV1:
        try:
            with self._session_factory() as session, session.begin():
                changed = self._cas_execution(
                    session,
                    execution_id=command.execution_id,
                    expected_version=command.expected_version,
                    fencing_token=command.fencing_token,
                    from_states=(ExecutionState.ACCEPTED.value,),
                    to_state=ExecutionState.CONTEXT_READY.value,
                    values={"context_pack_ref": command.context_pack_ref},
                )
                self._append_event(
                    session,
                    execution_id=command.execution_id,
                    sequence=changed.version,
                    event_type="context_ready",
                    state=changed.state,
                )
                session.flush()
                return self._snapshot(session, command.execution_id)
        except TurnRuntimeCurrentnessConflict:
            with self._session_factory() as session:
                current = session.get(AtlasTurnExecutionRow, command.execution_id)
                accepted = (current.context_pack_ref if current else None,)
            self._record_stale_materialized_refs(
                command.execution_id,
                (("context_engineering", command.context_pack_ref, "release_context_pack"),),
                accepted_refs=accepted,
            )
            raise

    def reserve_acceptance_model_action(
        self, command: ReserveAcceptanceModelActionV1
    ) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.ACCEPTED.value,),
                to_state=ExecutionState.ACCEPTED.value,
            )
            budget = session.scalar(
                update(AtlasTurnBudgetCounterRow)
                .where(
                    AtlasTurnBudgetCounterRow.execution_id == command.execution_id,
                    AtlasTurnBudgetCounterRow.provider_invocations + 1
                    <= changed.max_provider_invocations,
                    command.context_tokens <= changed.context_token_budget,
                )
                .values(
                    provider_invocations=AtlasTurnBudgetCounterRow.provider_invocations
                    + 1,
                    context_tokens=AtlasTurnBudgetCounterRow.context_tokens
                    + command.context_tokens,
                )
                .returning(AtlasTurnBudgetCounterRow)
            )
            if budget is None:
                raise TurnRuntimeBudgetExceeded(
                    "provider invocation or per-invocation context token budget exceeded"
                )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="model_action_requested",
                state=changed.state,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def activate_research(
        self, command: ActivateResearchExecutionV1
    ) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            current = session.get(AtlasTurnExecutionRow, command.execution_id)
            if (
                current is None
                or current.result_kind != "agent_research"
                or current.context_pack_ref is not None
            ):
                raise TurnRuntimeCurrentnessConflict(
                    "research execution activation requires accepted research"
                )
            selections = [
                ExecutionPromptSkillSelectionTraceV1.model_validate(item)
                for item in current.prompt_skill_selections
            ]
            if (
                len(selections) != 1
                or selections[0].node != "resolver"
                or selections[0].status != "not_applicable"
            ):
                raise TurnRuntimeCurrentnessConflict(
                    "research execution activation requires not-applicable Resolver"
                )
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.ACCEPTED.value,),
                to_state=ExecutionState.CONTEXT_READY.value,
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="context_ready",
                state=changed.state,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def request_model_action(self, command: RequestModelActionV1) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(
                    ExecutionState.CONTEXT_READY.value,
                    ExecutionState.TOOL_COMPLETED.value,
                    ExecutionState.AWAITING_MODEL_ACTION.value,
                ),
                to_state=ExecutionState.AWAITING_MODEL_ACTION.value,
            )
            budget = session.scalar(
                update(AtlasTurnBudgetCounterRow)
                .where(
                    AtlasTurnBudgetCounterRow.execution_id == command.execution_id,
                    AtlasTurnBudgetCounterRow.provider_invocations + 1
                    <= changed.max_provider_invocations,
                    command.context_tokens <= changed.context_token_budget,
                    (
                        AtlasTurnBudgetCounterRow.retrieval_repairs
                        < changed.max_retrieval_repairs
                        if command.contract_repair
                        else True
                    ),
                )
                .values(
                    provider_invocations=AtlasTurnBudgetCounterRow.provider_invocations + 1,
                    context_tokens=AtlasTurnBudgetCounterRow.context_tokens
                    + command.context_tokens,
                    retrieval_repairs=(
                        AtlasTurnBudgetCounterRow.retrieval_repairs + 1
                        if command.contract_repair
                        else AtlasTurnBudgetCounterRow.retrieval_repairs
                    ),
                )
                .returning(AtlasTurnBudgetCounterRow)
            )
            if budget is None:
                raise TurnRuntimeBudgetExceeded(
                    "provider invocation, retrieval repair, or per-invocation context token budget exceeded"
                )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="model_action_requested",
                state=changed.state,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def claim_schema_retry(self, command: ClaimSchemaRetryV1) -> ExecutionSnapshotV1:
        scope = f"execution:{command.execution_id}"
        digest = _digest_model(command)
        key = (scope, "claim_schema_retry", command.claim_key)
        active_states = (
            ExecutionState.ACCEPTED.value,
            ExecutionState.CONTEXT_READY.value,
            ExecutionState.AWAITING_MODEL_ACTION.value,
            ExecutionState.TOOL_PENDING.value,
            ExecutionState.TOOL_COMPLETED.value,
            ExecutionState.GOVERNING_RESULT.value,
        )
        with self._session_factory() as session, session.begin():
            execution = session.scalar(
                select(AtlasTurnExecutionRow).where(
                    AtlasTurnExecutionRow.execution_id == command.execution_id,
                    AtlasTurnExecutionRow.state.in_(active_states),
                    AtlasTurnExecutionRow.deadline_at > func.clock_timestamp(),
                    self._active_lease_clause(
                        command.execution_id, command.fencing_token
                    ),
                ).with_for_update()
            )
            if execution is None:
                raise TurnRuntimeCurrentnessConflict(
                    "schema retry claim requires a live accepted execution and current fence"
                )

            replay = session.get(AtlasTurnRuntimeIdempotencyRow, key)
            if replay is not None:
                if replay.request_digest != digest:
                    raise TurnRuntimeReplayConflict(
                        "schema retry claim replay payload conflicts with the original"
                    )
                return self._snapshot(session, command.execution_id)

            inserted_key = session.scalar(
                insert(AtlasTurnRuntimeIdempotencyRow)
                .values(
                    scope_ref=scope,
                    operation="claim_schema_retry",
                    idempotency_key=command.claim_key,
                    request_digest=digest,
                    result_execution_id=command.execution_id,
                    result_version=execution.version,
                    created_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing()
                .returning(AtlasTurnRuntimeIdempotencyRow.idempotency_key)
            )
            if inserted_key is None:
                replay = session.get(AtlasTurnRuntimeIdempotencyRow, key)
                if replay is not None and replay.request_digest == digest:
                    return self._snapshot(session, command.execution_id)
                raise TurnRuntimeReplayConflict(
                    "schema retry claim identity already exists"
                )

            budget = session.scalar(
                update(AtlasTurnBudgetCounterRow)
                .where(
                    AtlasTurnBudgetCounterRow.execution_id == command.execution_id,
                    AtlasTurnBudgetCounterRow.schema_retries
                    < execution.max_schema_retries_per_turn,
                )
                .values(
                    schema_retries=AtlasTurnBudgetCounterRow.schema_retries + 1
                )
                .returning(AtlasTurnBudgetCounterRow)
            )
            if budget is None:
                raise TurnRuntimeBudgetExceeded(
                    "turn schema retry budget exhausted"
                )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def begin_tool(self, command: BeginToolInvocationV1) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.AWAITING_MODEL_ACTION.value,),
                to_state=ExecutionState.TOOL_PENDING.value,
            )
            budget = session.scalar(
                update(AtlasTurnBudgetCounterRow)
                .where(
                    AtlasTurnBudgetCounterRow.execution_id == command.execution_id,
                    AtlasTurnBudgetCounterRow.tool_invocations + 1 == command.invocation_ordinal,
                    AtlasTurnBudgetCounterRow.tool_invocations + 1 <= changed.max_tool_invocations,
                    AtlasTurnBudgetCounterRow.catalog_pages + command.reserve_catalog_pages
                    <= changed.max_catalog_pages,
                    AtlasTurnBudgetCounterRow.search_rounds + command.reserve_search_rounds
                    <= changed.max_search_rounds,
                    AtlasTurnBudgetCounterRow.model_visible_items
                    + command.reserve_model_visible_items
                    <= changed.max_model_visible_items_per_turn,
                    AtlasTurnBudgetCounterRow.tool_tokens
                    < changed.tool_token_budget,
                    command.reserve_tool_tokens <= changed.tool_token_budget,
                )
                .values(tool_invocations=AtlasTurnBudgetCounterRow.tool_invocations + 1)
                .returning(AtlasTurnBudgetCounterRow)
            )
            if budget is None:
                raise TurnRuntimeBudgetExceeded(
                    "tool invocation budget or sequential ordinal rejected"
                )
            now = func.clock_timestamp()
            session.add_all(
                [
                    AtlasTurnToolLedgerRow(
                        tool_invocation_id=command.tool_invocation_id,
                        execution_id=command.execution_id,
                        invocation_ordinal=command.invocation_ordinal,
                        tool_name=command.tool_name,
                        schema_version=command.schema_version,
                        arguments_digest=command.arguments_digest,
                        reserve_catalog_pages=command.reserve_catalog_pages,
                        reserve_document_candidates=command.reserve_document_candidates,
                        reserve_search_rounds=command.reserve_search_rounds,
                        reserve_model_visible_items=command.reserve_model_visible_items,
                        reserve_tool_tokens=command.reserve_tool_tokens,
                        result_ref=None,
                        result_digest=None,
                        status="started",
                        created_at=now,
                        completed_at=None,
                    ),
                    AtlasTurnStepLedgerRow(
                        step_id=command.tool_invocation_id,
                        execution_id=command.execution_id,
                        ordinal=command.invocation_ordinal,
                        step_kind="tool",
                        status="started",
                        input_digest=command.arguments_digest,
                        result_ref=None,
                        created_at=now,
                        completed_at=None,
                    ),
                ]
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="tool_started",
                state=changed.state,
                invocation_ordinal=command.invocation_ordinal,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def complete_tool(self, command: CompleteToolInvocationV1) -> ExecutionSnapshotV1:
        candidate_ids = tuple(dict.fromkeys(command.document_candidate_handles))
        item_ids = tuple(dict.fromkeys(command.model_visible_item_identities))
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.TOOL_PENDING.value,),
                to_state=ExecutionState.TOOL_COMPLETED.value,
            )
            tool = session.scalar(
                select(AtlasTurnToolLedgerRow).where(
                    AtlasTurnToolLedgerRow.tool_invocation_id == command.tool_invocation_id,
                    AtlasTurnToolLedgerRow.execution_id == command.execution_id,
                    AtlasTurnToolLedgerRow.invocation_ordinal == command.invocation_ordinal,
                    AtlasTurnToolLedgerRow.status == "started",
                )
            )
            if tool is None:
                raise TurnRuntimeCurrentnessConflict("tool invocation ledger is stale")
            if (
                command.catalog_pages > tool.reserve_catalog_pages
                or command.search_rounds > tool.reserve_search_rounds
                or command.tool_tokens > tool.reserve_tool_tokens
            ):
                raise TurnRuntimeBudgetExceeded(
                    "tool result exceeds its pre-side-effect reservation"
                )
            existing_candidates = set(
                session.scalars(
                    select(AtlasTurnDocumentCandidateLedgerRow.document_identity).where(
                        AtlasTurnDocumentCandidateLedgerRow.execution_id == command.execution_id,
                        AtlasTurnDocumentCandidateLedgerRow.document_identity.in_(
                            candidate_ids or ("",)
                        ),
                    )
                )
            )
            existing_items = set(
                session.scalars(
                    select(AtlasTurnModelVisibleItemLedgerRow.item_identity).where(
                        AtlasTurnModelVisibleItemLedgerRow.execution_id == command.execution_id,
                        AtlasTurnModelVisibleItemLedgerRow.item_identity.in_(
                            item_ids or ("",)
                        ),
                    )
                )
            )
            new_candidates = tuple(
                value for value in candidate_ids if value not in existing_candidates
            )
            new_items = tuple(value for value in item_ids if value not in existing_items)
            if (
                len(new_candidates) > tool.reserve_document_candidates
                or len(new_items) > tool.reserve_model_visible_items
            ):
                raise TurnRuntimeBudgetExceeded(
                    "tool identities exceed their pre-side-effect reservation"
                )
            budget = session.scalar(
                update(AtlasTurnBudgetCounterRow)
                .where(
                    AtlasTurnBudgetCounterRow.execution_id == command.execution_id,
                    AtlasTurnBudgetCounterRow.model_visible_items + len(new_items)
                    <= changed.max_model_visible_items_per_turn,
                    AtlasTurnBudgetCounterRow.catalog_pages + command.catalog_pages
                    <= changed.max_catalog_pages,
                    AtlasTurnBudgetCounterRow.search_rounds + command.search_rounds
                    <= changed.max_search_rounds,
                )
                .values(
                    document_candidates=AtlasTurnBudgetCounterRow.document_candidates
                    + len(new_candidates),
                    model_visible_items=AtlasTurnBudgetCounterRow.model_visible_items + len(new_items),
                    catalog_pages=AtlasTurnBudgetCounterRow.catalog_pages + command.catalog_pages,
                    search_rounds=AtlasTurnBudgetCounterRow.search_rounds + command.search_rounds,
                    tool_tokens=AtlasTurnBudgetCounterRow.tool_tokens + command.tool_tokens,
                )
                .returning(AtlasTurnBudgetCounterRow)
            )
            if budget is None:
                raise TurnRuntimeBudgetExceeded(
                    "catalog page, search round, candidate, evidence, or tool token budget exceeded"
                )
            for identity in new_candidates:
                session.add(
                    AtlasTurnDocumentCandidateLedgerRow(
                        execution_id=command.execution_id,
                        document_identity=identity,
                        first_invocation_ordinal=command.invocation_ordinal,
                    )
                )
            for identity in new_items:
                session.add(
                    AtlasTurnModelVisibleItemLedgerRow(
                        execution_id=command.execution_id,
                        item_identity=identity,
                        first_invocation_ordinal=command.invocation_ordinal,
                    )
                )
            now = func.clock_timestamp()
            tool.result_ref = command.result_ref
            tool.result_digest = command.result_digest
            tool.status = "completed"
            tool.completed_at = now
            step = session.get(AtlasTurnStepLedgerRow, command.tool_invocation_id)
            if step is None or step.status != "started":
                raise TurnRuntimeCurrentnessConflict("tool step ledger is stale")
            step.status = "completed"
            step.result_ref = command.result_ref
            step.completed_at = now
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="tool_completed",
                state=changed.state,
                invocation_ordinal=command.invocation_ordinal,
                result_ref=command.result_ref,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def begin_governance(self, command: BeginResultGovernanceV1) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.AWAITING_MODEL_ACTION.value,),
                to_state=ExecutionState.GOVERNING_RESULT.value,
            )
            self._append_event(
                session,
                execution_id=command.execution_id,
                sequence=changed.version,
                event_type="governance_started",
                state=changed.state,
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def prepare_terminal(self, command: PrepareTerminalV1) -> ExecutionSnapshotV1:
        intent_digest = _terminal_digest(command)
        intent_ref = f"terminal-intent:{command.execution_id}:{intent_digest}"
        with self._session_factory() as session, session.begin():
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.GOVERNING_RESULT.value,),
                to_state=ExecutionState.MATERIALIZING_TERMINAL.value,
                values={"terminal_commit_intent_ref": intent_ref},
            )
            if changed.result_kind != command.result_kind:
                raise TurnRuntimeTerminalConflict(
                    "terminal result kind differs from immutable allocation"
                )
            session.add(
                AtlasTurnTerminalIntentRow(
                    terminal_intent_ref=intent_ref,
                    execution_id=command.execution_id,
                    result_kind=command.result_kind,
                    evidence_pack_ref=command.evidence_pack_ref,
                    governed_answer_draft_ref=command.governed_answer_draft_ref,
                    citation_binding_draft_ref=command.citation_binding_draft_ref,
                    research_packet_ref=command.research_packet_ref,
                    research_packet_digest=command.research_packet_digest,
                    audit_draft_ref=command.audit_draft_ref,
                    intent_digest=intent_digest,
                    prepared_at=func.clock_timestamp(),
                )
            )
            session.flush()
            return self._snapshot(session, command.execution_id)

    def _lock_research_terminal_in_session(
        self,
        session: Session,
        command: CommitTerminalV1,
        *,
        research_id: str,
    ) -> str:
        execution = session.scalar(
            select(AtlasTurnExecutionRow)
            .where(AtlasTurnExecutionRow.execution_id == command.execution_id)
            .with_for_update()
        )
        if (
            execution is None
            or execution.result_kind != "agent_research"
            or execution.research_id != research_id
            or execution.terminal_commit_intent_ref
            != command.terminal_commit_intent_ref
        ):
            raise TurnRuntimeTerminalConflict(
                "research terminal execution identity does not match"
            )
        if execution.state == ExecutionState.MATERIALIZING_TERMINAL.value:
            if execution.version != command.expected_version:
                raise TurnRuntimeTerminalConflict(
                    "research terminal execution version is stale"
                )
            return "prepared"
        if execution.state == ExecutionState.TERMINAL_COMPLETED.value:
            if execution.version != command.expected_version + 1:
                raise TurnRuntimeTerminalConflict(
                    "research terminal replay version does not match"
                )
            return "completed"
        raise TurnRuntimeTerminalConflict(
            "research terminal execution is not publishable"
        )

    @staticmethod
    def _validate_research_terminal_intent_in_session(
        session: Session,
        command: CommitTerminalV1,
        *,
        packet_ref: str,
        packet_digest: str,
    ) -> AtlasTurnTerminalIntentRow:
        intent = session.get(
            AtlasTurnTerminalIntentRow, command.terminal_commit_intent_ref
        )
        if (
            intent is None
            or intent.execution_id != command.execution_id
            or intent.result_kind != "agent_research"
            or intent.research_packet_ref != packet_ref
            or intent.research_packet_digest != packet_digest
        ):
            raise TurnRuntimeTerminalConflict(
                "research terminal intent does not match packet ref and digest"
            )
        return intent

    def _replay_research_terminal_in_session(
        self,
        session: Session,
        command: CommitTerminalV1,
        *,
        packet_ref: str,
        packet_digest: str,
    ) -> ExecutionSnapshotV1:
        self._validate_research_terminal_intent_in_session(
            session,
            command,
            packet_ref=packet_ref,
            packet_digest=packet_digest,
        )
        outcome = session.get(AtlasTurnTerminalOutcomeRow, command.execution_id)
        if (
            outcome is None
            or outcome.outcome != "completed"
            or outcome.terminal_intent_ref != command.terminal_commit_intent_ref
        ):
            raise TurnRuntimeTerminalConflict(
                "research terminal outcome does not match its immutable intent"
            )
        return self._snapshot(session, command.execution_id)

    def _commit_terminal_in_session(
        self,
        session: Session,
        command: CommitTerminalV1,
        *,
        expected_result_kind: str,
    ) -> ExecutionSnapshotV1:
        try:
            changed = self._cas_execution(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                fencing_token=command.fencing_token,
                from_states=(ExecutionState.MATERIALIZING_TERMINAL.value,),
                to_state=ExecutionState.TERMINAL_COMPLETED.value,
                values={"terminal_failure_code": None},
            )
        except TurnRuntimeCurrentnessConflict as error:
            raise TurnRuntimeTerminalConflict(
                "terminal commit lost the execution CAS"
            ) from error
        if (
            changed.result_kind != expected_result_kind
            or changed.terminal_commit_intent_ref
            != command.terminal_commit_intent_ref
        ):
            raise TurnRuntimeTerminalConflict(
                "terminal result kind or intent is not the prepared value"
            )
        intent = session.get(
            AtlasTurnTerminalIntentRow, command.terminal_commit_intent_ref
        )
        if (
            intent is None
            or intent.execution_id != command.execution_id
            or intent.result_kind != expected_result_kind
        ):
            raise TurnRuntimeTerminalConflict("prepared terminal intent does not exist")
        session.add(
            AtlasTurnTerminalOutcomeRow(
                execution_id=command.execution_id,
                outcome="completed",
                terminal_intent_ref=command.terminal_commit_intent_ref,
                failure_code=None,
                detected_by=None,
                committed_at=func.clock_timestamp(),
            )
        )
        self._add_bound_release_intents(session, changed)
        self._add_staged_release_intents(session, command.execution_id)
        self._append_event(
            session,
            execution_id=command.execution_id,
            sequence=changed.version,
            event_type="terminal_completed",
            state=changed.state,
            result_ref=command.terminal_commit_intent_ref,
        )
        session.flush()
        return self._snapshot(session, command.execution_id)

    def _commit_research_terminal_in_session(
        self,
        session: Session,
        command: CommitTerminalV1,
        *,
        packet_ref: str,
        packet_digest: str,
    ) -> ExecutionSnapshotV1:
        self._validate_research_terminal_intent_in_session(
            session,
            command,
            packet_ref=packet_ref,
            packet_digest=packet_digest,
        )
        return self._commit_terminal_in_session(
            session,
            command,
            expected_result_kind="agent_research",
        )

    def commit_terminal(self, command: CommitTerminalV1) -> ExecutionSnapshotV1:
        with self._session_factory() as session, session.begin():
            execution = session.scalar(
                select(AtlasTurnExecutionRow)
                .where(AtlasTurnExecutionRow.execution_id == command.execution_id)
                .with_for_update()
            )
            if execution is None:
                raise TurnRuntimeTerminalConflict(
                    "terminal execution does not exist"
                )
            if execution.result_kind == "agent_research":
                raise TurnRuntimeTerminalConflict(
                    "agent research terminal publication requires the atomic owner pair"
                )
            return self._commit_terminal_in_session(
                session,
                command,
                expected_result_kind="conversation_answer",
            )

    def _terminal_failure(
        self,
        session: Session,
        *,
        execution_id: str,
        expected_version: int,
        failure_code: str,
        detected_by: str,
        extra_predicates: Iterable[object],
    ) -> ExecutionSnapshotV1:
        changed = session.scalar(
            update(AtlasTurnExecutionRow)
            .where(
                AtlasTurnExecutionRow.execution_id == execution_id,
                AtlasTurnExecutionRow.version == expected_version,
                AtlasTurnExecutionRow.state.not_in(_TERMINAL),
                *extra_predicates,
            )
            .values(
                state=ExecutionState.TERMINAL_FAILED.value,
                version=AtlasTurnExecutionRow.version + 1,
                terminal_failure_code=failure_code,
                updated_at=func.clock_timestamp(),
            )
            .returning(AtlasTurnExecutionRow)
        )
        if changed is None:
            raise TurnRuntimeTerminalConflict("terminal failure lost the execution CAS")
        session.add(
            AtlasTurnTerminalOutcomeRow(
                execution_id=execution_id,
                outcome="failed",
                terminal_intent_ref=None,
                failure_code=failure_code,
                detected_by=detected_by,
                committed_at=func.clock_timestamp(),
            )
        )
        self._add_bound_release_intents(session, changed)
        self._add_staged_release_intents(session, execution_id)
        self._add_prepared_release_intents(session, execution_id)
        self._append_event(
            session,
            execution_id=execution_id,
            sequence=changed.version,
            event_type="terminal_failed",
            state=changed.state,
            failure_code=failure_code,
        )
        session.flush()
        return self._snapshot(session, execution_id)

    def fail_carrier(self, command: FailCarrierExecutionV1) -> ExecutionSnapshotV1:
        lease_match = exists(
            select(1).where(
                AtlasTurnExecutionLeaseRow.execution_id == command.execution_id,
                AtlasTurnExecutionLeaseRow.holder_id == command.holder_id,
                AtlasTurnExecutionLeaseRow.lease_version == command.expected_lease_version,
                AtlasTurnExecutionLeaseRow.fencing_token == command.fencing_token,
                AtlasTurnExecutionLeaseRow.expires_at > func.clock_timestamp(),
            )
        )
        deadline_clause = (
            AtlasTurnExecutionRow.deadline_at <= func.clock_timestamp()
            if command.failure_code == "deadline_exceeded"
            else AtlasTurnExecutionRow.deadline_at > func.clock_timestamp()
        )
        with self._session_factory() as session, session.begin():
            return self._terminal_failure(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                failure_code=command.failure_code,
                detected_by=command.detected_by,
                extra_predicates=(lease_match, deadline_clause),
            )

    def finalize_expired(self, command: FinalizeExpiredExecutionV1) -> ExecutionSnapshotV1:
        expired_match = exists(
            select(1).where(
                AtlasTurnExecutionLeaseRow.execution_id == command.execution_id,
                AtlasTurnExecutionLeaseRow.lease_version == command.expected_lease_version,
                AtlasTurnExecutionLeaseRow.expires_at <= func.clock_timestamp(),
            )
        )
        with self._session_factory() as session, session.begin():
            return self._terminal_failure(
                session,
                execution_id=command.execution_id,
                expected_version=command.expected_version,
                failure_code=command.failure_code,
                detected_by=command.detected_by,
                extra_predicates=(expired_match,),
            )

    def renew_lease(self, command: RenewExecutionLeaseV1) -> ExecutionLeaseV1:
        with self._session_factory() as session, session.begin():
            lease = session.scalar(
                update(AtlasTurnExecutionLeaseRow)
                .where(
                    AtlasTurnExecutionLeaseRow.execution_id == command.execution_id,
                    AtlasTurnExecutionLeaseRow.holder_id == command.holder_id,
                    AtlasTurnExecutionLeaseRow.lease_version == command.expected_lease_version,
                    AtlasTurnExecutionLeaseRow.fencing_token == command.fencing_token,
                    AtlasTurnExecutionLeaseRow.expires_at > func.clock_timestamp(),
                    exists(
                        select(1).where(
                            AtlasTurnExecutionRow.execution_id == command.execution_id,
                            AtlasTurnExecutionRow.state.not_in(_TERMINAL),
                            AtlasTurnExecutionRow.deadline_at > func.clock_timestamp(),
                        )
                    ),
                )
                .values(
                    lease_version=AtlasTurnExecutionLeaseRow.lease_version + 1,
                    heartbeat_at=func.clock_timestamp(),
                    expires_at=func.clock_timestamp()
                    + func.make_interval(
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        select(AtlasTurnExecutionRow.ttl_seconds)
                        .where(AtlasTurnExecutionRow.execution_id == command.execution_id)
                        .scalar_subquery(),
                    ),
                )
                .returning(AtlasTurnExecutionLeaseRow)
            )
            if lease is None:
                raise TurnRuntimeLeaseConflict(
                    "lease is stale, expired, terminal, or belongs to another holder"
                )
            return _lease_model(lease)

    def fail_expired_leases(self, *, limit: int) -> list[ExecutionSnapshotV1]:
        limit = _bounded_limit(limit)
        with self._session_factory() as session:
            candidates = session.execute(
                select(
                    AtlasTurnExecutionRow.execution_id,
                    AtlasTurnExecutionRow.version,
                    AtlasTurnExecutionLeaseRow.lease_version,
                )
                .join(
                    AtlasTurnExecutionLeaseRow,
                    AtlasTurnExecutionLeaseRow.execution_id == AtlasTurnExecutionRow.execution_id,
                )
                .where(
                    AtlasTurnExecutionRow.state.not_in(_TERMINAL),
                    AtlasTurnExecutionLeaseRow.expires_at <= func.clock_timestamp(),
                )
                .order_by(AtlasTurnExecutionLeaseRow.expires_at, AtlasTurnExecutionRow.execution_id)
                .limit(limit)
            ).all()
        completed: list[ExecutionSnapshotV1] = []
        for execution_id, version, lease_version in candidates:
            try:
                completed.append(
                    self.finalize_expired(
                        FinalizeExpiredExecutionV1(
                        execution_id=execution_id,
                        expected_version=version,
                            expected_lease_version=lease_version,
                        failure_code="lease_expired",
                        detected_by="lease_sweep",
                        )
                    )
                )
            except TurnRuntimeTerminalConflict:
                # Another short transaction renewed or terminalized this exact
                # candidate after discovery. Its outcome remains authoritative.
                continue
        return completed

    def pending_release_intents(self, *, limit: int) -> list[ReleaseIntentV1]:
        limit = _bounded_limit(limit)
        with self._session_factory() as session, session.begin():
            ids = session.scalars(
                select(AtlasTurnReleaseIntentRow.release_intent_id)
                .where(
                    # A worker may die after claiming an intent. A due
                    # ``releasing`` row is therefore reclaimable by the
                    # reconciler; the owner operation remains idempotent.
                    AtlasTurnReleaseIntentRow.status.in_(("pending", "failed", "releasing")),
                    or_(
                        AtlasTurnReleaseIntentRow.next_attempt_at.is_(None),
                        AtlasTurnReleaseIntentRow.next_attempt_at <= func.clock_timestamp(),
                    ),
                )
                .order_by(
                    AtlasTurnReleaseIntentRow.created_at,
                    AtlasTurnReleaseIntentRow.release_intent_id,
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            if not ids:
                return []
            rows = session.scalars(
                update(AtlasTurnReleaseIntentRow)
                .where(AtlasTurnReleaseIntentRow.release_intent_id.in_(ids))
                .values(
                    status="releasing",
                    attempt_count=AtlasTurnReleaseIntentRow.attempt_count + 1,
                    next_attempt_at=func.clock_timestamp()
                    + func.make_interval(0, 0, 0, 0, 0, 0, 30),
                    failure_code=None,
                    updated_at=func.clock_timestamp(),
                )
                .returning(AtlasTurnReleaseIntentRow)
            ).all()
            by_id = {row.release_intent_id: row for row in rows}
            return [_release_model(by_id[intent_id]) for intent_id in ids]

    def complete_release_intent(self, command: CompleteReleaseIntentV1) -> ReleaseIntentV1:
        if command.outcome == "failed" and not command.failure_code:
            raise ValueError("failed release outcome requires failure_code")
        if command.outcome == "released" and command.failure_code is not None:
            raise ValueError("released outcome cannot include failure_code")
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                update(AtlasTurnReleaseIntentRow)
                .where(
                    AtlasTurnReleaseIntentRow.release_intent_id == command.release_intent_id,
                    AtlasTurnReleaseIntentRow.status == command.expected_status,
                )
                .values(
                    status=command.outcome,
                    failure_code=command.failure_code,
                    next_attempt_at=(
                        func.clock_timestamp() + func.make_interval(0, 0, 0, 0, 0, 0, 30)
                        if command.outcome == "failed"
                        else None
                    ),
                    updated_at=func.clock_timestamp(),
                )
                .returning(AtlasTurnReleaseIntentRow)
            )
            if row is None:
                raise TurnRuntimeCurrentnessConflict("release intent status is stale")
            return _release_model(row)

    def events_bounded(
        self,
        execution_id: str,
        *,
        limit: int,
    ) -> list[RuntimeEventV1]:
        if not execution_id or limit < 1 or limit > 201:
            raise ValueError("bounded execution event query is invalid")
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnRuntimeEventRow)
                .where(AtlasTurnRuntimeEventRow.execution_id == execution_id)
                .order_by(AtlasTurnRuntimeEventRow.sequence)
                .limit(limit)
            ).all()
            return [_event_model(row) for row in rows]

    def events(self, execution_id: str, *, after_sequence: int = 0) -> list[RuntimeEventV1]:
        if not execution_id or after_sequence < 0:
            raise ValueError("execution_id must be non-empty and after_sequence nonnegative")
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnRuntimeEventRow)
                .where(
                    AtlasTurnRuntimeEventRow.execution_id == execution_id,
                    AtlasTurnRuntimeEventRow.sequence > after_sequence,
                )
                .order_by(AtlasTurnRuntimeEventRow.sequence)
            ).all()
            return [_event_model(row) for row in rows]


TurnRuntimeOwner = PostgresTurnRuntimeOwner


__all__ = [
    "PostgresTurnRuntimeOwner",
    "TurnRuntimeOwner",
    "TurnRuntimeBudgetExceeded",
    "TurnRuntimeCurrentnessConflict",
    "TurnRuntimeError",
    "TurnRuntimeLeaseConflict",
    "TurnRuntimeReplayConflict",
    "TurnRuntimeTerminalConflict",
]
