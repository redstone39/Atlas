from __future__ import annotations

import json
import secrets
from pathlib import Path
from types import SimpleNamespace

from atlas_production.infrastructure import local_pilot_init
from atlas_production.modules.identity_access.local_pilot import (
    AdminBootstrapConfigurationError,
)


BOOTSTRAP_ENVIRONMENT = {
    "ATLAS_BOOTSTRAP_ADMIN_EMAIL": "operator@example.test",
    "ATLAS_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(18),
}


class _Composition:
    def initialize_local_pilot_target(self):
        return {
            "target_id": "target-local-pilot",
            "target_revision": 1,
            "storage_epoch": 1,
            "replayed": False,
        }


class _Runtime:
    def __init__(self) -> None:
        self.session_factory = object()
        self.bootstrap_calls = 0

    def bootstrap_schema(self) -> None:
        self.bootstrap_calls += 1


class _SeedCommand:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls = []

    def execute(self, **facts):
        self.calls.append(facts)
        return SimpleNamespace(actor_id=facts["actor_id"], created=True)


class _ProcessingSeedCommand:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls = 0

    def execute(self):
        self.calls += 1
        return SimpleNamespace(
            created=True,
            plugin_count=12,
            processing_profile_count=9,
        )


def test_main_configures_local_target_through_typed_composition(
    monkeypatch,
    capsys,
) -> None:
    runtime = _Runtime()
    command = _SeedCommand(runtime.session_factory)
    processing_command = _ProcessingSeedCommand(runtime.session_factory)

    def build_composition(selected):
        selected.bootstrap_schema()
        return _Composition()

    monkeypatch.setattr(
        local_pilot_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedLocalPilotAdminCommand",
        lambda session_factory: command,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda session_factory: processing_command,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "build_artifact_storage_composition",
        build_composition,
    )

    assert local_pilot_init.main(BOOTSTRAP_ENVIRONMENT) == 0
    assert runtime.bootstrap_calls == 1
    assert command.session_factory is runtime.session_factory
    assert processing_command.session_factory is runtime.session_factory
    assert processing_command.calls == 1
    assert command.calls == [
        {
            "actor_id": "user-admin-001",
            "display_name": "Atlas Admin",
            "email": "operator@example.test",
            "password": BOOTSTRAP_ENVIRONMENT["ATLAS_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ]
    assert json.loads(capsys.readouterr().out) == {
        "admin_actor_id": "user-admin-001",
        "admin_seeded": True,
        "processing_defaults_seeded": True,
        "processing_plugin_count": 12,
        "processing_profile_count": 9,
        "replayed": False,
        "status": "succeeded",
        "storage_epoch": 1,
        "target_id": "target-local-pilot",
        "target_revision": 1,
    }


def test_main_fails_closed_without_leaking_internal_error(
    monkeypatch,
    capsys,
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        local_pilot_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedLocalPilotAdminCommand",
        lambda _session_factory: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    monkeypatch.setattr(
        local_pilot_init,
        "build_artifact_storage_composition",
        lambda _runtime: _Composition(),
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda _session_factory: _ProcessingSeedCommand(object()),
    )

    assert local_pilot_init.main(BOOTSTRAP_ENVIRONMENT) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "local_pilot_initialization_failed",
        "status": "failed",
    }


def test_main_reports_bootstrap_configuration_rejection_without_secret(
    monkeypatch,
    capsys,
) -> None:
    runtime = _Runtime()
    secret = "short-secret"
    monkeypatch.setattr(
        local_pilot_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        local_pilot_init,
        "build_artifact_storage_composition",
        lambda _runtime: _Composition(),
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda _session_factory: _ProcessingSeedCommand(object()),
    )
    monkeypatch.setattr(
        local_pilot_init,
        "SeedLocalPilotAdminCommand",
        lambda _session_factory: SimpleNamespace(
            execute=lambda **_facts: (_ for _ in ()).throw(
                AdminBootstrapConfigurationError(
                    "identity_admin_bootstrap_configuration_invalid"
                )
            )
        ),
    )

    assert local_pilot_init.main({
        "ATLAS_BOOTSTRAP_ADMIN_EMAIL": "operator@example.test",
        "ATLAS_BOOTSTRAP_ADMIN_PASSWORD": secret,
    }) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "error_code": "identity_admin_bootstrap_configuration_invalid",
        "status": "rejected",
    }
    assert secret not in output


def test_deployment_initializers_keep_storage_carriers_distinct() -> None:
    infra_root = Path(__file__).resolve().parents[2] / "infra"
    base = (infra_root / "docker-compose.p1.yml").read_text(encoding="utf-8")
    host_mount = (
        infra_root / "docker-compose.artifact-host-mount.yml"
    ).read_text(encoding="utf-8")
    portainer = (
        infra_root / "docker-compose.portainer-smb.override.yml"
    ).read_text(encoding="utf-8")

    assert "atlas_production.infrastructure.local_pilot_init" in base
    assert "atlas_production.infrastructure.schema_init" in host_mount
    assert (
        "atlas_production.infrastructure.portainer_smb_init" in portainer
    )
    assert "local_pilot_init" not in host_mount
    assert "local_pilot_init" not in portainer


def test_api_runtime_can_reach_office_renderer_for_visual_inspection() -> None:
    infra_root = Path(__file__).resolve().parents[2] / "infra"
    base = (infra_root / "docker-compose.p1.yml").read_text(encoding="utf-8")
    api_service = base.split("\n  api:\n", maxsplit=1)[1].split(
        "\n  plugin-runner:\n", maxsplit=1
    )[0]

    assert "ATLAS_OFFICE_RENDERER_URL: http://office-renderer:8014" in api_service
