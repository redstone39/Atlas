from __future__ import annotations

from typing import Protocol

from atlas_production.modules.identity_access.records import UserRecord

from .api_models import (
    AnswerBehaviorRevisionV1,
    AnswerBehaviorStatus,
    AnswerBehaviorUpdateRequest,
)


class AnswerBehaviorOwner(Protocol):
    def current(self) -> AnswerBehaviorRevisionV1: ...

    def read_exact(
        self, *, revision: int, guidance_digest: str | None
    ) -> AnswerBehaviorRevisionV1: ...


class AnswerBehaviorAdmin(Protocol):
    def get(self, actor: UserRecord | None) -> AnswerBehaviorStatus: ...

    def update(
        self, actor: UserRecord | None, payload: AnswerBehaviorUpdateRequest
    ) -> AnswerBehaviorStatus: ...


class AnswerBehaviorRepository(Protocol):
    def status(self) -> AnswerBehaviorStatus: ...

    def update(
        self, *, actor_id: str, payload: AnswerBehaviorUpdateRequest
    ) -> AnswerBehaviorStatus: ...


__all__ = [
    "AnswerBehaviorAdmin",
    "AnswerBehaviorOwner",
    "AnswerBehaviorRepository",
]
