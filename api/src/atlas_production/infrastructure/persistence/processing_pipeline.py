from dataclasses import asdict
import json
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.processing_pipeline.records import (
    EvidenceRecord, PluginPackageRecord, PluginVersionRecord, RuntimeProfileRecord,
    ProcessingProfile, ProcessingProfileRevision, ProcessingRun,
    ParserAdapterInvocation, SourceRegion, ExtractionCandidate, CandidateGroup,
    PromotionDecision, KPELNormalizationHandoff, RoutingDecision, EvidenceBuildTrace,
    ProcessingIdempotencyRecord, PluginVersionRef,
    EvidencePageArtifact,
)
from atlas_production.modules.processing_pipeline.canonical_processing import (
    PROCESSING_SPEC_MAX_BYTES,
    ProcessingIdentity,
    ProcessingRevision,
    ProcessingRevisionState,
    canonical_processing_spec,
)

from .base import OrmBase
from .payload_policy import (
    EVIDENCE_LOCATOR_FIELDS,
    GENERAL_METADATA_MAX_BYTES,
    RUNTIME_POLICY_MAX_BYTES,
    serialize_typed_dataclass,
    validate_typed_patch,
    validate_typed_payload,
    validate_typed_sequence,
)


_PROCESSING_FIELDS = {
    PluginPackageRecord: ("package_id", "plugin_id", "plugin_version", "package_digest", "artifact_ref", "byte_size", "uploaded_by", "created_at"),
    PluginVersionRecord: ("plugin_id", "plugin_version", "package_digest", "runtime_profile", "plugin_kind", "status", "trust_provenance", "revision", "created_at", "updated_at", "diagnostic_code", "canary_passed_at", "descriptor"),
    RuntimeProfileRecord: ("runtime_profile_id", "description", "enabled", "created_at", "available_packages"),
    ProcessingProfile: ("profile_id", "display_name", "created_by", "created_at"),
    ProcessingProfileRevision: ("profile_id", "revision", "status", "accepted_media_types", "base_parser_plugin_ref", "mandatory_processor_plugin_refs", "eligible_processor_plugin_refs", "plugin_priority", "planner_enabled", "planner_model_route_id", "channel_registry_version", "trait_registry_version", "max_regions_per_plan", "max_modules_per_region", "max_total_plugin_invocations", "planner_failure_behavior", "created_by", "created_at", "activated_at"),
    ProcessingRun: ("run_id", "document_id", "document_version_id", "profile_id", "profile_revision", "status", "attempt", "created_by", "created_at", "updated_at", "media_type", "base_parser_plugin_ref", "mandatory_processor_plugin_refs", "eligible_processor_plugin_refs", "plugin_priority", "channel_registry_version", "trait_registry_version", "policy_snapshot_ref", "policy_snapshot_digest", "policy_snapshot_payload", "warning_codes", "failure_code"),
    ParserAdapterInvocation: ("invocation_id", "run_id", "plugin_ref", "status", "payload"),
    SourceRegion: ("region_id", "run_id", "payload"),
    ExtractionCandidate: ("candidate_id", "run_id", "payload"),
    CandidateGroup: ("group_id", "run_id", "payload"),
    PromotionDecision: ("decision_id", "run_id", "payload"),
    KPELNormalizationHandoff: ("handoff_id", "run_id", "payload"),
    RoutingDecision: ("routing_decision_id", "run_id", "payload"),
    EvidenceBuildTrace: ("trace_id", "run_id", "payload"),
    ProcessingIdempotencyRecord: ("idempotency_key", "operation", "request_digest", "response_payload", "status_code", "created_at"),
    EvidencePageArtifact: ("artifact_id", "tenant_id", "document_version_id", "source_page_index", "source_page_label", "artifact_kind", "artifact_digest", "content_length", "storage_artifact_id", "source_crop_box", "source_rotation", "geometry_transform_version", "renderer_version", "created_at", "processing_generation", "width", "height", "render_config_revision", "quality_flag_refs"),
}

