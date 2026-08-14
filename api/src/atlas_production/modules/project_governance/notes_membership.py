from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]


class CurrentProjectNotesMembershipSnapshot(BaseModel):
    """Current Project authority projected to Notes' equal-member model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: Identity
    project_id: Identity
    member: bool
    system_admin: bool
    reason: Literal[
        "member",
        "system_admin",
        "actor_inactive_or_missing",
        "actor_not_human",
        "project_missing",
        "missing_permission",
        "deny_grant",
        "invalid_hierarchy",
    ]


class CurrentProjectNotesMembershipReader(Protocol):
    """Reads Project Notes membership from Project Governance authority."""

    def current_project_notes_membership(
        self,
        *,
        actor_type: str,
        actor_id: Identity,
        project_id: Identity,
    ) -> CurrentProjectNotesMembershipSnapshot: ...


__all__ = [
    "CurrentProjectNotesMembershipReader",
    "CurrentProjectNotesMembershipSnapshot",
]
