from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from atlas_production.modules.identity_access.records import UserRecord


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=12_000)]
ReviewStatus = Literal[
    "pending",
    "reviewing",
    "retryable_failed",
    "completed",
    "completed_no_cases",
    "superseded",
    "failed",
]
TerminalStatus = Literal["completed", "failed"]

SCHEMA_VERSION = "conversation-review-v1"
REVIEW_PROMPT_REVISION = "conversation-review-triage-v1"
SEMANTIC_QUIET_PERIOD = timedelta(hours=2)
MAX_CASES = 3
MAX_CANONICAL_CASE_BYTES = 65_536


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def conversation_review_snapshot_digest(
    *,
    conversation_id: str,
    conversation_updated_at: AwareDatetime,
    expected_next_ordinal: int,
    latest_semantic_activity_at: AwareDatetime,
    turns: list["ConversationReviewSnapshotTurnV1"],
) -> str:
    projection = {
        "conversation_id": conversation_id,
        "conversation_updated_at": conversation_updated_at.isoformat(),
        "expected_next_ordinal": expected_next_ordinal,
        "latest_semantic_activity_at": latest_semantic_activity_at.isoformat(),
        "turns": [turn.model_dump(mode="json") for turn in turns],
    }
    return hashlib.sha256(_canonical(projection)).hexdigest()


def conversation_review_ref(
    *, conversation_id: str, snapshot_digest: str, review_prompt_revision: str
) -> str:
    identity = {
        "conversation_id": conversation_id,
        "review_prompt_revision": review_prompt_revision,
        "snapshot_digest": snapshot_digest,
    }
    return f"conversation-review:{hashlib.sha256(_canonical(identity)).hexdigest()}:v1"


class ConversationReviewSnapshotTurnV1(_StrictModel):
    position: int = Field(ge=1)
    turn_id: Identity
    execution_id: Identity
    retry_of_turn_id: Identity | None = None
    input_projection_ref: OpaqueRef
    user_text_digest: Digest
    terminal_status: TerminalStatus
    terminal_scan_sequence: int = Field(ge=1)
    terminal_commit_intent_ref: OpaqueRef | None = None
    terminal_committed_at: AwareDatetime
    governed_answer_draft_ref: OpaqueRef | None = None
    governed_answer_digest: Digest | None = None

    @model_validator(mode="after")
    def require_terminal_shape(self) -> "ConversationReviewSnapshotTurnV1":
        assistant_values = (
            self.terminal_commit_intent_ref,
            self.governed_answer_draft_ref,
            self.governed_answer_digest,
        )
        if self.terminal_status == "completed":
            if any(value is None for value in assistant_values):
                raise ValueError("completed snapshot turn requires governed answer lineage")
        elif any(value is not None for value in assistant_values):
            raise ValueError("failed snapshot turn cannot carry assistant answer lineage")
        if self.retry_of_turn_id == self.turn_id:
            raise ValueError("snapshot turn cannot retry itself")
        return self


class ConversationReviewSnapshotV1(_StrictModel):
    review_ref: OpaqueRef
    schema_version: Literal["conversation-review-v1"] = SCHEMA_VERSION
    review_prompt_revision: Literal["conversation-review-triage-v1"] = (
        REVIEW_PROMPT_REVISION
    )
    conversation_id: Identity
    conversation_updated_at: AwareDatetime
    expected_next_ordinal: int = Field(ge=1)
    latest_semantic_activity_at: AwareDatetime
    eligible_at: AwareDatetime
    snapshot_digest: Digest
    turns: list[ConversationReviewSnapshotTurnV1] = Field(min_length=1)

    @model_validator(mode="after")
    def require_exact_identity(self) -> "ConversationReviewSnapshotV1":
        positions = [turn.position for turn in self.turns]
        if positions != list(range(1, len(self.turns) + 1)):
            raise ValueError("snapshot turn positions must be contiguous and ordered")
        if len({turn.turn_id for turn in self.turns}) != len(self.turns):
            raise ValueError("snapshot turn ids must be unique")
        if len({turn.execution_id for turn in self.turns}) != len(self.turns):
            raise ValueError("snapshot execution ids must be unique")
        if self.expected_next_ordinal != len(self.turns) + 1:
            raise ValueError("snapshot next ordinal must bind membership cardinality")
        if self.eligible_at != self.latest_semantic_activity_at + SEMANTIC_QUIET_PERIOD:
            raise ValueError("snapshot eligibility must follow the semantic quiet period")
        expected_digest = conversation_review_snapshot_digest(
            conversation_id=self.conversation_id,
            conversation_updated_at=self.conversation_updated_at,
            expected_next_ordinal=self.expected_next_ordinal,
            latest_semantic_activity_at=self.latest_semantic_activity_at,
            turns=self.turns,
        )
        if self.snapshot_digest != expected_digest:
            raise ValueError("snapshot digest does not bind the source projection")
        expected_ref = conversation_review_ref(
            conversation_id=self.conversation_id,
            snapshot_digest=self.snapshot_digest,
            review_prompt_revision=self.review_prompt_revision,
        )
        if self.review_ref != expected_ref:
            raise ValueError("review ref does not bind the review identity")
        return self


