from typing import Literal

from pydantic import BaseModel, Field

from .user_messages import MessageReferenceModel


class ActorRef(BaseModel):
    actor_type: Literal["user", "group", "service_account"]
    actor_id: str
    issuer: str
    display_label: str | None = None
    display_email: str | None = None


class ErrorResponse(MessageReferenceModel):
    error_code: str
    correlation_id: str
    audit_event_ref: str | None = None


class AdminActionResult(MessageReferenceModel):
    request_id: str
    status: Literal["applied", "rejected", "access_denied"]
    target_ref: str | None
    audit_event_ref: str
