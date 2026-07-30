from __future__ import annotations

import json

from atlas_production.infrastructure.postgres_owner.processing_defaults import (
    SeedProcessingRegistryDefaultsCommand,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime


def main() -> int:
    try:
        runtime = PostgresRuntime.from_environment()
        runtime.bootstrap_schema()
        receipt = SeedProcessingRegistryDefaultsCommand(
            runtime.session_factory
        ).execute()
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "schema_initialization_failed",
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "succeeded",
                "processing_defaults_seeded": receipt.created,
                "processing_plugin_count": receipt.plugin_count,
                "processing_profile_count": receipt.processing_profile_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
