"""System Admin service for the global Answer behavior owner."""

from __future__ import annotations

from typing import Protocol

from atlas_production.modules.identity_access.records import UserRecord

from .public import (
    AnswerBehaviorError,
    AnswerBehaviorStatus,
    AnswerBehaviorUpdateRequest,
)


class AnswerBehaviorRepository(Protocol):
    def status(self) -> AnswerBehaviorStatus: ...

    def update(
        self, *, actor_id: str, payload: AnswerBehaviorUpdateRequest
    ) -> AnswerBehaviorStatus: ...


class AnswerBehaviorService:
    def __init__(self, repository: AnswerBehaviorRepository) -> None:
        self._repository = repository

    @staticmethod
    def _admin(actor: UserRecord | None) -> UserRecord:
        if actor is None or not actor.active or actor.system_role != "admin":
            raise AnswerBehaviorError(
                "access_denied", "permission.admin_permission_is_required", 403
            )
        return actor

    def get(self, actor: UserRecord | None) -> AnswerBehaviorStatus:
        self._admin(actor)
        return self._repository.status()

    def update(
        self,
        actor: UserRecord | None,
        payload: AnswerBehaviorUpdateRequest,
    ) -> AnswerBehaviorStatus:
        admin = self._admin(actor)
        return self._repository.update(actor_id=admin.actor_id, payload=payload)


__all__ = ["AnswerBehaviorService"]
