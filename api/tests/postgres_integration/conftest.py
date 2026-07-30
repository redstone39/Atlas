from __future__ import annotations

import os

import pytest
from sqlalchemy.engine import make_url

from atlas_production.infrastructure.postgres_runtime import PostgresRuntime


@pytest.fixture(scope="session")
def postgres_url() -> str:
    database_url = os.environ.get("ATLAS_TEST_POSTGRES_URL")
    if not database_url:
        raise RuntimeError(
            "ATLAS_TEST_POSTGRES_URL is required for PostgreSQL integration tests"
        )
    parsed = make_url(database_url)
    if not parsed.drivername.startswith("postgresql"):
        raise RuntimeError("PostgreSQL integration requires a PostgreSQL URL")
    database_name = parsed.database or ""
    if (
        not database_name.startswith("atlas_baseline_test_")
        or database_name == "atlas_production"
    ):
        raise RuntimeError(
            "PostgreSQL integration requires an atlas_baseline_test_* database"
        )
    return database_url


@pytest.fixture(scope="session")
def postgres_runtime(postgres_url: str) -> PostgresRuntime:
    runtime = PostgresRuntime.from_url(postgres_url)
    try:
        yield runtime
    finally:
        runtime.engine.dispose()
