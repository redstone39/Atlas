from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


PromptSkillCategory = Literal["understanding", "planner", "answer"]
SkillName = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromptSkillRefV1(_ClosedModel):
    category: PromptSkillCategory
    name: SkillName
    revision: int = Field(ge=1)
    content_digest: Digest


class PromptSkillCatalogRefV1(_ClosedModel):
    category: PromptSkillCategory
    catalog_revision: int = Field(ge=1)
    catalog_digest: Digest


class PromptSkillInstructionsV1(_ClosedModel):
    name: SkillName
    revision: int = Field(ge=1)
    content_digest: Digest
    instructions: str = Field(min_length=1)


class PromptSkillSelectorCandidateV1(_ClosedModel):
    selection_id: str = Field(min_length=1, max_length=160)
    name: SkillName
    description: str = Field(min_length=1, max_length=1024)
    ref: PromptSkillRefV1


class PromptSkillRevisionV1(_ClosedModel):
    ref: PromptSkillRefV1
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    created_by: str = Field(min_length=1, max_length=200)
    created_at: datetime
    enabled: bool = False
    source: str | None = None
    instructions: str | None = None


class PromptSkillControlV1(_ClosedModel):
    category: PromptSkillCategory
    name: SkillName
    head_revision: int = Field(ge=1)
    enabled_revision: int | None = Field(default=None, ge=1)
    control_revision: int = Field(ge=1)


class PromptSkillSummaryV1(_ClosedModel):
    control: PromptSkillControlV1
    head: PromptSkillRevisionV1
    revisions: list[PromptSkillRevisionV1] = Field(default_factory=list)


class PromptSkillListV1(_ClosedModel):
    items: list[PromptSkillSummaryV1]


class PromptSkillCatalogV1(_ClosedModel):
    ref: PromptSkillCatalogRefV1
    skills: list[PromptSkillSelectorCandidateV1]


class PromptSkillMutationOutcomeV1(_ClosedModel):
    skill: PromptSkillSummaryV1
    revision: PromptSkillRevisionV1 | None = None
    replayed: bool = False


class PromptSkillLifecycleRequest(_ClosedModel):
    expected_control_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class PromptSkillApprovedPublishV1(_ClosedModel):
    disposition: Literal["add", "revise"]
    category: PromptSkillCategory
    name: SkillName
    source: str = Field(min_length=1, max_length=32_768)
    source_digest: Digest
    expected_catalogs: list[PromptSkillCatalogRefV1] = Field(
        min_length=3, max_length=3
    )
    expected_target: PromptSkillRefV1 | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)

    def model_post_init(self, __context: object) -> None:
        if hashlib.sha256(self.source.encode("utf-8")).hexdigest() != self.source_digest:
            raise ValueError("approved Skill source digest does not bind source")
        if [ref.category for ref in self.expected_catalogs] != [
            "understanding",
            "planner",
            "answer",
        ]:
            raise ValueError("approved Skill requires ordered exact catalog preimages")
        if self.disposition == "add" and self.expected_target is not None:
            raise ValueError("add publication cannot carry an existing target")
        if self.disposition == "revise" and (
            self.expected_target is None
            or self.expected_target.category != self.category
            or self.expected_target.name != self.name
        ):
            raise ValueError("revise publication requires its exact target preimage")


__all__ = [
    "PromptSkillApprovedPublishV1",
    "PromptSkillCatalogRefV1",
    "PromptSkillCatalogV1",
    "PromptSkillCategory",
    "PromptSkillControlV1",
    "PromptSkillInstructionsV1",
    "PromptSkillLifecycleRequest",
    "PromptSkillListV1",
    "PromptSkillMutationOutcomeV1",
    "PromptSkillRefV1",
    "PromptSkillRevisionV1",
    "PromptSkillSelectorCandidateV1",
    "PromptSkillSummaryV1",
]
