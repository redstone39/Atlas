from __future__ import annotations

import json

from atlas_production.infrastructure.composition import (
    build_artifact_storage_composition,
)
from atlas_production.infrastructure.postgres_owner.processing_defaults import (
    SeedProcessingRegistryDefaultsCommand,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError


def main() -> int:
    try:
        runtime = PostgresRuntime.from_environment()
        artifact_storage = build_artifact_storage_composition(runtime)
        processing_receipt = SeedProcessingRegistryDefaultsCommand(
            runtime.session_factory
        ).execute()
        receipt = artifact_storage.initialize_local_pilot_target()
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
