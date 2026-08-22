from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationRow,
)
from atlas_production.modules.context_engineering.public import (
    TurnInputProjectionAuditReader,
)
from atlas_production.modules.conversation.public import (
    ConversationOwner,
    ConversationRetryLineageOwner,
    ConversationTurnCursorV1,
    ConversationTurnMemberV1,
)
from atlas_production.modules.conversation_review.public import (
    ConversationReviewClaimV1,
    ConversationReviewOwner,
    ConversationReviewProposalV1,
    ConversationReviewSnapshotTurnV1,
    ConversationReviewSnapshotV1,
    ConversationReviewV1,
    SEMANTIC_QUIET_PERIOD,
    conversation_review_ref,
    conversation_review_snapshot_digest,
)
from atlas_production.modules.result_governance.public import (
    ResultGovernanceDraftOwnerV2,
)
from atlas_production.modules.turn_runtime.public import TurnRuntimeOwner

SessionFactory = Callable[[], Session]


_PAGE_LIMIT = 100


class ConversationReviewSourceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConversationReviewTranscriptSegmentV1(_StrictModel):
    segment_id: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=12_000)


class ConversationReviewTranscriptTurnV1(_StrictModel):
    position: int = Field(ge=1)
    turn_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=200)
    retry_of_turn_id: str | None = Field(default=None, min_length=1, max_length=200)
    original_user_text: str = Field(min_length=1, max_length=50_000)
    final_governed_assistant_segments: list[
        ConversationReviewTranscriptSegmentV1
    ] | None = None
    terminal_status: str = Field(pattern=r"^(completed|failed)$")

    @model_validator(mode="after")
    def require_terminal_projection(self) -> "ConversationReviewTranscriptTurnV1":
        if (self.terminal_status == "completed") != (
            self.final_governed_assistant_segments is not None
        ):
            raise ValueError("transcript terminal status and assistant projection disagree")
        if (
            self.final_governed_assistant_segments is not None
            and not self.final_governed_assistant_segments
        ):
            raise ValueError("completed transcript turn requires governed segments")
        return self


class ConversationReviewTranscriptV1(_StrictModel):
    review_ref: str = Field(min_length=1, max_length=300)
    conversation_id: str = Field(min_length=1, max_length=200)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    turns: list[ConversationReviewTranscriptTurnV1] = Field(min_length=1)

    @model_validator(mode="after")
    def require_ordered_turns(self) -> "ConversationReviewTranscriptV1":
        if [turn.position for turn in self.turns] != list(
            range(1, len(self.turns) + 1)
        ):
            raise ValueError("transcript turns must remain in snapshot order")
        return self


