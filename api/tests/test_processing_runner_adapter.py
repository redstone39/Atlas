from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from atlas_production.infrastructure.processing_runner_adapter import (
    HttpProcessingPluginRunner,
    ProcessingRunnerError,
)
from atlas_production.async_runtime import workflows


def test_runner_failure_preserves_only_a_bounded_exception_type(monkeypatch) -> None:
    request = httpx.Request("POST", "http://runner/internal/v1/invocations")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            request=request,
            json={
                "ok": False,
                "error": {"code": "plugin_execution_failed", "type": "ValueError"},
            },
        ),
    )

    with pytest.raises(ProcessingRunnerError) as raised:
        HttpProcessingPluginRunner("http://runner").invoke(
            {"artifact": b"pdf", "package": None, "timeout_seconds": 1}
        )

    assert raised.value.safe_code == "plugin_execution_failed"
    assert raised.value.safe_type == "ValueError"
    assert ProcessingRunnerError(
        "plugin_execution_failed", safe_type="unsafe type: secret"
    ).safe_type is None


@pytest.mark.parametrize(
    ("safe_code", "safe_type"),
    [
        ("plugin_interrupted", "KeyboardInterrupt"),
        ("plugin_timeout", None),
        ("plugin_execution_failed", "KeyboardInterrupt"),
    ],
)
def test_base_parser_carrier_interruptions_remain_transient(
    safe_code: str, safe_type: str | None
) -> None:
    error = ProcessingRunnerError(safe_code, safe_type=safe_type)

    assert workflows._processing_runner_failure_is_transient(error)


def test_base_parser_value_error_remains_a_deterministic_failure() -> None:
    error = ProcessingRunnerError(
        "plugin_execution_failed", safe_type="ValueError"
    )

    assert not workflows._processing_runner_failure_is_transient(error)


def test_layout_failure_reports_deterministic_layout_warning() -> None:
    assert workflows._processor_warning_code(
        {"plugin_id": "atlas-docling-layout"}
    ) == "layout_detection_failed"
    assert workflows._processor_warning_code(
        {"plugin_id": "atlas-generic-text"}
    ) is None
    assert workflows._processor_warning_code(
        {"plugin_id": "atlas-rapidocr"}
    ) == "image_ocr_failed"


def test_docling_layout_timeout_defaults_to_sixty_and_accepts_operator_value() -> None:
    assert workflows._read_docling_layout_timeout_seconds({}) == 60
    assert workflows._read_docling_layout_timeout_seconds(
        {"ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS": "120"}
    ) == 120


@pytest.mark.parametrize("raw_value", ["", "0", "601", "not-a-number", "1.5"])
def test_docling_layout_timeout_rejects_invalid_operator_values(
    raw_value: str,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS must be an integer from 1 to 600",
    ):
        workflows._read_docling_layout_timeout_seconds(
            {"ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS": raw_value}
        )


def test_invalid_docling_layout_timeout_fails_module_startup() -> None:
    environment = os.environ.copy()
    environment["ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS"] = "invalid"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import atlas_production.async_runtime.workflows",
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert completed.returncode != 0
    assert (
        "ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS must be an integer from 1 to 600"
        in completed.stderr
    )


def test_operator_timeout_changes_only_docling_layout_limits(monkeypatch) -> None:
    inherited_deadline = datetime(2026, 7, 30, tzinfo=timezone.utc)
    started_at = inherited_deadline + timedelta(seconds=5)
    monkeypatch.setattr(workflows, "_DOCLING_LAYOUT_TIMEOUT_SECONDS", 120)

    layout_timeout, layout_deadline = workflows._processor_invocation_limits(
        {"plugin_id": "atlas-docling-layout"},
        inherited_deadline=inherited_deadline,
        now=started_at,
    )
    generic_timeout, generic_deadline = workflows._processor_invocation_limits(
        {"plugin_id": "atlas-generic-text"},
        inherited_deadline=inherited_deadline,
        now=started_at,
    )

    assert (layout_timeout, layout_deadline) == (
        120,
        started_at + timedelta(seconds=120),
    )
    assert (generic_timeout, generic_deadline) == (60, inherited_deadline)


def test_production_compose_passes_docling_timeout_only_to_processing_worker() -> None:
    compose = (
        Path(__file__).resolve().parents[2]
        / "infra"
        / "docker-compose.p1.yml"
    ).read_text(encoding="utf-8")
    service_prefix, processing_and_after = compose.split(
        "\n  celery-processing:\n",
        maxsplit=1,
    )
    processing_service = processing_and_after.split(
        "\n  celery-indexing:\n",
        maxsplit=1,
    )[0]

    assert "ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS" not in service_prefix
    assert (
        "ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS: "
        "${ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS-60}"
    ) in processing_service
    assert "ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS:-60" not in processing_service
