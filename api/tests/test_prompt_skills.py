from __future__ import annotations
from datetime import datetime, timezone


import pytest
from sqlalchemy import Text
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.persistence.prompt_skills import (
    AtlasPromptSkillRevisionRow,
)
from atlas_production.infrastructure.postgres_owner import prompt_skills as prompt_skill_owner
from atlas_production.infrastructure.postgres_owner.prompt_skills import (
    PostgresPromptSkillOwner,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.prompt_skills.public import (
    PromptSkillError,
    PromptSkillListV1,
    PromptSkillControlV1,
    PromptSkillLifecycleRequest,
    PromptSkillMutationOutcomeV1,
    PromptSkillRefV1,
    PromptSkillRevisionV1,
    PromptSkillSummaryV1,
    PromptSkillService,
)
from atlas_production.modules.prompt_skills.service import (
    MAX_PROMPT_SKILL_SOURCE_BYTES,
    parse_skill_file,
)
from atlas_production.openapi_app import create_openapi_app


def _skill(
    *,
    name: str = "compare-options",
    description: str = "Compare alternatives.",
    extra: str = "",
    body: str = "Compare the alternatives before selecting one.",
) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra}"
        "---\n"
        f"{body}\n"
    ).encode()


def _skill_with_size(size: int, *, terminal_lf: bool = True) -> bytes:
    prefix = (
        b"---\n"
        b"name: compare-options\n"
        b"description: Compare alternatives.\n"
        b"---\n"
    )
    suffix = b"\n" if terminal_lf else b""
    body_size = size - len(prefix) - len(suffix)
    assert body_size > 0
    return prefix + (b"x" * body_size) + suffix


def _actor(*, admin: bool = True, active: bool = True) -> UserRecord:
    return UserRecord(
        actor_id="actor-1",
        display_name="Admin",
        email="admin@example.test",
        system_role="admin" if admin else "member",
        password_digest=None,
        active=active,
    )


class _Repository:
    def __init__(self) -> None:
        self.upload_args = None
        self.upload_calls = 0
        self.list_categories: list[str] = []

    def list_skills(self, category):
        self.list_categories.append(category)
        return PromptSkillListV1(items=[])

    def upload_parsed(self, **kwargs):
        self.upload_calls += 1
        self.upload_args = kwargs
        ref = PromptSkillRefV1(
            category=kwargs["category"],
            name=kwargs["path_name"],
            revision=kwargs["expected_head_revision"] + 1,
            content_digest=kwargs["content_digest"],
        )
        revision = PromptSkillRevisionV1(
            ref=ref,
            description=kwargs["description"],
            license=kwargs["license"],
            compatibility=kwargs["compatibility"],
            metadata=kwargs["metadata"],
            created_by=kwargs["actor_id"],
            created_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        )
        control = PromptSkillControlV1(
            category=kwargs["category"],
            name=kwargs["path_name"],
            head_revision=ref.revision,
            enabled_revision=None,
            control_revision=1,
        )
        return PromptSkillMutationOutcomeV1(
            skill=PromptSkillSummaryV1(
                control=control,
                head=revision,
                revisions=[revision],
            ),
            revision=revision,
        )


class _Principal:
    def __init__(self, actor):
        self.actor = actor

    def current_user(self, _token):
        return self.actor


def _composition(repository: _Repository, actor=None) -> ApiComposition:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(
        current_principal=_Principal(actor or _actor()),
        prompt_skills=PromptSkillService(repository),
    )
    return ApiComposition(**values)


def test_parse_skill_file_normalizes_and_accepts_supported_frontmatter() -> None:
    parsed = parse_skill_file(
        "SKILL.md",
        _skill(
            extra=(
                "license: Apache-2.0\n"
                "compatibility: Atlas Deep planner\n"
                "allowed-tools: \"\"\n"
                "metadata:\n  owner: planning\n"
            )
        ).replace(b"\n", b"\r\n"),
    )

    assert parsed.name == "compare-options"
    assert parsed.license == "Apache-2.0"
    assert parsed.compatibility == "Atlas Deep planner"
    assert parsed.metadata == {"owner": "planning"}
    assert "\r" not in parsed.source
    assert parsed.source.endswith("\n") and not parsed.source.endswith("\n\n")
    assert len(parsed.content_digest) == 64


