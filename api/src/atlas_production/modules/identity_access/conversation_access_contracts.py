from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationScopeDecision:
    scope: set[tuple[str, str]]
    access_decision_id: str | None
    denied_reason: str | None


@dataclass(frozen=True)
class ConversationAccessContext:
    owner_actor_id: str
    scope_mode: str
    tag_refs: tuple[tuple[str, str], ...]


def conversation_access_context(
    *,
    owner_actor_id: str,
    scope_mode: str,
    tag_refs: list[dict[str, str]],
) -> ConversationAccessContext:
    return ConversationAccessContext(
        owner_actor_id=owner_actor_id,
        scope_mode=scope_mode,
        tag_refs=tuple(
            (str(tag.get("tag_type")), str(tag.get("tag_id")))
            for tag in tag_refs
        ),
    )
