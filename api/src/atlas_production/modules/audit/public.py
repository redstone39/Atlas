from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from atlas_production.modules.result_governance.public import (
    EvidenceReviewStatusV2,
    RetrievalStatusV1,
    VerificationStatusV1,
)

from .service import (
    SENSITIVE_AUDIT_KEY_FRAGMENTS,
    audit_event_status,
    safe_audit_metadata,
    safe_audit_value,
)

from .api_models import (
    AuditEvent,
    AuditEventList,
)


class _StrictTurnAuditDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TurnAuditStepV1(_StrictTurnAuditDraftModel):
    ordinal: int = Field(ge=1)
    step_kind: Literal["model", "tool", "governance", "citation", "terminal"]
    operation: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "failed", "replayed", "skipped"]
    safe_input_digest: Digest
    result_ref: OpaqueRef | None = None
    result_digest: Digest | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0, le=40)


class MaterializeTurnAuditDraftV1(_StrictTurnAuditDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    claimed_evidence_handles: list[Identity] = Field(max_length=100)
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: Digest
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    citation_binding_draft_ref: OpaqueRef
    citation_binding_digest: Digest
    retrieval_status: RetrievalStatusV1
    verification_status: VerificationStatusV1
    terminal_status: Literal["terminal_completed"]
    steps: list[TurnAuditStepV1] = Field(max_length=40)
    idempotency_key: Identity


class TurnAuditDraftV1(_StrictTurnAuditDraftModel):
    draft_ref: OpaqueRef
    schema_version: Literal["turn-audit-draft-v1"] = "turn-audit-draft-v1"
    execution_id: Identity
    claimed_evidence_handles: list[Identity]
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: Digest
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    citation_binding_draft_ref: OpaqueRef
    citation_binding_digest: Digest
    retrieval_status: RetrievalStatusV1
    verification_status: VerificationStatusV1
    terminal_status: Literal["terminal_completed"]
    steps: list[TurnAuditStepV1]
    digest: Digest
    created_at: AwareDatetime


class MaterializeTurnAuditDraftV2(_StrictTurnAuditDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    claimed_evidence_handles: list[Identity] = Field(max_length=100)
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: Digest
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    citation_binding_draft_ref: OpaqueRef
    citation_binding_digest: Digest
    retrieval_status: RetrievalStatusV1
    evidence_review_status: EvidenceReviewStatusV2
    terminal_status: Literal["terminal_completed"]
    steps: list[TurnAuditStepV1] = Field(max_length=40)
    idempotency_key: Identity


class TurnAuditDraftV2(_StrictTurnAuditDraftModel):
    draft_ref: OpaqueRef
    schema_version: Literal["turn-audit-draft-v2"] = "turn-audit-draft-v2"
    execution_id: Identity
    claimed_evidence_handles: list[Identity]
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: Digest
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    citation_binding_draft_ref: OpaqueRef
    citation_binding_digest: Digest
    retrieval_status: RetrievalStatusV1
    evidence_review_status: EvidenceReviewStatusV2
    terminal_status: Literal["terminal_completed"]
    steps: list[TurnAuditStepV1]
    digest: Digest
    created_at: AwareDatetime


class ReleaseTurnAuditDraftV1(_StrictTurnAuditDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    idempotency_key: Identity


class TurnAuditDraftReleaseV1(_StrictTurnAuditDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    schema_version: Literal["turn-audit-draft-release-v1"] = "turn-audit-draft-release-v1"
    released_at: AwareDatetime


class TurnAuditDraftOwner(Protocol):
    def materialize(self, command: MaterializeTurnAuditDraftV1) -> TurnAuditDraftV1: ...

    def read(self, draft_ref: OpaqueRef) -> TurnAuditDraftV1 | None: ...

    def release(self, command: ReleaseTurnAuditDraftV1) -> TurnAuditDraftReleaseV1: ...


class TurnAuditDraftOwnerV2(Protocol):
    def materialize_v2(self, command: MaterializeTurnAuditDraftV2) -> TurnAuditDraftV2: ...

    def read_v2(self, draft_ref: OpaqueRef) -> TurnAuditDraftV2 | None: ...


__all__ = [
    "SENSITIVE_AUDIT_KEY_FRAGMENTS",
    "audit_event_status",
    "safe_audit_metadata",
    "safe_audit_value",
    "AuditEvent",
    "AuditEventList",
    "MaterializeTurnAuditDraftV1",
    "MaterializeTurnAuditDraftV2",
    "ReleaseTurnAuditDraftV1",
    "TurnAuditDraftOwner",
    "TurnAuditDraftOwnerV2",
    "TurnAuditDraftReleaseV1",
    "TurnAuditDraftV1",
    "TurnAuditDraftV2",
    "TurnAuditStepV1",
]
