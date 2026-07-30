from __future__ import annotations

from dataclasses import dataclass, field

from atlas_production.shared.public import (
    AdminActionResult,
)
from atlas_production.shared.user_messages import MessageParams, validate_message_reference


@dataclass
class TeamAccessError(Exception):
    error_code: str
    message_code: str
    status_code: int
    audit_event_ref: str | None = None
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(self.message_code, self.message_params)


@dataclass(frozen=True)
class TeamActionOutcome:
    result: AdminActionResult
    success_status_code: int


@dataclass(frozen=True)
class TeamAuditCommand:
    event_type: str
    actor_id: str | None
    target_ref: str
    message_code: str
    metadata: dict[str, object]
    scope_type: str | None = None
    scope_id: str | None = None
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))
