from typing import Literal

from pydantic import BaseModel, Field

from atlas_production.shared.user_messages import MessageReferenceModel


class AuditEvent(MessageReferenceModel):
    event_id: str
    event_type: str
    actor_id: str | None
    target_ref: str | None
    project_id: str | None
    scope_type: str | None = None
    scope_id: str | None = None
    document_id: str | None = None
    metadata: dict
    created_at: str


class AuditEventList(BaseModel):
    events: list[AuditEvent]
