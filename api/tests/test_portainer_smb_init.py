from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure import portainer_smb_init
from atlas_production.infrastructure.portainer_smb_init import (
    PortainerSmbInitializationError,
    initialize_portainer_smb,
    main,
)
from atlas_production.modules.artifact_storage.records import (
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)
from atlas_production.modules.identity_access.local_pilot import (
    AdminBootstrapConfigurationError,
)


SWITCH_MODE = "operator_accepted_unverified"


class Composition:
    def __init__(self, *, error: ValueError | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def configure_portainer_target(self, **kwargs) -> Mapping[str, object]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        generation = int(kwargs["generation"])
        return {
            "generation": generation,
            "verification_mode": "operator_accepted_unverified",
            "evidence_claim": "OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
            "committed_blob_count": 2,
            "storage_epoch": generation,
        }


def test_initializer_forwards_validated_cli_contract_to_typed_composition() -> None:
    composition = Composition()

    receipt = initialize_portainer_smb(
        composition,  # type: ignore[arg-type]
        generation=3,
        switch_mode=SWITCH_MODE,
        risk_acknowledgement=UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
    )

    assert receipt["generation"] == 3
    assert composition.calls == [{
        "generation": 3,
        "switch_mode": SWITCH_MODE,
        "risk_acknowledgement": UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
        "raw_path": "/srv/atlas-artifacts",
    }]


def test_initializer_maps_stale_generation_to_operator_error() -> None:
    composition = Composition(error=ValueError("Portainer generation was superseded"))

    with pytest.raises(PortainerSmbInitializationError) as exc_info:
        initialize_portainer_smb(
            composition,  # type: ignore[arg-type]
            generation=2,
            switch_mode=SWITCH_MODE,
            risk_acknowledgement=UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
        )

    assert exc_info.value.error_code == "portainer_smb_generation_stale"


def test_initializer_maps_typed_provider_rejection_to_operator_error() -> None:
    composition = Composition(error=ValueError("artifact target changed"))

    with pytest.raises(PortainerSmbInitializationError) as exc_info:
        initialize_portainer_smb(
            composition,  # type: ignore[arg-type]
            generation=2,
            switch_mode=SWITCH_MODE,
            risk_acknowledgement=UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
        )

    assert exc_info.value.error_code == "portainer_smb_initialization_rejected"


@pytest.mark.parametrize(
    ("switch_mode", "acknowledgement", "expected_code"),
    (
        (
            "full_hash",
            UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
            "portainer_smb_switch_mode_invalid",
        ),
        (
            SWITCH_MODE,
            "not-accepted",
            "portainer_smb_risk_acknowledgement_invalid",
        ),
    ),
)
def test_initializer_rejects_invalid_risk_contract(
    switch_mode: str,
    acknowledgement: str,
    expected_code: str,
) -> None:
    with pytest.raises(PortainerSmbInitializationError) as exc_info:
        initialize_portainer_smb(
            Composition(),  # type: ignore[arg-type]
            generation=1,
            switch_mode=switch_mode,
            risk_acknowledgement=acknowledgement,
        )
    assert exc_info.value.error_code == expected_code


def test_main_success_preserves_json_contract(monkeypatch, capsys) -> None:
    runtime = SimpleNamespace(session_factory=object())
    composition = Composition()
    events: list[str] = []
    admin_calls: list[dict[str, str]] = []

    def configure_portainer_target(**facts):
        events.append("storage")
        return composition.configure_portainer_target(**facts)

    def seed_processing_defaults():
        events.append("processing")
        return SimpleNamespace(
            created=True,
            plugin_count=12,
            processing_profile_count=9,
        )

    def seed_admin(**facts):
        events.append("identity")
        admin_calls.append(facts)
        return SimpleNamespace(actor_id=facts["actor_id"], created=True)

    monkeypatch.setattr(
        portainer_smb_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "build_artifact_storage_composition",
        lambda selected: SimpleNamespace(
            configure_portainer_target=configure_portainer_target,
        ),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda session_factory: SimpleNamespace(
            execute=seed_processing_defaults,
        ),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedLocalPilotAdminCommand",
        lambda session_factory: SimpleNamespace(
            execute=seed_admin,
        ),
    )
    environment = _valid_environment()
    exit_code = main(environment)

    assert exit_code == 0
    assert events == ["processing", "identity", "storage"]
    assert admin_calls == [
        {
            "actor_id": "user-admin-001",
            "display_name": "Atlas Admin",
            "email": "operator@example.test",
            "password": environment["ATLAS_BOOTSTRAP_ADMIN_PASSWORD"],
        }
    ]
    assert json.loads(capsys.readouterr().out) == {
        "committed_blob_count": 2,
        "evidence_claim": "OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
        "generation": 1,
        "processing_defaults_seeded": True,
        "processing_plugin_count": 12,
        "processing_profile_count": 9,
        "status": "succeeded",
        "storage_epoch": 1,
        "verification_mode": "operator_accepted_unverified",
    }


def test_main_fails_closed_when_admin_seed_fails_without_leaking_secret(
    monkeypatch,
    capsys,
) -> None:
    runtime = SimpleNamespace(session_factory=object())
    monkeypatch.setattr(
        portainer_smb_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "build_artifact_storage_composition",
        lambda selected: Composition(),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda session_factory: SimpleNamespace(
            execute=lambda: SimpleNamespace(
                created=False,
                plugin_count=12,
                processing_profile_count=9,
            )
        ),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedLocalPilotAdminCommand",
        lambda session_factory: SimpleNamespace(
            execute=lambda **facts: (_ for _ in ()).throw(
                RuntimeError(facts["password"])
            )
        ),
    )

    environment = _valid_environment()
    assert main(environment) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "error_code": "portainer_smb_initialization_failed",
        "status": "failed",
    }
    assert environment["ATLAS_BOOTSTRAP_ADMIN_PASSWORD"] not in output