_PLUGIN_DESCRIPTOR_FIELDS = {
    "entrypoint", "accepted_media_types", "accepted_region_kinds",
    "accepted_element_kind_hints", "accepted_content_kind_hints",
    "produced_channels", "declared_capabilities", "output_contract_version",
    "network_access", "license_expression", "sdk_api_version",
    "signature_key_id", "sbom_present", "sbom_spdx_version",
    "checksums_verified",
}
_NESTED_PAYLOAD_FIELDS = {
    ParserAdapterInvocation: {
        "invocation_kind", "region_id", "failure_code",
    },
    SourceRegion: {
        "source_region_identity", "region_kind", "element_kind_hint",
        "content_kind_hint", "locator_draft", "normalized_text_ref",
        "structured_content_ref", "quality_flag_refs",
    },
    ExtractionCandidate: {
        "source_region_ids", "channel_id", "output_contract_version",
        "candidate_payload_ref", "content_kind_hint", "element_kind_hint",
        "structured_content_ref", "native_artifact_ref", "table_grid",
        "cell_bboxes", "table_asset_refs", "figure_asset_refs",
        "content_rendition_ref", "preview_region", "quality_flag_refs", "plugin_id",
        "plugin_version", "package_digest",
    },
    CandidateGroup: {
        "region_id", "channel_id", "output_contract_version", "candidate_ids",
    },
    PromotionDecision: {
        "candidate_group_id", "selected_candidate_id", "reason_code",
    },
    KPELNormalizationHandoff: {
        "selected_candidate_ids", "promotion_decision_id",
        "canonical_element_kind", "claim_support_role",
        "channel_registry_version", "policy_snapshot_ref",
        "canonical_locator_payloads",
    },
    RoutingDecision: {
        "region_id", "plugin_id", "status", "reason_code", "invocation_id",
    },
    EvidenceBuildTrace: {
        "warnings", "profile_id", "profile_revision", "failed_channels",
    },
}
_PROCESSING_REPLAY_FIELDS = {
    "error_code", "message_code", "message_params", "correlation_id", "audit_event_ref",
    "operation_audit_event_ref", "package_id", "plugin_id", "plugin_version",
    "package_digest", "artifact_ref", "byte_size", "uploaded_by", "created_at",
    "runtime_profile", "plugin_kind", "status", "trust_provenance", "revision",
    "updated_at", "diagnostic_code", "canary_passed_at", "descriptor", "active",
    "signature_key_id", "license_expression", "sdk_api_version", "sbom_present",
    "sbom_spdx_version", "checksums_verified", "profile_id", "display_name",
    "created_by", "accepted_media_types", "base_parser_plugin_ref",
    "mandatory_processor_plugin_refs", "eligible_processor_plugin_refs",
    "plugin_priority", "planner_enabled", "planner_model_route_id",
    "channel_registry_version", "trait_registry_version", "max_regions_per_plan",
    "max_modules_per_region", "max_total_plugin_invocations",
    "planner_failure_behavior", "activated_at", "run_id", "document_id",
    "document_version_id", "attempt", "media_type", "policy_snapshot_ref",
    "policy_snapshot_digest", "policy_snapshot_payload", "warning_codes",
    "failure_code",
}


def _closed_patch(
    payload: dict[str, Any], *, family: str, allowed_fields: set[str]
) -> dict[str, Any]:
    if not payload:
        return validate_typed_payload(
            {}, family=family, allowed_fields=frozenset()
        )
    return validate_typed_patch(
        payload,
        family=family,
        allowed_fields=allowed_fields,
        max_bytes=GENERAL_METADATA_MAX_BYTES,
    )