class ConversationReviewSource:
    def __init__(
        self,
        *,
        conversations: ConversationOwner,
        retry_lineage: ConversationRetryLineageOwner,
        input_reader: TurnInputProjectionAuditReader,
        runtime: TurnRuntimeOwner,
        governance: ResultGovernanceDraftOwnerV2,
        reviews: ConversationReviewOwner,
    ) -> None:
        self._conversations = conversations
        self._retry_lineage = retry_lineage
        self._input_reader = input_reader
        self._runtime = runtime
        self._governance = governance
        self._reviews = reviews

    def assemble_and_register(
        self, conversation_id: str, *, observed_at: datetime
    ) -> ConversationReviewV1 | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.status != "active":
            return None
        members = self._all_members(conversation_id)
        if not members:
            return None
        retry_sources = self._retry_lineage.retry_sources(conversation_id)
        turns: list[ConversationReviewSnapshotTurnV1] = []
        semantic_activity = max(member.created_at for member in members)
        for position, member in enumerate(members, start=1):
            runtime_snapshot = self._runtime.snapshot(member.execution_id)
            if (
                runtime_snapshot.execution_id != member.execution_id
                or runtime_snapshot.turn_id != member.turn_id
                or runtime_snapshot.conversation_id != conversation_id
            ):
                raise ConversationReviewSourceError(
                    "conversation_review_runtime_identity_mismatch"
                )
            projection = self._input_reader.get_input_projection(member.execution_id)
            if projection is None or projection.execution_id != member.execution_id:
                raise ConversationReviewSourceError(
                    "conversation_review_input_projection_missing"
                )
            outcome = self._runtime.terminal_outcome(member.execution_id)
            if outcome is None:
                return None
            semantic_activity = max(semantic_activity, outcome.committed_at)
            governed_ref = None
            governed_digest = None
            terminal_ref = None
            if outcome.outcome == "completed":
                terminal_ref = outcome.terminal_commit_intent_ref
                governed_ref = outcome.governed_answer_draft_ref
                if terminal_ref is None or governed_ref is None:
                    raise ConversationReviewSourceError(
                        "conversation_review_terminal_lineage_missing"
                    )
                draft = self._governance.read_v2(governed_ref)
                if draft is None or draft.execution_id != member.execution_id:
                    raise ConversationReviewSourceError(
                        "conversation_review_governed_answer_missing"
                    )
                governed_digest = draft.digest
            turns.append(
                ConversationReviewSnapshotTurnV1(
                    position=position,
                    turn_id=member.turn_id,
                    execution_id=member.execution_id,
                    retry_of_turn_id=retry_sources.get(member.turn_id),
                    input_projection_ref=projection.projection_ref,
                    user_text_digest=_digest_text(projection.original_user_input),
                    terminal_status=outcome.outcome,
                    terminal_scan_sequence=outcome.scan_sequence,
                    terminal_commit_intent_ref=terminal_ref,
                    terminal_committed_at=outcome.committed_at,
                    governed_answer_draft_ref=governed_ref,
                    governed_answer_digest=governed_digest,
                )
            )
        current = self._conversations.get(conversation_id)
        current_members = self._all_members(conversation_id)
        if (
            current is None
            or current.status != "active"
            or current.updated_at != conversation.updated_at
            or len(current_members) != len(members)
            or current_members != members
        ):
            return None
        if observed_at < semantic_activity + SEMANTIC_QUIET_PERIOD:
            return None
        snapshot_digest = conversation_review_snapshot_digest(
            conversation_id=conversation_id,
            conversation_updated_at=conversation.updated_at,
            expected_next_ordinal=len(members) + 1,
            latest_semantic_activity_at=semantic_activity,
            turns=turns,
        )
        snapshot = ConversationReviewSnapshotV1(
            review_ref=conversation_review_ref(
                conversation_id=conversation_id,
                snapshot_digest=snapshot_digest,
                review_prompt_revision="conversation-review-triage-v1",
            ),
            conversation_id=conversation_id,
            conversation_updated_at=conversation.updated_at,
            expected_next_ordinal=len(members) + 1,
            latest_semantic_activity_at=semantic_activity,
            eligible_at=semantic_activity + SEMANTIC_QUIET_PERIOD,
            snapshot_digest=snapshot_digest,
            turns=turns,
        )
        return self._reviews.register_snapshot(snapshot)

    def rehydrate(
        self, snapshot: ConversationReviewSnapshotV1
    ) -> ConversationReviewTranscriptV1:
        turns: list[ConversationReviewTranscriptTurnV1] = []
        for source in snapshot.turns:
            runtime_snapshot = self._runtime.snapshot(source.execution_id)
            if (
                runtime_snapshot.execution_id != source.execution_id
                or runtime_snapshot.turn_id != source.turn_id
                or runtime_snapshot.conversation_id != snapshot.conversation_id
            ):
                raise ConversationReviewSourceError(
                    "conversation_review_runtime_identity_mismatch"
                )
            projection = self._input_reader.get_input_projection(source.execution_id)
            if (
                projection is None
                or projection.execution_id != source.execution_id
                or projection.projection_ref != source.input_projection_ref
                or _digest_text(projection.original_user_input)
                != source.user_text_digest
            ):
                raise ConversationReviewSourceError(
                    "conversation_review_input_projection_changed"
                )
            outcome = self._runtime.terminal_outcome(source.execution_id)
            if (
                outcome is None
                or outcome.outcome != source.terminal_status
                or outcome.scan_sequence != source.terminal_scan_sequence
                or outcome.terminal_commit_intent_ref
                != source.terminal_commit_intent_ref
                or outcome.committed_at != source.terminal_committed_at
                or outcome.governed_answer_draft_ref
                != source.governed_answer_draft_ref
            ):
                raise ConversationReviewSourceError(
                    "conversation_review_terminal_outcome_changed"
                )
            segments = None
            if source.terminal_status == "completed":
                if source.governed_answer_draft_ref is None:
                    raise ConversationReviewSourceError(
                        "conversation_review_governed_answer_missing"
                    )
                draft = self._governance.read_v2(
                    source.governed_answer_draft_ref
                )
                if (
                    draft is None
                    or draft.execution_id != source.execution_id
                    or draft.digest != source.governed_answer_digest
                ):
                    raise ConversationReviewSourceError(
                        "conversation_review_governed_answer_changed"
                    )
                segments = [
                    ConversationReviewTranscriptSegmentV1(
                        segment_id=segment.segment_id, text=segment.text
                    )
                    for segment in draft.segments
                ]
            turns.append(
                ConversationReviewTranscriptTurnV1(
                    position=source.position,
                    turn_id=source.turn_id,
                    execution_id=source.execution_id,
                    retry_of_turn_id=source.retry_of_turn_id,
                    original_user_text=projection.original_user_input,
                    final_governed_assistant_segments=segments,
                    terminal_status=source.terminal_status,
                )
            )
        return ConversationReviewTranscriptV1(
            review_ref=snapshot.review_ref,
            conversation_id=snapshot.conversation_id,
            snapshot_digest=snapshot.snapshot_digest,
            turns=turns,
        )

    def _all_members(self, conversation_id: str) -> list[ConversationTurnMemberV1]:
        members: list[ConversationTurnMemberV1] = []
        cursor: ConversationTurnCursorV1 | None = None
        while True:
            page = self._conversations.candidate_turns_after(
                conversation_id, after=cursor, limit=_PAGE_LIMIT
            )
            if not page:
                return members
            if cursor is not None and (
                page[0].ordinal,
                page[0].turn_id,
            ) <= (cursor.ordinal, cursor.turn_id):
                raise ConversationReviewSourceError(
                    "conversation_review_membership_scan_did_not_advance"
                )
            members.extend(page)
            if len(page) < _PAGE_LIMIT:
                return members
            last = page[-1]
            cursor = ConversationTurnCursorV1(
                ordinal=last.ordinal, turn_id=last.turn_id
            )


