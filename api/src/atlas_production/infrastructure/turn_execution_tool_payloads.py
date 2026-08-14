from __future__ import annotations

from dataclasses import dataclass

from atlas_production.infrastructure.turn_execution_foundation import (
    _action_reservation,
    _digest,
    _model_visible_item_identities,
    _ref,
)
from atlas_production.modules.audit.public import TurnAuditStepV1
from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    FindKnowledgeDocumentsV1,
    KnowledgeToolActionV1,
    RetrievalInvocationEnvelopeV1,
)
from atlas_production.modules.result_governance.public import RetrievalStatusV1
from atlas_production.modules.turn_runtime.public import (
    BeginToolInvocationV1,
    CompleteToolInvocationV1,
    ExecutionSnapshotV1,
)


@dataclass(frozen=True)
class _ToolReservationProjection:
    arguments: dict[str, object]
    action_digest: str
    invocation_ordinal: int
    invocation_id: str
    max_output_tokens: int
    command: BeginToolInvocationV1


def _tool_reservation_projection(
    *,
    execution_id: str,
    snapshot: ExecutionSnapshotV1,
    action: KnowledgeToolActionV1,
    completed_action_digests: set[str],
) -> _ToolReservationProjection:
    arguments = action.model_dump(mode="json")
    if isinstance(action, (FindKnowledgeDocumentsV1, DiscoverRelevantDocumentsV1)):
        arguments["runtime_max_output_tokens"] = snapshot.policy.tool_token_budget
        arguments["tokenizer_profile"] = snapshot.route.tokenizer_profile
    action_digest = _digest(arguments)
    pages, candidates, searches, model_visible_items, tokens = _action_reservation(
        action, snapshot
    )
    if action_digest in completed_action_digests:
        candidates = 0
        model_visible_items = 0
    invocation_ordinal = snapshot.budget.tool_invocations + 1
    invocation_id = _ref(
        "tool-invocation",
        f"{execution_id}:{invocation_ordinal}:{action_digest}",
    )
    return _ToolReservationProjection(
        arguments=arguments,
        action_digest=action_digest,
        invocation_ordinal=invocation_ordinal,
        invocation_id=invocation_id,
        max_output_tokens=tokens,
        command=BeginToolInvocationV1(
            execution_id=execution_id,
            expected_version=snapshot.version,
            fencing_token=snapshot.lease.fencing_token,
            tool_invocation_id=invocation_id,
            invocation_ordinal=invocation_ordinal,
            tool_name=action.action,
            schema_version=f"{action.action.replace('_', '-')}-v1",
            arguments_digest=action_digest,
            reserve_catalog_pages=pages,
            reserve_document_candidates=candidates,
            reserve_search_rounds=searches,
            reserve_model_visible_items=model_visible_items,
            reserve_tool_tokens=tokens,
        ),
    )


def _complete_tool_command(
    *,
    execution_id: str,
    snapshot: ExecutionSnapshotV1,
    action: KnowledgeToolActionV1,
    invocation_id: str,
    invocation_ordinal: int,
    envelope: RetrievalInvocationEnvelopeV1,
) -> CompleteToolInvocationV1:
    return CompleteToolInvocationV1(
        execution_id=execution_id,
        expected_version=snapshot.version,
        fencing_token=snapshot.lease.fencing_token,
        tool_invocation_id=invocation_id,
        invocation_ordinal=invocation_ordinal,
        result_ref=envelope.result_ref,
        result_digest=envelope.result_digest,
        document_candidate_handles=envelope.document_candidate_handles,
        model_visible_item_identities=list(
            _model_visible_item_identities(envelope.observation)
        ),
        catalog_pages=(
            1
            if isinstance(action, DiscoverRelevantDocumentsV1)
            else envelope.catalog_pages
        ),
        search_rounds=envelope.search_rounds,
        tool_tokens=envelope.tool_tokens,
    )


def _retrieval_error_status(
    envelope: RetrievalInvocationEnvelopeV1,
) -> RetrievalStatusV1 | None:
    if envelope.observation.result_type != "knowledge_tool_error":
        return None
    return {
        "access_denied": "access_denied",
        "budget_exhausted": "budget_exhausted",
        "tool_failed": "tool_failed",
        "invalid_handle": "tool_failed",
        "catalog_stale": "tool_failed",
        "navigation_unavailable": None,
    }[envelope.observation.error_code]


def _tool_audit_step(
    *,
    ordinal: int,
    action: KnowledgeToolActionV1,
    arguments: dict[str, object],
    envelope: RetrievalInvocationEnvelopeV1,
) -> TurnAuditStepV1:
    return TurnAuditStepV1(
        ordinal=ordinal,
        step_kind="tool",
        operation=action.action,
        status="replayed" if envelope.replayed else "completed",
        safe_input_digest=_digest(arguments),
        result_ref=envelope.result_ref,
        result_digest=envelope.result_digest,
        output_tokens=envelope.tool_tokens,
        evidence_count=len(envelope.evidence_lineage),
    )