def _processing_policy_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    result = validate_typed_payload(
        payload,
        family="processing policy snapshot",
        allowed_fields=(
            "policy_snapshot_schema", "document", "document_version", "tags",
            "project_policies", "project_acl",
        ),
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )
    validate_typed_payload(
        result["document"],
        family="processing policy document",
        allowed_fields={"document_id", "lifecycle_status", "source_digest"},
    )
    validate_typed_payload(
        result["document_version"],
        family="processing policy document version",
        allowed_fields={
            "document_version_id", "status", "source_digest", "content_digest",
        },
    )
    for tag in result["tags"]:
        validate_typed_payload(
            tag,
            family="processing policy tag",
            allowed_fields={"tag_type", "tag_id"},
        )
    for policy in result["project_policies"]:
        validate_typed_payload(
            policy,
            family="processing project policy",
            allowed_fields={"project_id", "policy_profile_id"},
        )
    for grant in result["project_acl"]:
        validate_typed_payload(
            grant,
            family="processing project ACL provenance",
            allowed_fields={
                "grant_id", "project_id", "subject_type", "subject_id",
                "role", "effect", "status",
            },
        )
    return result


def _processing_payload(record: Any) -> dict[str, Any]:
    fields = _PROCESSING_FIELDS.get(type(record))
    if fields is None:
        raise TypeError(f"no processing payload serializer for {type(record).__name__}")
    overrides: dict[str, Any] = {}
    if isinstance(record, PluginVersionRecord):
        overrides["descriptor"] = _closed_patch(
            record.descriptor,
            family="processing plugin descriptor",
            allowed_fields=_PLUGIN_DESCRIPTOR_FIELDS,
        )
    if isinstance(record, ProcessingRun):
        overrides["policy_snapshot_payload"] = _processing_policy_snapshot(
            record.policy_snapshot_payload
        )
    nested_fields = _NESTED_PAYLOAD_FIELDS.get(type(record))
    if nested_fields is not None:
        nested = dict(record.payload)
        if isinstance(record, ExtractionCandidate):
            nested["table_grid"] = None
            nested["cell_bboxes"] = None
        nested = _closed_patch(
            nested,
            family=f"processing {type(record).__name__} payload",
            allowed_fields=nested_fields,
        )
        if isinstance(record, SourceRegion) and nested.get("locator_draft"):
            validate_typed_patch(
                nested["locator_draft"],
                family="processing source locator",
                allowed_fields=EVIDENCE_LOCATOR_FIELDS,
            )
        if isinstance(record, KPELNormalizationHandoff):
            for locator in nested.get("canonical_locator_payloads", []):
                validate_typed_patch(
                    locator,
                    family="processing canonical locator",
                    allowed_fields=EVIDENCE_LOCATOR_FIELDS,
                )
        overrides["payload"] = nested
    if isinstance(record, ProcessingIdempotencyRecord):
        overrides["response_payload"] = _closed_patch(
            record.response_payload,
            family="processing idempotency response",
            allowed_fields=_PROCESSING_REPLAY_FIELDS,
        )
    return serialize_typed_dataclass(
        record,
        family=f"processing {type(record).__name__} metadata",
        allowed_fields=fields,
        overrides=overrides or None,
    )


def evidence_page_artifact_payload(record: EvidencePageArtifact) -> dict[str, Any]:
    if (
        record.artifact_kind == "page_image"
        and (
            type(record.width) is not int
            or record.width <= 0
            or type(record.height) is not int
            or record.height <= 0
            or not isinstance(record.render_config_revision, str)
            or not record.render_config_revision.strip()
        )
    ):
        raise ValueError("page_image_metadata_invalid")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in record.quality_flag_refs
    ):
        raise ValueError("page_image_quality_flags_invalid")
    return _processing_payload(record)


def processing_identity_spec_payload(
    record: ProcessingIdentity | dict[str, Any],
) -> dict[str, Any]:
    spec = record.processing_spec if isinstance(record, ProcessingIdentity) else record
    return canonical_processing_spec(spec)


