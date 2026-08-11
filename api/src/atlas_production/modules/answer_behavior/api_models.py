from __future__ import annotations

from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from atlas_production.modules.conversation.public import ResponseLanguage


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AnswerBehaviorRevisionV1(_StrictModel):
    revision: int = Field(ge=0)
    custom_guidance: str | None = Field(default=None, max_length=2000)
    guidance_digest: Digest | None = None
    created_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_revision_shape(self) -> "AnswerBehaviorRevisionV1":
        empty = self.revision == 0
        if empty != (
            self.custom_guidance is None
            and self.guidance_digest is None
            and self.created_at is None
        ):
            raise ValueError("revision zero is the only empty Answer behavior revision")
        if not empty and (
            self.guidance_digest is None or self.created_at is None
        ):
            raise ValueError("positive Answer behavior revision requires immutable content")
        return self


class AnswerBehaviorUpdateRequest(_StrictModel):
    custom_guidance: str | None = None
    expected_revision: int = Field(ge=0)
    idempotency_key: Identity

    @field_validator("custom_guidance")
    @classmethod
    def normalize_custom_guidance(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 2000:
            raise ValueError("custom guidance exceeds 2000 characters")
        return normalized


class AnswerBehaviorStatus(_StrictModel):
    revision: int = Field(ge=0)
    custom_guidance: str | None = Field(default=None, max_length=2000)
    guidance_digest: Digest | None = None
    updated_by: Identity | None = None
    updated_at: AwareDatetime | None = None
    audit_event_ref: Identity | None = None

    @model_validator(mode="after")
    def require_status_shape(self) -> "AnswerBehaviorStatus":
        empty = self.revision == 0
        if empty and any(
            value is not None
            for value in (
                self.custom_guidance,
                self.guidance_digest,
                self.updated_by,
                self.updated_at,
                self.audit_event_ref,
            )
        ):
            raise ValueError("revision zero is the only empty Answer behavior status")
        if not empty and (
            self.guidance_digest is None
            or self.updated_by is None
            or self.updated_at is None
            or self.audit_event_ref is None
        ):
            raise ValueError("positive Answer behavior status requires trace metadata")
        return self


class AnswerBehaviorInputV1(_StrictModel):
    response_language: ResponseLanguage
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: Digest | None = None
    custom_guidance: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_input_snapshot_shape(self) -> "AnswerBehaviorInputV1":
        empty = self.applied_guidance_revision == 0
        if empty != (
            self.applied_guidance_digest is None and self.custom_guidance is None
        ):
            raise ValueError("Answer input guidance snapshot is inconsistent")
        if not empty and self.applied_guidance_digest is None:
            raise ValueError("positive Answer input revision requires immutable digest")
        return self


__all__ = [
    "AnswerBehaviorInputV1",
    "AnswerBehaviorRevisionV1",
    "AnswerBehaviorStatus",
    "AnswerBehaviorUpdateRequest",
]
