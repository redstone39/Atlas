from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint

from atlas_production.infrastructure.persistence import (
    audit_events,
    async_processing,
    authorization,
    citation_preview,
    context_engineering,
    conversation,
    retrieval,
    result_governance,
    turn_runtime,
    turn_execution,
)
from atlas_production.infrastructure.persistence.schema import OrmBase


OWNER_MODULES = (
    authorization,
    context_engineering,
    conversation,
    retrieval,
    turn_runtime,
    turn_execution,
)
OWNER_BY_TABLE = {
    table_name: module.OWNER
    for module in OWNER_MODULES
    for table_name in module.OWNER_TABLES
}
OWNER_BY_TABLE.update(
    {name: "result_governance" for name in result_governance.TURN_RESULT_GOVERNANCE_OWNER_TABLES}
)
OWNER_BY_TABLE.update(
    {name: "citation_preview" for name in citation_preview.TURN_CITATION_OWNER_TABLES}
)
OWNER_BY_TABLE.update(
    {name: "audit" for name in audit_events.TURN_AUDIT_OWNER_TABLES}
)


def _constraint_names(table_name: str, kind: type) -> set[str]:
    return {
        constraint.name
        for constraint in OrmBase.metadata.tables[table_name].constraints
        if isinstance(constraint, kind) and constraint.name is not None
    }


def _unique_columns(table_name: str, constraint_name: str) -> list[str]:
    table = OrmBase.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, UniqueConstraint) and item.name == constraint_name
    )
    return [column.name for column in constraint.columns]


def test_owner_tables_are_registered_once_and_partitioned() -> None:
    expected = set(OWNER_BY_TABLE)
    assert len(expected) == (
        sum(len(module.OWNER_TABLES) for module in OWNER_MODULES)
        + len(result_governance.TURN_RESULT_GOVERNANCE_OWNER_TABLES)
        + len(citation_preview.TURN_CITATION_OWNER_TABLES)
        + len(audit_events.TURN_AUDIT_OWNER_TABLES)
    )
    assert expected <= set(OrmBase.metadata.tables)
    assert len(expected) == 45


def test_owner_tables_have_no_cross_owner_foreign_keys() -> None:
    for table_name, owner in OWNER_BY_TABLE.items():
        table = OrmBase.metadata.tables[table_name]
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name in OWNER_BY_TABLE
            assert OWNER_BY_TABLE[foreign_key.column.table.name] == owner


def test_all_owner_timestamps_are_timezone_aware() -> None:
    timestamp_columns = []
    for table_name in OWNER_BY_TABLE:
        for column in OrmBase.metadata.tables[table_name].columns:
            if isinstance(column.type, DateTime):
                timestamp_columns.append(column)
                assert column.type.timezone is True, f"{table_name}.{column.name}"
    assert timestamp_columns


def test_conversation_membership_and_idempotency_constraints() -> None:
    assert "uq_atlas_turn_member_ordinal" in _constraint_names(
        "atlas_turn_conversation_members", UniqueConstraint
    )
    assert "ck_atlas_turn_member_role" in _constraint_names(
        "atlas_turn_conversation_members", CheckConstraint
    )
    idempotency = OrmBase.metadata.tables["atlas_turn_conversation_idempotency"]
    assert [column.name for column in idempotency.primary_key.columns] == [
        "scope_ref", "operation", "idempotency_key"
    ]


def test_immutable_owner_outputs_have_replay_uniqueness() -> None:
    assert "uq_atlas_turn_access_grant_idempotency" in _constraint_names(
        "atlas_turn_access_grants", UniqueConstraint
    )
    assert "uq_atlas_turn_context_pack_idempotency" in _constraint_names(
        "atlas_turn_context_packs", UniqueConstraint
    )
    projection = OrmBase.metadata.tables["atlas_turn_input_projections"]
    assert [column.name for column in projection.primary_key.columns] == [
        "projection_ref"
    ]
    assert projection.c.execution_id.unique is True
    assert {
        "ck_atlas_turn_input_projection_original",
        "ck_atlas_turn_input_projection_resolver_outcome",
        "ck_atlas_turn_input_projection_rewrite_outcome",
        "ck_atlas_turn_input_projection_rewrite_after_resolver",
    } <= _constraint_names(projection.name, CheckConstraint)
    assert "uq_atlas_turn_retrieval_replay" in _constraint_names(
        "atlas_turn_retrieval_invocations", UniqueConstraint
    )
    assert _unique_columns(
        "atlas_turn_retrieval_invocations", "uq_atlas_turn_retrieval_replay"
    ) == [
        "execution_id",
        "catalog_ref",
        "action",
        "schema_version",
        "arguments_digest",
    ]
    assert "uq_atlas_turn_retrieval_release_idempotency" in _constraint_names(
        "atlas_turn_retrieval_releases", UniqueConstraint
    )
    recent = OrmBase.metadata.tables["atlas_turn_context_pack_recent_exchanges"]
    assert [column.name for column in recent.primary_key.columns] == [
        "context_pack_ref", "position"
    ]
    assert "ck_atlas_turn_context_recent_user_text" in _constraint_names(
        recent.name, CheckConstraint
    )
    summary = OrmBase.metadata.tables["atlas_turn_context_summaries"]
    assert "ck_atlas_turn_context_summary_tokens" in _constraint_names(
        summary.name, CheckConstraint
    )


