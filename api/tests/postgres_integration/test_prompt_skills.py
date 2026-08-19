from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Barrier

from sqlalchemy import delete, select

import pytest

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.prompt_skills import (
    AtlasPromptSkillCatalogRevisionRow,
    AtlasPromptSkillControlRow,
    AtlasPromptSkillIdempotencyRow,
    AtlasPromptSkillRevisionRow,
)
from atlas_production.infrastructure.postgres_owner.prompt_skills import (
    PostgresPromptSkillOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.prompt_skills.public import (
    PromptSkillError,
    PromptSkillLifecycleRequest,
    PromptSkillService,
)
from atlas_production.modules.prompt_skills.service import (
    MAX_PROMPT_SKILL_SOURCE_BYTES,
)


ADMIN = UserRecord("prompt-skills-admin", "Admin", None, "admin", None)
CATEGORIES = ("understanding", "planner", "answer")


def _source(name: str, body: str, *, license: str | None = None) -> bytes:
    license_line = "" if license is None else f"license: {license}\n"
    return (
        f"---\nname: {name}\ndescription: Compare options safely.\n"
        f"{license_line}---\n{body}\n"
    ).encode()

def _exact_size_source_without_terminal_lf(name: str) -> bytes:
    prefix = (
        f"---\nname: {name}\ndescription: Compare options safely.\n---\n"
    ).encode()
    return prefix + (b"x" * (MAX_PROMPT_SKILL_SOURCE_BYTES - len(prefix)))


@pytest.fixture(autouse=True)
def clean_rows(postgres_runtime: PostgresRuntime):
    def clean() -> None:
        with postgres_runtime.session_factory() as session, session.begin():
            session.execute(
                delete(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.target_ref.like("prompt-skill:%")
                )
            )
            session.execute(delete(AtlasPromptSkillIdempotencyRow))
            session.execute(delete(AtlasPromptSkillControlRow))
            session.execute(delete(AtlasPromptSkillRevisionRow))
            session.execute(
                delete(AtlasPromptSkillCatalogRevisionRow).where(
                    AtlasPromptSkillCatalogRevisionRow.catalog_revision > 1
                )
            )
    clean()
    yield
    clean()


@pytest.mark.parametrize("category", CATEGORIES)
def test_prompt_skill_lifecycle_pins_immutable_catalogs_and_safe_audit(
    postgres_runtime: PostgresRuntime,
    category: str,
) -> None:
    owner = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    service = PromptSkillService(owner)

    empty = owner.current_catalog(category)
    assert empty.catalog_revision == 1
    assert owner.read_catalog(empty).skills == []

    first = service.upload(
        ADMIN,
        category=category,
        path_name="compare-options",
        filename="SKILL.md",
        content=_source("compare-options", "Use a decision table."),
        expected_head_revision=0,
        idempotency_key=f"{category}-prompt-upload-1",
    )
    replay = service.upload(
        ADMIN,
        category=category,
        path_name="compare-options",
        filename="SKILL.md",
        content=_source("compare-options", "Use a decision table."),
        expected_head_revision=0,
        idempotency_key=f"{category}-prompt-upload-1",
    )
    assert replay.replayed is True
    assert replay.revision == first.revision
    assert owner.current_catalog(category) == empty

    enabled = service.enable(
        ADMIN,
        category=category,
        name="compare-options",
        revision=1,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=1,
            idempotency_key=f"{category}-prompt-enable-1",
        ),
    )
    catalog_two = owner.current_catalog(category)
    assert catalog_two.catalog_revision == 2
    assert owner.read_catalog(catalog_two).skills[0].ref == first.revision.ref
    assert enabled.skill.control.enabled_revision == 1

    second = service.upload(
        ADMIN,
        category=category,
        path_name="compare-options",
        filename="SKILL.md",
        content=_source("compare-options", "Compare tradeoffs explicitly."),
        expected_head_revision=1,
        idempotency_key=f"{category}-prompt-upload-2",
    )
    assert second.revision.enabled is False
    assert second.skill.control.enabled_revision == 1
    assert owner.current_catalog(category) == catalog_two

    switched = service.enable(
        ADMIN,
        category=category,
        name="compare-options",
        revision=2,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=3,
            idempotency_key=f"{category}-prompt-enable-2",
        ),
    )
    catalog_three = owner.current_catalog(category)
    assert switched.skill.control.enabled_revision == 2
    assert catalog_three.catalog_revision == 3

    disabled = service.disable(
        ADMIN,
        category=category,
        name="compare-options",
        revision=2,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=4,
            idempotency_key=f"{category}-prompt-disable-2",
        ),
    )
    assert disabled.skill.control.enabled_revision is None
    assert owner.read_catalog(owner.current_catalog(category)).skills == []
    assert owner.read_catalog(catalog_two).skills[0].ref.revision == 1
    assert owner.read_catalog(catalog_three).skills[0].ref.revision == 2
    assert owner.read_instructions(first.revision.ref).instructions == (
        "Use a decision table."
    )

    with pytest.raises(PromptSkillError) as stale:
        service.disable(
            ADMIN,
            category=category,
            name="compare-options",
            revision=2,
            request=PromptSkillLifecycleRequest(
                expected_control_revision=4,
                idempotency_key=f"{category}-prompt-disable-stale",
            ),
        )
    assert stale.value.status_code == 412

    with postgres_runtime.session_factory() as session:
        rows = list(
            session.scalars(
                select(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.target_ref.like("prompt-skill:%")
                )
            )
        )
    assert len(rows) == 5
    assert all("instructions" not in str(row.event_metadata) for row in rows)
    assert all("Use a decision table" not in str(row.event_metadata) for row in rows)

