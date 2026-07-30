from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atlas_production.shared.public import (
    AdminActionResult,
)
from atlas_production.shared.user_messages import MessageParams, validate_message_reference
from .api_models import (
    ModelRouteStatus,
    ProviderConnectionStatus,
)


@dataclass
class ModelRoutingError(Exception):
    error_code: str
    message_code: str
    status_code: int
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(self.message_code, self.message_params)


@dataclass(frozen=True)
class ModelRouteOutcome:
    result: ModelRouteStatus | ProviderConnectionStatus | AdminActionResult
    success_status_code: int


@dataclass(frozen=True)
class ModelRouteAuditCommand:
    event_type: str
    actor_id: str | None
    target_ref: str
    message_code: str
    metadata: dict[str, object]
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_params", validate_message_reference(self.message_code, self.message_params))


@dataclass(frozen=True)
class ModelInvocationHandle:
    invocation_id: str
    route_id: str
    provider_type: str
    model_name: str
    prompt_snapshot_ref: str
    response_schema_name: str
    response_schema_digest: str
    route_revision: int
    runtime_policy_schema_version: str
    runtime_policy_revision: int
    runtime_policy_snapshot: dict[str, Any]
    created_at: str
    invocation_purpose: str = "conversation"
    subject_kind: str = "conversation"
    subject_ref: str | None = None
    request_artifact_ref: str | None = None
    execution_key: str | None = None
    prompt_digest: str | None = None
    input_digest: str | None = None
    input_content_type: str | None = None
    input_width: int | None = None
    input_height: int | None = None
    attempt_ordinal: int | None = None
    repair_origin_error_codes: tuple[str, ...] = ()
