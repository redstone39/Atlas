from typing import Any

from atlas_production.shared.public import (
    AuditEventRecord,
)

from .api_models import AuditEvent


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