def test_prompt_skill_same_name_isolated_across_categories(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    service = PromptSkillService(owner)

    revisions = {}
    for category in CATEGORIES:
        uploaded = service.upload(
            ADMIN,
            category=category,
            path_name="shared-name",
            filename="SKILL.md",
            content=_source("shared-name", f"Instructions for {category}."),
            expected_head_revision=0,
            idempotency_key=f"{category}-shared-name-upload",
        )
        service.enable(
            ADMIN,
            category=category,
            name="shared-name",
            revision=1,
            request=PromptSkillLifecycleRequest(
                expected_control_revision=1,
                idempotency_key=f"{category}-shared-name-enable",
            ),
        )
        revisions[category] = uploaded.revision.ref

    assert {ref.category for ref in revisions.values()} == set(CATEGORIES)
    for category in CATEGORIES:
        catalog = owner.read_catalog(owner.current_catalog(category))
        assert [candidate.ref for candidate in catalog.skills] == [revisions[category]]


def test_prompt_skill_catalog_rejects_cross_category_ref_before_exact_read(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    service = PromptSkillService(owner)
    service.upload(
        ADMIN,
        category="understanding",
        path_name="resolver-skill",
        filename="SKILL.md",
        content=_source("resolver-skill", "Resolve the request."),
        expected_head_revision=0,
        idempotency_key="understanding-tamper-upload",
    )
    service.enable(
        ADMIN,
        category="understanding",
        name="resolver-skill",
        revision=1,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=1,
            idempotency_key="understanding-tamper-enable",
        ),
    )

    with postgres_runtime.session_factory() as session, session.begin():
        row = session.get(
            AtlasPromptSkillCatalogRevisionRow,
            ("understanding", 2),
        )
        assert row is not None
        tampered_refs = [{**row.refs[0], "category": "answer"}]
        row.refs = tampered_refs
        row.catalog_digest = hashlib.sha256(
            json.dumps(
                tampered_refs,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    with pytest.raises(PromptSkillError) as caught:
        owner.current_catalog("understanding")
    assert caught.value.status_code == 503
    assert caught.value.error_code == "prompt_skill_integrity_error"


def test_prompt_skill_long_license_round_trips_through_postgres(
    postgres_runtime: PostgresRuntime,
) -> None:
    service = PromptSkillService(
        PostgresPromptSkillOwner(postgres_runtime.session_factory)
    )
    license_value = "L" * 2048

    uploaded = service.upload(
        ADMIN,
        category="planner",
        path_name="long-license",
        filename="SKILL.md",
        content=_source("long-license", "Preserve this license.", license=license_value),
        expected_head_revision=0,
        idempotency_key="prompt-long-license",
    )

    assert uploaded.revision.license == license_value
    assert service.get_revision(
        ADMIN,
        category="planner",
        name="long-license",
        revision=1,
    ).license == license_value


def test_exact_size_source_without_terminal_lf_round_trips_canonically(
    postgres_runtime: PostgresRuntime,
) -> None:
    service = PromptSkillService(
        PostgresPromptSkillOwner(postgres_runtime.session_factory)
    )
    uploaded = service.upload(
        ADMIN,
        category="planner",
        path_name="exact-size",
        filename="SKILL.md",
        content=_exact_size_source_without_terminal_lf("exact-size"),
        expected_head_revision=0,
        idempotency_key="prompt-exact-size-without-lf",
    )

    exact = service.get_revision(
        ADMIN,
        category="planner",
        name="exact-size",
        revision=1,
    )
    assert len(exact.source.encode()) == MAX_PROMPT_SKILL_SOURCE_BYTES + 1
    assert exact.ref.content_digest == uploaded.revision.ref.content_digest


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("description", "Tampered selector description."),
        ("instructions", "Compare options safely."),
    ],
)
def test_prompt_skill_exact_reads_reject_source_unbound_denormalized_fields(
    postgres_runtime: PostgresRuntime,
    field_name: str,
    tampered_value: str,
) -> None:
    owner = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    service = PromptSkillService(owner)
    uploaded = service.upload(
        ADMIN,
        category="planner",
        path_name="integrity-check",
        filename="SKILL.md",
        content=_source("integrity-check", "Use the canonical instructions."),
        expected_head_revision=0,
        idempotency_key=f"prompt-integrity-upload-{field_name}",
    )
    service.enable(
        ADMIN,
        category="planner",
        name="integrity-check",
        revision=1,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=1,
            idempotency_key=f"prompt-integrity-enable-{field_name}",
        ),
    )
    catalog_ref = owner.current_catalog("planner")
    with postgres_runtime.session_factory() as session, session.begin():
        row = session.get(
            AtlasPromptSkillRevisionRow,
            ("planner", "integrity-check", 1),
        )
        assert row is not None
        setattr(row, field_name, tampered_value)

    for read in (
        lambda: owner.read_catalog(catalog_ref),
        lambda: owner.read_instructions(uploaded.revision.ref),
    ):
        with pytest.raises(PromptSkillError) as caught:
            read()
        assert caught.value.error_code == "prompt_skill_integrity_error"


