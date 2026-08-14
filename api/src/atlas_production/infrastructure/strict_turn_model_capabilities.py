from __future__ import annotations

from copy import deepcopy
from typing import Any

from atlas_production.modules.model_routing.public import ProviderFunctionTool
from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    ExpandKnowledgeV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    ListKnowledgeDocumentsV1,
    NavigateDocumentV1,
    SearchKnowledgeV1,
)
from atlas_production.modules.turn_execution.public import (
    FinalizeAnswerV1,
    TurnModelCapabilitySnapshotV1,
    finalize_answer_schema,
)
from atlas_production.providers import build_native_json_schema


_ACTION_MODELS = {
    "list_knowledge_documents": ListKnowledgeDocumentsV1,
    "find_knowledge_documents": FindKnowledgeDocumentsV1,
    "discover_relevant_documents": DiscoverRelevantDocumentsV1,
    "search_knowledge": SearchKnowledgeV1,
    "inspect_knowledge": InspectKnowledgeV1,
    "inspect_visual": InspectVisualV1,
    "expand_knowledge": ExpandKnowledgeV1,
    "navigate_document": NavigateDocumentV1,
}

_TOOL_DESCRIPTIONS = {
    "list_knowledge_documents": (
        "List authorized document candidates and their names, versions, tags, "
        "modalities, and current-execution handles, at most 10 per call. Continue "
        "with next_cursor when more candidates are needed before selecting documents."
    ),
    "find_knowledge_documents": (
        "Find authorized document candidates by one concise identity keyword for a "
        "document name, model, version, or tag, at most 10 per call. This searches "
        "document identity only, not document content. Review the returned names, "
        "continue with next_cursor or another identity keyword when useful, then "
        "use search_knowledge with selected document handles for content questions."
    ),
    "discover_relevant_documents": (
        "Discover up to 20 authorized document candidates by a natural-language "
        "content query of 1 to 4000 characters. Candidate previews guide document "
        "selection only and are not evidence. Select disclosed document handles "
        "and use search_knowledge to obtain exact evidence."
    ),
    "search_knowledge": (
        "Search evidence only inside the non-empty subset of disclosed document "
        "handles selected for this call. You may change the query or selected "
        "handles and search again within the current budget."
    ),
    "inspect_knowledge": "Inspect already obtained evidence handles.",
    "inspect_visual": (
        "This is the visual inspection tool. Use it to directly view an authorized "
        "document page or visual region. Call it proactively whenever visual "
        "inspection would help you understand, verify, compare, or resolve ambiguity "
        "in the requested task; the user does not need to ask explicitly. It is "
        "especially useful for figures, diagrams, images, shapes, visual labels, "
        "relative positions, page layouts, waveforms, schematics, and visually "
        "encoded tables. Any page_handle in current capabilities, whether returned "
        "by search_knowledge or navigate_document, is a legal target. Text "
        "extraction, snippets, captions, and navigation metadata may help locate the "
        "page but do not replace visual inspection when the conclusion depends on "
        "visual content. For comparisons, inspect every material visual target."
    ),
    "expand_knowledge": "Expand from already obtained evidence handles.",
    "navigate_document": (
        "Explore one selected document's fixed structure with overview, search, "
        "or around. Each returned page_handle is a legal target for "
        "inspect_visual when it remains in current capabilities. Returned "
        "locations and page handles are navigation choices, not evidence; "
        "inspect text or visuals before relying on content."
    ),
}


def _array_enum(schema: dict[str, Any], path: tuple[str, ...], values: list[str]) -> None:
    current = schema
    for key in path:
        current = current[key]
    if values:
        current["items"]["enum"] = values
    else:
        current.clear()
        current["type"] = "null"


def _integer_enum(
    schema: dict[str, Any], path: tuple[str, ...], values: list[int]
) -> None:
    current = schema
    for key in path:
        current = current[key]
    current["enum"] = values


def _nullable_string_enum(schema: dict[str, Any], name: str, values: list[str]) -> None:
    field = schema["properties"][name]
    if not values:
        field.clear()
        field["type"] = "null"
        return
    target = next(
        (item for item in field.get("anyOf", []) if item.get("type") == "string"),
        field,
    )
    target["enum"] = values


