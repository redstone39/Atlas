from __future__ import annotations

from dataclasses import dataclass, field

from atlas_production.shared.user_messages import MessageParams, validate_message_reference


@dataclass(frozen=True)
class DocumentAuditCommand:
    event_type: str
    actor_id: str
    target_ref: str
    project_id: str | None
    scope_type: str | None
    scope_id: str | None
    document_id: str | None
    message_code: str
    metadata: dict[str, object]
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))
