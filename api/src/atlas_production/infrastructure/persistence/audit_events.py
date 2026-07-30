from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.audit.public import safe_audit_metadata
from atlas_production.shared.public import AuditEventRecord, utc_now_iso

from .base import OrmBase
from .model_routing import serialize_model_runtime_policy
from .payload_policy import (
    FAILURE_SUMMARY_MAX_BYTES,
    GENERAL_METADATA_MAX_BYTES,
    PersistedPayloadPolicyError,
    validate_typed_patch,
    validate_typed_payload,
)


_AUDIT_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "event_type",
        "actor_id",
        "target_ref",
        "project_id",
        "message_code",
        "message_params",
        "metadata",
        "created_at",
        "scope_type",
        "scope_id",
        "document_id",
    }
)


_AUDIT_METADATA_FIELDS = frozenset(
    {
        "access_decision_id",
        "access_decision_ids",
        "active",
        "active_processing_generation",
        "admin_global_history_access",
        "agent_actor_id",
        "allow_member_download",
        "artifact_id",
        "attempt",
        "attempt_ended_at",
        "attempt_id",
        "attempt_started_at",
        "batch_id",
        "canonical_mime",
        "candidate_event_id",
        "change_id",
        "connection_id",
        "command",
        "delivery_mode",
        "discovery_status",
        "document_id",
        "document_format",
        "document_version_id",
        "effect",
        "elapsed_seconds",
        "email",
        "evidence_count",
        "failure_code",
        "guidance_character_count",
        "guidance_digest",
        "inherit_parent_documents",
        "intake_status",
        "invite_id",
        "invocation_id",
        "invocation_kind",
        "job_id",
        "lifecycle_status",
        "logical_identity",
        "member_actor_id",
        "member_actor_type",
        "model_invocation_refs",
        "new_default_route_id",
        "next_attempt",
        "operation",
        "operation_id",
        "package_digest",
        "parent_team_id",
        "plugin_id",
        "plugin_version",
        "policy_profile_id",
        "previous_default_route_id",
        "prior_run_id",
        "processing_generation",
        "profile_id",
        "provider_type",
        "raw_sha256",
        "replayed",
        "reason",
        "reason_code",
        "request_id",
        "request_fingerprint",
        "resource_lifecycle_epoch",
        "response_kind",
        "revision",
        "role",
        "route_id",
        "route_status",
        "run_id",
        "runtime_policy",
        "runtime_policy_digest",
        "runtime_policy_revision",
        "scope_mode",
        "scope_role",
        "source_connection_id",
        "source_download_restricted",
        "source_kind",
        "status",
        "supports_vision",
        "subject_id",
        "subject_type",
        "system_role",
        "tag_refs",
        "target_connection_id",
        "team_id",
        "tested_route_ids",
        "terminal_status",
        "token_fingerprint",
        "trace_id",
        "trace_ref",
        "turn_request_id",
        "validation_state",
        "verification_mode",
        "viewer_item_id",
        "visual_watermark_rendered",
    }
)
_AUDIT_BOOLEAN_FIELDS = frozenset(
    {
        "active",
        "admin_global_history_access",
        "allow_member_download",
        "inherit_parent_documents",
        "replayed",
        "source_download_restricted",
        "supports_vision",
        "visual_watermark_rendered",
    }
)
_AUDIT_INTEGER_FIELDS = frozenset(
    {
        "active_processing_generation",
        "attempt",
        "elapsed_seconds",
        "evidence_count",
        "guidance_character_count",
        "processing_generation",
        "next_attempt",
        "resource_lifecycle_epoch",
        "revision",
        "runtime_policy_revision",
    }
)
_AUDIT_STRING_LIST_FIELDS = frozenset(
    {"access_decision_ids", "model_invocation_refs", "tested_route_ids"}
)


def _bounded_message_code(message_code: str) -> str:
    if not isinstance(message_code, str):
        raise PersistedPayloadPolicyError("audit message code must be text")
    if len(message_code.encode("utf-8")) > FAILURE_SUMMARY_MAX_BYTES:
        raise PersistedPayloadPolicyError(
            "audit message code exceeds the 1024-byte persisted summary limit"
        )
    return message_code


def _audit_tag_refs_payload(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise PersistedPayloadPolicyError("audit tag refs must be a list")
    result = []
    for item in value:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(field), str) for field in ("tag_type", "tag_id")
        ):
            raise PersistedPayloadPolicyError(
                "audit tag ref does not match its typed contract"
            )
        result.append(
            validate_typed_payload(
                item,
                family="audit tag ref",
                allowed_fields={"tag_type", "tag_id"},
                max_bytes=GENERAL_METADATA_MAX_BYTES,
            )
        )
    return result