class _ReviewPublicationOwner(Protocol):
    def complete_in_session(
        self,
        session: Session,
        claim: ConversationReviewClaimV1,
        proposal: ConversationReviewProposalV1,
        model_invocation_refs: list[str],
        observed_at: datetime,
    ) -> ConversationReviewV1: ...

    def supersede_in_session(
        self,
        session: Session,
        claim: ConversationReviewClaimV1,
        observed_at: datetime,
    ) -> ConversationReviewV1: ...

    def read(self, review_ref: str) -> ConversationReviewV1 | None: ...


class ConversationReviewPublicationCoordinator:
    def __init__(
        self,
        session_factory: SessionFactory,
        reviews: _ReviewPublicationOwner,
    ) -> None:
        self._session_factory = session_factory
        self._reviews = reviews

    def finalize(
        self,
        claim: ConversationReviewClaimV1,
        proposal: ConversationReviewProposalV1,
        model_invocation_refs: list[str],
        *,
        observed_at: datetime,
    ) -> ConversationReviewV1:
        review = self._reviews.read(claim.review_ref)
        if review is None:
            raise ConversationReviewSourceError("conversation_review_snapshot_missing")
        snapshot = review.snapshot
        with self._session_factory() as session, session.begin():
            conversation = session.scalar(
                select(AtlasTurnConversationRow)
                .where(
                    AtlasTurnConversationRow.conversation_id
                    == snapshot.conversation_id
                )
                .with_for_update()
            )
            if (
                conversation is None
                or conversation.status != "active"
                or conversation.updated_at != snapshot.conversation_updated_at
                or conversation.next_ordinal != snapshot.expected_next_ordinal
            ):
                return self._reviews.supersede_in_session(
                    session, claim, observed_at
                )
            return self._reviews.complete_in_session(
                session,
                claim,
                proposal,
                model_invocation_refs,
                observed_at,
            )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()




__all__ = [
    "ConversationReviewPublicationCoordinator",
    "ConversationReviewSource",
    "ConversationReviewSourceError",
    "ConversationReviewTranscriptSegmentV1",
    "ConversationReviewTranscriptTurnV1",
    "ConversationReviewTranscriptV1",
]
