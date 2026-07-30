from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnAccessGrantRefV1(_StrictModel):
    grant_ref: OpaqueRef
    schema_version: Literal["turn-access-grant-v1"] = "turn-access-grant-v1"
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: Identity
    authorization_revision: int = Field(ge=1)
    issued_at: AwareDatetime
    deadline_at: AwareDatetime


class CreateTurnAccessGrantV1(_StrictModel):
    execution_id: Identity
    actor_id: Identity
    conversation_id: Identity
    deadline_at: AwareDatetime
    idempotency_key: Identity


class ReleaseTurnAccessGrantV1(_StrictModel):
    execution_id: Identity
    grant_ref: OpaqueRef
    idempotency_key: Identity


class GrantDocumentResourceV1(_StrictModel):
    resource_ref: OpaqueRef
    lifecycle_epoch: int = Field(ge=1)
    document_version_ref: OpaqueRef
    processing_generation_ref: OpaqueRef
    index_generation_ref: OpaqueRef
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    modalities: list[Literal["text", "table", "figure"]] = Field(min_length=1, max_length=3)
    tags: list[str] = Field(max_length=50)
    language: str | None = Field(max_length=50)
    created_at_label: str | None = Field(max_length=100)
    searchable_content: str = Field(max_length=8000)
    version_label: str | None = Field(max_length=200)


class MaterializeGrantDocumentResourcesV1(_StrictModel):
    execution_id: Identity
    grant_ref: OpaqueRef
    authorization_revision: int = Field(ge=1)
    resources: list[GrantDocumentResourceV1] = Field(max_length=1000)
    idempotency_key: Identity


class GrantDocumentResourceSnapshotV1(_StrictModel):
    grant_ref: OpaqueRef
    authorization_revision: int = Field(ge=1)
    resources: list[GrantDocumentResourceV1] = Field(max_length=1000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class LineageResourceV1(_StrictModel):
    resource_ref: OpaqueRef
    resource_kind: Literal["document", "turn", "summary", "evidence", "citation"]
    lifecycle_epoch: int = Field(ge=1)
    version_ref: OpaqueRef | None = None
    generation_ref: OpaqueRef | None = None
    processing_generation_ref: OpaqueRef | None = None
    index_generation_ref: OpaqueRef | None = None


class VisibilityDecisionV1(_StrictModel):
    resource_ref: OpaqueRef
    decision: Literal["visible", "hidden"]
    reason: Literal[
        "authorized",
        "access_revoked",
        "resource_inactive",
        "version_changed",
        "generation_changed",
        "dependency_hidden",
    ]


class CurrentGrantAuthorizationSnapshotV1(_StrictModel):
    """Current external authority snapshot used to mint an immutable grant."""

    actor_id: Identity
    conversation_id: Identity
    authorization_revision: int = Field(ge=1)
    snapshot_ref: OpaqueRef
    authorized: bool


class CurrentResourceAuthorizationSnapshotV1(_StrictModel):
    """Current ACL and exact lineage for one canonical document resource."""

    actor_id: Identity
    resource_ref: OpaqueRef
    resource_kind: Literal["document"] = "document"
    authorization_revision: int = Field(ge=1)
    snapshot_ref: OpaqueRef
    authorized: bool
    active: bool
    lifecycle_epoch: int | None = Field(default=None, ge=1)
    version_ref: OpaqueRef | None = None
    generation_ref: OpaqueRef | None = None
    processing_generation_ref: OpaqueRef | None = None
    index_generation_ref: OpaqueRef | None = None


class CurrentResourceAuthorizationReader(Protocol):
    """Reads current ACL/currentness outside Authorization-owned transactions."""

    def current_grant_authorization(
        self, *, actor_id: Identity, conversation_id: Identity
    ) -> CurrentGrantAuthorizationSnapshotV1: ...

    def current_resource_authorizations(
        self, *, actor_id: Identity, resource_refs: tuple[OpaqueRef, ...]
    ) -> tuple[CurrentResourceAuthorizationSnapshotV1, ...]: ...


class GrantDocumentResourceOwner(Protocol):
    def materialize_grant_document_resources(
        self, command: MaterializeGrantDocumentResourcesV1
    ) -> GrantDocumentResourceSnapshotV1: ...

    def grant_document_resources(
        self, *, execution_id: Identity, grant_ref: OpaqueRef
    ) -> GrantDocumentResourceSnapshotV1: ...

    def current_grant_document_resources(
        self, *, execution_id: Identity, grant_ref: OpaqueRef
    ) -> GrantDocumentResourceSnapshotV1: ...


class AuthorizationOwner(GrantDocumentResourceOwner, Protocol):
    def create_grant(self, command: CreateTurnAccessGrantV1) -> TurnAccessGrantRefV1: ...

    def release_grant(self, command: ReleaseTurnAccessGrantV1) -> None: ...

    def release_execution_grant(
        self, *, execution_id: Identity, idempotency_key: Identity
    ) -> None: ...

    def current_visibility(
        self,
        *,
        actor_id: Identity,
        resources: list[LineageResourceV1],
    ) -> list[VisibilityDecisionV1]: ...


__all__ = [
    "AuthorizationOwner",
    "CreateTurnAccessGrantV1",
    "CurrentGrantAuthorizationSnapshotV1",
    "CurrentResourceAuthorizationReader",
    "CurrentResourceAuthorizationSnapshotV1",
    "GrantDocumentResourceSnapshotV1",
    "GrantDocumentResourceV1",
    "GrantDocumentResourceOwner",
    "LineageResourceV1",
    "ReleaseTurnAccessGrantV1",
    "MaterializeGrantDocumentResourcesV1",
    "TurnAccessGrantRefV1",
    "VisibilityDecisionV1",
]
