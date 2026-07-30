from __future__ import annotations

from dataclasses import asdict, is_dataclass
import base64
import json
import re
from typing import Any, Iterable, Mapping


RUNTIME_POLICY_MAX_BYTES = 16_384
GENERAL_METADATA_MAX_BYTES = 65_536
SEARCH_PROJECTION_MAX_BYTES = 4_096
FAILURE_SUMMARY_MAX_BYTES = 1_024

MODEL_RUNTIME_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "tokenizer_profile",
        "max_tool_executions",
        "max_provider_invocations",
        "max_catalog_pages",
        "max_search_rounds",
        "max_unique_evidence",
        "max_retrieval_repairs",
        "max_schema_retries_per_turn",
        "max_selected_anchor_pages_per_round",
        "provider_invocation_timeout_seconds",
        "tool_execution_timeout_seconds",
        "turn_timeout_seconds",
        "context_window_tokens",
        "max_input_tokens_per_invocation",
        "max_output_tokens_per_invocation",
        "max_tool_result_tokens_per_execution",
        "max_total_tokens_per_conversation",
        "revision",
    }
)

EVIDENCE_LOCATOR_FIELDS = frozenset(
    {
        "selector_kind",
        "page_number",
        "ordinal",
        "table_element_id",
        "row_index",
        "column_index",
        "cell_id",
        "cell_bbox",
        "cell_text_fingerprint",
        "coordinate_system",
        "source_region_id",
        "block_id",
        "bbox",
        "start_offset",
        "end_offset",
        "extraction_version",
        "line_start", "line_end", "row_number", "paragraph_index", "paragraph_kind",
        "style_name", "table_index", "row_count", "column_count",
        "relationship_id", "part_name", "image_index", "slide_number",
        "slide_width", "slide_height", "shape_index", "shape_name",
        "left", "top", "width", "height", "sheet_index", "sheet_name",
        "cell_coordinates", "table_name", "cell_range", "anchor_row",
        "anchor_column", "alignment_anchors", "column_count", "table_width", "document_format",
        "preview_kind", "evidence_modality", "preview_region",
        "parser_id", "parser_revision", "profile_id", "profile_revision",
        "processor_id", "processor_revision", "processor_engine",
        "processor_engine_revision", "processor_source_type", "image_digest",
        "processing_native_image_artifact_ref", "visual_request_artifact_ref",
        "visual_response_artifact_ref", "visual_execution_key",
        "model_route_id", "model_route_revision", "model_name",
        "visual_schema_digest", "visual_prompt_revision",
        "raster_renderer_version", "raster_config_digest", "raster_dpi",
        "raster_width", "raster_height",
        "preview_artifact_ref", "preview_artifact_id", "preview_artifact_digest",
        "preview_page_number", "preview_image_width", "preview_image_height",
        "preview_source_kind",
        "preview_renderer_revision", "preview_render_config_revision",
        "alignment_method", "alignment_version",
    }
)


