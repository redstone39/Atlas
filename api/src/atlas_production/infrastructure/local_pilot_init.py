from __future__ import annotations

import json
import os
from typing import Mapping

from atlas_production.infrastructure.composition import (
    build_artifact_storage_composition,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    SeedLocalPilotAdminCommand,
)
from atlas_production.infrastructure.postgres_owner.processing_defaults import (
    SeedProcessingRegistryDefaultsCommand,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.identity_access.local_pilot import (
    ADMIN_ACTOR_ID,
    ADMIN_DISPLAY_NAME,
    BOOTSTRAP_ADMIN_EMAIL_ENV,
    BOOTSTRAP_ADMIN_PASSWORD_ENV,
    AdminBootstrapConfigurationError,
)


def main(environment: Mapping[str, str] | None = None) -> int:
    env = environment if environment is not None else os.environ
    try:
        runtime = PostgresRuntime.from_environment()
        artifact_storage = build_artifact_storage_composition(runtime)
        processing_receipt = SeedProcessingRegistryDefaultsCommand(
            runtime.session_factory
        ).execute()
        admin_receipt = SeedLocalPilotAdminCommand(runtime.session_factory).execute(
            actor_id=ADMIN_ACTOR_ID,
            display_name=ADMIN_DISPLAY_NAME,
            email=env.get(BOOTSTRAP_ADMIN_EMAIL_ENV),
            password=env.get(BOOTSTRAP_ADMIN_PASSWORD_ENV),
        )
        receipt = artifact_storage.initialize_local_pilot_target()
    except AdminBootstrapConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": exc.error_code,
                },
                sort_keys=True,
            )
        )
        return 1
    except ArtifactStorageError as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": exc.error_code,
                    "message_code": exc.message_code,
                },
                sort_keys=True,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "local_pilot_initialization_failed",
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "succeeded",
                "admin_actor_id": admin_receipt.actor_id,
                "admin_seeded": admin_receipt.created,
                "processing_defaults_seeded": processing_receipt.created,
                "processing_plugin_count": processing_receipt.plugin_count,
                "processing_profile_count": processing_receipt.processing_profile_count,
                "target_id": receipt["target_id"],
                "target_revision": receipt["target_revision"],
                "storage_epoch": receipt["storage_epoch"],
                "replayed": receipt["replayed"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
