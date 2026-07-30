from __future__ import annotations

from dataclasses import dataclass, field

from atlas_production.shared.user_messages import MessageParams, validate_message_reference

from .api_models import (
    SessionState,
)


@dataclass(frozen=True)
class IdentityAccessError(Exception):
    error_code: str
    message_code: str
    status_code: int
    audit_event_ref: str | None = None
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))


@dataclass(frozen=True)
class LoginOutcome:
    session: SessionState
    raw_session_token: str


@dataclass(frozen=True)
class IdentityAuditCommand:
    event_type: str
    actor_id: str | None
    target_ref: str
    scope_type: str | None
    scope_id: str | None
    message_code: str
    metadata: dict[str, object]
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))