def _audit_metadata_payload(metadata: dict[str, Any] | None) -> dict[str, Any]:
    values = safe_audit_metadata(metadata)
    if not values:
        return {}
    unknown = frozenset(values) - _AUDIT_METADATA_FIELDS
    if unknown:
        raise PersistedPayloadPolicyError(
            f"audit metadata fields do not match allowlist; extra={sorted(unknown)}"
        )
    if "runtime_policy" in values:
        values["runtime_policy"] = serialize_model_runtime_policy(
            values["runtime_policy"]
        )
    if "tag_refs" in values:
        values["tag_refs"] = _audit_tag_refs_payload(values["tag_refs"])
    for key, value in values.items():
        if key in _AUDIT_BOOLEAN_FIELDS and not isinstance(value, bool):
            raise PersistedPayloadPolicyError(
                f"audit metadata field {key} must be boolean"
            )
        if key in _AUDIT_INTEGER_FIELDS and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise PersistedPayloadPolicyError(
                f"audit metadata field {key} must be a non-negative integer"
            )
        if key in _AUDIT_STRING_LIST_FIELDS and (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
        ):
            raise PersistedPayloadPolicyError(
                f"audit metadata field {key} must be a list of text references"
            )
        if (
            key not in _AUDIT_BOOLEAN_FIELDS
            and key not in _AUDIT_INTEGER_FIELDS
            and key not in _AUDIT_STRING_LIST_FIELDS
            and key not in {"runtime_policy", "tag_refs"}
            and value is not None
            and not isinstance(value, str)
        ):
            raise PersistedPayloadPolicyError(
                f"audit metadata field {key} must be text or null"
            )
    return validate_typed_patch(
        values,
        family="audit metadata",
        allowed_fields=_AUDIT_METADATA_FIELDS,
        max_bytes=GENERAL_METADATA_MAX_BYTES,
    )


def _audit_event_payload(event: AuditEventRecord) -> dict[str, Any]:
    payload = asdict(event)
    if frozenset(payload) != _AUDIT_EVENT_FIELDS:
        raise PersistedPayloadPolicyError(
            "audit event fields do not match the persistence contract"
        )
    payload["message_code"] = _bounded_message_code(event.message_code)
    payload["metadata"] = _audit_metadata_payload(event.metadata)
    return payload


class AtlasAuditEventRow(OrmBase):
    __tablename__ = "atlas_audit_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    message_code: Mapped[str] = mapped_column(Text, nullable=False)
    message_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasTurnAuditDraftRow(OrmBase):
    """Audit-owner safe immutable terminal preparation projection."""

    __tablename__ = "atlas_turn_audit_drafts"

    draft_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    terminal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    retrieval_status: Mapped[str] = mapped_column(String(30), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "schema_version IN ('turn-audit-draft-v1','turn-audit-draft-v2')",
            name="ck_atlas_turn_audit_draft_schema",
        ),
        CheckConstraint(
            "terminal_status = 'terminal_completed'",
            name="ck_atlas_turn_audit_draft_terminal",
        ),
        CheckConstraint(
            "retrieval_status IN ('not_used','evidence_found','no_evidence','access_denied','tool_failed','budget_exhausted')",
            name="ck_atlas_turn_audit_draft_retrieval",
        ),
        CheckConstraint(
            "verification_status IN ('verified','partially_verified','unverified','evidence_aligned','questionable')",
            name="ck_atlas_turn_audit_draft_verification",
        ),
        CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_audit_draft_digest",
        ),
        CheckConstraint(
            "octet_length(payload::text) <= 1048576",
            name="ck_atlas_turn_audit_draft_payload_bytes",
        ),
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_audit_draft_idempotency",
        ),
    )


class AtlasTurnAuditDraftReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_audit_draft_releases"

    release_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    draft_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_audit_drafts.draft_ref", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_audit_draft_release_idempotency",
        ),
        UniqueConstraint(
            "execution_id", "draft_ref",
            name="uq_atlas_turn_audit_draft_release_binding",
        ),
    )


TURN_AUDIT_OWNER_TABLES = frozenset(
    {AtlasTurnAuditDraftRow.__tablename__, AtlasTurnAuditDraftReleaseRow.__tablename__}
)


def _record_from_row(row: AtlasAuditEventRow) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=row.event_id,
        event_type=row.event_type,
        actor_id=row.actor_id,
        target_ref=row.target_ref,
        project_id=row.project_id,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        document_id=row.document_id,
        message_code=row.message_code,
        message_params=row.message_params,
        metadata=row.event_metadata,
        created_at=row.created_at,
    )


def read_recent_events(session: Session, *, limit: int) -> list[AuditEventRecord]:
    """Read a bounded, current audit projection without hydrating store history."""
    rows = session.scalars(
        select(AtlasAuditEventRow)
        .order_by(
            AtlasAuditEventRow.created_at.desc(),
            AtlasAuditEventRow.event_id.desc(),
        )
        .limit(limit)
    ).all()
    return [_record_from_row(row) for row in rows]


def add_event_rows(
    session: Session,
    events: Iterable[AuditEventRecord],
) -> None:
    """Add complete request/task-local audit facts to the caller's Session."""
    for event in events:
        payload = _audit_event_payload(event)
        session.add(
            AtlasAuditEventRow(
                event_id=payload["event_id"],
                event_type=payload["event_type"],
                actor_id=payload["actor_id"],
                target_ref=payload["target_ref"],
                project_id=payload["project_id"],
                scope_type=payload["scope_type"],
                scope_id=payload["scope_id"],
                document_id=payload["document_id"],
                message_code=payload["message_code"],
                message_params=payload["message_params"],
                event_metadata=payload["metadata"],
                created_at=payload["created_at"],
            )
        )
