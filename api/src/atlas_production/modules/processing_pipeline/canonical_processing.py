from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Literal, Mapping


ProcessingRevisionState = Literal["building", "ready", "failed", "cancelled"]

PROCESSING_SPEC_SCHEMA_VERSION = 1
PROCESSING_SPEC_MAX_BYTES = 16_384
PROCESSING_SPEC_MATERIAL_FIELDS = (
    "parser",
    "ocr",
    "renderer",
    "normalization",
    "chunking",
    "embedding",
    "indexing",
)
PROCESSING_SPEC_EXCLUDED_FIELDS = frozenset(
    {
        "document_id",
        "document_version_id",
        "processing_generation",
        "job_id",
        "actor_id",
        "timestamp",
        "created_at",
        "updated_at",
        "acl",
        "tags",
        "display_metadata",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_NESTING_DEPTH = 12
_MAX_COLLECTION_ITEMS = 1_024
_MAX_STRING_BYTES = 4_096


def _validate_json_value(value: Any, *, path: str, depth: int = 0) -> Any:
    if depth > _MAX_NESTING_DEPTH:
        raise ValueError(f"{path}: processing_spec_nesting_too_deep")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: processing_spec_non_finite_number")
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise ValueError(f"{path}: processing_spec_string_too_large")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError(f"{path}: processing_spec_object_too_large")
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path}: processing_spec_key_invalid")
            if key in PROCESSING_SPEC_EXCLUDED_FIELDS:
                continue
            result[key] = _validate_json_value(
                nested,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError(f"{path}: processing_spec_array_too_large")
        return [
            _validate_json_value(item, path=f"{path}[]", depth=depth + 1)
            for item in value
        ]
    raise ValueError(f"{path}: processing_spec_value_invalid")


def canonical_processing_spec(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Return the closed material projection used by persistence and hashing."""

    if not isinstance(configuration, Mapping):
        raise ValueError("processing_spec_must_be_object")
    if any(not isinstance(key, str) or not key for key in configuration):
        raise ValueError("processing_spec_key_invalid")
    allowed = {
        "schema_version",
        *PROCESSING_SPEC_MATERIAL_FIELDS,
        *PROCESSING_SPEC_EXCLUDED_FIELDS,
    }
    unknown = sorted(set(configuration) - allowed)
    if unknown:
        raise ValueError(f"processing_spec_unknown_fields:{','.join(unknown)}")
    if configuration.get("schema_version") != PROCESSING_SPEC_SCHEMA_VERSION:
        raise ValueError("processing_spec_schema_version_invalid")

    missing = [name for name in PROCESSING_SPEC_MATERIAL_FIELDS if name not in configuration]
    if missing:
        raise ValueError(f"processing_spec_missing_fields:{','.join(missing)}")

    canonical: dict[str, Any] = {"schema_version": PROCESSING_SPEC_SCHEMA_VERSION}
    for name in PROCESSING_SPEC_MATERIAL_FIELDS:
        rule = configuration[name]
        if not isinstance(rule, Mapping) or not rule:
            raise ValueError(f"processing_spec_{name}_rules_invalid")
        canonical[name] = _validate_json_value(rule, path=name)

    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > PROCESSING_SPEC_MAX_BYTES:
        raise ValueError("processing_spec_too_large")
    return canonical


def processing_fingerprint(configuration: Mapping[str, Any]) -> str:
    canonical = canonical_processing_spec(configuration)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field}_invalid")


def _require_ref(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_invalid")


@dataclass(frozen=True)
class ProcessingIdentity:
    processing_identity_id: str
    source_sha256: str
    processing_fingerprint: str
    processing_spec: Mapping[str, Any]
    source_artifact_id: str
    source_artifact_checksum_sha256: str
    created_at: str
    current_revision_id: str | None = None

    def __post_init__(self) -> None:
        _require_ref(self.processing_identity_id, field="processing_identity_id")
        _require_sha256(self.source_sha256, field="source_sha256")
        _require_sha256(self.processing_fingerprint, field="processing_fingerprint")
        _require_ref(self.source_artifact_id, field="source_artifact_id")
        _require_sha256(
            self.source_artifact_checksum_sha256,
            field="source_artifact_checksum_sha256",
        )
        if self.source_artifact_checksum_sha256 != self.source_sha256:
            raise ValueError("source_artifact_checksum_mismatch")
        _require_ref(self.created_at, field="created_at")
        if self.current_revision_id is not None:
            _require_ref(self.current_revision_id, field="current_revision_id")
        canonical = canonical_processing_spec(self.processing_spec)
        if processing_fingerprint(canonical) != self.processing_fingerprint:
            raise ValueError("processing_fingerprint_mismatch")
        object.__setattr__(self, "processing_spec", canonical)


@dataclass(frozen=True)
class ProcessingRevision:
    processing_revision_id: str
    processing_identity_id: str
    revision_number: int
    state: ProcessingRevisionState
    created_at: str
    manifest_digest: str | None = None
    page_artifact_count: int | None = None
    evidence_count: int | None = None
    chunk_count: int | None = None
    index_point_count: int | None = None
    finalized_at: str | None = None

    def __post_init__(self) -> None:
        _require_ref(self.processing_revision_id, field="processing_revision_id")
        _require_ref(self.processing_identity_id, field="processing_identity_id")
        if type(self.revision_number) is not int or self.revision_number <= 0:
            raise ValueError("revision_number_invalid")
        if self.state not in {"building", "ready", "failed", "cancelled"}:
            raise ValueError("processing_revision_state_invalid")
        _require_ref(self.created_at, field="created_at")
        counts = (
            self.page_artifact_count,
            self.evidence_count,
            self.chunk_count,
            self.index_point_count,
        )
        if any(value is not None and (type(value) is not int or value < 0) for value in counts):
            raise ValueError("processing_revision_count_invalid")
        if self.manifest_digest is not None:
            _require_sha256(self.manifest_digest, field="manifest_digest")
        if self.state == "ready":
            if self.manifest_digest is None or any(value is None for value in counts):
                raise ValueError("ready_revision_manifest_incomplete")
            if self.finalized_at is None:
                raise ValueError("ready_revision_finalized_at_required")
        elif self.manifest_digest is not None or any(value is not None for value in counts):
            raise ValueError("non_ready_revision_manifest_not_allowed")
        if self.state != "building" and self.finalized_at is None:
            raise ValueError("terminal_revision_finalized_at_required")
        if self.finalized_at is not None:
            _require_ref(self.finalized_at, field="finalized_at")


@dataclass(frozen=True, slots=True)
class ProcessingRevisionPin:
    """One Document binding's exact ready canonical-processing lineage."""

    document_binding_id: str
    processing_identity_id: str
    processing_revision_id: str
    document_version_ref: str
    source_artifact_id: str
    source_artifact_checksum_sha256: str
    revision_state: Literal["ready"]
    processing_generation_ref: str
    index_generation_id: str
    manifest_digest: str

    def __post_init__(self) -> None:
        for field, value in (
            ("document_binding_id", self.document_binding_id),
            ("processing_identity_id", self.processing_identity_id),
            ("processing_revision_id", self.processing_revision_id),
            ("document_version_ref", self.document_version_ref),
            ("source_artifact_id", self.source_artifact_id),
            ("processing_generation_ref", self.processing_generation_ref),
            ("index_generation_id", self.index_generation_id),
        ):
            _require_ref(value, field=field)
        _require_sha256(
            self.source_artifact_checksum_sha256,
            field="source_artifact_checksum_sha256",
        )
        if self.revision_state != "ready":
            raise ValueError("processing_revision_pin_not_ready")
        _require_sha256(self.manifest_digest, field="manifest_digest")
