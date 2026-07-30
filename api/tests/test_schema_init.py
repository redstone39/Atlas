from __future__ import annotations

import json
from types import SimpleNamespace

from atlas_production.infrastructure import schema_init


class _Runtime:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.session_factory = object()

    def bootstrap_schema(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def test_schema_initializer_applies_baseline(monkeypatch, capsys) -> None:
    runtime = _Runtime()
    seed_calls = []
    monkeypatch.setattr(
        schema_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        schema_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda session_factory: SimpleNamespace(
            execute=lambda: seed_calls.append(session_factory)
            or SimpleNamespace(
                created=True,
                plugin_count=12,
                processing_profile_count=9,
            )
        ),
    )

    assert schema_init.main() == 0
    assert runtime.calls == 1
    assert seed_calls == [runtime.session_factory]
    assert json.loads(capsys.readouterr().out) == {
        "processing_defaults_seeded": True,
        "processing_plugin_count": 12,
        "processing_profile_count": 9,
        "status": "succeeded",
    }


def test_schema_initializer_fails_closed(monkeypatch, capsys) -> None:
    runtime = _Runtime(error=RuntimeError("secret"))
    monkeypatch.setattr(
        schema_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )

    assert schema_init.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "schema_initialization_failed",
        "status": "failed",
    }
