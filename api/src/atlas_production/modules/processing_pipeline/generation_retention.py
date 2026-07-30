from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationRetentionResourceV1(_StrictModel):
    document_version_ref: OpaqueRef
    processing_generation_ref: OpaqueRef
    processing_revision_ref: OpaqueRef | None = None
    index_generation_ref: OpaqueRef
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CreateGenerationRetentionV1(_StrictModel):
    execution_id: Identity
    resources: list[GenerationRetentionResourceV1] = Field(max_length=1000)
    idempotency_key: Identity


class GenerationRetentionRefV1(_StrictModel):
    retention_ref: OpaqueRef
    execution_id: Identity
    resource_count: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class ReleaseGenerationRetentionV1(_StrictModel):
    execution_id: Identity
    retention_ref: OpaqueRef
    idempotency_key: Identity


class GenerationRetentionOwner(Protocol):
    def create_generation_retention(
        self, command: CreateGenerationRetentionV1
    ) -> GenerationRetentionRefV1: ...

    def release_generation_retention(
        self, command: ReleaseGenerationRetentionV1
    ) -> None: ...

    def release_execution_generation_retention(
        self, *, execution_id: Identity, idempotency_key: Identity
    ) -> None: ...


__all__ = [
    "CreateGenerationRetentionV1",
    "GenerationRetentionOwner",
    "GenerationRetentionRefV1",
    "GenerationRetentionResourceV1",
    "ReleaseGenerationRetentionV1",
]
