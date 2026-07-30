from __future__ import annotations

import json
import os
from typing import Mapping

from atlas_production.infrastructure.composition import (
    ArtifactStorageComposition,
    build_artifact_storage_composition,
)
from atlas_production.infrastructure.postgres_owner.processing_defaults import (
    SeedProcessingRegistryDefaultsCommand,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    SeedLocalPilotAdminCommand,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.identity_access.local_pilot import (
    ADMIN_ACTOR_ID,
    ADMIN_DISPLAY_NAME,
    BOOTSTRAP_ADMIN_EMAIL_ENV,
    BOOTSTRAP_ADMIN_PASSWORD_ENV,
    AdminBootstrapConfigurationError,
)

from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.artifact_storage.records import (
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)


_PORTAINER_TARGET_PREFIX = "target-portainer-smb-g"
_PORTAINER_SWITCH_MODE = "operator_accepted_unverified"
_RAW_PATH = "/srv/atlas-artifacts"


class PortainerSmbInitializationError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _target_identity(generation: int) -> tuple[str, int, str]:
    if generation < 1:
        raise PortainerSmbInitializationError("portainer_smb_generation_invalid")
    target_id = f"{_PORTAINER_TARGET_PREFIX}{generation}"
    if len(target_id) > 71:
        raise PortainerSmbInitializationError("portainer_smb_generation_invalid")
    return target_id, generation, f"portainer-smb-g{generation}"


def initialize_portainer_smb(
    composition: ArtifactStorageComposition,
    *,
    generation: int,
    switch_mode: str,
    risk_acknowledgement: str,
    raw_path: str = _RAW_PATH,
):
    _target_identity(generation)
    if switch_mode != _PORTAINER_SWITCH_MODE:
        raise PortainerSmbInitializationError(
            "portainer_smb_switch_mode_invalid"
        )
    if risk_acknowledgement != UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT:
        raise PortainerSmbInitializationError(
            "portainer_smb_risk_acknowledgement_invalid"
        )

    try:
        return composition.configure_portainer_target(
            generation=generation,
            switch_mode=switch_mode,
            risk_acknowledgement=risk_acknowledgement,
            raw_path=raw_path,
        )
    except ValueError as exc:
        if str(exc) in {
            "Portainer generation was superseded",
            "artifact_storage_target_generation_stale",
        }:
            raise PortainerSmbInitializationError(
                "portainer_smb_generation_stale"
            ) from exc
        raise PortainerSmbInitializationError(
            "portainer_smb_initialization_rejected"
        ) from exc


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise PortainerSmbInitializationError(
            "portainer_smb_required_variable_missing"
        )
    return value


def _generation(environment: Mapping[str, str]) -> int:
    raw = _required(environment, "ATLAS_SMB_GENERATION")
    try:
        return int(raw)
    except ValueError as exc:
        raise PortainerSmbInitializationError(
            "portainer_smb_generation_invalid"
        ) from exc


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_smb_coordinates(environment: Mapping[str, str]) -> None:
    host = _required(environment, "ATLAS_SMB_HOST")
    share = _required(environment, "ATLAS_SMB_SHARE")
    subdirectory = _required(environment, "ATLAS_SMB_SUBDIR")
    username = _required(environment, "ATLAS_SMB_USERNAME")
    domain = environment.get("ATLAS_SMB_DOMAIN", "")
    version = environment.get("ATLAS_SMB_VERSION", "3.1.1")
    values = (host, share, subdirectory, username, domain)
    invalid = any("," in value or _has_control_character(value) for value in values)
    invalid = invalid or any(character in host for character in ("/", "\\"))
    invalid = invalid or host in {".", ".."}
    invalid = invalid or any(character in share for character in ("/", "\\"))
    invalid = invalid or share in {".", ".."}
    segments = subdirectory.split("/")
    invalid = invalid or subdirectory.startswith(("/", "\\"))
    invalid = invalid or "\\" in subdirectory
    invalid = invalid or any(segment in {"", ".", ".."} for segment in segments)
    invalid = invalid or version not in {"3.1.1", "3.0"}
    if invalid:
        raise PortainerSmbInitializationError("portainer_smb_variables_invalid")


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def main(environment: Mapping[str, str] | None = None) -> int:
    env = environment or os.environ
    try:
        generation = _generation(env)
        switch_mode = _required(env, "ATLAS_ARTIFACT_SWITCH_MODE")
        risk_acknowledgement = _required(env, "ATLAS_ARTIFACT_SWITCH_ACK")
        _validate_smb_coordinates(env)
        runtime = PostgresRuntime.from_environment()
        composition = build_artifact_storage_composition(runtime)
        processing_receipt = SeedProcessingRegistryDefaultsCommand(
            runtime.session_factory
        ).execute()
        SeedLocalPilotAdminCommand(runtime.session_factory).execute(
            actor_id=ADMIN_ACTOR_ID,
            display_name=ADMIN_DISPLAY_NAME,
            email=env.get(BOOTSTRAP_ADMIN_EMAIL_ENV),
            password=env.get(BOOTSTRAP_ADMIN_PASSWORD_ENV),
        )
        receipt = initialize_portainer_smb(
            composition,
            generation=generation,
            switch_mode=switch_mode,
            risk_acknowledgement=risk_acknowledgement,
        )
    except PortainerSmbInitializationError as exc:
        _emit({"status": "rejected", "error_code": exc.error_code})
        return 1
    except AdminBootstrapConfigurationError as exc:
        _emit({"status": "rejected", "error_code": exc.error_code})
        return 1
    except ArtifactStorageError as exc:
        _emit(
            {
                "status": "rejected",
                "error_code": exc.error_code,
                "message_code": exc.message_code,
            }
        )
        return 1
    except Exception:
        _emit(
            {
                "status": "failed",
                "error_code": "portainer_smb_initialization_failed",
            }
        )
        return 1

    _emit(
        {
            "status": "succeeded",
            "generation": generation,
            "verification_mode": receipt["verification_mode"],
            "evidence_claim": receipt["evidence_claim"],
            "committed_blob_count": receipt["committed_blob_count"],
            "processing_defaults_seeded": processing_receipt.created,
            "processing_plugin_count": processing_receipt.plugin_count,
            "processing_profile_count": processing_receipt.processing_profile_count,
            "storage_epoch": receipt["storage_epoch"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
