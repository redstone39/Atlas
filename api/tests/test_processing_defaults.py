from __future__ import annotations

from atlas_production.infrastructure.postgres_owner import processing_defaults


class _Session:
    def __init__(self) -> None:
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_default_graph_preserves_builtin_parser_and_profile_contract() -> None:
    defaults = processing_defaults._defaults("2026-07-19T00:00:00Z")

    assert {row.runtime_profile_id for row in defaults.runtime_profiles} == {
        "atlas-python-v1",
        "atlas-docling-cpu-v1",
    }
    assert len(defaults.plugin_versions) == 12
    assert {row.plugin_id for row in defaults.plugin_versions} >= {
        "atlas-pypdf",
        "atlas-python-docx",
        "atlas-python-pptx",
        "atlas-openpyxl",
        "atlas-plain-text",
        "atlas-csv",
        "atlas-generic-text",
        "atlas-rapidocr",
        "atlas-docling-layout",
    }
    assert all(row.status == "verified" for row in defaults.plugin_versions)
    assert all(
        row.trust_provenance == "platform_builtin"
        for row in defaults.plugin_versions
    )
    profile_ids = {row.profile_id for row in defaults.revisions}
    assert len(profile_ids) == 9
    assert all(profile_id.startswith("profile-") for profile_id in profile_ids)
    assert all(row.status == "active" for row in defaults.revisions)


def test_seed_command_atomically_creates_complete_graph_when_registry_empty(
    monkeypatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(
        processing_defaults,
        "acquire_owner_locks",
        lambda selected, **_kwargs: None,
    )
    monkeypatch.setattr(
        processing_defaults,
        "_registry_is_empty",
        lambda selected: True,
    )

    receipt = processing_defaults.SeedProcessingRegistryDefaultsCommand(
        lambda: session
    ).execute()

    assert receipt.created is True
    assert (receipt.runtime_profile_count, receipt.plugin_count, receipt.processing_profile_count) == (2, 12, 9)
    assert len(session.added) == 2 + 12 + 12 + 9 + 9 + 1
    assert session.commits == 1
    assert session.rollbacks == 0


def test_seed_command_preserves_existing_administrator_registry(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(
        processing_defaults,
        "acquire_owner_locks",
        lambda selected, **_kwargs: None,
    )
    monkeypatch.setattr(
        processing_defaults,
        "_registry_is_empty",
        lambda selected: False,
    )

    receipt = processing_defaults.SeedProcessingRegistryDefaultsCommand(
        lambda: session
    ).execute()

    assert receipt.created is False
    assert (receipt.runtime_profile_count, receipt.plugin_count, receipt.processing_profile_count) == (2, 12, 9)
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 0
