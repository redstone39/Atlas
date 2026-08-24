from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from atlas_production.shared.user_messages import MessageReferenceModel

from .records import ModelRouteRuntimePolicy, ModelRouteRuntimePolicyInput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictMessageReferenceModel(MessageReferenceModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConnectionCreateRequest(StrictModel):
    display_name: str
    provider_type: Literal["openai_compatible", "azure_openai", "anthropic"]
    endpoint_url: str
    api_version: str | None = None
    api_key: SecretStr
    idempotency_key: str


class ProviderConnectionUpdateRequest(StrictModel):
    display_name: str | None = None
    endpoint_url: str | None = None
    api_version: str | None = None
    api_key: SecretStr | None = None
    enabled: bool | None = None
    expected_revision: int
    idempotency_key: str


class ProviderConnectionTestRequest(StrictModel):
    expected_revision: int
    idempotency_key: str


class ProviderConnectionStatus(StrictMessageReferenceModel):
    connection_id: str
    display_name: str
    provider_type: Literal["openai_compatible", "azure_openai", "anthropic"]
    endpoint_url: str
    api_version: str | None = None
    credential_configured: bool
    status: Literal[
        "credential_required",
        "configured",
        "verified",
        "verification_failed",
        "disabled",
    ]
    enabled: bool
    linked_model_count: int
    revision: int
    last_verified_at: str | None
    last_rotated_at: str | None
    audit_event_ref: str


class ProviderConnectionListResult(StrictModel):
    connections: list[ProviderConnectionStatus]


class ProviderConnectionTestResult(StrictMessageReferenceModel):
    connection: ProviderConnectionStatus
    validation_status: Literal["passed", "failed"]
    tested_route_ids: list[str]
    audit_event_ref: str


class ProviderModelDiscoveryResult(StrictMessageReferenceModel):
    connection_id: str
    discovery_status: Literal["available", "unavailable"]
    models: list[str]


class ModelRouteCreateRequest(StrictModel):
    display_name: str
    model_name: str
    connection_id: str
    enabled: bool
    supports_vision: bool = False
    runtime_policy: ModelRouteRuntimePolicyInput
    idempotency_key: str


class ModelRouteUpdateRequest(StrictModel):
    display_name: str | None = None
    model_name: str | None = None
    connection_id: str | None = None
    enabled: bool | None = None
    supports_vision: bool | None = None
    runtime_policy: ModelRouteRuntimePolicyInput
    expected_revision: int
    idempotency_key: str


class ModelRouteTestRequest(StrictModel):
    expected_revision: int
    idempotency_key: str


class ModelRouteStatus(StrictMessageReferenceModel):
    route_id: str
    display_name: str
    provider_type: str
    model_name: str
    connection_id: str
    status: Literal["configured", "test_passed", "test_failed", "disabled"]
    enabled: bool
    supports_vision: bool
    revision: int
    runtime_policy: ModelRouteRuntimePolicy
    audit_event_ref: str
    is_text_default: bool = False
    is_vision_default: bool = False


class ModelRouteListResult(StrictModel):
    routes: list[ModelRouteStatus]
    text_default_route_id: str | None = None
    vision_default_route_id: str | None = None


class ModelRouteDefaultRequest(StrictModel):
    expected_revision: int
    idempotency_key: str
