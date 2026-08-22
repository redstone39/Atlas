from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.conversation_review import (
    CONVERSATION_REVIEW_SCAN_SEQUENCE,
    AtlasConversationLearningCaseRow,
    AtlasConversationLearningCaseTurnRow,
    AtlasConversationReviewRow,
    AtlasConversationReviewSnapshotTurnRow,
)
from atlas_production.modules.conversation_review.public import (
    MAX_CANONICAL_CASE_BYTES,
    ConversationLearningCaseProposalV1,
    ConversationReviewClaimV1,
    ConversationReviewCursorV1,
    ConversationReviewProposalV1,
    ConversationReviewSnapshotTurnV1,
    ConversationReviewSnapshotV1,
    ConversationReviewV1,
)


SessionFactory = Callable[[], Session]
_RETRY_DELAY = timedelta(seconds=30)
_COMPLETED_STATUSES = frozenset({"completed", "completed_no_cases"})


class ConversationReviewConflict(RuntimeError):
    """Immutable review identity, result, or exact replay conflicts."""


class ConversationReviewClaimLost(ConversationReviewConflict):
    """The supplied claim is stale, expired, taken over, or no longer live."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _case_projection(
    cases: list[ConversationLearningCaseProposalV1],
) -> list[dict[str, object]]:
    projection = [case.model_dump(mode="json") for case in cases]
    if len(_canonical(projection)) > MAX_CANONICAL_CASE_BYTES:
        raise ConversationReviewConflict("conversation_review_output_too_large")
    return projection


def _result_digest(
    row: AtlasConversationReviewRow,
    cases: list[ConversationLearningCaseProposalV1],
    model_invocation_refs: list[str],
) -> str:
    projection = {
        "cases": _case_projection(cases),
        "model_invocation_refs": model_invocation_refs,
        "review_ref": row.review_ref,
        "route": {
            "route_id": row.pinned_route_id,
            "route_revision": row.pinned_route_revision,
            "runtime_policy_revision": row.pinned_runtime_policy_revision,
        },
        "snapshot_digest": row.snapshot_digest,
    }
    return hashlib.sha256(_canonical(projection)).hexdigest()


def _snapshot_turn_from_row(
    row: AtlasConversationReviewSnapshotTurnRow,
) -> ConversationReviewSnapshotTurnV1:
    return ConversationReviewSnapshotTurnV1(
        position=row.position,
        turn_id=row.turn_id,
        execution_id=row.execution_id,
        retry_of_turn_id=row.retry_of_turn_id,
        input_projection_ref=row.input_projection_ref,
        user_text_digest=row.user_text_digest,
        terminal_status=row.terminal_status,
        terminal_scan_sequence=row.terminal_scan_sequence,
        terminal_commit_intent_ref=row.terminal_commit_intent_ref,
        terminal_committed_at=row.terminal_committed_at,
        governed_answer_draft_ref=row.governed_answer_draft_ref,
        governed_answer_digest=row.governed_answer_digest,
    )


def _snapshot_from_rows(
    row: AtlasConversationReviewRow,
    turn_rows: list[AtlasConversationReviewSnapshotTurnRow],
) -> ConversationReviewSnapshotV1:
    return ConversationReviewSnapshotV1(
        review_ref=row.review_ref,
        schema_version=row.schema_version,
        review_prompt_revision=row.review_prompt_revision,
        conversation_id=row.conversation_id,
        conversation_updated_at=row.conversation_updated_at,
        expected_next_ordinal=row.expected_next_ordinal,
        latest_semantic_activity_at=row.latest_semantic_activity_at,
        eligible_at=row.eligible_at,
        snapshot_digest=row.snapshot_digest,
        turns=[_snapshot_turn_from_row(turn) for turn in turn_rows],
    )


def _load_cases(
    session: Session,
    review_ref: str,
    turn_rows: list[AtlasConversationReviewSnapshotTurnRow],
) -> list[ConversationLearningCaseProposalV1]:
    case_rows = session.scalars(
        select(AtlasConversationLearningCaseRow)
        .where(AtlasConversationLearningCaseRow.review_ref == review_ref)
        .order_by(AtlasConversationLearningCaseRow.case_ordinal)
    ).all()
    membership_rows = session.scalars(
        select(AtlasConversationLearningCaseTurnRow)
        .where(AtlasConversationLearningCaseTurnRow.review_ref == review_ref)
        .order_by(
            AtlasConversationLearningCaseTurnRow.case_ordinal,
            AtlasConversationLearningCaseTurnRow.turn_position,
        )
    ).all()
    turn_ids_by_position = {turn.position: turn.turn_id for turn in turn_rows}
    memberships_by_case: dict[int, list[AtlasConversationLearningCaseTurnRow]] = {}
    for membership in membership_rows:
        memberships_by_case.setdefault(membership.case_ordinal, []).append(membership)
    cases: list[ConversationLearningCaseProposalV1] = []
    for case_row in case_rows:
        memberships = memberships_by_case.pop(case_row.case_ordinal, [])
        primary = [item for item in memberships if item.is_primary_assistant]
        if len(primary) != 1 or any(
            item.turn_position not in turn_ids_by_position for item in memberships
        ):
            raise ConversationReviewConflict("learning case membership integrity changed")
        cases.append(
            ConversationLearningCaseProposalV1(
                case_ordinal=case_row.case_ordinal,
                title=case_row.title,
                learning_evidence=case_row.learning_evidence,
                generalization_hypothesis=case_row.generalization_hypothesis,
                investigation_question=case_row.investigation_question,
                selection_rationale=case_row.selection_rationale,
                involved_turn_ids=[
                    turn_ids_by_position[item.turn_position] for item in memberships
                ],
                primary_assistant_turn_id=turn_ids_by_position[
                    primary[0].turn_position
                ],
            )
        )
    if memberships_by_case:
        raise ConversationReviewConflict("orphan learning case memberships exist")
    return cases


def _review_from_session(
    session: Session, row: AtlasConversationReviewRow
) -> ConversationReviewV1:
    turn_rows = list(
        session.scalars(
            select(AtlasConversationReviewSnapshotTurnRow)
            .where(AtlasConversationReviewSnapshotTurnRow.review_ref == row.review_ref)
            .order_by(AtlasConversationReviewSnapshotTurnRow.position)
        ).all()
    )
    try:
        snapshot = _snapshot_from_rows(row, turn_rows)
        cases = _load_cases(session, row.review_ref, turn_rows)
        review = ConversationReviewV1(
            snapshot=snapshot,
            status=row.status,
            attempt=row.attempt,
            fence=row.fence,
            pinned_route_id=row.pinned_route_id,
            pinned_route_revision=row.pinned_route_revision,
            pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
            model_invocation_refs=list(row.model_invocation_refs),
            failure_code=row.failure_code,
            cases=cases,
            review_digest=row.review_digest,
            scan_sequence=row.scan_sequence,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise ConversationReviewConflict("persisted conversation review is invalid") from exc
    if row.status in _COMPLETED_STATUSES:
        expected = _result_digest(row, cases, list(row.model_invocation_refs))
        if row.review_digest != expected:
            raise ConversationReviewConflict("review digest does not bind persisted result")
    return review


def _claim_from_row(row: AtlasConversationReviewRow) -> ConversationReviewClaimV1:
    if row.claim_token is None or row.lease_expires_at is None:
        raise ConversationReviewConflict("review row does not carry a live claim")
    return ConversationReviewClaimV1(
        review_ref=row.review_ref,
        attempt=row.attempt,
        fence=row.fence,
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
        pinned_route_id=row.pinned_route_id,
        pinned_route_revision=row.pinned_route_revision,
        pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
    )


def _require_lease_seconds(lease_seconds: int) -> None:
    if lease_seconds < 1 or lease_seconds > 3600:
        raise ValueError("review lease seconds must be between 1 and 3600")


def _require_safe_identity(value: str, field_name: str, maximum: int) -> None:
    if not value or len(value) > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum} characters")


def _claim_identity_matches(row: AtlasConversationReviewRow, claim: ConversationReviewClaimV1) -> bool:
    return (
        row.review_ref == claim.review_ref
        and row.attempt == claim.attempt
        and row.fence == claim.fence
        and row.claim_token == claim.claim_token
    )


def _lock_live_claim(
    session: Session,
    claim: ConversationReviewClaimV1,
    observed_at: datetime,
) -> AtlasConversationReviewRow:
    row = session.scalar(
        select(AtlasConversationReviewRow)
        .where(AtlasConversationReviewRow.review_ref == claim.review_ref)
        .with_for_update()
    )
    if (
        row is None
        or not _claim_identity_matches(row, claim)
        or row.status != "reviewing"
        or row.lease_expires_at is None
        or row.lease_expires_at <= observed_at
        or row.lease_expires_at != claim.lease_expires_at
        or row.pinned_route_id != claim.pinned_route_id
        or row.pinned_route_revision != claim.pinned_route_revision
        or row.pinned_runtime_policy_revision != claim.pinned_runtime_policy_revision
    ):
        raise ConversationReviewClaimLost("conversation review claim is no longer live")
    return row


def _validate_proposal_against_snapshot(
    proposal: ConversationReviewProposalV1,
    turn_rows: list[AtlasConversationReviewSnapshotTurnRow],
) -> dict[str, int]:
    positions = {turn.turn_id: turn.position for turn in turn_rows}
    turns = {turn.turn_id: turn for turn in turn_rows}
    for case in proposal.cases:
        if any(turn_id not in positions for turn_id in case.involved_turn_ids):
            raise ConversationReviewConflict("case references an unknown snapshot turn")
        ordered = sorted(case.involved_turn_ids, key=positions.__getitem__)
        if case.involved_turn_ids != ordered:
            raise ConversationReviewConflict("case turns are not in snapshot order")
        primary = turns[case.primary_assistant_turn_id]
        if primary.terminal_status != "completed" or primary.governed_answer_draft_ref is None:
            raise ConversationReviewConflict("case primary turn lacks a governed answer")
        if not any(
            turns[turn_id].position > primary.position
            and turns[turn_id].retry_of_turn_id is None
            for turn_id in case.involved_turn_ids
        ):
            raise ConversationReviewConflict(
                "case requires a later fresh user semantic response"
            )
    _case_projection(proposal.cases)
    return positions


class PostgresConversationReviewOwner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def register_snapshot(
        self, snapshot: ConversationReviewSnapshotV1
    ) -> ConversationReviewV1:
        with self._session_factory() as session, session.begin():
            inserted_ref = session.scalar(
                pg_insert(AtlasConversationReviewRow)
                .values(
                    review_ref=snapshot.review_ref,
                    schema_version=snapshot.schema_version,
                    review_prompt_revision=snapshot.review_prompt_revision,
                    conversation_id=snapshot.conversation_id,
                    conversation_updated_at=snapshot.conversation_updated_at,
                    expected_next_ordinal=snapshot.expected_next_ordinal,
                    latest_semantic_activity_at=snapshot.latest_semantic_activity_at,
                    eligible_at=snapshot.eligible_at,
                    snapshot_digest=snapshot.snapshot_digest,
                    status="pending",
                    attempt=0,
                    fence=0,
                    worker_id=None,
                    claim_token=None,
                    lease_expires_at=None,
                    next_attempt_at=None,
                    pinned_route_id=None,
                    pinned_route_revision=None,
                    pinned_runtime_policy_revision=None,
                    model_invocation_refs=[],
                    failure_code=None,
                    review_digest=None,
                    scan_sequence=None,
                    completed_at=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AtlasConversationReviewRow.conversation_id,
                        AtlasConversationReviewRow.schema_version,
                        AtlasConversationReviewRow.snapshot_digest,
                        AtlasConversationReviewRow.review_prompt_revision,
                    ]
                )
                .returning(AtlasConversationReviewRow.review_ref)
            )
            if inserted_ref is not None:
                if inserted_ref != snapshot.review_ref:
                    raise ConversationReviewConflict(
                        "snapshot insert returned another review identity"
                    )
                session.add_all(
                    [
                        AtlasConversationReviewSnapshotTurnRow(
                            review_ref=snapshot.review_ref,
                            **turn.model_dump(mode="python"),
                        )
                        for turn in snapshot.turns
                    ]
                )
                session.flush()
            row = session.scalar(
                select(AtlasConversationReviewRow)
                .where(AtlasConversationReviewRow.review_ref == snapshot.review_ref)
                .with_for_update()
            )
            if row is None:
                raise ConversationReviewConflict("snapshot identity conflicts")
            review = _review_from_session(session, row)
            if review.snapshot != snapshot:
                raise ConversationReviewConflict("snapshot replay source projection conflicts")
            return review

    def claim_next(
        self, worker_id: str, observed_at: datetime, lease_seconds: int = 300
    ) -> ConversationReviewClaimV1 | None:
        _require_safe_identity(worker_id, "worker_id", 200)
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasConversationReviewRow)
                .where(
                    AtlasConversationReviewRow.eligible_at <= observed_at,
                    or_(
                        AtlasConversationReviewRow.status == "pending",
                        (
                            (AtlasConversationReviewRow.status == "retryable_failed")
                            & (AtlasConversationReviewRow.next_attempt_at <= observed_at)
                        ),
                        (
                            (AtlasConversationReviewRow.status == "reviewing")
                            & (AtlasConversationReviewRow.lease_expires_at <= observed_at)
                        ),
                    ),
                )
                .order_by(
                    AtlasConversationReviewRow.eligible_at,
                    AtlasConversationReviewRow.review_ref,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "reviewing"
            row.attempt += 1
            row.fence += 1
            row.worker_id = worker_id
            row.claim_token = uuid.uuid4().hex
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.next_attempt_at = None
            row.pinned_route_id = None
            row.pinned_route_revision = None
            row.pinned_runtime_policy_revision = None
            row.failure_code = None
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def pin_route(
        self,
        claim: ConversationReviewClaimV1,
        route_id: str,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: datetime,
    ) -> ConversationReviewClaimV1:
        _require_safe_identity(route_id, "route_id", 200)
        if route_revision < 1 or runtime_policy_revision < 1:
            raise ValueError("review route revisions must be positive")
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            current = (
                row.pinned_route_id,
                row.pinned_route_revision,
                row.pinned_runtime_policy_revision,
            )
            requested = (route_id, route_revision, runtime_policy_revision)
            if current == (None, None, None):
                row.pinned_route_id = route_id
                row.pinned_route_revision = route_revision
                row.pinned_runtime_policy_revision = runtime_policy_revision
                row.updated_at = observed_at
                session.flush()
            elif current != requested:
                raise ConversationReviewConflict("model_route_revision_conflict")
            return _claim_from_row(row)

    def renew_claim(
        self,
        claim: ConversationReviewClaimV1,
        observed_at: datetime,
        lease_seconds: int = 300,
    ) -> ConversationReviewClaimV1:
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def complete(
        self,
        claim: ConversationReviewClaimV1,
        proposal: ConversationReviewProposalV1,
        model_invocation_refs: list[str],
        observed_at: datetime,
    ) -> ConversationReviewV1:
        with self._session_factory() as session, session.begin():
            return self.complete_in_session(
                session, claim, proposal, model_invocation_refs, observed_at
            )

    def complete_in_session(
        self,
        session: Session,
        claim: ConversationReviewClaimV1,
        proposal: ConversationReviewProposalV1,
        model_invocation_refs: list[str],
        observed_at: datetime,
    ) -> ConversationReviewV1:
        if not model_invocation_refs or len(set(model_invocation_refs)) != len(
            model_invocation_refs
        ):
            raise ValueError("model invocation refs must be nonempty and unique")
        for invocation_ref in model_invocation_refs:
            _require_safe_identity(invocation_ref, "model_invocation_ref", 300)
        row = session.scalar(
            select(AtlasConversationReviewRow)
            .where(AtlasConversationReviewRow.review_ref == claim.review_ref)
            .with_for_update()
        )
        if row is None or not _claim_identity_matches(row, claim):
            raise ConversationReviewClaimLost("conversation review claim identity changed")
        turn_rows = list(
            session.scalars(
                select(AtlasConversationReviewSnapshotTurnRow)
                .where(
                    AtlasConversationReviewSnapshotTurnRow.review_ref == claim.review_ref
                )
                .order_by(AtlasConversationReviewSnapshotTurnRow.position)
            ).all()
        )
        positions = _validate_proposal_against_snapshot(proposal, turn_rows)
        expected_digest = _result_digest(row, proposal.cases, model_invocation_refs)
        if row.status in _COMPLETED_STATUSES:
            review = _review_from_session(session, row)
            if (
                row.review_digest != expected_digest
                or list(row.model_invocation_refs) != model_invocation_refs
                or review.cases != proposal.cases
            ):
                raise ConversationReviewConflict("completed review replay conflicts")
            return review
        if (
            row.status != "reviewing"
            or row.lease_expires_at is None
            or row.lease_expires_at <= observed_at
            or row.lease_expires_at != claim.lease_expires_at
            or row.pinned_route_id is None
            or row.pinned_route_id != claim.pinned_route_id
            or row.pinned_route_revision != claim.pinned_route_revision
            or row.pinned_runtime_policy_revision
            != claim.pinned_runtime_policy_revision
        ):
            raise ConversationReviewClaimLost("conversation review claim cannot publish")
        for case in proposal.cases:
            session.add(
                AtlasConversationLearningCaseRow(
                    review_ref=row.review_ref,
                    case_ordinal=case.case_ordinal,
                    title=case.title,
                    learning_evidence=case.learning_evidence,
                    generalization_hypothesis=case.generalization_hypothesis,
                    investigation_question=case.investigation_question,
                    selection_rationale=case.selection_rationale,
                )
            )
            session.add_all(
                [
                    AtlasConversationLearningCaseTurnRow(
                        review_ref=row.review_ref,
                        case_ordinal=case.case_ordinal,
                        turn_position=positions[turn_id],
                        is_primary_assistant=(
                            turn_id == case.primary_assistant_turn_id
                        ),
                    )
                    for turn_id in case.involved_turn_ids
                ]
            )
        row.status = "completed" if proposal.cases else "completed_no_cases"
        row.worker_id = None
        row.lease_expires_at = None
        row.next_attempt_at = None
        row.model_invocation_refs = list(model_invocation_refs)
        row.failure_code = None
        row.review_digest = expected_digest
        row.scan_sequence = session.scalar(
            select(CONVERSATION_REVIEW_SCAN_SEQUENCE.next_value())
        )
        row.completed_at = observed_at
        row.updated_at = observed_at
        session.flush()
        return _review_from_session(session, row)

    def fail(
        self,
        claim: ConversationReviewClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: datetime,
    ) -> ConversationReviewV1:
        _require_safe_identity(failure_code, "failure_code", 100)
        target = "retryable_failed" if retryable else "failed"
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasConversationReviewRow)
                .where(AtlasConversationReviewRow.review_ref == claim.review_ref)
                .with_for_update()
            )
            if row is None or not _claim_identity_matches(row, claim):
                raise ConversationReviewClaimLost("conversation review claim identity changed")
            if row.status == target:
                if row.failure_code != failure_code:
                    raise ConversationReviewConflict("failed review replay conflicts")
                return _review_from_session(session, row)
            if (
                row.status != "reviewing"
                or row.lease_expires_at is None
                or row.lease_expires_at <= observed_at
                or row.lease_expires_at != claim.lease_expires_at
            ):
                raise ConversationReviewClaimLost("conversation review claim cannot fail")
            row.status = target
            row.worker_id = None
            row.lease_expires_at = None
            row.next_attempt_at = observed_at + _RETRY_DELAY if retryable else None
            row.failure_code = failure_code
            row.updated_at = observed_at
            session.flush()
            return _review_from_session(session, row)

    def supersede(
        self, claim: ConversationReviewClaimV1, observed_at: datetime
    ) -> ConversationReviewV1:
        with self._session_factory() as session, session.begin():
            return self.supersede_in_session(session, claim, observed_at)

    def supersede_in_session(
        self,
        session: Session,
        claim: ConversationReviewClaimV1,
        observed_at: datetime,
    ) -> ConversationReviewV1:
        row = session.scalar(
            select(AtlasConversationReviewRow)
            .where(AtlasConversationReviewRow.review_ref == claim.review_ref)
            .with_for_update()
        )
        if row is None or not _claim_identity_matches(row, claim):
            raise ConversationReviewClaimLost("conversation review claim identity changed")
        if row.status == "superseded":
            return _review_from_session(session, row)
        if (
            row.status != "reviewing"
            or row.lease_expires_at is None
            or row.lease_expires_at <= observed_at
            or row.lease_expires_at != claim.lease_expires_at
        ):
            raise ConversationReviewClaimLost(
                "conversation review claim cannot be superseded"
            )
        row.status = "superseded"
        row.worker_id = None
        row.lease_expires_at = None
        row.next_attempt_at = None
        row.failure_code = None
        row.updated_at = observed_at
        session.flush()
        return _review_from_session(session, row)

    def read(self, review_ref: str) -> ConversationReviewV1 | None:
        _require_safe_identity(review_ref, "review_ref", 300)
        with self._session_factory() as session, session.begin():
            row = session.get(AtlasConversationReviewRow, review_ref)
            return _review_from_session(session, row) if row is not None else None

    def latest_completed_for_conversation(
        self, conversation_id: str
    ) -> ConversationReviewV1 | None:
        _require_safe_identity(conversation_id, "conversation_id", 200)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasConversationReviewRow)
                .where(
                    AtlasConversationReviewRow.conversation_id == conversation_id,
                    AtlasConversationReviewRow.status.in_(_COMPLETED_STATUSES),
                )
                .order_by(
                    AtlasConversationReviewRow.completed_at.desc(),
                    AtlasConversationReviewRow.review_ref.desc(),
                )
                .limit(1)
            )
            return _review_from_session(session, row) if row is not None else None

    def list_after(
        self, cursor: ConversationReviewCursorV1 | None, limit: int
    ) -> list[ConversationReviewV1]:
        if limit < 1 or limit > 100:
            raise ValueError("conversation review scan limit must be between 1 and 100")
        statement = select(AtlasConversationReviewRow).where(
            AtlasConversationReviewRow.status.in_(_COMPLETED_STATUSES)
        )
        if cursor is not None:
            statement = statement.where(
                tuple_(
                    AtlasConversationReviewRow.scan_sequence,
                    AtlasConversationReviewRow.review_ref,
                )
                > (cursor.scan_sequence, cursor.review_ref)
            )
        statement = statement.order_by(
            AtlasConversationReviewRow.scan_sequence,
            AtlasConversationReviewRow.review_ref,
        ).limit(limit)
        with self._session_factory() as session, session.begin():
            session.execute(text("LOCK TABLE atlas_conversation_reviews IN SHARE MODE"))
            rows = session.scalars(statement).all()
            return [_review_from_session(session, row) for row in rows]


__all__ = [
    "ConversationReviewClaimLost",
    "ConversationReviewConflict",
    "PostgresConversationReviewOwner",
]