class AtlasProcessingIdentityRow(OrmBase):
    __tablename__ = "atlas_processing_identities"

    processing_identity_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(
        String,
        ForeignKey(
            "atlas_artifacts.artifact_id",
            name="fk_atlas_processing_identity_source_artifact",
            use_alter=True,
        ),
        nullable=False,
    )
    source_artifact_checksum_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    current_revision_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_sha256",
            "processing_fingerprint",
            name="uq_atlas_processing_identity_key",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_processing_identity_source_sha256",
        ),
        CheckConstraint(
            "processing_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_processing_identity_fingerprint",
        ),
        CheckConstraint(
            "source_artifact_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_processing_identity_artifact_checksum",
        ),
        CheckConstraint(
            "source_sha256 = source_artifact_checksum_sha256",
            name="ck_atlas_processing_identity_source_artifact_match",
        ),
        CheckConstraint(
            "jsonb_typeof(processing_spec) = 'object' "
            f"AND octet_length(processing_spec::text) <= {PROCESSING_SPEC_MAX_BYTES}",
            name="ck_atlas_processing_identity_spec_bound",
        ),
        ForeignKeyConstraint(
            ["current_revision_id", "processing_identity_id"],
            [
                "atlas_processing_revisions.processing_revision_id",
                "atlas_processing_revisions.processing_identity_id",
            ],
            name="fk_atlas_processing_identity_current_revision",
            use_alter=True,
        ),
    )


class AtlasProcessingRevisionRow(OrmBase):
    __tablename__ = "atlas_processing_revisions"

    processing_revision_id: Mapped[str] = mapped_column(String, primary_key=True)
    processing_identity_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("atlas_processing_identities.processing_identity_id"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, index=True)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_artifact_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    finalized_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "processing_identity_id",
            "revision_number",
            name="uq_atlas_processing_revision_number",
        ),
        UniqueConstraint(
            "processing_revision_id",
            "processing_identity_id",
            name="uq_atlas_processing_revision_identity",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_atlas_processing_revision_number",
        ),
        CheckConstraint(
            "state IN ('building','ready','failed','cancelled')",
            name="ck_atlas_processing_revision_state",
        ),
        CheckConstraint(
            "manifest_digest IS NULL OR manifest_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_processing_revision_manifest",
        ),
        CheckConstraint(
            "(page_artifact_count IS NULL OR page_artifact_count >= 0) "
            "AND (evidence_count IS NULL OR evidence_count >= 0) "
            "AND (chunk_count IS NULL OR chunk_count >= 0) "
            "AND (index_point_count IS NULL OR index_point_count >= 0)",
            name="ck_atlas_processing_revision_counts",
        ),
        CheckConstraint(
            "(state = 'ready' AND manifest_digest IS NOT NULL "
            "AND page_artifact_count IS NOT NULL AND evidence_count IS NOT NULL "
            "AND chunk_count IS NOT NULL AND index_point_count IS NOT NULL "
            "AND finalized_at IS NOT NULL) "
            "OR (state = 'building' AND manifest_digest IS NULL "
            "AND page_artifact_count IS NULL AND evidence_count IS NULL "
            "AND chunk_count IS NULL AND index_point_count IS NULL "
            "AND finalized_at IS NULL) "
            "OR (state IN ('failed','cancelled') AND manifest_digest IS NULL "
            "AND page_artifact_count IS NULL AND evidence_count IS NULL "
            "AND chunk_count IS NULL AND index_point_count IS NULL "
            "AND finalized_at IS NOT NULL)",
            name="ck_atlas_processing_revision_terminal_metadata",
        ),
        Index(
            "ux_atlas_processing_revision_building",
            "processing_identity_id",
            unique=True,
            postgresql_where=text("state = 'building'"),
        ),
    )


def processing_identity_row(record: ProcessingIdentity) -> AtlasProcessingIdentityRow:
    return AtlasProcessingIdentityRow(
        processing_identity_id=record.processing_identity_id,
        source_sha256=record.source_sha256,
        processing_fingerprint=record.processing_fingerprint,
        processing_spec=processing_identity_spec_payload(record),
        source_artifact_id=record.source_artifact_id,
        source_artifact_checksum_sha256=record.source_artifact_checksum_sha256,
        current_revision_id=record.current_revision_id,
        created_at=record.created_at,
    )


