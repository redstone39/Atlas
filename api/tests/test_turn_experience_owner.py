from __future__ import annotations

import importlib

import pytest

from atlas_production.infrastructure.persistence.payload_policy import JSONB_PAYLOAD_REGISTRY
from atlas_production.infrastructure.postgres_owner.turn_experience import (
    PostgresTurnExperienceStore,
)
from atlas_production.modules.turn_experience import public


EXPECTED_PUBLIC_SYMBOLS = [
    "MaterializeTurnExperienceV1",
    "TurnExperienceCorrectionV1",
    "TurnExperienceCursorV1",
    "TurnExperienceDeepTraceV1",
    "TurnExperienceExecutionSkillSelectionV1",
    "TurnExperienceEvaluationV1",
    "TurnExperienceEvidenceCheckV1",
    "TurnExperienceGovernanceV1",
    "TurnExperiencePlanGenerationV1",
    "TurnExperienceRouteRefV1",
    "TurnExperienceSkillSelectionV1",
    "TurnExperienceStore",
    "TurnExperienceTerminalV1",
    "TurnExperienceUsageV1",
    "TurnExperienceV1",
]


def test_public_declaration_matches_closed_executable_contract() -> None:
    assert public.__all__ == EXPECTED_PUBLIC_SYMBOLS


def test_experience_payload_has_dedicated_64_kib_registry_boundary() -> None:
    assert JSONB_PAYLOAD_REGISTRY["atlas_turn_experiences.payload"] == (
        "derived_experience_metadata",
        65_536,
        "turn_experience_v1",
    )


@pytest.mark.parametrize("limit", [0, 101])
def test_store_rejects_unbounded_scan_before_database_access(limit: int) -> None:
    store = PostgresTurnExperienceStore(
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened"))
    )
    with pytest.raises(ValueError, match="between 1 and 100"):
        store.list_after(None, limit)


def test_single_development_baseline_registers_experience_table() -> None:
    baseline = importlib.import_module(
        "atlas_production.migrations.versions.20260711_0001_development_baseline"
    )
    assert "atlas_turn_experiences" in baseline.ATR020_OWNER_TABLES
