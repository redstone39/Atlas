from dataclasses import dataclass, field
from typing import Any

from .user_messages import MessageParams, validate_message_reference


@dataclass
class AuditEventRecord:
    event_id: str
    event_type: str
    actor_id: str | None
    target_ref: str | None
    project_id: str | None
    message_code: str
    metadata: dict[str, Any]
    created_at: str
    message_params: MessageParams = field(default_factory=dict)
    scope_type: str | None = None
    scope_id: str | None = None
    document_id: str | None = None

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(
            self.message_code,
            self.message_params,
        )
