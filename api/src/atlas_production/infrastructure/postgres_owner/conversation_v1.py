"""Conversation-owned persistence for the strict turn runtime.

This repository deliberately accepts already allocated opaque execution/turn
identities.  It never calls, imports, or shares a transaction with another
runtime owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Literal

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationIdempotencyRow,
    AtlasTurnFeedbackRevisionRow,
    AtlasTurnConversationScopeTagRow,
    AtlasTurnConversationMemberRow,
    AtlasTurnConversationRow,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]


class ConversationStoreConflict(RuntimeError):
    """A replay identity or optimistic ordinal was reused with new meaning."""
class TurnFeedbackStoreError(RuntimeError):
    def __init__(
        self,
        reason: Literal[
            "not_found",
            "revision_conflict",
            "idempotency_conflict",
            "history_invalid",
        ],
    ) -> None:
        super().__init__(reason)
        self.reason = reason




def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: str
    owner_actor_id: str
    title: str
    status: Literal["active", "archived"]
    response_language: Literal["zh-TW", "en"]
    reasoning_mode: Literal["standard", "deep"]
    next_ordinal: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CreateConversationInput:
    conversation_id: str
    actor_id: str
    title: str
    idempotency_key: str
    response_language: Literal["zh-TW", "en"]
    tag_refs: tuple[tuple[Literal["project", "team"], str], ...] = ()


@dataclass(frozen=True, slots=True)
class TurnMemberRecord:
    turn_id: str
    conversation_id: str
    execution_id: str
    role: Literal["user", "assistant"]
    ordinal: int
    created_at: datetime
    retry_of_turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppendTurnMemberInput:
    conversation_id: str
    actor_id: str
    turn_id: str
    execution_id: str
    role: Literal["user", "assistant"]
    expected_next_ordinal: int
    idempotency_key: str
    operation: Literal["create_turn", "retry_turn"] = "create_turn"
    retry_of_turn_id: str | None = None
    reasoning_mode: Literal["standard", "deep"] | None = None


@dataclass(frozen=True, slots=True)
class ArchiveConversationInput:
    conversation_id: str
    actor_id: str
    expected_next_ordinal: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ArchiveConversationResult:
    conversation: ConversationRecord
    audit_event_ref: str
@dataclass(frozen=True, slots=True)
class ReviseTurnFeedbackInput:
    conversation_id: str
    turn_id: str
    actor_id: str
    feedback: Literal["helpful", "not_helpful"]
    expected_revision: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class TurnFeedbackRecord:
    turn_id: str
    feedback: Literal["helpful", "not_helpful"]
    revision: int
    actor_id: str
    updated_at: datetime




def _conversation(row: AtlasTurnConversationRow) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=row.conversation_id,
        owner_actor_id=row.owner_actor_id,
        title=row.title,
        status=row.status,  # type: ignore[arg-type]
        response_language=row.response_language,  # type: ignore[arg-type]
        reasoning_mode=row.reasoning_mode,  # type: ignore[arg-type]
        next_ordinal=row.next_ordinal,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _member(
    row: AtlasTurnConversationMemberRow, *, retry_of_turn_id: str | None = None
) -> TurnMemberRecord:
    return TurnMemberRecord(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        execution_id=row.execution_id,
        role=row.role,  # type: ignore[arg-type]
        ordinal=row.ordinal,
        created_at=row.created_at,
        retry_of_turn_id=retry_of_turn_id,
    )
def _feedback(row: AtlasTurnFeedbackRevisionRow) -> TurnFeedbackRecord:
    if (
        row.feedback not in {"helpful", "not_helpful"}
        or row.revision < 1
        or not row.actor_id
        or row.created_at.tzinfo is None
    ):
        raise TurnFeedbackStoreError("history_invalid")
    return TurnFeedbackRecord(
        turn_id=row.turn_id,
        feedback=row.feedback,  # type: ignore[arg-type]
        revision=row.revision,
        actor_id=row.actor_id,
        updated_at=row.created_at,
    )




class PostgresConversationV1Store:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(self, command: CreateConversationInput) -> ConversationRecord:
        request = asdict(command)
        request_digest = _digest(request)
        scope_ref = f"actor:{command.actor_id}"
        with self._session_factory() as session, session.begin():
            replay = session.get(
                AtlasTurnConversationIdempotencyRow,
                (scope_ref, "create_conversation", command.idempotency_key),
            )
            if replay is not None:
                if replay.request_digest != request_digest:
                    raise ConversationStoreConflict("conversation create replay payload changed")
                row = session.get(AtlasTurnConversationRow, replay.conversation_id)
                if row is None:
                    raise ConversationStoreConflict("conversation replay target is missing")
                return _conversation(row)
            if session.get(AtlasTurnConversationRow, command.conversation_id) is not None:
                raise ConversationStoreConflict("conversation identity already exists")
            created_at = _now()
            row = AtlasTurnConversationRow(
                conversation_id=command.conversation_id,
                owner_actor_id=command.actor_id,
                title=command.title,
                status="active",
                response_language=command.response_language,
                reasoning_mode="standard",
                next_ordinal=1,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            session.add_all(
                AtlasTurnConversationScopeTagRow(
                    conversation_id=command.conversation_id,
                    tag_type=tag_type,
                    tag_id=tag_id,
                )
                for tag_type, tag_id in command.tag_refs
            )
            session.add(
                AtlasTurnConversationIdempotencyRow(
                    scope_ref=scope_ref,
                    operation="create_conversation",
                    idempotency_key=command.idempotency_key,
                    actor_id=command.actor_id,
                    request_digest=request_digest,
                    conversation_id=command.conversation_id,
                    turn_id=None,
                    execution_id=None,
                    response_payload=json.dumps(
                        {"conversation_id": command.conversation_id}, separators=(",", ":")
                    ),
                    created_at=created_at,
                )
            )
            session.flush()
            return _conversation(row)

    def append_turn_member(self, command: AppendTurnMemberInput) -> TurnMemberRecord:
        if command.expected_next_ordinal < 1:
            raise ValueError("expected_next_ordinal must be positive")
        if (command.operation == "create_turn") != (command.reasoning_mode is not None):
            raise ValueError("fresh membership requires exactly one reasoning mode")
        request_digest = _digest(asdict(command))
        scope_ref = f"conversation:{command.conversation_id}"
        with self._session_factory() as session, session.begin():
            replay = session.get(
                AtlasTurnConversationIdempotencyRow,
                (scope_ref, command.operation, command.idempotency_key),
            )
            if replay is not None:
                if replay.request_digest != request_digest or replay.turn_id != command.turn_id:
                    raise ConversationStoreConflict("turn membership replay payload changed")
                row = session.get(AtlasTurnConversationMemberRow, replay.turn_id)
                if row is None:
                    raise ConversationStoreConflict("turn membership replay target is missing")
                retry_of_turn_id = json.loads(replay.response_payload).get(
                    "retry_of_turn_id"
                )
                return _member(row, retry_of_turn_id=retry_of_turn_id)

            existing = session.get(AtlasTurnConversationMemberRow, command.turn_id)
            by_execution = session.scalar(
                select(AtlasTurnConversationMemberRow).where(
                    AtlasTurnConversationMemberRow.execution_id == command.execution_id
                )
            )
            if existing is not None or by_execution is not None:
                raise ConversationStoreConflict("turn or execution identity already belongs to a membership")

            changed = session.execute(
                update(AtlasTurnConversationRow)
                .where(
                    AtlasTurnConversationRow.conversation_id == command.conversation_id,
                    AtlasTurnConversationRow.owner_actor_id == command.actor_id,
                    AtlasTurnConversationRow.status == "active",
                    AtlasTurnConversationRow.next_ordinal == command.expected_next_ordinal,
                )
                .values(
                    next_ordinal=command.expected_next_ordinal + 1,
                    **(
                        {"reasoning_mode": command.reasoning_mode}
                        if command.operation == "create_turn"
                        else {}
                    ),
                    updated_at=_now(),
                )
            ).rowcount
            if changed != 1:
                raise ConversationStoreConflict("conversation ordinal CAS failed")

            created_at = _now()
            row = AtlasTurnConversationMemberRow(
                turn_id=command.turn_id,
                conversation_id=command.conversation_id,
                execution_id=command.execution_id,
                role=command.role,
                ordinal=command.expected_next_ordinal,
                created_at=created_at,
            )
            session.add(row)
            session.add(
                AtlasTurnConversationIdempotencyRow(
                    scope_ref=scope_ref,
                    operation=command.operation,
                    idempotency_key=command.idempotency_key,
                    actor_id=command.actor_id,
                    request_digest=request_digest,
                    conversation_id=command.conversation_id,
                    turn_id=command.turn_id,
                    execution_id=command.execution_id,
                    response_payload=json.dumps(
                        {
                            "turn_id": command.turn_id,
                            "execution_id": command.execution_id,
                            "retry_of_turn_id": command.retry_of_turn_id,
                        },
                        separators=(",", ":"),
                    ),
                    created_at=created_at,
                )
            )
            session.flush()
            return _member(row, retry_of_turn_id=command.retry_of_turn_id)

    def archive(
        self,
        command: ArchiveConversationInput,
        *,
        audit_event: AuditEventRecord,
    ) -> ArchiveConversationResult:
        if command.expected_next_ordinal < 1:
            raise ValueError("expected_next_ordinal must be positive")
        expected_target = f"conversation:{command.conversation_id}"
        if (
            audit_event.event_type != "conversation_archived"
            or audit_event.actor_id != command.actor_id
            or audit_event.target_ref != expected_target
        ):
            raise ValueError("conversation archive audit event does not match command")
        request_digest = _digest(asdict(command))
        scope_ref = expected_target
        with self._session_factory() as session, session.begin():
            replay = session.get(
                AtlasTurnConversationIdempotencyRow,
                (scope_ref, "archive_conversation", command.idempotency_key),
            )
            if replay is not None:
                if replay.request_digest != request_digest:
                    raise ConversationStoreConflict(
                        "conversation archive replay payload changed"
                    )
                row = session.get(AtlasTurnConversationRow, replay.conversation_id)
                payload = json.loads(replay.response_payload)
                audit_event_ref = payload.get("audit_event_ref")
                if (
                    row is None
                    or row.owner_actor_id != command.actor_id
                    or row.status != "archived"
                    or audit_event_ref != audit_event.event_id
                ):
                    raise ConversationStoreConflict(
                        "conversation archive replay evidence is missing"
                    )
                return ArchiveConversationResult(
                    conversation=_conversation(row),
                    audit_event_ref=audit_event_ref,
                )

            archived_at = _now()
            changed = session.execute(
                update(AtlasTurnConversationRow)
                .where(
                    AtlasTurnConversationRow.conversation_id
                    == command.conversation_id,
                    AtlasTurnConversationRow.owner_actor_id == command.actor_id,
                    AtlasTurnConversationRow.status == "active",
                    AtlasTurnConversationRow.next_ordinal
                    == command.expected_next_ordinal,
                )
                .values(status="archived", updated_at=archived_at)
            ).rowcount
            if changed != 1:
                raise ConversationStoreConflict("conversation archive CAS failed")

            AuditEventWriter(session).append(audit_event)
            session.add(
                AtlasTurnConversationIdempotencyRow(
                    scope_ref=scope_ref,
                    operation="archive_conversation",
                    idempotency_key=command.idempotency_key,
                    actor_id=command.actor_id,
                    request_digest=request_digest,
                    conversation_id=command.conversation_id,
                    turn_id=None,
                    execution_id=None,
                    response_payload=json.dumps(
                        {"audit_event_ref": audit_event.event_id},
                        separators=(",", ":"),
                    ),
                    created_at=archived_at,
                )
            )
            session.flush()
            row = session.get(AtlasTurnConversationRow, command.conversation_id)
            if row is None or row.status != "archived":
                raise ConversationStoreConflict("archived conversation is missing")
            return ArchiveConversationResult(
                conversation=_conversation(row),
                audit_event_ref=audit_event.event_id,
            )

    def revise_turn_feedback(
        self, command: ReviseTurnFeedbackInput
    ) -> TurnFeedbackRecord:
        if command.expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        request_digest = _digest(asdict(command))
        scope_ref = f"turn:{command.turn_id}"
        with self._session_factory() as session, session.begin():
            replay = self._feedback_replay(
                session, command=command, request_digest=request_digest
            )
            if replay is not None:
                return replay

            conversation = session.scalar(
                select(AtlasTurnConversationRow)
                .where(
                    AtlasTurnConversationRow.conversation_id
                    == command.conversation_id
                )
                .with_for_update()
            )
            if (
                conversation is None
                or conversation.owner_actor_id != command.actor_id
                or conversation.status != "active"
            ):
                raise TurnFeedbackStoreError("not_found")

            replay = self._feedback_replay(
                session, command=command, request_digest=request_digest
            )
            if replay is not None:
                return replay

            member = session.get(AtlasTurnConversationMemberRow, command.turn_id)
            if member is None or member.conversation_id != command.conversation_id:
                raise TurnFeedbackStoreError("not_found")

            current = self._current_feedback(session, command.turn_id)
            current_revision = 0 if current is None else current.revision
            if command.expected_revision != current_revision:
                raise TurnFeedbackStoreError("revision_conflict")

            if current is None or current.feedback != command.feedback:
                updated_at = _now()
                current = TurnFeedbackRecord(
                    turn_id=command.turn_id,
                    feedback=command.feedback,
                    revision=current_revision + 1,
                    actor_id=command.actor_id,
                    updated_at=updated_at,
                )
                session.add(
                    AtlasTurnFeedbackRevisionRow(
                        turn_id=current.turn_id,
                        revision=current.revision,
                        feedback=current.feedback,
                        actor_id=current.actor_id,
                        created_at=current.updated_at,
                    )
                )

            session.add(
                AtlasTurnConversationIdempotencyRow(
                    scope_ref=scope_ref,
                    operation="revise_turn_feedback",
                    idempotency_key=command.idempotency_key,
                    actor_id=command.actor_id,
                    request_digest=request_digest,
                    conversation_id=command.conversation_id,
                    turn_id=command.turn_id,
                    execution_id=None,
                    response_payload=json.dumps(
                        {
                            "feedback": current.feedback,
                            "revision": current.revision,
                            "updated_at": current.updated_at.isoformat(),
                        },
                        separators=(",", ":"),
                    ),
                    created_at=_now(),
                )
            )
            session.flush()
            return current

    def current_turn_feedback(self, turn_id: str) -> TurnFeedbackRecord | None:
        with self._session_factory() as session:
            return self._current_feedback(session, turn_id)

    @staticmethod
    def _current_feedback(
        session: Session, turn_id: str
    ) -> TurnFeedbackRecord | None:
        result = session.execute(
            select(
                AtlasTurnFeedbackRevisionRow,
                func.count().over(),
            )
            .where(AtlasTurnFeedbackRevisionRow.turn_id == turn_id)
            .order_by(AtlasTurnFeedbackRevisionRow.revision.desc())
            .limit(1)
        ).one_or_none()
        if result is None:
            return None
        row, count = result
        if count != row.revision:
            raise TurnFeedbackStoreError("history_invalid")
        return _feedback(row)

    @staticmethod
    def _feedback_replay(
        session: Session,
        *,
        command: ReviseTurnFeedbackInput,
        request_digest: str,
    ) -> TurnFeedbackRecord | None:
        replay = session.get(
            AtlasTurnConversationIdempotencyRow,
            (f"turn:{command.turn_id}", "revise_turn_feedback", command.idempotency_key),
        )
        if replay is None:
            return None
        if (
            replay.request_digest != request_digest
            or replay.actor_id != command.actor_id
            or replay.conversation_id != command.conversation_id
            or replay.turn_id != command.turn_id
        ):
            raise TurnFeedbackStoreError("idempotency_conflict")
        try:
            payload = json.loads(replay.response_payload)
            if set(payload) != {"feedback", "revision", "updated_at"}:
                raise ValueError("unexpected feedback replay fields")
            row = session.get(
                AtlasTurnFeedbackRevisionRow,
                (command.turn_id, payload["revision"]),
            )
            if row is None:
                raise ValueError("feedback replay revision is missing")
            record = _feedback(row)
            if (
                record.feedback != payload["feedback"]
                or record.updated_at.isoformat() != payload["updated_at"]
                or record.actor_id != command.actor_id
            ):
                raise ValueError("feedback replay evidence changed")
            return record
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TurnFeedbackStoreError("history_invalid") from error

    def get(self, conversation_id: str) -> ConversationRecord | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnConversationRow, conversation_id)
            return None if row is None else _conversation(row)

    def get_turn(self, turn_id: str) -> TurnMemberRecord | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnConversationMemberRow, turn_id)
            return None if row is None else _member(row)

    def list_for_actor(self, actor_id: str) -> tuple[ConversationRecord, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnConversationRow)
                .where(
                    AtlasTurnConversationRow.owner_actor_id == actor_id,
                    AtlasTurnConversationRow.status == "active",
                )
                .order_by(AtlasTurnConversationRow.updated_at.desc(), AtlasTurnConversationRow.conversation_id)
            ).all()
            return tuple(_conversation(row) for row in rows)

    def list_all(self) -> tuple[ConversationRecord, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnConversationRow).order_by(
                    AtlasTurnConversationRow.updated_at.desc(),
                    AtlasTurnConversationRow.conversation_id,
                )
            ).all()
            return tuple(_conversation(row) for row in rows)

    def list_active_updated_before(
        self,
        *,
        cutoff: datetime,
        after: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[ConversationRecord, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("conversation scan limit must be between 1 and 100")
        statement = select(AtlasTurnConversationRow).where(
            AtlasTurnConversationRow.status == "active",
            AtlasTurnConversationRow.updated_at <= cutoff,
        )
        if after is not None:
            statement = statement.where(
                tuple_(
                    AtlasTurnConversationRow.updated_at,
                    AtlasTurnConversationRow.conversation_id,
                )
                > after
            )
        with self._session_factory() as session:
            rows = session.scalars(
                statement.order_by(
                    AtlasTurnConversationRow.updated_at,
                    AtlasTurnConversationRow.conversation_id,
                ).limit(limit)
            ).all()
            return tuple(_conversation(row) for row in rows)

    def candidate_turns_after(
        self,
        conversation_id: str,
        *,
        after: tuple[int, str] | None,
        limit: int,
    ) -> tuple[TurnMemberRecord, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("conversation turn scan limit must be between 1 and 100")
        statement = select(AtlasTurnConversationMemberRow).where(
            AtlasTurnConversationMemberRow.conversation_id == conversation_id
        )
        if after is not None:
            statement = statement.where(
                tuple_(
                    AtlasTurnConversationMemberRow.ordinal,
                    AtlasTurnConversationMemberRow.turn_id,
                )
                > after
            )
        with self._session_factory() as session:
            rows = session.scalars(
                statement.order_by(
                    AtlasTurnConversationMemberRow.ordinal,
                    AtlasTurnConversationMemberRow.turn_id,
                ).limit(limit)
            ).all()
            return tuple(_member(row) for row in rows)

    def candidate_turns(self, conversation_id: str) -> tuple[TurnMemberRecord, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnConversationMemberRow)
                .where(AtlasTurnConversationMemberRow.conversation_id == conversation_id)
                .order_by(AtlasTurnConversationMemberRow.ordinal)
            ).all()
            return tuple(_member(row) for row in rows)

    def retry_sources(self, conversation_id: str) -> dict[str, str]:
        with self._session_factory() as session:
            retry_rows = session.scalars(
                select(AtlasTurnConversationIdempotencyRow).where(
                    AtlasTurnConversationIdempotencyRow.conversation_id == conversation_id,
                    AtlasTurnConversationIdempotencyRow.operation == "retry_turn",
                )
            ).all()
            return {
                item.turn_id: json.loads(item.response_payload).get("retry_of_turn_id")
                for item in retry_rows
                if item.turn_id is not None
                and json.loads(item.response_payload).get("retry_of_turn_id") is not None
            }


__all__ = [
    "ArchiveConversationInput",
    "ArchiveConversationResult",
    "AppendTurnMemberInput",
    "ConversationRecord",
    "ConversationStoreConflict",
    "CreateConversationInput",
    "PostgresConversationV1Store",
    "ReviseTurnFeedbackInput",
    "TurnFeedbackRecord",
    "TurnFeedbackStoreError",
    "TurnMemberRecord",
]