def processing_identity_record(row: AtlasProcessingIdentityRow) -> ProcessingIdentity:
    return ProcessingIdentity(
        processing_identity_id=row.processing_identity_id,
        source_sha256=row.source_sha256,
        processing_fingerprint=row.processing_fingerprint,
        processing_spec=row.processing_spec,
        source_artifact_id=row.source_artifact_id,
        source_artifact_checksum_sha256=row.source_artifact_checksum_sha256,
        current_revision_id=row.current_revision_id,
        created_at=row.created_at,
    )


def processing_revision_row(record: ProcessingRevision) -> AtlasProcessingRevisionRow:
    return AtlasProcessingRevisionRow(
        processing_revision_id=record.processing_revision_id,
        processing_identity_id=record.processing_identity_id,
        revision_number=record.revision_number,
        state=record.state,
        manifest_digest=record.manifest_digest,
        page_artifact_count=record.page_artifact_count,
        evidence_count=record.evidence_count,
        chunk_count=record.chunk_count,
        index_point_count=record.index_point_count,
        created_at=record.created_at,
        finalized_at=record.finalized_at,
    )


def processing_revision_record(row: AtlasProcessingRevisionRow) -> ProcessingRevision:
    return ProcessingRevision(
        processing_revision_id=row.processing_revision_id,
        processing_identity_id=row.processing_identity_id,
        revision_number=row.revision_number,
        state=cast(ProcessingRevisionState, row.state),
        manifest_digest=row.manifest_digest,
        page_artifact_count=row.page_artifact_count,
        evidence_count=row.evidence_count,
        chunk_count=row.chunk_count,
        index_point_count=row.index_point_count,
        created_at=row.created_at,
        finalized_at=row.finalized_at,
    )


class AtlasEvidenceRow(OrmBase):
    __tablename__ = "atlas_evidence"

    evidence_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    document_title: Mapped[str] = mapped_column(String, nullable=False)
    locator_label: Mapped[str] = mapped_column(String, nullable=False)
    snippet: Mapped[str] = mapped_column(String(4096), nullable=False)
    content: Mapped[str] = mapped_column(String(4096), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    processing_generation: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    processing_revision_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("atlas_processing_revisions.processing_revision_id"),
        nullable=True,
        index=True,
    )
    source_region_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    output_contract_version: Mapped[str] = mapped_column(String, nullable=False)
    claim_support_role: Mapped[str] = mapped_column(String, nullable=False)
    locator_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    processing_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    promotion_decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_flag_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    trace_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_evidence_id: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    __table_args__ = (
        CheckConstraint(
            "char_length(snippet) <= 4096 AND char_length(content) <= 4096",
            name="ck_atlas_evidence_projection_bounds",
        ),
    )


def _payload_row(name: str, table: str):
    return type(name, (OrmBase,), {"__tablename__": table, "id": mapped_column(String, primary_key=True), "payload": mapped_column(JSONB, nullable=False)})


AtlasPluginPackageRow = _payload_row("AtlasPluginPackageRow", "atlas_processing_plugin_packages")
AtlasPluginVersionRow = _payload_row("AtlasPluginVersionRow", "atlas_processing_plugin_versions")
AtlasRuntimeProfileRow = _payload_row("AtlasRuntimeProfileRow", "atlas_processing_runtime_profiles")
AtlasProcessingProfileRow = _payload_row("AtlasProcessingProfileRow", "atlas_processing_profiles")
AtlasProcessingProfileRevisionRow = _payload_row("AtlasProcessingProfileRevisionRow", "atlas_processing_profile_revisions")
AtlasProcessingRunRow = _payload_row("AtlasProcessingRunRow", "atlas_processing_runs")
AtlasParserAdapterInvocationRow = _payload_row("AtlasParserAdapterInvocationRow", "atlas_parser_adapter_invocations")
AtlasSourceRegionRow = _payload_row("AtlasSourceRegionRow", "atlas_source_regions")
AtlasExtractionCandidateRow = _payload_row("AtlasExtractionCandidateRow", "atlas_extraction_candidates")
AtlasCandidateGroupRow = _payload_row("AtlasCandidateGroupRow", "atlas_candidate_groups")
AtlasPromotionDecisionRow = _payload_row("AtlasPromotionDecisionRow", "atlas_promotion_decisions")
AtlasKpelHandoffRow = _payload_row("AtlasKpelHandoffRow", "atlas_kpel_normalization_handoffs")
AtlasProcessingRoutingDecisionRow = _payload_row("AtlasProcessingRoutingDecisionRow", "atlas_processing_routing_decisions")
AtlasEvidenceBuildTraceRow = _payload_row("AtlasEvidenceBuildTraceRow", "atlas_evidence_build_traces")
AtlasProcessingIdempotencyRow = _payload_row("AtlasProcessingIdempotencyRow", "atlas_processing_idempotency")
class AtlasEvidencePageArtifactRow(OrmBase):
    __tablename__ = "atlas_evidence_page_artifacts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    renderer_version: Mapped[str] = mapped_column(String, nullable=False)
    processing_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_revision_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("atlas_processing_revisions.processing_revision_id"),
        nullable=True,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint(
        "tenant_id", "document_version_id", "source_page_index", "processing_generation",
        name="uq_atlas_evidence_page_artifact_generation_page",
    ), CheckConstraint(
        "source_page_index >= 0 AND processing_generation >= 0", name="ck_atlas_evidence_page_source_index",
    ))


