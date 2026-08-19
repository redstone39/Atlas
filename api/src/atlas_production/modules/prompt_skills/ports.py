from __future__ import annotations

from typing import Protocol

from atlas_production.modules.identity_access.records import UserRecord

from .api_models import (
    PromptSkillCatalogRefV1,
    PromptSkillCatalogV1,
    PromptSkillCategory,
    PromptSkillInstructionsV1,
    PromptSkillLifecycleRequest,
    PromptSkillListV1,
    PromptSkillMutationOutcomeV1,
    PromptSkillRefV1,
    PromptSkillRevisionV1,
)


class PromptSkillCatalog(Protocol):
    def current_catalog(self, category: PromptSkillCategory) -> PromptSkillCatalogRefV1: ...
    def read_catalog(self, ref: PromptSkillCatalogRefV1) -> PromptSkillCatalogV1: ...


class PromptSkillExactReader(Protocol):
    def read_instructions(self, ref: PromptSkillRefV1) -> PromptSkillInstructionsV1: ...


class PromptSkillAdmin(Protocol):
    def list_skills(self, actor: UserRecord | None, category: PromptSkillCategory) -> PromptSkillListV1: ...
    def get_revision(
        self,
        actor: UserRecord | None,
        category: PromptSkillCategory,
        name: str,
        revision: int,
    ) -> PromptSkillRevisionV1: ...
    def upload(
        self,
        actor: UserRecord | None,
        *,
        category: PromptSkillCategory,
        path_name: str,
        filename: str,
        content: bytes,
        expected_head_revision: int,
        idempotency_key: str,
    ) -> PromptSkillMutationOutcomeV1: ...
    def enable(
        self,
        actor: UserRecord | None,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1: ...
    def disable(
        self,
        actor: UserRecord | None,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1: ...


class PromptSkillRepository(PromptSkillCatalog, PromptSkillExactReader, Protocol):
    def list_skills(self, category: PromptSkillCategory) -> PromptSkillListV1: ...
    def get_revision_by_identity(
        self,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        include_body: bool,
    ) -> PromptSkillRevisionV1: ...
    def upload_parsed(
        self,
        *,
        actor_id: str,
        category: PromptSkillCategory,
        path_name: str,
        source: str,
        description: str,
        license: str | None,
        compatibility: str | None,
        metadata: dict[str, str],
        instructions: str,
        content_digest: str,
        expected_head_revision: int,
        idempotency_key: str,
    ) -> PromptSkillMutationOutcomeV1: ...
    def mutate_enabled(
        self,
        *,
        actor_id: str,
        ref: PromptSkillRefV1,
        enable: bool,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1: ...


__all__ = [
    "PromptSkillAdmin",
    "PromptSkillCatalog",
    "PromptSkillExactReader",
    "PromptSkillRepository",
]
