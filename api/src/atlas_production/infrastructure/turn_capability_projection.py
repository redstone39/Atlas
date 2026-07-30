"""Carrier-local projection of model-visible legal knowledge choices."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from atlas_production.modules.retrieval.public import (
    DocumentNavigationResultV1,
    KnowledgeCatalogPageV1,
    KnowledgeExpansionResultV1,
    KnowledgeInspectionResultV1,
    KnowledgeSearchResultV1,
    KnowledgeToolObservationV1,
    RelevantDocumentDiscoveryResultV1,
    VisualInspectionResultV1,
)
from atlas_production.modules.turn_execution.public import (
    TurnModelCapabilityLimitsV1,
    TurnModelCapabilitySnapshotV1,
    TurnModelDocumentOptionV1,
    TurnModelEvidenceOptionV1,
    TurnModelNavigationOptionV1,
    TurnModelVisualOptionV1,
)
from atlas_production.modules.turn_runtime.public import ExecutionSnapshotV1


_MODALITIES = ["text", "table", "figure"]
_EXPAND_DIRECTIONS = [
    "previous_page",
    "next_page",
    "figure_context",
    "related_evidence",
]
_NAVIGATION_RELATIONS = ["previous", "next", "parent", "children", "same_page"]
def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def project_turn_model_capabilities(
    snapshot: ExecutionSnapshotV1,
    *,
    catalog_document_count: int,
    observations: Sequence[KnowledgeToolObservationV1],
    contract_repair_remaining: int,
) -> TurnModelCapabilitySnapshotV1:
    """Build one complete legal-choice projection from owner-produced observations."""

    if snapshot.catalog_ref is None:
        raise ValueError("capability projection requires an accepted catalog")
    if contract_repair_remaining not in {0, 1}:
        raise ValueError("contract repair remaining must be zero or one")

    document_order: list[str] = []
    documents: dict[str, TurnModelDocumentOptionV1] = {}
    evidence_order: list[str] = []
    evidence: dict[str, TurnModelEvidenceOptionV1] = {}
    visual_order: list[str] = []
    visuals: dict[str, TurnModelVisualOptionV1] = {}
    navigation_order: list[str] = []
    navigation: dict[str, TurnModelNavigationOptionV1] = {}

    def remember_visual(option: TurnModelVisualOptionV1) -> None:
        if option.handle not in visuals:
            visual_order.append(option.handle)
        visuals[option.handle] = option

    def remember_document(option: TurnModelDocumentOptionV1) -> None:
        if option.document_handle not in documents:
            document_order.append(option.document_handle)
        current = documents.get(option.document_handle)
        if current is None or current.media_type is None:
            documents[option.document_handle] = option

    def remember_navigation(option: TurnModelNavigationOptionV1) -> None:
        if option.navigation_handle not in navigation:
            navigation_order.append(option.navigation_handle)
        navigation[option.navigation_handle] = option

    def remember_evidence(option: TurnModelEvidenceOptionV1) -> None:
        if option.evidence_handle not in evidence:
            evidence_order.append(option.evidence_handle)
        evidence[option.evidence_handle] = option
        if option.page_handle is not None:
            if option.page_number is None:
                raise ValueError("page handle requires page number")
            remember_visual(
                TurnModelVisualOptionV1(
                    handle=option.page_handle,
                    handle_kind="page",
                    document_handle=option.document_handle,
                    page_number=option.page_number,
                )
            )
        remember_document(
            TurnModelDocumentOptionV1(
                document_handle=option.document_handle,
                display_name=option.document_display_name,
                media_type=None,
                modalities=option.modalities,
                tags=[],
                version_label=None,
            )
        )

    for observation in observations:
        if isinstance(observation, KnowledgeCatalogPageV1):
            for item in observation.documents:
                remember_document(
                    TurnModelDocumentOptionV1(
                        document_handle=item.document_handle,
                        display_name=item.display_name,
                        media_type=item.media_type,
                        modalities=item.modalities,
                        tags=item.tags,
                        version_label=item.version_label,
                    )
                )
        elif isinstance(observation, RelevantDocumentDiscoveryResultV1):
            for item in observation.candidates:
                remember_document(
                    TurnModelDocumentOptionV1(
                        document_handle=item.document_handle,
                        display_name=item.document_display_name,
                        media_type=item.media_type,
                        modalities=item.modalities,
                        tags=[],
                        version_label=None,
                    )
                )
        elif isinstance(observation, (KnowledgeSearchResultV1, KnowledgeExpansionResultV1)):
            for item in observation.evidence:
                remember_evidence(
                    TurnModelEvidenceOptionV1(
                        evidence_handle=item.evidence_handle,
                        document_handle=item.document_handle,
                        document_display_name=item.document_display_name,
                        locator_label=item.locator_label,
                        snippet=item.snippet,
                        modalities=item.modalities,
                        page_handle=item.page_handle,
                        page_number=item.page_number,
                    )
                )
        elif isinstance(observation, VisualInspectionResultV1):
            remember_visual(
                TurnModelVisualOptionV1(
                    handle=observation.page_handle,
                    handle_kind="page",
                    document_handle=observation.document_handle,
                    page_number=observation.page_number,
                )
            )
            remember_visual(
                TurnModelVisualOptionV1(
                    handle=observation.visual_handle,
                    handle_kind="visual",
                    document_handle=observation.document_handle,
                    page_number=observation.page_number,
                )
            )
        elif isinstance(observation, KnowledgeInspectionResultV1):
            for item in observation.items:
                remember_evidence(
                    TurnModelEvidenceOptionV1(
                        evidence_handle=item.evidence_handle,
                        document_handle=item.document_handle,
                        document_display_name=item.document_display_name,
                        locator_label=item.locator_label,
                        snippet=item.content[:4096],
                        modalities=item.modalities,
                    )
                )
        elif isinstance(observation, DocumentNavigationResultV1):
            for item in observation.targets:
                remember_navigation(
                    TurnModelNavigationOptionV1(
                        navigation_handle=item.navigation_handle,
                        document_handle=item.document_handle,
                        kind=item.kind,
                        label=item.label,
                        page_number=item.page_number,
                    )
                )
                if item.page_handle is not None:
                    remember_visual(
                        TurnModelVisualOptionV1(
                            handle=item.page_handle,
                            handle_kind="page",
                            document_handle=item.document_handle,
                            page_number=item.page_number,
                        )
                    )

    budget = snapshot.budget
    policy = snapshot.policy
    max_output_tokens = policy.tool_token_budget
    tool_allowed = (
        budget.tool_invocations < policy.max_tool_invocations
        and budget.tool_tokens < policy.tool_token_budget
        and max_output_tokens >= 256
    )
    remaining_evidence = max(0, policy.max_unique_evidence - budget.unique_evidence)
    catalog_allowed = (
        tool_allowed
        and budget.catalog_pages < policy.max_catalog_pages
    )
    search_allowed = (
        tool_allowed
        and bool(documents)
        and catalog_document_count > 0
        and budget.search_rounds < policy.max_search_rounds
        and remaining_evidence > 0
    )
    inspect_allowed = tool_allowed and bool(evidence)
    inspect_visual_allowed = (
        tool_allowed
        and remaining_evidence > 0
        and bool(visuals)
    )
    expand_allowed = search_allowed and bool(evidence)
    navigation_allowed = tool_allowed and bool(documents)

    allowed_actions = []
    if catalog_allowed:
        allowed_actions.extend(
            [
                "list_knowledge_documents",
                "find_knowledge_documents",
                "discover_relevant_documents",
            ]
        )
    if search_allowed:
        allowed_actions.append("search_knowledge")
    if inspect_allowed:
        allowed_actions.append("inspect_knowledge")
    if inspect_visual_allowed:
        allowed_actions.append("inspect_visual")
    if expand_allowed:
        allowed_actions.append("expand_knowledge")
    if navigation_allowed:
        allowed_actions.append("navigate_document")
    allowed_actions.append("finalize_answer")

    limits = TurnModelCapabilityLimitsV1(
        max_page_size=10 if catalog_allowed else 0,
        max_discovery_limit=20 if catalog_allowed else 0,
        max_search_limit=(
            min(20, remaining_evidence) if search_allowed else 0
        ),
        max_expand_limit=(
            min(20, remaining_evidence) if expand_allowed else 0
        ),
        max_navigation_limit=20 if navigation_allowed else 0,
        max_output_tokens=max_output_tokens if tool_allowed else 0,
    )
    payload = {
        "schema_version": "turn-model-capabilities-v1",
        "execution_id": snapshot.execution_id,
        "catalog_ref": snapshot.catalog_ref,
        "allowed_actions": allowed_actions,
        "documents": [documents[handle].model_dump(mode="json") for handle in document_order],
        "evidence": [evidence[handle].model_dump(mode="json") for handle in evidence_order],
        "visuals": [visuals[handle].model_dump(mode="json") for handle in visual_order],
        "navigation": [
            navigation[handle].model_dump(mode="json") for handle in navigation_order
        ],
        "allowed_modalities": _MODALITIES,
        "allowed_expand_directions": _EXPAND_DIRECTIONS,
        "allowed_navigation_relations": _NAVIGATION_RELATIONS,
        "catalog_wide_search_allowed": False,
        "limits": limits.model_dump(mode="json"),
        "contract_repair_remaining": contract_repair_remaining,
    }
    return TurnModelCapabilitySnapshotV1(**payload, digest=_digest(payload))


__all__ = ["project_turn_model_capabilities"]