@pytest.mark.parametrize(
    ("filename", "content", "message_code"),
    [
        ("skill.md", _skill(), "prompt_skills.filename_must_be_skill_md"),
        ("SKILL.md", b"\xff", "prompt_skills.skill_file_must_be_utf8"),
        ("SKILL.md", b"name: no-frontmatter\n", "prompt_skills.frontmatter_is_required"),
        ("SKILL.md", _skill(name="Upper"), "prompt_skills.name_is_invalid"),
        ("SKILL.md", _skill(name="a" * 65), "prompt_skills.name_is_invalid"),
        ("SKILL.md", _skill(body=""), "prompt_skills.instructions_are_required"),
        (
            "SKILL.md",
            _skill(extra="allowed-tools: web_search\n"),
            "prompt_skills.allowed_tools_are_not_supported",
        ),
    ],
)
def test_parse_skill_file_rejects_invalid_contracts(
    filename: str, content: bytes, message_code: str
) -> None:
    with pytest.raises(PromptSkillError) as caught:
        parse_skill_file(filename, content)
    assert caught.value.status_code == 422
    assert caught.value.message_code == message_code


def test_parser_accepts_64_character_name() -> None:
    parsed = parse_skill_file("SKILL.md", _skill(name="a" * 64))
    assert len(parsed.name) == 64

def test_parser_and_storage_preserve_source_bounded_long_license() -> None:
    license_value = "L" * 2048
    repository = _Repository()

    outcome = PromptSkillService(repository).upload(
        _actor(),
        category="planner",
        path_name="compare-options",
        filename="SKILL.md",
        content=_skill(extra=f"license: {license_value}\n"),
        expected_head_revision=0,
        idempotency_key="long-license",
    )

    assert outcome.revision.license == license_value
    assert repository.upload_args["license"] == license_value
    assert isinstance(AtlasPromptSkillRevisionRow.__table__.c.license.type, Text)


@pytest.mark.parametrize("category", ("understanding", "planner", "answer"))
def test_postgres_owner_locks_global_idempotency_key_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    class StopAfterLock(Exception):
        pass

    class Session:
        def __init__(self) -> None:
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def rollback(self) -> None:
            self.rollbacks += 1

    session = Session()
    lock_plans: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        prompt_skill_owner,
        "acquire_owner_locks",
        lambda _session, *, domain_keys=(), identity_keys=(): lock_plans.append(
            tuple(domain_keys)
        ),
    )

    def stop_replay(*_args, **_kwargs):
        raise StopAfterLock

    monkeypatch.setattr(
        PostgresPromptSkillOwner,
        "_replay",
        staticmethod(stop_replay),
    )
    owner = PostgresPromptSkillOwner(lambda: session)

    with pytest.raises(StopAfterLock):
        owner.upload_parsed(
            actor_id="admin",
            category=category,
            path_name="skill-a",
            source="source",
            description="description",
            license=None,
            compatibility=None,
            metadata={},
            instructions="instructions",
            content_digest="a" * 64,
            expected_head_revision=0,
            idempotency_key="shared-key",
        )
    with pytest.raises(StopAfterLock):
        owner.mutate_enabled(
            actor_id="admin",
            ref=PromptSkillRefV1(
                category=category,
                name="skill-b",
                revision=1,
                content_digest="b" * 64,
            ),
            enable=True,
            request=PromptSkillLifecycleRequest(
                expected_control_revision=1,
                idempotency_key="shared-key",
            ),
        )

    assert lock_plans == [
        (
            "prompt-skills:idempotency:shared-key",
            f"prompt-skills:{category}:skill-a",
        ),
        (
            "prompt-skills:idempotency:shared-key",
            f"prompt-skills:{category}:catalog",
            f"prompt-skills:{category}:skill-b",
        ),
    ]
    assert session.rollbacks == 2


def test_service_requires_active_system_admin() -> None:
    service = PromptSkillService(_Repository())
    for actor in (None, _actor(admin=False), _actor(active=False)):
        with pytest.raises(PromptSkillError) as caught:
            service.list_skills(actor, "planner")
        assert caught.value.status_code == 403
        assert caught.value.message_code == "permission.admin_permission_is_required"


def test_upload_cross_checks_path_and_keeps_parsed_body_private() -> None:
    repository = _Repository()
    service = PromptSkillService(repository)

    result = service.upload(
        _actor(),
        category="planner",
        path_name="compare-options",
        filename="SKILL.md",
        content=_skill(),
        expected_head_revision=0,
        idempotency_key="request-1",
    )

    assert result is not None
    assert repository.upload_args["path_name"] == "compare-options"
    assert repository.upload_args["expected_head_revision"] == 0
    assert repository.upload_args["instructions"] == (
        "Compare the alternatives before selecting one."
    )

    with pytest.raises(PromptSkillError) as caught:
        service.upload(
            _actor(),
            category="planner",
            path_name="different-name",
            filename="SKILL.md",
            content=_skill(),
            expected_head_revision=0,
            idempotency_key="request-2",
        )
    assert caught.value.message_code == (
        "prompt_skills.path_and_frontmatter_name_must_match"
    )


