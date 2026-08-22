from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.learner.public import (
    LearnerExperienceCursorV1,
    LearnerExperienceReader,
    LearnerExperienceV1,
)

Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=12_000)]
FailureCode = Annotated[str, Field(min_length=1, max_length=100)]

SCHEMA_VERSION = "consolidation-v1"
CONSOLIDATOR_PROMPT_REVISION = "consolidator-generalize-v1"
MAX_CANONICAL_CONSOLIDATION_BYTES = 65_536

ConsolidationRunStatus = Literal[
    "pending", "consolidating", "retryable_failed", "completed", "failed"
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class ConsolidatorExperienceBindingV1(_StrictModel):
    experience_ref: OpaqueRef
    experience_digest: Digest
    scan_sequence: int = Field(ge=1)


class ConsolidatedExperienceV1(_StrictModel):
    behavior: BoundedText
    applicability: BoundedText
    supporting_experience_refs: list[OpaqueRef] = Field(min_length=1, max_length=10)
    counterexample_experience_refs: list[OpaqueRef] = Field(
        default_factory=list, max_length=10
    )
    unresolved_issue: BoundedText | None = None

    @model_validator(mode="after")
    def require_grounded_unique_refs(self) -> "ConsolidatedExperienceV1":
        supporting = self.supporting_experience_refs
        counterexamples = self.counterexample_experience_refs
        if len(set(supporting)) != len(supporting):
            raise ValueError("supporting Experience refs must be ordered and unique")
        if len(set(counterexamples)) != len(counterexamples):
            raise ValueError("counterexample Experience refs must be ordered and unique")
        if set(supporting).intersection(counterexamples):
            raise ValueError("supporting and counterexample Experience refs must be disjoint")
        return self


class ConsolidationPayloadV1(_StrictModel):
    source_bindings: list[ConsolidatorExperienceBindingV1] = Field(
        min_length=10, max_length=10
    )
    experiences: list[ConsolidatedExperienceV1] = Field(
        default_factory=list, max_length=64
    )

    @model_validator(mode="after")
    def require_exact_order_and_grounding(self) -> "ConsolidationPayloadV1":
        sequences = [binding.scan_sequence for binding in self.source_bindings]
        refs = [binding.experience_ref for binding in self.source_bindings]
        if len(set(refs)) != 10 or len(set(sequences)) != 10:
            raise ValueError("consolidation source bindings must be unique")
        if sequences != sorted(sequences):
            raise ValueError("consolidation source bindings must preserve scan order")
        allowed = set(refs)
        for experience in self.experiences:
            if not set(experience.supporting_experience_refs).issubset(allowed):
                raise ValueError("generalized experience cites an unknown supporting Experience")
            if not set(experience.counterexample_experience_refs).issubset(allowed):
                raise ValueError("generalized experience cites an unknown counterexample Experience")
        if len(_canonical(self.model_dump(mode="json"))) > MAX_CANONICAL_CONSOLIDATION_BYTES:
            raise ValueError("consolidation payload exceeds canonical byte limit")
        return self


def consolidation_run_ref(
    *,
    source_bindings: list[ConsolidatorExperienceBindingV1],
    schema_version: str = SCHEMA_VERSION,
    prompt_revision: str = CONSOLIDATOR_PROMPT_REVISION,
) -> str:
    if len(source_bindings) != 10:
        raise ValueError("consolidation identity requires exactly ten source bindings")
    projection = {
        "prompt_revision": prompt_revision,
        "schema_version": schema_version,
        "source_bindings": [binding.model_dump(mode="json") for binding in source_bindings],
    }
    return f"consolidation:{hashlib.sha256(_canonical(projection)).hexdigest()}:v1"


class ConsolidationV1(_StrictModel):
    consolidation_ref: OpaqueRef
    payload: ConsolidationPayloadV1
    digest: Digest
    scan_sequence: int = Field(ge=1)
    created_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def require_bound_completed_result(self) -> "ConsolidationV1":
        expected_ref = consolidation_run_ref(source_bindings=self.payload.source_bindings)
        if self.consolidation_ref != expected_ref:
            raise ValueError("consolidation ref does not bind source batch")
        expected_digest = hashlib.sha256(
            _canonical(self.payload.model_dump(mode="json"))
        ).hexdigest()
        if self.digest != expected_digest:
            raise ValueError("consolidation digest does not bind payload")
        if self.completed_at < self.created_at:
            raise ValueError("consolidation completion cannot precede creation")
        return self


class ConsolidationCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    consolidation_ref: OpaqueRef


class ConsolidationRunClaimV1(_StrictModel):
    consolidation_ref: OpaqueRef
    source_bindings: list[ConsolidatorExperienceBindingV1] = Field(
        min_length=10, max_length=10
    )
    attempt: int = Field(ge=1)
    fence: int = Field(ge=1)
    claim_token: Identity
    lease_expires_at: AwareDatetime
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_claim_shape(self) -> "ConsolidationRunClaimV1":
        expected_ref = consolidation_run_ref(source_bindings=self.source_bindings)
        if self.consolidation_ref != expected_ref:
            raise ValueError("consolidation claim does not bind source batch")
        route = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route) != all(value is None for value in route):
            raise ValueError("consolidation route pin must be wholly present or absent")
        return self