def test_retrieval_jsonb_has_database_byte_caps() -> None:
    assert "ck_atlas_turn_catalog_document_descriptor_bytes" in _constraint_names(
        "atlas_turn_catalog_documents", CheckConstraint
    )
    assert "ck_atlas_turn_retrieval_arguments_bytes" in _constraint_names(
        "atlas_turn_retrieval_invocations", CheckConstraint
    )
    assert "ck_atlas_turn_retrieval_observation_bytes" in _constraint_names(
        "atlas_turn_retrieval_results", CheckConstraint
    )
    assert {
        "ck_atlas_turn_retrieval_evidence_pack_count",
        "ck_atlas_turn_retrieval_evidence_pack_bytes",
    } <= _constraint_names("atlas_turn_retrieval_evidence_packs", CheckConstraint)


def test_catalog_pins_authority_and_exact_document_lineage() -> None:
    catalog = OrmBase.metadata.tables["atlas_turn_knowledge_catalogs"]
    assert "authorization_revision" in catalog.columns
    assert "generation_retention_ref" in catalog.columns
    assert "ck_atlas_turn_catalog_authorization_revision" in _constraint_names(
        catalog.name, CheckConstraint
    )
    document = OrmBase.metadata.tables["atlas_turn_catalog_documents"]
    assert {
        "lifecycle_epoch",
        "document_version_ref",
        "processing_generation_ref",
        "index_generation_ref",
        "manifest_digest",
    } <= set(document.columns.keys())
    assert {
        "ck_atlas_turn_catalog_document_lifecycle_epoch",
        "ck_atlas_turn_catalog_document_manifest_digest",
    } <= _constraint_names(document.name, CheckConstraint)


def test_processing_owner_retention_replaces_legacy_retrieval_pins() -> None:
    tables = OrmBase.metadata.tables
    assert {
        "atlas_processing_generation_retentions",
        "atlas_processing_generation_retention_entries",
    } <= set(tables)
    assert {
        "atlas_retrieval_requests",
        "atlas_retrieval_universe_entries",
        "atlas_retrieval_uses",
    }.isdisjoint(tables)
    claim = tables[async_processing.AtlasProcessingGenerationRetentionRow.__tablename__]
    entry = tables[
        async_processing.AtlasProcessingGenerationRetentionEntryRow.__tablename__
    ]
    assert {"execution_id", "digest", "status", "release_idempotency_key"} <= set(
        claim.columns.keys()
    )
    assert {
        "retention_ref",
        "index_generation_id",
        "document_version_id",
        "processing_generation",
        "manifest_digest",
    } <= set(entry.columns.keys())
    assert {foreign_key.column.table.name for foreign_key in entry.foreign_keys} == {
        "atlas_processing_generation_retentions",
        "atlas_index_generations",
    }


def test_context_lineage_allows_multiple_resources_from_one_source_turn() -> None:
    lineage = OrmBase.metadata.tables["atlas_turn_context_lineage_edges"]
    assert {
        "edge_id",
        "dependent_context_pack_ref",
        "source_turn_id",
        "source_resource_ref",
        "source_resource_kind",
        "dependency_kind",
        "lifecycle_epoch",
        "version_ref",
        "generation_ref",
    } <= set(lineage.columns.keys())
    assert "uq_atlas_turn_context_lineage_edge" not in _constraint_names(
        lineage.name, UniqueConstraint
    )


