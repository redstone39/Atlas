from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.shared.public import AuditEventRecord
from atlas_production.shared.user_messages import (
    MessageParams,
    validate_message_reference,
)

from .api_models import AuditEvent, AuditEventList

class _AuditEventReader(Protocol):
    def recent_events(self, *, limit: int = 50) -> list[AuditEventRecord]: ...


class _ReadAuditWriter(Protocol):
    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        message_code: str,
        metadata: dict[str, object] | None = None,
        **facts: object,
    ) -> object: ...


@dataclass
class AuditEventReadError(Exception):
    error_code: str
    message_code: str
    status_code: int
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(
            self.message_code,
            self.message_params,
        )


@dataclass(frozen=True, slots=True)
class AdminAuditEventReadService:
    reader: _AuditEventReader
    audit_writer: _ReadAuditWriter

    def list_admin(
        self,
        actor: UserRecord | None,
        *,
        limit: int = 50,
    ) -> AuditEventList:
        if actor is None:
            raise AuditEventReadError(
                "unauthenticated",
                "auth.please_sign_in_before_using_admin_tools",
                401,
            )
        if not actor.active or actor.system_role != "admin":
            raise AuditEventReadError(
                "access_denied",
                "permission.admin_permission_is_required",
                403,
            )
        self.audit_writer.append_read_audit(
            "read_audit_events",
            actor_id=actor.actor_id,
            target_ref="audit-events:*",
            message_code="audit.admin_listed_audit_events",
            metadata={"admin_global_history_access": True},
        )
        return AuditEventList(
            events=[
                audit_event_status(event)
                for event in self.reader.recent_events(limit=limit)
            ]
        )

SENSITIVE_AUDIT_KEY_FRAGMENTS = (
    "api_key",
    "bearer",
    "connection_string",
    "credential",
    "password",
    "raw_token",
    "secret",
    "session_token",
    "token_digest",
)


def safe_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {key: safe_audit_value(key, value) for key, value in metadata.items()}


def safe_audit_value(key: str, value: Any) -> Any:
    normalized = key.lower()
    if any(fragment in normalized for fragment in SENSITIVE_AUDIT_KEY_FRAGMENTS):
        return "[redacted]"
    if isinstance(value, dict):
        return safe_audit_metadata(value)
    if isinstance(value, list):
        return [safe_audit_value(key, item) for item in value]
    if isinstance(value, str) and "atlas_agent_" in value:
        return "[redacted]"
    return value


def audit_event_status(event: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        actor_id=event.actor_id,
        target_ref=event.target_ref,
        project_id=event.project_id,
        scope_type=event.scope_type,
        scope_id=event.scope_id,
        document_id=event.document_id,
        message_code=event.message_code,
        message_params=event.message_params,
        metadata=event.metadata,
        created_at=event.created_at,
    )