@pytest.mark.parametrize("category", ("understanding", "planner", "answer"))
def test_admin_routes_upload_and_fail_closed_on_missing_cas(category: str) -> None:
    repository = _Repository()
    client = TestClient(create_app(_composition(repository)))

    listed = client.get(f"/api/v1/admin/prompt-skills?category={category}")
    assert listed.status_code == 200
    assert listed.json() == {"items": []}

    missing_headers = client.post(
        f"/api/v1/admin/prompt-skills/{category}/compare-options/revisions",
        files={"file": ("SKILL.md", _skill(), "text/markdown")},
    )
    assert missing_headers.status_code == 422
    assert missing_headers.json()["message_code"] == (
        "prompt_skills.if_match_is_required"
    )

    uploaded = client.post(
        f"/api/v1/admin/prompt-skills/{category}/compare-options/revisions",
        headers={"Idempotency-Key": "upload-route-1", "If-Match": "0"},
        files={"file": ("SKILL.md", _skill(), "text/markdown")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["revision"]["ref"]["revision"] == 1
    assert uploaded.json()["skill"]["control"]["enabled_revision"] is None
    assert repository.list_categories == [category]



def test_admin_upload_route_bounds_skill_file_and_denies_non_admin_before_owner_call() -> None:
    repository = _Repository()
    client = TestClient(create_app(_composition(repository)))
    headers = {"Idempotency-Key": "bounded-upload", "If-Match": "0"}

    exact = client.post(
        "/api/v1/admin/prompt-skills/planner/compare-options/revisions",
        headers=headers,
        files={
            "file": (
                "SKILL.md",
                _skill_with_size(MAX_PROMPT_SKILL_SOURCE_BYTES),
                "text/markdown",
            )
        },
    )
    exact_without_terminal_lf = client.post(
        "/api/v1/admin/prompt-skills/planner/compare-options/revisions",
        headers={**headers, "Idempotency-Key": "bounded-upload-without-lf"},
        files={
            "file": (
                "SKILL.md",
                _skill_with_size(
                    MAX_PROMPT_SKILL_SOURCE_BYTES,
                    terminal_lf=False,
                ),
                "text/markdown",
            )
        },
    )
    oversized = client.post(
        "/api/v1/admin/prompt-skills/planner/compare-options/revisions",
        headers={**headers, "Idempotency-Key": "oversized-upload"},
        files={
            "file": (
                "SKILL.md",
                _skill_with_size(MAX_PROMPT_SKILL_SOURCE_BYTES + 1),
                "text/markdown",
            )
        },
    )
    non_admin_repository = _Repository()
    non_admin = TestClient(
        create_app(_composition(non_admin_repository, actor=_actor(admin=False)))
    ).post(
        "/api/v1/admin/prompt-skills/planner/compare-options/revisions",
        headers={**headers, "Idempotency-Key": "non-admin-upload"},
        files={
            "file": (
                "SKILL.md",
                _skill_with_size(MAX_PROMPT_SKILL_SOURCE_BYTES + 1),
                "text/markdown",
            )
        },
    )

    assert exact.status_code == 201
    assert exact_without_terminal_lf.status_code == 201
    assert oversized.status_code == 422
    assert oversized.json()["message_code"] == (
        "prompt_skills.skill_file_size_is_invalid"
    )
    assert repository.upload_calls == 2
    assert non_admin.status_code == 403
    assert non_admin_repository.upload_calls == 0

def test_admin_routes_deny_non_admin() -> None:
    client = TestClient(
        create_app(_composition(_Repository(), actor=_actor(admin=False)))
    )
    response = client.get("/api/v1/admin/prompt-skills")
    assert response.status_code == 403
    assert response.json()["error_code"] == "access_denied"


def test_openapi_exposes_exact_skill_slot_admin_categories() -> None:
    schema = create_openapi_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/admin/prompt-skills" in paths
    assert (
        "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}"
        in paths
    )
    assert (
        "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}/enable"
        in paths
    )
    assert (
        "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}/disable"
        in paths
    )
    category_schema = paths["/api/v1/admin/prompt-skills"]["get"]["parameters"][0][
        "schema"
    ]
    assert category_schema["enum"] == ["understanding", "planner", "answer"]
    assert category_schema["default"] == "planner"