def test_runtime_has_independent_versioned_lease_and_dedup_ledgers() -> None:
    execution = OrmBase.metadata.tables["atlas_turn_executions"]
    assert {
        "max_tool_invocations", "max_catalog_pages",
        "max_search_rounds", "max_unique_evidence", "max_provider_invocations",
        "max_reasoning_revision_cycles",
        "context_token_budget", "tool_token_budget", "deadline_seconds",
        "heartbeat_interval_seconds", "ttl_seconds", "failure_sweep_interval_seconds",
    } <= set(execution.columns.keys())
    assert {
        "response_language",
        "reasoning_mode",
        "reasoning_trace",
        "applied_guidance_revision",
        "applied_guidance_digest",
    } <= set(execution.columns.keys())
    assert execution.c.reasoning_trace.type.none_as_null is True
    assert "max_document_candidates" not in execution.columns
    assert "route_policy" not in execution.columns
    assert "lease_policy" not in execution.columns
    assert {
        "ck_atlas_turn_execution_nonnegative_policy",
        "ck_atlas_turn_execution_provider_budget",
        "ck_atlas_turn_execution_positive_policy",
        "ck_atlas_turn_execution_lease_policy",
        "ck_atlas_turn_execution_response_language",
        "ck_atlas_turn_execution_reasoning_mode",
        "ck_atlas_turn_execution_reasoning_trace",
        "ck_atlas_turn_execution_guidance_snapshot",
    } <= _constraint_names(execution.name, CheckConstraint)


def test_conversation_language_and_answer_behavior_are_durably_bounded() -> None:
    conversation = OrmBase.metadata.tables["atlas_turn_conversations"]
    assert conversation.c.response_language.nullable is False
    assert conversation.c.reasoning_mode.nullable is False
    assert "ck_atlas_turn_conversation_response_language" in _constraint_names(
        conversation.name, CheckConstraint
    )
    assert "ck_atlas_turn_conversation_reasoning_mode" in _constraint_names(
        conversation.name, CheckConstraint
    )
    guidance = OrmBase.metadata.tables[
        "atlas_turn_answer_behavior_revisions"
    ]
    assert guidance.c.custom_guidance.nullable is True
    assert {
        "ck_atlas_turn_answer_behavior_revision",
        "ck_atlas_turn_answer_behavior_guidance_length",
        "ck_atlas_turn_answer_behavior_guidance_digest",
        "ck_atlas_turn_answer_behavior_request_digest",
    } <= _constraint_names(guidance.name, CheckConstraint)
    assert "uq_atlas_turn_answer_behavior_idempotency" in _constraint_names(
        guidance.name, UniqueConstraint
    )
    lease = OrmBase.metadata.tables["atlas_turn_execution_leases"]
    assert {"lease_version", "fencing_token", "expires_at"} <= set(lease.columns.keys())
    assert "ck_atlas_turn_execution_lease_times" in _constraint_names(
        lease.name, CheckConstraint
    )
    candidate = OrmBase.metadata.tables["atlas_turn_document_candidate_ledger"]
    evidence = OrmBase.metadata.tables["atlas_turn_unique_evidence_ledger"]
    assert [column.name for column in candidate.primary_key.columns] == [
        "execution_id", "document_identity"
    ]
    assert [column.name for column in evidence.primary_key.columns] == [
        "execution_id", "evidence_identity"
    ]
    tool = OrmBase.metadata.tables["atlas_turn_tool_ledger"]
    assert {
        "reserve_catalog_pages",
        "reserve_document_candidates",
        "reserve_search_rounds",
        "reserve_unique_evidence",
        "reserve_tool_tokens",
    } <= set(tool.columns.keys())
    assert "ck_atlas_turn_tool_reservations_nonnegative" in _constraint_names(
        tool.name, CheckConstraint
    )


def test_terminal_events_and_release_intents_are_single_winner_shapes() -> None:
    events = OrmBase.metadata.tables["atlas_turn_runtime_events"]
    assert {
        "reasoning_phase",
        "progress_status",
        "cycle",
        "message_code",
        "message_params",
    } <= set(events.columns.keys())
    assert "ck_atlas_turn_runtime_event_reasoning_shape" in _constraint_names(
        events.name, CheckConstraint
    )
    assert "ck_atlas_turn_runtime_event_message_params_bound" in _constraint_names(
        events.name, CheckConstraint
    )
    assert "uq_atlas_turn_runtime_event_sequence" in _constraint_names(
        "atlas_turn_runtime_events", UniqueConstraint
    )
    assert "ck_atlas_turn_terminal_outcome_shape" in _constraint_names(
        "atlas_turn_terminal_outcomes", CheckConstraint
    )
    assert "uq_atlas_turn_release_intent_idempotency" in _constraint_names(
        "atlas_turn_release_intents", UniqueConstraint
    )
    allocation_replay = OrmBase.metadata.tables["atlas_turn_runtime_idempotency"]
    assert [column.name for column in allocation_replay.primary_key.columns] == [
        "scope_ref", "operation", "idempotency_key"
    ]
    assert "result_execution_id" in allocation_replay.columns


def test_schema_files_do_not_define_repository_behavior() -> None:
    # Schema providers stay import-only and expose no clock-derived default that
    # would make immutable timestamps process-authoritative.
    for table_name in OWNER_BY_TABLE:
        for column in OrmBase.metadata.tables[table_name].columns:
            if isinstance(column.type, DateTime):
                assert column.default is None
