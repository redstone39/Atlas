"""Typed, transport-neutral contracts exposed to processing plugins.

These are deliberately pre-KPEL drafts. They contain no authorization decision,
canonical status, citation/audit identifier, storage path, or credential.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any, Literal, Protocol, runtime_checkable


JsonObject = Mapping[str, Any]
RegionKind = Literal["page", "slide", "paragraph", "table", "figure", "image_region"]
ContentKindHint = Literal["text", "table", "figure", "formula", "image", "unknown"]
REGION_KINDS = frozenset({"page", "slide", "paragraph", "table", "figure", "image_region"})
CONTENT_KIND_HINTS = frozenset({"text", "table", "figure", "formula", "image", "unknown"})
PREVIEW_REGION_KINDS = frozenset({"paragraph", "table", "figure", "image"})
PREVIEW_REGION_COORDINATE_SYSTEM = "pdf_crop_box_relative_bottom_left"
PREVIEW_REGION_GEOMETRY_VERSION = "docling-page-region-v1"
_OPAQUE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^/\\\s]+$")
_ATLAS_OWNED_KEYS = frozenset({
    "acl_decision", "acl_decision_id", "authorization_decision",
    "canonical_status", "canonical_element_kind", "content_element",
    "evidence_id", "citation_id", "audit_event", "audit_event_id",
    "index_operation", "database_write", "db_mutation", "storage_path",
    "raw_path", "raw_filename", "source_filename", "credential",
    "credentials", "password", "secret", "session_token", "access_token",
})


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must use UTC")


def _opaque(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _required(value, field_name)
    if not _OPAQUE_REF.fullmatch(value) or value.lower().startswith(("file:", "http:", "https:")):
        raise ValueError(f"{field_name} must be an opaque non-path reference")


def _validate_common_input(value: Any) -> None:
    for name in (
        "run_id", "invocation_id", "document_id", "document_version_id",
        "artifact_ref", "media_type", "profile_id", "policy_snapshot_ref",
    ):
        _required(getattr(value, name), name)
    if value.profile_revision <= 0:
        raise ValueError("profile_revision must be positive")
    _opaque(value.artifact_ref, "artifact_ref")
    _utc(value.deadline_at, "deadline_at")


def validate_plugin_output_payload(value: Any, *, location: str = "output") -> None:
    """Reject Atlas-owned semantics even when nested in plugin-defined JSON."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _ATLAS_OWNED_KEYS:
                raise ValueError(f"{location} contains Atlas-owned field {key!r}")
            validate_plugin_output_payload(item, location=f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_plugin_output_payload(item, location=f"{location}[{index}]")


def validate_preview_region(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ValueError("preview_region must be an object")
    required = {
        "page_number", "region_kind", "source_element_id", "coordinate_system",
        "rectangles", "page_width", "page_height", "geometry_version",
    }
    if set(value) != required:
        raise ValueError("preview_region fields do not match the contract")
    page_number = value["page_number"]
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number <= 0:
        raise ValueError("preview_region page_number must be positive")
    if value["region_kind"] not in PREVIEW_REGION_KINDS:
        raise ValueError("preview_region region_kind is not registered")
    _required(value["source_element_id"], "preview_region source_element_id")
    if value["coordinate_system"] != PREVIEW_REGION_COORDINATE_SYSTEM:
        raise ValueError("preview_region coordinate_system is unsupported")
    if value["geometry_version"] != PREVIEW_REGION_GEOMETRY_VERSION:
        raise ValueError("preview_region geometry_version is unsupported")
    for name in ("page_width", "page_height"):
        number = value[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"preview_region {name} must be a positive finite number")
    rectangles = value["rectangles"]
    if not isinstance(rectangles, Sequence) or isinstance(rectangles, (str, bytes)) or not rectangles:
        raise ValueError("preview_region rectangles must be non-empty")
    for rectangle in rectangles:
        if not isinstance(rectangle, Sequence) or isinstance(rectangle, (str, bytes)) or len(rectangle) != 4:
            raise ValueError("preview_region rectangle must contain four numbers")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in rectangle):
            raise ValueError("preview_region rectangle must contain finite numbers")
        x0, y0, x1, y1 = rectangle
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or x1 > value["page_width"] or y1 > value["page_height"]:
            raise ValueError("preview_region rectangle is outside the declared page")
    validate_plugin_output_payload(value, location="preview_region")


@dataclass(frozen=True, slots=True)
class ParserInput:
    run_id: str
    invocation_id: str
    document_id: str
    document_version_id: str
    artifact_ref: str
    media_type: str
    profile_id: str
    profile_revision: int
    policy_snapshot_ref: str
    deadline_at: datetime
    batch_id: str
    unit_start: int
    unit_end: int
    resume_cursor: str | None
    plugin_config: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common_input(self)
        _required(self.batch_id, "batch_id")
        for name in ("unit_start", "unit_end"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive one-based integer")
        if self.unit_end < self.unit_start:
            raise ValueError("unit_end must be greater than or equal to unit_start")
        _opaque(self.resume_cursor, "resume_cursor")


@dataclass(frozen=True, slots=True)
class RegionInput:
    run_id: str
    invocation_id: str
    document_id: str
    document_version_id: str
    artifact_ref: str
    media_type: str
    profile_id: str
    profile_revision: int
    policy_snapshot_ref: str
    deadline_at: datetime
    region_id: str
    region_kind: RegionKind
    content_kind_hint: ContentKindHint
    locator_draft: JsonObject
    element_kind_hint: str | None = None
    normalized_text_ref: str | None = None
    structured_content_ref: str | None = None
    native_artifact_ref: str | None = None
    active_trait_hints: Sequence[str] = field(default_factory=tuple)
    plugin_config: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common_input(self)
        for name in ("region_id", "region_kind", "content_kind_hint"):
            _required(getattr(self, name), name)
        if self.region_kind not in REGION_KINDS:
            raise ValueError("region_kind is not registered")
        if self.content_kind_hint not in CONTENT_KIND_HINTS:
            raise ValueError("content_kind_hint is not registered")
        if not isinstance(self.locator_draft, Mapping) or not self.locator_draft:
            raise ValueError("locator_draft must be a non-empty mapping")
        _opaque(self.normalized_text_ref, "normalized_text_ref")
        _opaque(self.structured_content_ref, "structured_content_ref")
        _opaque(self.native_artifact_ref, "native_artifact_ref")
        validate_plugin_output_payload(self.locator_draft, location="locator_draft")


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Invocation-scoped capabilities supplied by the isolated Atlas runner."""

    artifact_broker: Any
    logger: Any
    deadline_at: datetime
    invocation_metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _utc(self.deadline_at, "deadline_at")


@dataclass(frozen=True, slots=True)
class SourceRegionDraft:
    source_region_identity: str
    region_kind: RegionKind
    content_kind_hint: ContentKindHint
    locator_draft: JsonObject
    element_kind_hint: str | None = None
    parent_region_identity: str | None = None
    normalized_text_ref: str | None = None
    structured_content_ref: str | None = None
    native_artifact_ref: str | None = None
    quality_flag_refs: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("source_region_identity", "region_kind", "content_kind_hint"):
            _required(getattr(self, name), name)
        if self.region_kind not in REGION_KINDS:
            raise ValueError("region_kind is not registered")
        if self.content_kind_hint not in CONTENT_KIND_HINTS:
            raise ValueError("content_kind_hint is not registered")
        if not isinstance(self.locator_draft, Mapping) or not self.locator_draft:
            raise ValueError("locator_draft must be a non-empty mapping")
        for name in ("normalized_text_ref", "structured_content_ref", "native_artifact_ref"):
            _opaque(getattr(self, name), name)
        validate_plugin_output_payload(self.locator_draft, location="locator_draft")


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    source_region_ids: Sequence[str]
    channel_id: str
    output_contract_version: str
    candidate_payload_ref: str
    content_kind_hint: ContentKindHint = "unknown"
    element_kind_hint: str | None = None
    structured_content_ref: str | None = None
    native_artifact_ref: str | None = None
    table_grid: JsonObject | None = None
    cell_bboxes: Mapping[str, Sequence[float]] | None = None
    table_asset_refs: Sequence[str] = field(default_factory=tuple)
    figure_asset_refs: Sequence[str] = field(default_factory=tuple)
    content_rendition_ref: str | None = None
    quality_flag_refs: Sequence[str] = field(default_factory=tuple)
    preview_region: JsonObject | None = None

    def __post_init__(self) -> None:
        if not self.source_region_ids or any(not item for item in self.source_region_ids):
            raise ValueError("source_region_ids must contain non-empty ids")
        for name in ("channel_id", "output_contract_version", "candidate_payload_ref", "content_kind_hint"):
            _required(getattr(self, name), name)
        if self.content_kind_hint not in CONTENT_KIND_HINTS:
            raise ValueError("content_kind_hint is not registered")
        for name in (
            "candidate_payload_ref", "structured_content_ref", "native_artifact_ref",
            "content_rendition_ref",
        ):
            _opaque(getattr(self, name), name)
        for collection in (self.table_asset_refs, self.figure_asset_refs):
            for ref in collection:
                _opaque(ref, "asset_ref")
        validate_plugin_output_payload(self.table_grid, location="table_grid")
        validate_plugin_output_payload(self.cell_bboxes, location="cell_bboxes")
        validate_preview_region(self.preview_region)


@runtime_checkable
class BaseParserPlugin(Protocol):
    def parse(self, request: ParserInput, context: PluginContext) -> AsyncIterator[SourceRegionDraft]: ...


@runtime_checkable
class RegionProcessorPlugin(Protocol):
    def process(self, request: RegionInput, context: PluginContext) -> AsyncIterator[CandidateDraft]: ...
