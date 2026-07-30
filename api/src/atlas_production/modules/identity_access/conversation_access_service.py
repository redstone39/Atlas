from __future__ import annotations

from .records import (
    UserRecord,
)
from .conversation_access_contracts import (
    ConversationAccessContext,
    ConversationScopeDecision,
)
from .conversation_access_ports import ConversationAccessRepository


class ConversationAccessService:
    def __init__(self, repository: ConversationAccessRepository) -> None:
        self.repository = repository

    def resolve_scope(
        self,
        actor: UserRecord,
        conversation: ConversationAccessContext,
        *,
        action: str = "workspace_query",
    ) -> ConversationScopeDecision:
        effective_scope = self.repository.effective_scope(actor)
        if conversation.scope_mode == "selected_tags":
            requested = set(conversation.tag_refs)
            if not requested or not requested.issubset(effective_scope):
                decision_id = self.repository.persist_scope_decision(actor, set(), action=action, allowed=False, reason="unauthorized_tag_filter")
                return ConversationScopeDecision(
                    scope=set(),
                    access_decision_id=decision_id,
                    denied_reason="unauthorized_tag_filter",
                )
            decision_id = self.repository.persist_scope_decision(actor, requested, action=action, allowed=True, reason="authorized_scope")
            return ConversationScopeDecision(
                scope=requested,
                access_decision_id=decision_id,
                denied_reason=None,
            )
        if not effective_scope:
            decision_id = self.repository.persist_scope_decision(actor, set(), action=action, allowed=False, reason="missing_permission")
            return ConversationScopeDecision(
                scope=set(),
                access_decision_id=decision_id,
                denied_reason="missing_permission",
            )
        decision_id = self.repository.persist_scope_decision(actor, effective_scope, action=action, allowed=True, reason="authorized_scope")
        return ConversationScopeDecision(
            scope=effective_scope,
            access_decision_id=decision_id,
            denied_reason=None,
        )

    def can_open(
        self,
        actor: UserRecord,
        conversation: ConversationAccessContext,
    ) -> bool:
        return conversation.owner_actor_id == actor.actor_id
