from __future__ import annotations

import importlib
from pathlib import Path

from atlas_production.infrastructure.persistence import (
    audit_events,
    authorization,
    citation_preview,
    context_engineering,
    conversation,
    retrieval,
    prompt_skills,
    result_governance,
    turn_runtime,
    answer_behavior,
)


baseline = importlib.import_module(
    "atlas_production.migrations.versions.20260711_0001_development_baseline"
)

LEGACY_TURN_TABLES = {
    "atlas_citation_viewer_access",
    "atlas_citation_viewer_sessions",
    "atlas_citation_viewer_items",
    "atlas_conversations",
    "atlas_conversation_summaries",
    "atlas_conversation_turns",
    "atlas_response_segment_records",
    "atlas_claim_records",
    "atlas_claim_evidence_links",
    "atlas_claim_support_assessments",
    "atlas_turn_requests",
    "atlas_runtime_attempts",
    "atlas_runtime_events",
    "atlas_citation_anchors",
    "atlas_context_packs",
    "atlas_conversation_plans",
    "atlas_evidence_packs",
    "atlas_prompt_snapshots",
    "atlas_tool_invocations",
}


def test_development_baseline_registers_every_owner_table() -> None:
    expected = set().union(
        authorization.OWNER_TABLES,
        audit_events.TURN_AUDIT_OWNER_TABLES,
        citation_preview.TURN_CITATION_OWNER_TABLES,
        context_engineering.OWNER_TABLES,
        conversation.OWNER_TABLES,
        retrieval.OWNER_TABLES,
        result_governance.TURN_RESULT_GOVERNANCE_OWNER_TABLES,
        turn_runtime.OWNER_TABLES,
        prompt_skills.OWNER_TABLES,
        answer_behavior.OWNER_TABLES,
    )
    assert baseline.ATR020_OWNER_TABLES == expected
    assert {table.name for table in baseline._atr020_tables()} == expected


def test_conversation_scope_tags_are_normalized_owner_state() -> None:
    table = conversation.AtlasTurnConversationScopeTagRow.__table__
    assert table.name == "atlas_turn_conversation_scope_tags"
    assert [column.name for column in table.primary_key.columns] == [
        "conversation_id",
        "tag_type",
        "tag_id",
    ]
    assert next(iter(table.c.conversation_id.foreign_keys)).ondelete == "CASCADE"
    assert {
        constraint.name for constraint in table.constraints
    } >= {"ck_atlas_turn_conversation_scope_tag_type"}


def test_development_baseline_remains_the_single_root_revision() -> None:
    assert baseline.revision == "20260711_0001"
    assert baseline.down_revision is None


def test_development_baseline_does_not_create_superseded_turn_schema() -> None:
    source = Path(baseline.__file__).read_text()
    for table in LEGACY_TURN_TABLES:
        assert f"create_table('{table}'" not in source
        assert f'__tablename__ = "{table}"' not in source