def _tool(
    model: type, capabilities: TurnModelCapabilitySnapshotV1
) -> ProviderFunctionTool:
    action = next(iter(model.model_fields["action"].annotation.__args__))
    application_schema = deepcopy(model.model_json_schema())
    limits = capabilities.limits
    if "max_output_tokens" in application_schema["properties"]:
        _integer_enum(
            application_schema,
            ("properties", "max_output_tokens"),
            [limits.max_output_tokens],
        )
    if action == "list_knowledge_documents":
        _integer_enum(
            application_schema,
            ("properties", "page_size"),
            list(range(1, limits.max_page_size + 1)),
        )
    if action == "discover_relevant_documents":
        _integer_enum(
            application_schema,
            ("properties", "limit"),
            list(range(1, limits.max_discovery_limit + 1)),
        )
    if action == "search_knowledge":
        _integer_enum(
            application_schema,
            ("properties", "limit"),
            list(range(1, limits.max_search_limit + 1)),
        )
        _array_enum(
            application_schema,
            ("properties", "document_handles"),
            [item.document_handle for item in capabilities.documents],
        )
        _array_enum(
            application_schema,
            ("properties", "required_modalities"),
            list(capabilities.allowed_modalities),
        )
    elif action == "inspect_knowledge":
        _array_enum(
            application_schema,
            ("properties", "handles"),
            [item.evidence_handle for item in capabilities.evidence],
        )
    elif action == "inspect_visual":
        application_schema["properties"]["handle"]["enum"] = [
            item.handle for item in capabilities.visuals
        ]
    elif action == "expand_knowledge":
        _integer_enum(
            application_schema,
            ("properties", "limit"),
            list(range(1, limits.max_expand_limit + 1)),
        )
        _array_enum(
            application_schema,
            ("properties", "anchor_handles"),
            [item.evidence_handle for item in capabilities.evidence],
        )
        application_schema["properties"]["direction"]["enum"] = list(
            capabilities.allowed_expand_directions
        )
    elif action == "navigate_document":
        _integer_enum(
            application_schema,
            ("properties", "limit"),
            list(range(1, limits.max_navigation_limit + 1)),
        )
        _nullable_string_enum(
            application_schema,
            "document_handle",
            [item.document_handle for item in capabilities.documents],
        )
        _nullable_string_enum(
            application_schema,
            "navigation_handle",
            [item.navigation_handle for item in capabilities.navigation],
        )
        _nullable_string_enum(
            application_schema,
            "relation",
            list(capabilities.allowed_navigation_relations),
        )
    schema = build_native_json_schema(
        f"{action}_v1_{capabilities.digest[:12]}", application_schema
    )
    provider_schema = schema.schema
    if action == "expand_knowledge":
        provider_schema["properties"]["anchor_handles"]["maxItems"] = (
            limits.max_expand_anchor_handles
        )
    return ProviderFunctionTool(
        name=action,
        description=_TOOL_DESCRIPTIONS[action],
        parameters=provider_schema,
        strict=True,
    )


def _final_schema(capabilities: TurnModelCapabilitySnapshotV1):
    application_schema = deepcopy(finalize_answer_schema())
    return build_native_json_schema(
        f"finalize_answer_v1_{capabilities.digest[:12]}", application_schema
    )


def _duplicates(values: list[str]) -> bool:
    return len(values) != len(set(values))


def _within_capabilities(
    action, capabilities: TurnModelCapabilitySnapshotV1
) -> bool:
    if action.action not in capabilities.allowed_actions:
        return False
    limits = capabilities.limits
    if not isinstance(
        action,
        (
            FinalizeAnswerV1,
            FindKnowledgeDocumentsV1,
            DiscoverRelevantDocumentsV1,
            InspectVisualV1,
        ),
    ):
        if (
            limits.max_output_tokens < 256
            or action.max_output_tokens > limits.max_output_tokens
        ):
            return False
    if isinstance(action, ListKnowledgeDocumentsV1):
        return action.page_size <= limits.max_page_size
    if isinstance(action, FindKnowledgeDocumentsV1):
        return limits.max_page_size >= 10
    if isinstance(action, DiscoverRelevantDocumentsV1):
        return action.limit <= limits.max_discovery_limit
    if isinstance(action, SearchKnowledgeV1):
        documents = {item.document_handle for item in capabilities.documents}
        return (
            action.limit <= limits.max_search_limit
            and bool(action.document_handles)
            and not _duplicates(action.document_handles)
            and set(action.document_handles).issubset(documents)
            and set(action.required_modalities).issubset(capabilities.allowed_modalities)
        )
    if isinstance(action, InspectKnowledgeV1):
        evidence = {item.evidence_handle for item in capabilities.evidence}
        return not _duplicates(action.handles) and set(action.handles).issubset(evidence)
    if isinstance(action, InspectVisualV1):
        return action.handle in {item.handle for item in capabilities.visuals}
    if isinstance(action, ExpandKnowledgeV1):
        evidence = {item.evidence_handle for item in capabilities.evidence}
        return (
            action.limit <= limits.max_expand_limit
            and len(action.anchor_handles) <= limits.max_expand_anchor_handles
            and not _duplicates(action.anchor_handles)
            and set(action.anchor_handles).issubset(evidence)
            and action.direction in capabilities.allowed_expand_directions
        )
    if isinstance(action, NavigateDocumentV1):
        documents = {item.document_handle for item in capabilities.documents}
        navigation = {item.navigation_handle for item in capabilities.navigation}
        return (
            action.limit <= limits.max_navigation_limit
            and (
                (
                    action.mode in {"overview", "search"}
                    and action.document_handle in documents
                )
                or (
                    action.mode == "around"
                    and action.navigation_handle in navigation
                    and action.relation in capabilities.allowed_navigation_relations
                )
            )
        )
    assert isinstance(action, FinalizeAnswerV1)
    return True