def test_main_maps_bootstrap_configuration_error_without_leaking_secret(
    monkeypatch,
    capsys,
) -> None:
    secret = "too-short"
    runtime = SimpleNamespace(session_factory=object())
    monkeypatch.setattr(
        portainer_smb_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "build_artifact_storage_composition",
        lambda _selected: Composition(),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda _session_factory: SimpleNamespace(
            execute=lambda: SimpleNamespace(
                created=False,
                plugin_count=12,
                processing_profile_count=9,
            )
        ),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedLocalPilotAdminCommand",
        lambda _session_factory: SimpleNamespace(
            execute=lambda **_facts: (_ for _ in ()).throw(
                AdminBootstrapConfigurationError(
                    "identity_admin_bootstrap_configuration_invalid"
                )
            )
        ),
    )
    environment = _valid_environment()
    environment["ATLAS_BOOTSTRAP_ADMIN_PASSWORD"] = secret

    assert main(environment) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "error_code": "identity_admin_bootstrap_configuration_invalid",
        "status": "rejected",
    }
    assert secret not in output


def test_main_rejection_output_does_not_echo_smb_material(capsys) -> None:
    secret = "do-not-log-this-password"
    exit_code = main({
        "ATLAS_SMB_PASSWORD": secret,
        "ATLAS_ARTIFACT_SWITCH_MODE": SWITCH_MODE,
        "ATLAS_ARTIFACT_SWITCH_ACK": UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
    })

    output = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(output) == {
        "error_code": "portainer_smb_required_variable_missing",
        "status": "rejected",
    }
    assert secret not in output


def _valid_environment() -> dict[str, str]:
    return {
        "ATLAS_SMB_HOST": "smb.example.internal",
        "ATLAS_SMB_SHARE": "atlas",
        "ATLAS_SMB_SUBDIR": "artifacts",
        "ATLAS_SMB_USERNAME": "atlas-operator",
        "ATLAS_SMB_DOMAIN": "",
        "ATLAS_SMB_VERSION": "3.1.1",
        "ATLAS_SMB_GENERATION": "1",
        "ATLAS_ARTIFACT_SWITCH_MODE": SWITCH_MODE,
        "ATLAS_ARTIFACT_SWITCH_ACK": UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
        "ATLAS_BOOTSTRAP_ADMIN_EMAIL": "operator@example.test",
        "ATLAS_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(18),
    }


@pytest.mark.parametrize(
    ("variable", "value"),
    (
        ("ATLAS_SMB_HOST", "server/share"),
        ("ATLAS_SMB_SHARE", "atlas,ro"),
        ("ATLAS_SMB_SUBDIR", "../artifacts"),
        ("ATLAS_SMB_SUBDIR", "/artifacts"),
        ("ATLAS_SMB_SUBDIR", "artifacts\\nested"),
        ("ATLAS_SMB_USERNAME", "atlas,uid=0"),
        ("ATLAS_SMB_DOMAIN", "domain\nname"),
        ("ATLAS_SMB_VERSION", "2.0"),
    ),
)
def test_main_rejects_invalid_non_secret_smb_variables_without_echoing_them(
    variable: str,
    value: str,
    capsys,
) -> None:
    environment = _valid_environment()
    environment[variable] = value

    exit_code = main(environment)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(output) == {
        "error_code": "portainer_smb_variables_invalid",
        "status": "rejected",
    }
    assert value not in output
