from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PluginRefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugin_id: str
    plugin_version: str
    package_digest: str
    runtime_profile: str


class IdempotentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str | None = Field(default=None, min_length=1)


class PluginMutationRequest(IdempotentRequest):
    expected_revision: int | None = Field(default=None, ge=1)


class ProfileCreateRequest(IdempotentRequest):
    display_name: str = Field(min_length=1)


class ProfileRevisionCreateRequest(IdempotentRequest):
    accepted_media_types: list[str] = Field(min_length=1)
    base_parser_plugin_ref: PluginRefInput
    mandatory_processor_plugin_refs: list[PluginRefInput] = Field(default_factory=list)
    eligible_processor_plugin_refs: list[PluginRefInput] = Field(default_factory=list)
    plugin_priority: list[PluginRefInput] = Field(default_factory=list)
    planner_enabled: bool = False
    planner_model_route_id: str | None = None
    channel_registry_version: str = Field(min_length=1)
    trait_registry_version: str = Field(min_length=1)
    max_regions_per_plan: int = Field(gt=0)
    max_modules_per_region: int = Field(gt=0)
    max_total_plugin_invocations: int = Field(gt=0)
    planner_failure_behavior: Literal["mandatory_only"] = "mandatory_only"


class ProfileActivateRequest(IdempotentRequest):
    expected_revision: int | None = Field(default=None, ge=1)


class PackageUploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1)
