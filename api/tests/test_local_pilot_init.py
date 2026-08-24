from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from atlas_production.infrastructure import local_pilot_init


class LocalArtifactComposition:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def initialize_local_pilot_target(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("public synthetic initializer failure")
        return {
            "target_id": "target-local-pilot",
            "target_revision": 1,
            "storage_epoch": 1,
            "replayed": False,
        }


def test_main_initializes_processing_and_storage_without_a_seeded_admin(
    monkeypatch,
    capsys,
) -> None:
    runtime = SimpleNamespace(session_factory=object())
    composition = LocalArtifactComposition()
    events: list[str] = []

    monkeypatch.setattr(
        local_pilot_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "build_artifact_storage_composition",
        lambda selected: composition,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda selected: SimpleNamespace(
            execute=lambda: (
                events.append("processing"),
                SimpleNamespace(
                    created=True,
                    plugin_count=12,
                    processing_profile_count=9,
                ),
            )[1]
        ),
    )

    assert local_pilot_init.main() == 0
    assert events == ["processing"]
    assert composition.calls == 1
    assert json.loads(capsys.readouterr().out) == {
        "processing_defaults_seeded": True,
        "processing_plugin_count": 12,
        "processing_profile_count": 9,
        "replayed": False,
        "status": "succeeded",
        "storage_epoch": 1,
        "target_id": "target-local-pilot",
        "target_revision": 1,
    }


def test_main_fails_closed_without_exposing_internal_exception(
    monkeypatch,
    capsys,
) -> None:
    marker = "public-synthetic-private-detail"
    monkeypatch.setattr(
        local_pilot_init.PostgresRuntime,
        "from_environment",
        lambda: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    assert local_pilot_init.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "error_code": "local_pilot_initialization_failed",
        "status": "failed",
    }
    assert marker not in output


def test_deployment_initializers_do_not_seed_fixed_identity_credentials() -> None:
    repository = Path(__file__).resolve().parents[2]
    sources = "\n".join(
        (repository / path).read_text()
        for path in (
            "api/src/atlas_production/infrastructure/local_pilot_init.py",
            "api/src/atlas_production/infrastructure/portainer_smb_init.py",
            "infra/docker-compose.p1.yml",
        )
    )

    assert "SeedLocalPilotAdminCommand" not in sources
    assert "ATLAS_BOOTSTRAP_ADMIN_PASSWORD" not in sources
    assert "ATLAS_BOOTSTRAP_ADMIN_EMAIL" not in sources


def test_api_runtime_keeps_office_renderer_reachable_for_visual_inspection() -> None:
    compose = (
        Path(__file__).resolve().parents[2] / "infra/docker-compose.p1.yml"
    ).read_text()
    api_service = compose.split("\n  api:\n", 1)[1].split("\n  notes-collaboration:\n", 1)[0]

    assert "ATLAS_OFFICE_RENDERER_URL: http://office-renderer:8014" in api_service