def test_prompt_skill_global_idempotency_key_serializes_different_targets(
    postgres_runtime: PostgresRuntime,
) -> None:
    service = PromptSkillService(
        PostgresPromptSkillOwner(postgres_runtime.session_factory)
    )
    start = Barrier(2)

    def upload(target: tuple[str, str]):
        category, name = target
        start.wait(timeout=10)
        try:
            return service.upload(
                ADMIN,
                category=category,
                path_name=name,
                filename="SKILL.md",
                content=_source(name, f"Instructions for {category}."),
                expected_head_revision=0,
                idempotency_key="prompt-shared-idempotency",
            )
        except PromptSkillError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                upload,
                (("understanding", "shared-name"), ("answer", "shared-name")),
            )
        )

    successes = [
        outcome for outcome in outcomes if not isinstance(outcome, PromptSkillError)
    ]
    conflicts = [
        outcome for outcome in outcomes if isinstance(outcome, PromptSkillError)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].error_code == "idempotency_conflict"
    with postgres_runtime.session_factory() as session:
        assert len(list(session.scalars(select(AtlasPromptSkillRevisionRow)))) == 1
        assert len(list(session.scalars(select(AtlasPromptSkillControlRow)))) == 1
        assert len(list(session.scalars(select(AtlasPromptSkillIdempotencyRow)))) == 1


@pytest.mark.parametrize("category", CATEGORIES)
def test_prompt_skill_category_lock_serializes_enable_disable_catalog_updates(
    postgres_runtime: PostgresRuntime,
    category: str,
) -> None:
    owner = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    service = PromptSkillService(owner)
    first = service.upload(
        ADMIN,
        category=category,
        path_name="skill-a",
        filename="SKILL.md",
        content=_source("skill-a", "Instructions for A."),
        expected_head_revision=0,
        idempotency_key=f"{category}-prompt-upload-a",
    )
    second = service.upload(
        ADMIN,
        category=category,
        path_name="skill-b",
        filename="SKILL.md",
        content=_source("skill-b", "Instructions for B."),
        expected_head_revision=0,
        idempotency_key=f"{category}-prompt-upload-b",
    )
    service.enable(
        ADMIN,
        category=category,
        name="skill-a",
        revision=1,
        request=PromptSkillLifecycleRequest(
            expected_control_revision=1,
            idempotency_key=f"{category}-prompt-enable-a",
        ),
    )
    start = Barrier(2)

    def switch(name: str):
        start.wait(timeout=10)
        if name == "skill-a":
            return service.disable(
                ADMIN,
                category=category,
                name=name,
                revision=1,
                request=PromptSkillLifecycleRequest(
                    expected_control_revision=2,
                    idempotency_key=f"{category}-prompt-disable-a",
                ),
            )
        return service.enable(
            ADMIN,
            category=category,
            name=name,
            revision=1,
            request=PromptSkillLifecycleRequest(
                expected_control_revision=1,
                idempotency_key=f"{category}-prompt-enable-b",
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(switch, ("skill-a", "skill-b")))

    current = owner.current_catalog(category)
    assert current.catalog_revision == 4
    assert [candidate.ref for candidate in owner.read_catalog(current).skills] == [
        second.revision.ref
    ]
    assert {outcome.skill.control.name for outcome in outcomes} == {
        "skill-a",
        "skill-b",
    }
    assert owner.read_instructions(first.revision.ref).instructions == (
        "Instructions for A."
    )
