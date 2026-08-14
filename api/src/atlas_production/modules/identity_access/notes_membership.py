from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]


class CurrentTeamNotesMembershipSnapshot(BaseModel):
    """Current Team authority projected to Notes' equal-member model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: Identity
    team_id: Identity
    member: bool
    system_admin: bool
    reason: Literal[
        "member",
        "system_admin",
        "actor_inactive_or_missing",
        "actor_not_human",
        "team_missing_or_retired",
        "missing_membership",
        "invalid_hierarchy",
    ]


class CurrentTeamNotesMembershipReader(Protocol):
    """Reads Team Notes membership from Identity Access authority."""

    def current_team_notes_membership(
        self,
        *,
        actor_type: str,
        actor_id: Identity,
        team_id: Identity,
    ) -> CurrentTeamNotesMembershipSnapshot: ...


__all__ = [
    "CurrentTeamNotesMembershipReader",
    "CurrentTeamNotesMembershipSnapshot",
]
