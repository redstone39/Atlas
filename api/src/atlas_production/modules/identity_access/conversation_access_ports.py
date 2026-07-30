from __future__ import annotations

from typing import Protocol

from .records import UserRecord
from .conversation_access_contracts import (
    ConversationAccessContext,
    ConversationScopeDecision,
)


class ConversationAccessRuntime(Protocol):
    def resolve_scope(
        self,
        actor: UserRecord,
        conversation: ConversationAccessContext,
        *,
        action: str = "workspace_query",
    ) -> ConversationScopeDecision: ...

    def can_open(
        self,
        actor: UserRecord,
        conversation: ConversationAccessContext,
    ) -> bool: ...


class ConversationAccessRepository(Protocol):
    def effective_scope(
        self,
        actor: UserRecord,
    ) -> set[tuple[str, str]]: ...

    def persist_scope_decision(
        self,
        actor: UserRecord,
        scope: set[tuple[str, str]],
        *,
        action: str,
        allowed: bool,
        reason: str,
    ) -> str: ...
