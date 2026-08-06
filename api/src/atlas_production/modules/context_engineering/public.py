from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnInputProjectionV1(_StrictModel):
    projection_ref: OpaqueRef
    execution_id: Identity
    original_user_input: str = Field(min_length=1, max_length=50000)
    resolver_output: str | None = Field(
        default=None, min_length=1, max_length=50000
    )
    rewritten_user_input: str | None = Field(
        default=None, min_length=1, max_length=50000
    )
    resolver_invocation_ref: OpaqueRef | None = None
    rewrite_invocation_ref: OpaqueRef | None = None
    resolver_failure_code: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    rewrite_failure_code: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CreateTurnInputProjectionV1(_StrictModel):
    projection_ref: OpaqueRef
    execution_id: Identity
    original_user_input: str = Field(min_length=1, max_length=50000)


class RecordResolverProjectionV1(_StrictModel):
    execution_id: Identity
    resolver_output: str | None = Field(
        default=None, min_length=1, max_length=50000
    )
    resolver_invocation_ref: OpaqueRef | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_one_resolver_outcome(self) -> "RecordResolverProjectionV1":
        if (self.resolver_output is None) == (self.failure_code is None):
            raise ValueError("resolver stage requires exactly one output or failure")
        return self


class RecordRewriteProjectionV1(_StrictModel):
    execution_id: Identity
    rewritten_user_input: str | None = Field(
        default=None, min_length=1, max_length=50000
    )
    rewrite_invocation_ref: OpaqueRef | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_one_rewrite_outcome(self) -> "RecordRewriteProjectionV1":
        if (self.rewritten_user_input is None) == (self.failure_code is None):
            raise ValueError("rewrite stage requires exactly one output or failure")
        return self


class ModelUserTextSegmentV3(_StrictModel):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=50000)


class ModelUserInputV3(_StrictModel):
    role: Literal["user"] = "user"
    content_segments: list[ModelUserTextSegmentV3] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_bounded_combined_text(self) -> "ModelUserInputV3":
        if len("\n".join(segment.text for segment in self.content_segments)) > 50000:
            raise ValueError("combined current user input exceeds 50000 characters")
        return self

    def as_text(self) -> str:
        return "\n".join(segment.text for segment in self.content_segments)


class ContextMessageV3(_StrictModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=50000)
    verification_status: Literal[
        "verified", "partially_verified", "unverified", "not_applicable"
    ] = "not_applicable"


class ContextExchangeV3(_StrictModel):
    logical_turn_id: Identity
    representative_turn_id: Identity
    representative_content_digest: Digest
    user_message: ContextMessageV3
    assistant_message: ContextMessageV3 | None = None
    direct_document_ids: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_roles_and_unique_resources(self) -> "ContextExchangeV3":
        if self.user_message.role != "user":
            raise ValueError("exchange user_message must use the user role")
        if self.assistant_message is not None and self.assistant_message.role != "assistant":
            raise ValueError("exchange assistant_message must use the assistant role")
        if len(set(self.direct_document_ids)) != len(self.direct_document_ids):
            raise ValueError("exchange direct document ids contain duplicates")
        return self


class ContextSummarySourceV3(_StrictModel):
    logical_turn_id: Identity
    representative_turn_id: Identity
    representative_content_digest: Digest
    direct_document_ids: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_resources(self) -> "ContextSummarySourceV3":
        if len(set(self.direct_document_ids)) != len(self.direct_document_ids):
            raise ValueError("summary source direct document ids contain duplicates")
        return self


class ContextSummaryV4(_StrictModel):
    summary_ref: OpaqueRef
    schema_version: Literal["context-summary-v4"] = "context-summary-v4"
    parent_summary_ref: OpaqueRef | None = None
    historical_user_context: str = Field(max_length=50000)
    assistant_pending_verification_context: str = Field(max_length=50000)
    token_count: int = Field(ge=1, le=6000)
    sources: list[ContextSummarySourceV3]
    digest: Digest

    @model_validator(mode="after")
    def require_bounded_combined_text(self) -> "ContextSummaryV4":
        combined = self.historical_user_context + self.assistant_pending_verification_context
        if not combined:
            raise ValueError("summary content must not be empty")
        if len(combined) > 50000:
            raise ValueError("combined summary content exceeds 50000 characters")
        return self


class ContextSummaryInputV4(_StrictModel):
    summary_ref: OpaqueRef
    parent_summary_ref: OpaqueRef | None = None
    historical_user_context: str = Field(max_length=50000)
    assistant_pending_verification_context: str = Field(max_length=50000)
    token_count: int = Field(ge=1, le=6000)
    sources: list[ContextSummarySourceV3]

    @model_validator(mode="after")
    def require_bounded_combined_text(self) -> "ContextSummaryInputV4":
        combined = self.historical_user_context + self.assistant_pending_verification_context
        if not combined:
            raise ValueError("summary content must not be empty")
        if len(combined) > 50000:
            raise ValueError("combined summary content exceeds 50000 characters")
        return self


class ContextLineageEdgeV3(_StrictModel):
    dependent_turn_id: Identity
    dependent_context_pack_ref: OpaqueRef
    source_turn_id: Identity
    source_resource_ref: OpaqueRef | None = None
    source_resource_kind: Literal["turn", "summary", "document", "evidence", "citation"]
    dependency_kind: Literal["recent_turn", "summary_source", "knowledge_hint"]
    lifecycle_epoch: int | None = Field(default=None, ge=1)
    version_ref: OpaqueRef | None = None
    generation_ref: OpaqueRef | None = None