class ConversationLearningCaseProposalV1(_StrictModel):
    case_ordinal: int = Field(ge=1, le=MAX_CASES)
    title: Annotated[str, Field(min_length=1, max_length=500)]
    learning_evidence: BoundedText
    generalization_hypothesis: BoundedText
    investigation_question: BoundedText
    selection_rationale: BoundedText
    involved_turn_ids: list[Identity] = Field(min_length=2)
    primary_assistant_turn_id: Identity

    @model_validator(mode="after")
    def require_grounded_shape(self) -> "ConversationLearningCaseProposalV1":
        text_values = (
            self.title,
            self.learning_evidence,
            self.generalization_hypothesis,
            self.investigation_question,
            self.selection_rationale,
        )
        if any(not value.strip() for value in text_values):
            raise ValueError("case natural-language fields cannot be blank")
        if len(set(self.involved_turn_ids)) != len(self.involved_turn_ids):
            raise ValueError("case involved turns must be unique")
        if self.primary_assistant_turn_id not in self.involved_turn_ids:
            raise ValueError("case primary assistant turn must be involved")
        return self


class ConversationReviewProposalV1(_StrictModel):
    cases: list[ConversationLearningCaseProposalV1] = Field(
        default_factory=list, max_length=MAX_CASES
    )

    @model_validator(mode="after")
    def require_ordered_cases(self) -> "ConversationReviewProposalV1":
        ordinals = [case.case_ordinal for case in self.cases]
        if ordinals != list(range(1, len(self.cases) + 1)):
            raise ValueError("case ordinals must be contiguous and ordered")
        projection = [case.model_dump(mode="json") for case in self.cases]
        if len(_canonical(projection)) > MAX_CANONICAL_CASE_BYTES:
            raise ValueError("conversation review output exceeds canonical byte limit")
        return self


class ConversationReviewClaimV1(_StrictModel):
    review_ref: OpaqueRef
    attempt: int = Field(ge=1)
    fence: int = Field(ge=1)
    claim_token: Identity
    lease_expires_at: AwareDatetime
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_complete_route_pin(self) -> "ConversationReviewClaimV1":
        route_values = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route_values) != all(
            value is None for value in route_values
        ):
            raise ValueError("review route pin must be wholly present or absent")
        return self


class ConversationReviewCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    review_ref: OpaqueRef


class ConversationReviewV1(_StrictModel):
    snapshot: ConversationReviewSnapshotV1
    status: ReviewStatus
    attempt: int = Field(ge=0)
    fence: int = Field(ge=0)
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)
    model_invocation_refs: list[OpaqueRef] = Field(default_factory=list)
    failure_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    cases: list[ConversationLearningCaseProposalV1] = Field(
        default_factory=list, max_length=MAX_CASES
    )
    review_digest: Digest | None = None
    scan_sequence: int | None = Field(default=None, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_lifecycle_shape(self) -> "ConversationReviewV1":
        route_values = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route_values) != all(
            value is None for value in route_values
        ):
            raise ValueError("persisted route pin must be wholly present or absent")
        terminal = self.status in {"completed", "completed_no_cases"}
        if terminal:
            if (
                self.review_digest is None
                or self.completed_at is None
                or self.scan_sequence is None
            ):
                raise ValueError(
                    "completed review requires digest, sequence, and completion time"
                )
            if any(value is None for value in route_values):
                raise ValueError("completed review requires pinned route provenance")
            if not self.model_invocation_refs:
                raise ValueError("completed review requires model invocation provenance")
            if self.failure_code is not None:
                raise ValueError("completed review cannot carry a failure code")
            if self.status == "completed_no_cases" and self.cases:
                raise ValueError("completed-no-cases review cannot carry cases")
            if self.status == "completed" and not self.cases:
                raise ValueError("completed review requires cases")
        elif (
            self.cases
            or self.review_digest is not None
            or self.completed_at is not None
            or self.scan_sequence is not None
        ):
            raise ValueError("non-completed review cannot expose a result")
        if self.status in {"retryable_failed", "failed"}:
            if self.failure_code is None:
                raise ValueError("failed review status requires a safe failure code")
        elif self.failure_code is not None:
            raise ValueError("non-failed review status cannot carry a failure code")
        return self


