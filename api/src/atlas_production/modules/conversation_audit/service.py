from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Protocol

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.citation_preview.public import (
    ProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.workspace_turn.public import (
    WorkspaceConversationDetailV1,
    WorkspaceTurnApplication,
    WorkspaceTurnError,
)

from .api_models import AdminConversationListResult, RuntimeTraceDetail
from .contracts import ConversationAuditError


class ReadAuditWriter(Protocol):
    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        message_code: str,
        metadata: dict[str, object] | None = None,
        **facts: object,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class ConversationAuditService:
    """Admin-only, audited current projection over strict owner contracts."""

    workspace: WorkspaceTurnApplication
    audit_writer: ReadAuditWriter

    @staticmethod
    def _admin(actor: UserRecord | None) -> UserRecord:
        if actor is None or not actor.active or actor.system_role != "admin":
            raise ConversationAuditError(
                "access_denied", "permission.admin_permission_is_required", 403
            )
        return actor

    def _audit(self, actor: UserRecord, event_type: str, target_ref: str, message_code: str) -> None:
        self.audit_writer.append_read_audit(
            event_type,
            actor_id=actor.actor_id,
            target_ref=target_ref,
            message_code=message_code,
            metadata={"admin_global_history_access": True},
        )

    def list_admin(
        self, actor: UserRecord | None, *, limit: int = 50, cursor: str | None = None
    ) -> AdminConversationListResult:
        admin = self._admin(actor)
        self._audit(
            admin, "read_conversation", "conversation:*",
            "audit.admin_listed_conversation_history",
        )
        items = self.workspace.audit_list_conversations(actor_id=admin.actor_id)
        start = self._cursor_start(items, cursor)
        page = items[start : start + limit]
        next_cursor = self._encode_cursor(page[-1]) if start + limit < len(items) and page else None
        return AdminConversationListResult(conversations=page, next_cursor=next_cursor)

    def get_admin(
        self, actor: UserRecord | None, conversation_id: str
    ) -> WorkspaceConversationDetailV1:
        admin = self._admin(actor)
        self._audit(
            admin, "read_conversation", f"conversation:{conversation_id}",
            "audit.admin_opened_conversation_transcript",
        )
        try:
            return self.workspace.audit_get_conversation(
                actor_id=admin.actor_id, conversation_id=conversation_id
            )
        except WorkspaceTurnError as error:
            raise ConversationAuditError(
                error.error_code, error.message_code, error.status_code
            ) from error

    def get_runtime(
        self, actor: UserRecord | None, conversation_id: str, turn_id: str
    ) -> RuntimeTraceDetail:
        admin = self._admin(actor)
        self._audit(
            admin, "read_runtime_trace", f"conversation:{conversation_id}/turn:{turn_id}",
            "audit.admin_opened_bounded_runtime_trace",
        )
        try:
            snapshot, events, document_discovery = self.workspace.audit_execution(
                actor_id=admin.actor_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
        except WorkspaceTurnError as error:
            raise ConversationAuditError(
                error.error_code, error.message_code, error.status_code
            ) from error
        return RuntimeTraceDetail(
            execution_id=snapshot.execution_id,
            conversation_id=snapshot.conversation_id,
            turn_id=snapshot.turn_id,
            state=snapshot.state,
            version=snapshot.version,
            failure_code=snapshot.terminal_failure_code,
            applied_guidance_revision=snapshot.applied_guidance_revision,
            applied_guidance_digest=snapshot.applied_guidance_digest,
            budget=snapshot.budget,
            document_discovery=document_discovery,
            events=events,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )

    def read_declared_evidence(
        self,
        actor: UserRecord | None,
        conversation_id: str,
        turn_id: str,
        protected_open_ref: str,
    ) -> ProtectedDeclaredEvidenceV1:
        admin = self._admin(actor)
        self._audit(
            admin,
            "read_declared_evidence",
            f"conversation:{conversation_id}/turn:{turn_id}",
            "audit.admin_opened_declared_evidence",
        )
        try:
            return self.workspace.audit_read_declared_evidence(
                actor_id=admin.actor_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                protected_open_ref=protected_open_ref,
            )
        except WorkspaceTurnError as error:
            raise ConversationAuditError(
                error.error_code, error.message_code, error.status_code
            ) from error

    @staticmethod
    def _encode_cursor(conversation) -> str:
        raw = json.dumps(
            [conversation.updated_at.isoformat(), conversation.conversation_id],
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _cursor_start(items, cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            if not cursor or len(cursor) > 1024:
                raise ValueError
            value = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
            )
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not all(isinstance(part, str) and part for part in value)
            ):
                raise ValueError
            key = tuple(value)
            for index, item in enumerate(items):
                if (item.updated_at.isoformat(), item.conversation_id) == key:
                    return index + 1
            raise ValueError
        except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise ConversationAuditError(
                "invalid_cursor", "conversation.audit_cursor_is_invalid", 400
            ) from None