# Machine-readable inventory for every Production PostgreSQL JSONB column.
# Values are (classification, byte cap, named serializer family). Schema drift
# is rejected by the persistence-boundary test until this registry is updated.
JSONB_PAYLOAD_REGISTRY: dict[str, tuple[str, int, str]] = {
    "atlas_artifact_storage_targets.capabilities": ("storage_capabilities", RUNTIME_POLICY_MAX_BYTES, "artifact_storage_target_capabilities"),
    "atlas_artifact_write_attempts.intent_json": ("idempotency_lineage", RUNTIME_POLICY_MAX_BYTES, "artifact_write_attempt_intent"),
    "atlas_artifacts.metadata_json": ("artifact_metadata", GENERAL_METADATA_MAX_BYTES, "artifact_metadata"),
    "atlas_audit_events.metadata": ("audit_metadata", GENERAL_METADATA_MAX_BYTES, "audit_event_metadata"),
    "atlas_audit_events.message_params": ("user_message_params", GENERAL_METADATA_MAX_BYTES, "user_message_catalog"),
    "atlas_candidate_groups.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_candidate_group"),
    "atlas_documents.warning_codes": ("processing_metadata", 4096, "document_warning_codes"),
    "atlas_document_versions.payload": ("artifact_reference", GENERAL_METADATA_MAX_BYTES, "document_version"),
    "atlas_evidence.locator_payload": ("search_projection", GENERAL_METADATA_MAX_BYTES, "evidence_locator"),
    "atlas_evidence.quality_flag_refs": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "evidence_quality_refs"),
    "atlas_evidence_build_traces.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "evidence_build_trace"),
    "atlas_evidence_page_artifacts.payload": ("artifact_reference", GENERAL_METADATA_MAX_BYTES, "evidence_page_artifact"),
    "atlas_index_generations.embedding_profile": ("processing_metadata", 8192, "embedding_profile"),
    "atlas_extraction_candidates.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_extraction_candidate"),
    "atlas_kpel_normalization_handoffs.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_kpel_handoff"),
    "atlas_model_invocations.token_usage": ("routing_metadata", RUNTIME_POLICY_MAX_BYTES, "model_token_usage"),
    "atlas_model_invocations.repair_origin_error_codes": ("routing_metadata", 1024, "model_repair_error_codes"),
    "atlas_model_invocations.runtime_policy_snapshot": ("routing_policy", RUNTIME_POLICY_MAX_BYTES, "model_runtime_policy"),
    "atlas_model_routes.runtime_policy": ("routing_policy", RUNTIME_POLICY_MAX_BYTES, "model_runtime_policy"),
    "atlas_model_routing_idempotency.response_payload": ("idempotency_response", RUNTIME_POLICY_MAX_BYTES, "model_routing_replay"),
    "atlas_parser_adapter_invocations.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_parser_invocation"),
    "atlas_processing_idempotency.payload": ("idempotency_lineage", GENERAL_METADATA_MAX_BYTES, "processing_idempotency"),
    "atlas_processing_identities.processing_spec": ("processing_metadata", RUNTIME_POLICY_MAX_BYTES, "canonical_processing_spec"),
    "atlas_processing_plugin_packages.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_plugin_package"),
    "atlas_processing_plugin_versions.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_plugin_version"),
    "atlas_processing_profile_revisions.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_profile_revision"),
    "atlas_processing_profiles.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_profile"),
    "atlas_processing_request_snapshots.payload": ("routing_policy", RUNTIME_POLICY_MAX_BYTES, "processing_request_execution_snapshot"),
    "atlas_processing_routing_decisions.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_routing_decision"),
    "atlas_processing_runs.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_run"),
    "atlas_processing_runtime_profiles.payload": ("routing_policy", RUNTIME_POLICY_MAX_BYTES, "processing_runtime_profile"),
    "atlas_promotion_decisions.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_promotion_decision"),
    "atlas_source_regions.payload": ("processing_metadata", GENERAL_METADATA_MAX_BYTES, "processing_source_region"),
    "atlas_search_chunks.locator": ("search_projection", 8192, "search_chunk_locator"),
    "atlas_task_outbox.payload": ("processing_metadata", 4096, "celery_task_outbox"),
    "atlas_turn_catalog_documents.descriptor": (
        "search_projection",
        GENERAL_METADATA_MAX_BYTES,
        "turn_catalog_document_descriptor",
    ),
    "atlas_turn_grant_document_resources.descriptor": (
        "authorization_projection",
        16_384,
        "turn_grant_document_descriptor",
    ),
    "atlas_turn_governed_answer_drafts.payload": (
        "result_lineage",
        2_097_152,
        "turn_governed_answer_draft",
    ),
    "atlas_turn_citation_binding_drafts.payload": (
        "citation_projection",
        1_048_576,
        "turn_citation_binding_draft",
    ),
    "atlas_turn_audit_drafts.payload": (
        "audit_metadata",
        1_048_576,
        "turn_audit_draft",
    ),
    "atlas_turn_retrieval_evidence_packs.lineage_items": (
        "result_lineage",
        32768,
        "turn_retrieval_evidence_lineage",
    ),
    "atlas_turn_retrieval_invocations.canonical_arguments": (
        "idempotency_lineage",
        RUNTIME_POLICY_MAX_BYTES,
        "turn_retrieval_canonical_arguments",
    ),
    "atlas_turn_retrieval_results.observation": (
        "search_projection",
        262_144,
        "turn_retrieval_observation",
    ),
}


_BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_CANONICAL_CONTENT_KEYS = frozenset(
    {
        "answer",
        "answer_text",
        "artifact_base64",
        "canonical_block_text",
        "canonical_text",
        "complete_answer",
        "complete_prompt",
        "complete_tool_output",
        "content_base64",
        "input_text",
        "messages",
        "model_input",
        "model_output",
        "original_source",
        "package_base64",
        "prompt",
        "protected_payload",
        "raw_path",
        "redacted_payload",
        "segment_canonical_text",
        "source_text",
        "source_text_snapshot",
        "storage_path_ref",
        "tool_input",
        "tool_output",
        "tool_payload",
    }
)


