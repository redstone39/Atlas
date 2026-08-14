from __future__ import annotations

import hashlib
import json
from typing import cast

from atlas_production.modules.retrieval.public import (
    DocumentNavigationResultV1,
    DiscoverRelevantDocumentsV1,
    ExpandKnowledgeV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    KnowledgeExpansionResultV1,
    KnowledgeInspectionResultV1,
    KnowledgeSearchResultV1,
    KnowledgeToolActionV1,
    KnowledgeToolObservationV1,
    ListKnowledgeDocumentsV1,
    NavigateDocumentV1,
    SearchKnowledgeV1,
    VisualInspectionResultV1,
)
from atlas_production.modules.turn_execution.public import TurnModelInputV3
from atlas_production.modules.turn_runtime.public import (
    ExecutionSnapshotV1,
    SchemaRetryOriginCode,
)

_DISCOVERY_PAGE_SIZE = 10
_SCHEMA_RETRY_ORIGIN_CODES = frozenset(
    {
        "provider_output_decode_error",
        "provider_output_schema_error",
        "deep_reasoning_plan_invalid",
        "deep_reasoning_replan_invalid",
        "deep_reasoning_evaluation_semantic_shape_invalid",
    }
)


def _contract_repair_remaining(snapshot: ExecutionSnapshotV1) -> int:
    return snapshot.policy.max_retrieval_repairs - snapshot.budget.retrieval_repairs


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _ref(kind: str, execution_id: str) -> str:
    return f"{kind}:{hashlib.sha256(f'{kind}:{execution_id}'.encode()).hexdigest()}"


def _schema_retry_origin(error: Exception) -> SchemaRetryOriginCode | None:
    safe_code = getattr(error, "safe_code", "")
    if safe_code not in _SCHEMA_RETRY_ORIGIN_CODES:
        return None
    return cast(SchemaRetryOriginCode, safe_code)


def _action_reservation(
    action: KnowledgeToolActionV1,
    snapshot: ExecutionSnapshotV1,
) -> tuple[int, int, int, int, int]:
    admitted_tool_max = snapshot.policy.tool_token_budget
    if isinstance(action, ListKnowledgeDocumentsV1):
        return (1, action.page_size, 0, 0, action.max_output_tokens)
    if isinstance(action, FindKnowledgeDocumentsV1):
        return (
            1,
            _DISCOVERY_PAGE_SIZE,
            0,
            0,
            admitted_tool_max,
        )
    if isinstance(action, DiscoverRelevantDocumentsV1):
        return (
            1,
            action.limit,
            0,
            0,
            admitted_tool_max,
        )
    if isinstance(action, SearchKnowledgeV1):
        # Search is restricted to already disclosed documents, so it cannot
        # add a new document-candidate identity. Each result can add one
        # evidence handle and one page handle to the model-visible total.
        return (0, 0, 1, action.limit * 2, action.max_output_tokens)
    if isinstance(action, InspectKnowledgeV1):
        # Inspected evidence and its documents were already obtained and counted.
        return (0, 0, 0, 0, action.max_output_tokens)
    if isinstance(action, InspectVisualV1):
        return (0, 0, 0, 1, admitted_tool_max)
    if isinstance(action, NavigateDocumentV1):
        return (
            0,
            0,
            1 if action.mode == "search" else 0,
            action.limit * 2,
            action.max_output_tokens,
        )
    assert isinstance(action, ExpandKnowledgeV1)
    # Expansion may surface another authorized binding for related evidence,
    # so preserve the existing candidate reservation.
    return (0, action.limit, 0, action.limit * 2, action.max_output_tokens)


def _model_visible_item_identities(
    observation: KnowledgeToolObservationV1,
) -> tuple[str, ...]:
    """Return model-selectable handles in deterministic result order."""

    identities: list[str] = []
    if isinstance(observation, (KnowledgeSearchResultV1, KnowledgeExpansionResultV1)):
        for item in observation.evidence:
            identities.append(item.evidence_handle)
            if item.page_handle is not None:
                identities.append(item.page_handle)
    elif isinstance(observation, KnowledgeInspectionResultV1):
        identities.extend(item.evidence_handle for item in observation.items)
    elif isinstance(observation, VisualInspectionResultV1):
        # inspect_visual can only target an existing page/visual capability, so
        # the returned page handle is already admitted. A new crop/full render
        # can add at most the result visual handle; exact replay adds none.
        identities.extend((observation.page_handle, observation.visual_handle))
    elif isinstance(observation, DocumentNavigationResultV1):
        for item in observation.targets:
            identities.append(item.navigation_handle)
            if item.page_handle is not None:
                identities.append(item.page_handle)
    return tuple(dict.fromkeys(identities))


def _has_legal_tool(
    snapshot: ExecutionSnapshotV1, *, has_documents: bool, has_evidence: bool
) -> bool:
    budget = snapshot.budget
    policy = snapshot.policy
    if (
        budget.tool_invocations >= policy.max_tool_invocations
        or budget.tool_tokens >= policy.tool_token_budget
        or policy.tool_token_budget < 256
    ):
        return False
    can_catalog = budget.catalog_pages < policy.max_catalog_pages
    can_search_or_expand = (
        budget.search_rounds < policy.max_search_rounds
        and budget.model_visible_items < policy.max_model_visible_items_per_turn
    )
    return (
        can_catalog
        or (can_search_or_expand and (has_documents or has_evidence))
        or has_documents
        or has_evidence
    )


def _validate_model_input(
    snapshot: ExecutionSnapshotV1, model_input: TurnModelInputV3
) -> None:
    if (
        model_input.execution_id != snapshot.execution_id
        or model_input.context_pack_ref != snapshot.context_pack_ref
        or model_input.knowledge_catalog_ref != snapshot.catalog_ref
        or model_input.budget != snapshot.budget
        or model_input.policy != snapshot.policy
        or model_input.capabilities.execution_id != snapshot.execution_id
        or model_input.capabilities.catalog_ref != snapshot.catalog_ref
    ):
        raise ValueError("model input does not match the authoritative runtime snapshot")