class ContextLineageGraphV3(_StrictModel):
    candidate_turn_ids: list[Identity] = Field(max_length=500)
    edges: list[ContextLineageEdgeV3] = Field(max_length=2000)

    @model_validator(mode="after")
    def require_known_dependents(self) -> "ContextLineageGraphV3":
        candidates = set(self.candidate_turn_ids)
        if any(edge.dependent_turn_id not in candidates for edge in self.edges):
            raise ValueError("lineage edge dependent is not a candidate turn")
        return self


class ContextPackV3(_StrictModel):
    context_pack_ref: OpaqueRef
    schema_version: Literal["context-pack-v3"] = "context-pack-v3"
    execution_id: Identity
    input_projection_ref: OpaqueRef
    model_user_input: str = Field(min_length=1, max_length=50000)
    recent_tail: list[ContextExchangeV3]
    summary: ContextSummaryV4 | None = None
    dependencies: list[ContextLineageEdgeV3] = Field(max_length=2000)
    token_budget: int = Field(ge=1)
    digest: Digest
    created_at: AwareDatetime


class MaterializeContextPackV3(_StrictModel):
    context_pack_ref: OpaqueRef
    execution_id: Identity
    input_projection_ref: OpaqueRef
    conversation_id: Identity
    dependent_turn_id: Identity
    model_user_input: ModelUserInputV3
    recent_tail: list[ContextExchangeV3]
    summary: ContextSummaryInputV4 | None = None
    source_lineage: list[ContextLineageEdgeV3] = Field(max_length=2000)
    token_budget: int = Field(ge=1)
    idempotency_key: Identity

    @model_validator(mode="after")
    def require_owned_lineage_target(self) -> "MaterializeContextPackV3":
        if any(
            edge.dependent_turn_id != self.dependent_turn_id
            or edge.dependent_context_pack_ref != self.context_pack_ref
            for edge in self.source_lineage
        ):
            raise ValueError("lineage edge dependent/context target does not match command")
        representative_ids = {
            exchange.representative_turn_id for exchange in self.recent_tail
        }
        if len(representative_ids) != len(self.recent_tail):
            raise ValueError("recent tail contains duplicate representative turns")
        recent_lineage = [
            edge for edge in self.source_lineage if edge.dependency_kind == "recent_turn"
        ]
        if (
            len(recent_lineage) != len(representative_ids)
            or {edge.source_turn_id for edge in recent_lineage} != representative_ids
            or any(
                edge.source_resource_kind != "turn" or edge.source_resource_ref is not None
                for edge in recent_lineage
            )
        ):
            raise ValueError("recent exchanges require exact base lineage edges")
        summary_ids = (
            {source.representative_turn_id for source in self.summary.sources}
            if self.summary is not None
            else set()
        )
        if self.summary is not None and len(summary_ids) != len(self.summary.sources):
            raise ValueError("summary sources contain duplicate representative turns")
        summary_lineage = [
            edge for edge in self.source_lineage if edge.dependency_kind == "summary_source"
        ]
        if (
            len(summary_lineage) != len(summary_ids)
            or {edge.source_turn_id for edge in summary_lineage} != summary_ids
            or any(
                edge.source_resource_kind != "summary"
                or self.summary is None
                or edge.source_resource_ref != self.summary.summary_ref
                for edge in summary_lineage
            )
        ):
            raise ValueError("summary sources require exact base lineage edges")
        return self


class ReleaseContextPackV3(_StrictModel):
    release_ref: OpaqueRef
    execution_id: Identity
    context_pack_ref: OpaqueRef
    idempotency_key: Identity


class ContextPackReleaseV3(_StrictModel):
    release_ref: OpaqueRef
    execution_id: Identity
    context_pack_ref: OpaqueRef
    schema_version: Literal["context-pack-release-v3"] = "context-pack-release-v3"
    released_at: AwareDatetime


class ContextEngineeringReader(Protocol):
    def get(self, context_pack_ref: OpaqueRef) -> ContextPackV3 | None: ...

    def lineage_graph(self, turn_ids: list[Identity]) -> ContextLineageGraphV3: ...


class ContextEngineeringOwner(ContextEngineeringReader, Protocol):
    def materialize(self, command: MaterializeContextPackV3) -> ContextPackV3: ...

    def release(self, command: ReleaseContextPackV3) -> ContextPackReleaseV3: ...

    def release_execution_context(
        self, *, execution_id: Identity, idempotency_key: Identity
    ) -> None: ...


class TurnInputProjectionAuditReader(Protocol):
    def get_input_projection(
        self, execution_id: Identity
    ) -> TurnInputProjectionV1 | None: ...


class TurnInputProjectionOwner(TurnInputProjectionAuditReader, Protocol):
    def create_input_projection(
        self, command: CreateTurnInputProjectionV1
    ) -> TurnInputProjectionV1: ...

    def record_resolver_projection(
        self, command: RecordResolverProjectionV1
    ) -> TurnInputProjectionV1: ...

    def record_rewrite_projection(
        self, command: RecordRewriteProjectionV1
    ) -> TurnInputProjectionV1: ...


__all__ = [
    "CreateTurnInputProjectionV1",
    "ContextEngineeringOwner",
    "ContextEngineeringReader",
    "ContextExchangeV3",
    "ContextLineageEdgeV3",
    "ContextLineageGraphV3",
    "ContextMessageV3",
    "ContextPackReleaseV3",
    "ContextPackV3",
    "ContextSummaryInputV4",
    "ContextSummarySourceV3",
    "ContextSummaryV4",
    "ModelUserInputV3",
    "ModelUserTextSegmentV3",
    "MaterializeContextPackV3",
    "RecordResolverProjectionV1",
    "RecordRewriteProjectionV1",
    "ReleaseContextPackV3",
    "TurnInputProjectionAuditReader",
    "TurnInputProjectionOwner",
    "TurnInputProjectionV1",
]
