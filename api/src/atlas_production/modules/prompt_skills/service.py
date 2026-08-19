from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

import yaml

from atlas_production.modules.identity_access.records import UserRecord

from .api_models import (
    PromptSkillCategory,
    PromptSkillLifecycleRequest,
    PromptSkillListV1,
    PromptSkillMutationOutcomeV1,
    PromptSkillRefV1,
    PromptSkillRevisionV1,
)
from .contracts import PromptSkillError
from .ports import PromptSkillRepository


MAX_PROMPT_SKILL_SOURCE_BYTES = 32 * 1024
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


@dataclass(frozen=True)
class ParsedPromptSkill:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    source: str
    instructions: str
    content_digest: str


def _invalid(message_code: str = "prompt_skills.skill_file_is_invalid") -> PromptSkillError:
    return PromptSkillError("invalid_prompt_skill", message_code, 422)


def parse_skill_file(
    filename: str,
    content: bytes,
    *,
    persisted_source: bool = False,
) -> ParsedPromptSkill:
    if filename != "SKILL.md":
        raise _invalid("prompt_skills.filename_must_be_skill_md")
    max_bytes = MAX_PROMPT_SKILL_SOURCE_BYTES + (1 if persisted_source else 0)
    if not content or len(content) > max_bytes:
        raise _invalid("prompt_skills.skill_file_size_is_invalid")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _invalid("prompt_skills.skill_file_must_be_utf8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    if not text.startswith("---\n"):
        raise _invalid("prompt_skills.frontmatter_is_required")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise _invalid("prompt_skills.frontmatter_is_invalid")
    try:
        frontmatter = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise _invalid("prompt_skills.frontmatter_is_invalid") from exc
    if not isinstance(frontmatter, dict) or any(
        not isinstance(key, str) for key in frontmatter
    ):
        raise _invalid("prompt_skills.frontmatter_is_invalid")
    if set(frontmatter) - _ALLOWED_FRONTMATTER:
        raise _invalid("prompt_skills.frontmatter_has_unsupported_fields")

    name = frontmatter.get("name")
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 64
        or _NAME_PATTERN.fullmatch(name) is None
    ):
        raise _invalid("prompt_skills.name_is_invalid")
    description = frontmatter.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        raise _invalid("prompt_skills.description_is_invalid")

    license_value = frontmatter.get("license")
    if license_value is not None and (
        not isinstance(license_value, str) or not license_value.strip()
    ):
        raise _invalid("prompt_skills.license_is_invalid")
    compatibility = frontmatter.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str) or not 1 <= len(compatibility) <= 500
    ):
        raise _invalid("prompt_skills.compatibility_is_invalid")
    metadata_value = frontmatter.get("metadata", {})
    if not isinstance(metadata_value, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata_value.items()
    ):
        raise _invalid("prompt_skills.metadata_is_invalid")
    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools is not None and allowed_tools != "":
        raise _invalid("prompt_skills.allowed_tools_are_not_supported")

    instructions = text[end + 5 :].strip()
    if not instructions:
        raise _invalid("prompt_skills.instructions_are_required")
    return ParsedPromptSkill(
        name=name,
        description=description,
        license=license_value,
        compatibility=compatibility,
        metadata=dict(sorted(metadata_value.items())),
        source=text,
        instructions=instructions,
        content_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class PromptSkillService:
    def __init__(self, repository: PromptSkillRepository) -> None:
        self._repository = repository

    @staticmethod
    def _admin(actor: UserRecord | None) -> UserRecord:
        if actor is None or not actor.active or actor.system_role != "admin":
            raise PromptSkillError(
                "access_denied", "permission.admin_permission_is_required", 403
            )
        return actor

    def list_skills(
        self, actor: UserRecord | None, category: PromptSkillCategory
    ) -> PromptSkillListV1:
        self._admin(actor)
        return self._repository.list_skills(category)

    def get_revision(
        self,
        actor: UserRecord | None,
        category: PromptSkillCategory,
        name: str,
        revision: int,
    ) -> PromptSkillRevisionV1:
        self._admin(actor)
        return self._repository.get_revision_by_identity(
            category=category,
            name=name,
            revision=revision,
            include_body=True,
        )

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
    ) -> PromptSkillMutationOutcomeV1:
        admin = self._admin(actor)
        parsed = parse_skill_file(filename, content)
        if parsed.name != path_name:
            raise _invalid("prompt_skills.path_and_frontmatter_name_must_match")
        if not idempotency_key or len(idempotency_key) > 200:
            raise _invalid("prompt_skills.idempotency_key_is_invalid")
        if expected_head_revision < 0:
            raise _invalid("prompt_skills.expected_revision_is_invalid")
        return self._repository.upload_parsed(
            actor_id=admin.actor_id,
            category=category,
            path_name=path_name,
            source=parsed.source,
            description=parsed.description,
            license=parsed.license,
            compatibility=parsed.compatibility,
            metadata=parsed.metadata,
            instructions=parsed.instructions,
            content_digest=parsed.content_digest,
            expected_head_revision=expected_head_revision,
            idempotency_key=idempotency_key,
        )

    def enable(
        self,
        actor: UserRecord | None,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1:
        admin = self._admin(actor)
        stored = self._repository.get_revision_by_identity(
            category=category, name=name, revision=revision, include_body=False
        )
        return self._repository.mutate_enabled(
            actor_id=admin.actor_id, ref=stored.ref, enable=True, request=request
        )

    def disable(
        self,
        actor: UserRecord | None,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1:
        admin = self._admin(actor)
        stored = self._repository.get_revision_by_identity(
            category=category, name=name, revision=revision, include_body=False
        )
        return self._repository.mutate_enabled(
            actor_id=admin.actor_id, ref=stored.ref, enable=False, request=request
        )


__all__ = ["ParsedPromptSkill", "PromptSkillService", "parse_skill_file"]