COLLECTIONS = (
    ("plugin_packages", AtlasPluginPackageRow, PluginPackageRecord, "package_id"),
    ("runtime_profiles", AtlasRuntimeProfileRow, RuntimeProfileRecord, "runtime_profile_id"),
    ("processing_profiles", AtlasProcessingProfileRow, ProcessingProfile, "profile_id"),
    ("processing_runs", AtlasProcessingRunRow, ProcessingRun, "run_id"),
    ("parser_adapter_invocations", AtlasParserAdapterInvocationRow, ParserAdapterInvocation, "invocation_id"),
    ("source_regions", AtlasSourceRegionRow, SourceRegion, "region_id"),
    ("extraction_candidates", AtlasExtractionCandidateRow, ExtractionCandidate, "candidate_id"),
    ("candidate_groups", AtlasCandidateGroupRow, CandidateGroup, "group_id"),
    ("promotion_decisions", AtlasPromotionDecisionRow, PromotionDecision, "decision_id"),
    ("kpel_normalization_handoffs", AtlasKpelHandoffRow, KPELNormalizationHandoff, "handoff_id"),
    ("processing_routing_decisions", AtlasProcessingRoutingDecisionRow, RoutingDecision, "routing_decision_id"),
    ("evidence_build_traces", AtlasEvidenceBuildTraceRow, EvidenceBuildTrace, "trace_id"),
    ("processing_idempotency", AtlasProcessingIdempotencyRow, ProcessingIdempotencyRecord, "idempotency_key"),
)

REGISTRY_COLLECTIONS = COLLECTIONS[:3]
RUN_COLLECTIONS = COLLECTIONS[3:-1]


def _record_from_payload(record_type: type, payload: dict[str, Any]) -> Any:
    converted = dict(payload)
    if record_type is ParserAdapterInvocation:
        converted["plugin_ref"] = PluginVersionRef(**converted["plugin_ref"])
    if record_type in {ProcessingRun, ProcessingProfileRevision}:
        if converted.get("base_parser_plugin_ref"):
            converted["base_parser_plugin_ref"] = PluginVersionRef(
                **converted["base_parser_plugin_ref"]
            )
        for name in (
            "mandatory_processor_plugin_refs",
            "eligible_processor_plugin_refs",
            "plugin_priority",
        ):
            converted[name] = tuple(
                PluginVersionRef(**ref) for ref in converted.get(name, [])
            )
    return record_type(**converted)


def _merge_or_delete_registry_row(
    session: Session,
    row_type: type,
    *,
    row_id: str,
    record: Any | None,
) -> None:
    if record is None:
        session.query(row_type).filter_by(id=row_id).delete(
            synchronize_session=False,
        )
        return
    session.merge(row_type(id=row_id, payload=_processing_payload(record)))
