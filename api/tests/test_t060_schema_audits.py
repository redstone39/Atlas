from __future__ import annotations

import hashlib
from pathlib import Path
import os
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
OPENAPI_FIXTURE = API_ROOT / "tests/contracts/openapi-v1.json"
OWNER_AUDIT = REPO_ROOT / "infra/scripts/audit_postgres_owner_runtime"
CHECK_POSTGRES = API_ROOT / "scripts/check-postgres"
CONVERSATION_EVOLUTION_SMOKE = (
    API_ROOT / "scripts/smoke_public_conversation_evolution.py"
)


def test_schema_and_provider_tools_use_db_free_openapi_app() -> None:
    for path in (
        API_ROOT / "scripts/generate-openapi-fixture",
        REPO_ROOT / "infra/scripts/audit_provider_key_cutover",
    ):
        source = path.read_text(encoding="utf-8")
        assert "from atlas_production.openapi_app import create_openapi_app" in source
        assert "atlas_production." + "stores" not in source
    assert hashlib.sha256(OPENAPI_FIXTURE.read_bytes()).hexdigest() == (
        "04a7d651f0366bd2b137828eebe2461d857f9b69ab01803d44f767912bc4dd4b"
    )


def test_owner_runtime_audit_proves_its_seeded_negative_rules() -> None:
    result = subprocess.run(
        [str(OWNER_AUDIT), "--self-test-only"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "postgres_owner_runtime_negative_guard" in result.stdout


@pytest.mark.parametrize(
    ("database_url", "message"),
    [
        (None, "ATLAS_TEST_POSTGRES_URL is required"),
        ("sqlite:///tmp/atlas.db", "must use PostgreSQL"),
        ("postgresql://localhost/atlas_production", "dedicated atlas_baseline_test_"),
        ("postgresql://localhost/not_a_test_database", "dedicated atlas_baseline_test_"),
    ],
)
def test_check_postgres_rejects_missing_or_unsafe_database_before_connection(
    database_url: str | None,
    message: str,
) -> None:
    environment = os.environ.copy()
    environment.pop("ATLAS_TEST_POSTGRES_URL", None)
    if database_url is not None:
        environment["ATLAS_TEST_POSTGRES_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(CHECK_POSTGRES)],
        cwd=API_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert message in result.stderr

@pytest.mark.parametrize(
    ("database_url", "message"),
    [
        (None, "ATLAS_TEST_POSTGRES_URL is required"),
        (
            "not a url",
            "ATLAS_TEST_POSTGRES_URL must be a valid PostgreSQL URL",
        ),
        ("sqlite:///tmp/atlas.db", "ATLAS_TEST_POSTGRES_URL must use PostgreSQL"),
        (
            "postgresql://localhost/atlas_production",
            "PostgreSQL checks require a dedicated atlas_baseline_test_* database",
        ),
        (
            "postgresql://localhost/not_a_test_database",
            "PostgreSQL checks require a dedicated atlas_baseline_test_* database",
        ),
    ],
)
def test_conversation_evolution_smoke_rejects_unsafe_database_before_connection(
    database_url: str | None,
    message: str,
) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(API_ROOT / "src")
    environment.pop("ATLAS_TEST_POSTGRES_URL", None)
    if database_url is not None:
        environment["ATLAS_TEST_POSTGRES_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(CONVERSATION_EVOLUTION_SMOKE)],
        cwd=API_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert message in result.stderr


def test_check_postgres_rejects_connection_failure_before_schema_reset() -> None:
    environment = os.environ.copy()
    environment["ATLAS_TEST_POSTGRES_URL"] = (
        "postgresql://postgres:postgres@127.0.0.1:1/"
        "atlas_baseline_test_unreachable?connect_timeout=1"
    )
    result = subprocess.run(
        [sys.executable, str(CHECK_POSTGRES)],
        cwd=API_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Unable to connect to the dedicated PostgreSQL test database" in result.stderr


def test_check_postgres_never_creates_or_drops_a_database() -> None:
    source = CHECK_POSTGRES.read_text(encoding="utf-8").upper()
    assert "CREATE " + "DATABASE" not in source
    assert "DROP " + "DATABASE" not in source
    assert "DROP SCHEMA PUBLIC CASCADE" in source
