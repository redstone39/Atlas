from __future__ import annotations

import json
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
    environment = _valid_environment()
    exit_code = main(environment)

    assert exit_code == 0
    assert events == ["processing", "storage"]
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