class ConsolidationRunV1(_StrictModel):
    consolidation_ref: OpaqueRef
    source_bindings: list[ConsolidatorExperienceBindingV1] = Field(
        min_length=10, max_length=10
    )
    status: ConsolidationRunStatus
    attempt: int = Field(ge=0)
    fence: int = Field(ge=0)
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)
    model_invocation_refs: list[OpaqueRef] = Field(default_factory=list, max_length=64)
    failure_code: FailureCode | None = None
    next_attempt_at: AwareDatetime | None = None
    result_digest: Digest | None = None
    result_scan_sequence: int | None = Field(default=None, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_lifecycle_shape(self) -> "ConsolidationRunV1":
        expected_ref = consolidation_run_ref(source_bindings=self.source_bindings)
        if self.consolidation_ref != expected_ref:
            raise ValueError("consolidation run does not bind source batch")
        route = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route) != all(value is None for value in route):
            raise ValueError("consolidation route pin must be wholly present or absent")
        result = (self.result_digest, self.result_scan_sequence, self.completed_at)
        if self.status == "completed":
            if (
                any(value is None for value in route)
                or any(value is None for value in result)
                or not self.model_invocation_refs
            ):
                raise ValueError("completed consolidation requires route, invocation, and result provenance")
            if self.failure_code is not None or self.next_attempt_at is not None:
                raise ValueError("completed consolidation cannot carry failure state")
        elif any(value is not None for value in result) or self.model_invocation_refs:
            raise ValueError("non-completed consolidation cannot expose a result")
        if self.status in {"retryable_failed", "failed"}:
            if self.failure_code is None:
                raise ValueError("failed consolidation requires a safe failure code")
            if self.status == "retryable_failed" and self.next_attempt_at is None:
                raise ValueError("retryable consolidation requires next attempt time")
        elif self.failure_code is not None or self.next_attempt_at is not None:
            raise ValueError("non-failed consolidation cannot carry failure state")
        return self


class ConsolidationReader(Protocol):
    def read_consolidation(self, consolidation_ref: OpaqueRef) -> ConsolidationV1 | None: ...

    def list_consolidations_after(
        self, cursor: ConsolidationCursorV1 | None, limit: int
    ) -> list[ConsolidationV1]: ...


class ConsolidatorOwner(ConsolidationReader, Protocol):
    def reserve_next(
        self, reader: LearnerExperienceReader, observed_at: AwareDatetime
    ) -> ConsolidationRunV1 | None: ...

    def claim_next(
        self, worker_id: Identity, observed_at: AwareDatetime, lease_seconds: int = 300
    ) -> ConsolidationRunClaimV1 | None: ...

    def pin_route(
        self,
        claim: ConsolidationRunClaimV1,
        route_id: Identity,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: AwareDatetime,
    ) -> ConsolidationRunClaimV1: ...

    def renew_claim(
        self,
        claim: ConsolidationRunClaimV1,
        observed_at: AwareDatetime,
        lease_seconds: int = 300,
    ) -> ConsolidationRunClaimV1: ...

    def complete(
        self,
        claim: ConsolidationRunClaimV1,
        experiences: list[ConsolidatedExperienceV1],
        model_invocation_refs: list[OpaqueRef],
        observed_at: AwareDatetime,
    ) -> ConsolidationV1: ...

    def fail(
        self,
        claim: ConsolidationRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: AwareDatetime,
    ) -> ConsolidationRunV1: ...

    def read_run(self, consolidation_ref: OpaqueRef) -> ConsolidationRunV1 | None: ...

    def read_source_experiences(
        self,
        claim: ConsolidationRunClaimV1,
        reader: LearnerExperienceReader,
    ) -> list[LearnerExperienceV1]: ...


__all__ = [
    "CONSOLIDATOR_PROMPT_REVISION",
    "MAX_CANONICAL_CONSOLIDATION_BYTES",
    "SCHEMA_VERSION",
    "ConsolidatedExperienceV1",
    "ConsolidationCursorV1",
    "ConsolidationPayloadV1",
    "ConsolidationReader",
    "ConsolidationRunClaimV1",
    "ConsolidationRunStatus",
    "ConsolidationRunV1",
    "ConsolidationV1",
    "ConsolidatorExperienceBindingV1",
    "ConsolidatorOwner",
    "consolidation_run_ref",
]