class PersistedPayloadPolicyError(ValueError):
    """A JSONB write attempted to cross the Production payload boundary."""


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _validate_json_value(value: Any, *, family: str, path: tuple[str, ...]) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise PersistedPayloadPolicyError(
                    f"{family} contains a non-string JSON key"
                )
            key = raw_key.casefold()
            if key in _CANONICAL_CONTENT_KEYS and not _is_empty(item):
                raise PersistedPayloadPolicyError(
                    f"{family} contains prohibited canonical content at {'.'.join((*path, raw_key))}"
                )
            _validate_json_value(item, family=family, path=(*path, raw_key))
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 10_000:
            raise PersistedPayloadPolicyError(f"{family} contains an unbounded list")
        for index, item in enumerate(value):
            _validate_json_value(item, family=family, path=(*path, str(index)))
        return
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) >= 1024 and len(value) % 4 == 0 and _BASE64_LIKE.fullmatch(value):
            try:
                decoded = base64.b64decode(value, validate=True)
            except ValueError:
                decoded = b""
            if len(decoded) >= 768:
                raise PersistedPayloadPolicyError(
                    f"{family} contains a base64-like persisted value"
                )
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise PersistedPayloadPolicyError(
        f"{family} contains a non-JSON value of type {type(value).__name__}"
    )


def validate_typed_payload(
    payload: Mapping[str, Any],
    *,
    family: str,
    allowed_fields: Iterable[str],
    max_bytes: int = 65_536,
) -> dict[str, Any]:
    """Validate a closed top-level schema and a bounded, metadata-only JSON value."""

    allowed = frozenset(allowed_fields)
    actual = frozenset(payload)
    if actual != allowed:
        extra = sorted(actual - allowed)
        missing = sorted(allowed - actual)
        raise PersistedPayloadPolicyError(
            f"{family} fields do not match allowlist; extra={extra}, missing={missing}"
        )
    result = dict(payload)
    _validate_json_value(result, family=family, path=())
    try:
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersistedPayloadPolicyError(f"{family} is not JSON serializable") from exc
    if len(encoded) > max_bytes:
        raise PersistedPayloadPolicyError(
            f"{family} exceeds the {max_bytes}-byte persisted payload limit"
        )
    return result


def validate_typed_patch(
    payload: Mapping[str, Any],
    *,
    family: str,
    allowed_fields: Iterable[str],
    max_bytes: int = GENERAL_METADATA_MAX_BYTES,
) -> dict[str, Any]:
    """Validate a bounded partial update against a closed field set."""

    allowed = frozenset(allowed_fields)
    actual = frozenset(payload)
    if not actual or not actual.issubset(allowed):
        raise PersistedPayloadPolicyError(
            f"{family} patch fields do not match allowlist; extra={sorted(actual - allowed)}"
        )
    result = dict(payload)
    _validate_json_value(result, family=family, path=())
    encoded = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise PersistedPayloadPolicyError(
            f"{family} exceeds the {max_bytes}-byte persisted payload limit"
        )
    return result


def validate_typed_sequence(
    payload: Iterable[Any],
    *,
    family: str,
    max_bytes: int = GENERAL_METADATA_MAX_BYTES,
) -> list[Any]:
    result = list(payload)
    _validate_json_value(result, family=family, path=())
    encoded = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise PersistedPayloadPolicyError(
            f"{family} exceeds the {max_bytes}-byte persisted payload limit"
        )
    return result


def serialize_typed_dataclass(
    record: Any,
    *,
    family: str,
    allowed_fields: Iterable[str],
    overrides: Mapping[str, Any] | None = None,
    max_bytes: int = 65_536,
) -> dict[str, Any]:
    if not is_dataclass(record) or isinstance(record, type):
        raise TypeError("record must be a dataclass instance")
    payload = asdict(record)
    if overrides:
        unknown = frozenset(overrides) - frozenset(payload)
        if unknown:
            raise PersistedPayloadPolicyError(
                f"{family} override contains unknown fields: {sorted(unknown)}"
            )
        payload.update(overrides)
    return validate_typed_payload(
        payload,
        family=family,
        allowed_fields=allowed_fields,
        max_bytes=max_bytes,
    )
