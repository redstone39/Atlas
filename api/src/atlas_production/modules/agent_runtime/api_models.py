from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]




class AgentResearchScopeRefV1(_StrictModel):
    kind: Literal["project", "team"]
    id: Identity


class AllAuthorizedResearchScopeV1(_StrictModel):
    mode: Literal["all_authorized"] = "all_authorized"


class SelectedResearchScopeV1(_StrictModel):
    mode: Literal["selected"] = "selected"
    refs: list[AgentResearchScopeRefV1] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_refs(self) -> "SelectedResearchScopeV1":
        identities = [(ref.kind, ref.id) for ref in self.refs]
        if len(identities) != len(set(identities)):
            raise ValueError("selected research scope refs must be unique")
        return self


AgentResearchScopeV1 = Annotated[
    AllAuthorizedResearchScopeV1 | SelectedResearchScopeV1,
    Field(discriminator="mode"),
]


class StartAgentResearchV1(_StrictModel):
    question: str = Field(min_length=1, max_length=12_000)
    idempotency_key: str = Field(min_length=16, max_length=128)
    scope: AgentResearchScopeV1
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"] = (
        "evidence_packet"
    )

    @model_validator(mode="after")
    def reject_blank_question(self) -> "StartAgentResearchV1":
        if not self.question.strip():
            raise ValueError("research question must contain non-whitespace text")
        return self

    def canonical_payload_digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"idempotency_key"})
        scope = payload["scope"]
        if scope["mode"] == "selected":
            scope["refs"] = sorted(
                scope["refs"],
                key=lambda item: (item["kind"], item["id"]),
            )
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AcceptedScopeSnapshotV1(_StrictModel):
    scope_ref: OpaqueRef
    scope_digest: Digest
    project_ids: list[Identity] = Field(min_length=1, max_length=10_000)
    requested_refs: list[AgentResearchScopeRefV1] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def require_canonical_projects(self) -> "AcceptedScopeSnapshotV1":
        if self.project_ids != sorted(set(self.project_ids)):
            raise ValueError("accepted project ids must be sorted and unique")
        return self


class AcceptedResearchSnapshotV1(_StrictModel):
    scope: AcceptedScopeSnapshotV1
    grant_ref: OpaqueRef
    grant_digest: Digest
    catalog_ref: OpaqueRef
    catalog_digest: Digest
    policy_ref: OpaqueRef
    policy_digest: Digest
    budget_ref: OpaqueRef
    budget_digest: Digest


class ResearchEvidenceDescriptorV1(_StrictModel):
    evidence_id: Identity
    kind: Literal["text", "visual", "native"]
    title: str = Field(min_length=1, max_length=500)
    page: int | None = Field(default=None, ge=1)
    locator: str = Field(min_length=1, max_length=1_000)
    available_representations: list[Literal["text", "visual", "native"]] = Field(
        min_length=1, max_length=3
    )
    lineage_digest: Digest

    @model_validator(mode="after")
    def require_unique_representations(self) -> "ResearchEvidenceDescriptorV1":
        if len(self.available_representations) != len(
            set(self.available_representations)
        ):
            raise ValueError("evidence representations must be unique")
        return self


class ResearchFindingV1(_StrictModel):
    finding_id: Identity
    text: str = Field(min_length=1, max_length=12_000)
    evidence_ids: list[Identity] = Field(default_factory=list, max_length=100)
    evidence_assessment: Literal["aligned", "conflict", "insufficient"]

    @model_validator(mode="after")
    def require_evidence_shape(self) -> "ResearchFindingV1":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("finding evidence ids must be unique")
        if self.evidence_assessment in {"aligned", "conflict"} and not self.evidence_ids:
            raise ValueError("aligned or conflicting finding requires evidence")
        return self


class ResearchLimitV1(_StrictModel):
    code: Identity
    detail: str = Field(min_length=1, max_length=2_000)


class ResearchPacketV1(_StrictModel):
    schema_version: Literal["atlas-research-packet-v1"] = "atlas-research-packet-v1"
    research_id: Identity
    execution_id: Identity
    question_ref: OpaqueRef
    scope_ref: OpaqueRef
    scope_digest: Digest
    findings: list[ResearchFindingV1] = Field(min_length=1, max_length=100)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=100)
    research_limits: list[ResearchLimitV1] = Field(default_factory=list, max_length=100)
    evidence: list[ResearchEvidenceDescriptorV1] = Field(default_factory=list, max_length=1_000)
    packet_digest: Digest

    @staticmethod
    def digest_payload(payload: dict[str, object]) -> str:
        materialized = dict(payload)
        materialized.pop("packet_digest", None)
        encoded = json.dumps(
            materialized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def require_packet_integrity(self) -> "ResearchPacketV1":
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("research finding ids must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("research evidence ids must be unique")
        known = set(evidence_ids)
        if any(not set(finding.evidence_ids).issubset(known) for finding in self.findings):
            raise ValueError("finding references evidence outside the packet")
        if any(not question.strip() or len(question) > 12_000 for question in self.unresolved_questions):
            raise ValueError("unresolved questions must contain bounded non-whitespace text")
        expected = self.digest_payload(self.model_dump(mode="json"))
        if self.packet_digest != expected:
            raise ValueError("research packet digest does not match canonical payload")
        return self

    @classmethod
    def materialize(cls, **payload: object) -> "ResearchPacketV1":
        materialized = dict(payload)
        materialized.pop("packet_digest", None)
        materialized.setdefault("schema_version", "atlas-research-packet-v1")
        materialized["packet_digest"] = cls.digest_payload(materialized)
        return cls.model_validate(materialized)


class AgentResearchAnswerV1(_StrictModel):
    status: Literal["not_requested", "available", "unavailable"]
    packet_ref: OpaqueRef
    packet_digest: Digest
    governed_answer_ref: OpaqueRef | None = None
    citation_binding_ref: OpaqueRef | None = None
    unavailable_code: Identity | None = None

    @model_validator(mode="after")
    def require_answer_shape(self) -> "AgentResearchAnswerV1":
        answer_refs = (self.governed_answer_ref, self.citation_binding_ref)
        if self.status == "available":
            if any(ref is None for ref in answer_refs) or self.unavailable_code is not None:
                raise ValueError("available answer requires answer and citation refs only")
        elif self.status == "unavailable":
            if any(ref is not None for ref in answer_refs) or self.unavailable_code is None:
                raise ValueError("unavailable answer requires only unavailable_code")
        elif any(ref is not None for ref in answer_refs) or self.unavailable_code is not None:
            raise ValueError("not-requested answer cannot carry result refs")
        return self