class ConversationLearningSettingsV1(_StrictModel):
    enabled: bool = Field(strict=True)
    settings_revision: int = Field(ge=1, strict=True)
    updated_actor_id: Identity
    updated_at: AwareDatetime


class ConversationLearningSettingsUpdateRequestV1(_StrictModel):
    enabled: bool = Field(strict=True)
    expected_settings_revision: int = Field(ge=1, strict=True)
    idempotency_key: Identity


class ConversationLearningSettingsError(RuntimeError):
    def __init__(self, error_code: str, message_code: str, status_code: int) -> None:
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code


class ConversationReviewOwner(Protocol):
    def register_snapshot(
        self, snapshot: ConversationReviewSnapshotV1
    ) -> ConversationReviewV1: ...

    def claim_next(
        self, worker_id: Identity, observed_at: AwareDatetime, lease_seconds: int = 300
    ) -> ConversationReviewClaimV1 | None: ...

    def pin_route(
        self,
        claim: ConversationReviewClaimV1,
        route_id: Identity,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: AwareDatetime,
    ) -> ConversationReviewClaimV1: ...

    def renew_claim(
        self,
        claim: ConversationReviewClaimV1,
        observed_at: AwareDatetime,
        lease_seconds: int = 300,
    ) -> ConversationReviewClaimV1: ...

    def complete(
        self,
        claim: ConversationReviewClaimV1,
        proposal: ConversationReviewProposalV1,
        model_invocation_refs: list[OpaqueRef],
        observed_at: AwareDatetime,
    ) -> ConversationReviewV1: ...

    def fail(
        self,
        claim: ConversationReviewClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: AwareDatetime,
    ) -> ConversationReviewV1: ...

    def supersede(
        self, claim: ConversationReviewClaimV1, observed_at: AwareDatetime
    ) -> ConversationReviewV1: ...

    def read(self, review_ref: OpaqueRef) -> ConversationReviewV1 | None: ...

    def latest_completed_for_conversation(
        self, conversation_id: Identity
    ) -> ConversationReviewV1 | None: ...

    def list_after(
        self, cursor: ConversationReviewCursorV1 | None, limit: int
    ) -> list[ConversationReviewV1]: ...

    def get_learning_settings(self) -> ConversationLearningSettingsV1: ...

    def update_learning_settings(
        self,
        actor_id: Identity,
        payload: ConversationLearningSettingsUpdateRequestV1,
    ) -> ConversationLearningSettingsV1: ...


class ConversationLearningSettingsService:
    def __init__(self, owner: ConversationReviewOwner) -> None:
        self._owner = owner

    @staticmethod
    def _admin(actor: UserRecord | None) -> UserRecord:
        if actor is None or not actor.active or actor.system_role != "admin":
            raise ConversationLearningSettingsError(
                "access_denied",
                "permission.admin_permission_is_required",
                403,
            )
        return actor

    def get(self, actor: UserRecord | None) -> ConversationLearningSettingsV1:
        self._admin(actor)
        return self._owner.get_learning_settings()

    def update(
        self,
        actor: UserRecord | None,
        payload: ConversationLearningSettingsUpdateRequestV1,
    ) -> ConversationLearningSettingsV1:
        admin = self._admin(actor)
        return self._owner.update_learning_settings(admin.actor_id, payload)


__all__ = [
    "ConversationLearningSettingsError",
    "ConversationLearningSettingsService",
    "ConversationLearningSettingsUpdateRequestV1",
    "ConversationLearningSettingsV1",
    "ConversationLearningCaseProposalV1",
    "ConversationReviewClaimV1",
    "ConversationReviewCursorV1",
    "ConversationReviewOwner",
    "ConversationReviewProposalV1",
    "ConversationReviewSnapshotTurnV1",
    "ConversationReviewSnapshotV1",
    "ConversationReviewV1",
    "MAX_CANONICAL_CASE_BYTES",
    "REVIEW_PROMPT_REVISION",
    "SCHEMA_VERSION",
    "SEMANTIC_QUIET_PERIOD",
    "conversation_review_ref",
    "conversation_review_snapshot_digest",
]
