"""Public Conversation adapter over owner-local PostgreSQL persistence."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid4, uuid5

from atlas_production.infrastructure.postgres_owner.conversation_v1 import (
    AppendTurnMemberInput,
    ArchiveConversationInput,
    ConversationRecord,
    ConversationStoreConflict,
    CreateConversationInput,
    PostgresConversationV1Store,
    ReviseTurnFeedbackInput,
    SessionFactory,
    TurnMemberRecord,
    TurnFeedbackRecord,
    TurnFeedbackStoreError,
)
from atlas_production.modules.conversation.public import (
    AppendTurnMemberV1,
    ConversationArchiveError,
    ConversationArchiveResultV1,
    ConversationArchiveV1,
    ConversationCreateV1,
    ConversationMembershipConflict,
    ConversationTurnMemberV1,
    ConversationV1,
    TurnFeedbackError,
    TurnFeedbackRevisionV1,
    TurnFeedbackUpdateV1,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


_DEFAULT_TITLE = "New conversation"
_MAX_ORDINAL_CAS_ATTEMPTS = 8


def _conversation(record: ConversationRecord) -> ConversationV1:
    return ConversationV1(
        conversation_id=record.conversation_id,
        owner_actor_id=record.owner_actor_id,
        title=record.title,
        status=record.status,
        response_language=record.response_language,
        reasoning_mode=record.reasoning_mode,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _turn(record: TurnMemberRecord) -> ConversationTurnMemberV1:
    return ConversationTurnMemberV1(
        turn_id=record.turn_id,
        conversation_id=record.conversation_id,
        execution_id=record.execution_id,
        role=record.role,
        ordinal=record.ordinal,
        created_at=record.created_at,
    )
def _feedback(record: TurnFeedbackRecord) -> TurnFeedbackRevisionV1:
    return TurnFeedbackRevisionV1(
        feedback=record.feedback,
        revision=record.revision,
        updated_at=record.updated_at,
    )




class PostgresConversationV1Adapter:
    """Hides store records and the owner-internal ordinal CAS from callers."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._store = PostgresConversationV1Store(session_factory)

    def create(self, *, actor_id: str, command: ConversationCreateV1) -> ConversationV1:
        replay_key = command.idempotency_key or str(uuid4())
        conversation_id = (
            f"conversation-{uuid5(NAMESPACE_URL, f'conversation:{actor_id}:{replay_key}')}"
        )
        return _conversation(
            self._store.create(
                CreateConversationInput(
                    conversation_id=conversation_id,
                    actor_id=actor_id,
                    title=command.title or _DEFAULT_TITLE,
                    idempotency_key=replay_key,
                    response_language=command.response_language,
                    tag_refs=tuple(
                        (ref.tag_type, ref.tag_id) for ref in command.tag_refs
                    ),
                )
            )
        )

    def append_turn_member(
        self, *, actor_id: str, command: AppendTurnMemberV1
    ) -> ConversationTurnMemberV1:
        return self._append_turn_member(
            actor_id=actor_id, command=command, retry_of_turn_id=None
        )

    def archive(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        command: ConversationArchiveV1,
    ) -> ConversationArchiveResultV1:
        current = self._store.get(conversation_id)
        if current is None or current.owner_actor_id != actor_id:
            raise ConversationArchiveError("not_found")
        audit_event_ref = f"audit-{uuid5(NAMESPACE_URL, f'conversation-archive:{actor_id}:{conversation_id}:{command.idempotency_key}').hex}"
        try:
            result = self._store.archive(
                ArchiveConversationInput(
                    conversation_id=conversation_id,
                    actor_id=actor_id,
                    expected_next_ordinal=command.expected_next_ordinal,
                    idempotency_key=command.idempotency_key,
                ),
                audit_event=AuditEventRecord(
                    event_id=audit_event_ref,
                    event_type="conversation_archived",
                    actor_id=actor_id,
                    target_ref=f"conversation:{conversation_id}",
                    project_id=None,
                    message_code="conversation.was_archived",
                    metadata={"status": "archived"},
                    created_at=utc_now_iso(),
                ),
            )
        except ConversationStoreConflict as error:
            latest = self._store.get(conversation_id)
            reason = (
                "not_found"
                if latest is None
                or latest.owner_actor_id != actor_id
                or latest.status != "active"
                else "conflict"
            )
            raise ConversationArchiveError(reason) from error
        return ConversationArchiveResultV1(
            conversation=_conversation(result.conversation),
            audit_event_ref=result.audit_event_ref,
        )

    def revise_turn_feedback(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        turn_id: str,
        command: TurnFeedbackUpdateV1,
    ) -> TurnFeedbackRevisionV1:
        try:
            return _feedback(
                self._store.revise_turn_feedback(
                    ReviseTurnFeedbackInput(
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        actor_id=actor_id,
                        feedback=command.feedback,
                        expected_revision=command.expected_revision,
                        idempotency_key=command.idempotency_key,
                    )
                )
            )
        except TurnFeedbackStoreError as error:
            raise TurnFeedbackError(error.reason) from error

    def current_turn_feedback(
        self, turn_id: str
    ) -> TurnFeedbackRevisionV1 | None:
        try:
            record = self._store.current_turn_feedback(turn_id)
        except TurnFeedbackStoreError as error:
            raise TurnFeedbackError(error.reason) from error
        return None if record is None else _feedback(record)

    def append_retry_turn_member(
        self,
        *,
        actor_id: str,
        command: AppendTurnMemberV1,
        retry_of_turn_id: str,
    ) -> ConversationTurnMemberV1:
        if command.operation != "retry_turn":
            raise ValueError("retry lineage requires a retry_turn command")
        return self._append_turn_member(
            actor_id=actor_id,
            command=command,
            retry_of_turn_id=retry_of_turn_id,
        )

    def _append_turn_member(
        self,
        *,
        actor_id: str,
        command: AppendTurnMemberV1,
        retry_of_turn_id: str | None,
    ) -> ConversationTurnMemberV1:
        for _ in range(_MAX_ORDINAL_CAS_ATTEMPTS):
            replay = self.get_turn(command.turn_id)
            conversation = self._store.get(command.conversation_id)
            if (
                conversation is None
                or conversation.owner_actor_id != actor_id
                or conversation.status != "active"
            ):
                raise ConversationMembershipConflict(
                    "conversation changed before membership publication"
                )
            expected_ordinal = replay.ordinal if replay is not None else conversation.next_ordinal
            try:
                return _turn(
                    self._store.append_turn_member(
                        AppendTurnMemberInput(
                            conversation_id=command.conversation_id,
                            actor_id=actor_id,
                            turn_id=command.turn_id,
                            execution_id=command.execution_id,
                            role=command.role,
                            expected_next_ordinal=expected_ordinal,
                            idempotency_key=command.idempotency_key,
                            operation=command.operation,
                            retry_of_turn_id=retry_of_turn_id,
                            reasoning_mode=command.reasoning_mode,
                        )
                    )
                )
            except ConversationStoreConflict as error:
                if "ordinal CAS failed" not in str(error):
                    raise ConversationMembershipConflict(
                        "turn membership publication conflicted"
                    ) from error
                latest = self._store.get(command.conversation_id)
                if (
                    latest is None
                    or latest.owner_actor_id != actor_id
                    or latest.status != "active"
                ):
                    raise ConversationMembershipConflict(
                        "conversation changed before membership publication"
                    ) from error
        raise ConversationMembershipConflict(
            "conversation membership CAS retry limit exceeded"
        )

    def list_for_actor(self, actor_id: str) -> list[ConversationV1]:
        return [_conversation(record) for record in self._store.list_for_actor(actor_id)]

    def list_all(self) -> list[ConversationV1]:
        """Admin/audit projection source; authorization remains its caller's owner."""
        return [_conversation(record) for record in self._store.list_all()]

    def get(self, conversation_id: str) -> ConversationV1 | None:
        record = self._store.get(conversation_id)
        return None if record is None else _conversation(record)

    def get_turn(self, turn_id: str) -> ConversationTurnMemberV1 | None:
        if not turn_id:
            raise ValueError("turn_id must be non-empty")
        record = self._store.get_turn(turn_id)
        return None if record is None else _turn(record)

    def candidate_turns(self, conversation_id: str) -> list[ConversationTurnMemberV1]:
        return [_turn(record) for record in self._store.candidate_turns(conversation_id)]

    def retry_sources(self, conversation_id: str) -> dict[str, str]:
        return self._store.retry_sources(conversation_id)


__all__ = ["PostgresConversationV1Adapter"]
