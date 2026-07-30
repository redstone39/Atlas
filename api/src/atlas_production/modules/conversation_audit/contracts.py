from __future__ import annotations

from dataclasses import dataclass, field

from atlas_production.shared.user_messages import MessageParams, validate_message_reference


@dataclass
class ConversationAuditError(Exception):
    error_code: str
    message_code: str
    status_code: int
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(self.message_code, self.message_params)


@dataclass(frozen=True)
class ConversationAuditCommand:
    event_type: str
    actor_id: str | None
    target_ref: str
    message_code: str
    project_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))
