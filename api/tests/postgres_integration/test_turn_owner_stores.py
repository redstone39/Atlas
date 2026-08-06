from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier

import pytest
from sqlalchemy import delete, inspect

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.authorization import (
    AtlasAuthorizationRevisionRow,
    AtlasTurnAccessGrantReleaseRow,
    AtlasTurnAccessGrantRow,
)
from atlas_production.infrastructure.persistence.context_engineering import (
    AtlasTurnContextLineageEdgeRow,
    AtlasTurnContextPackRecentExchangeRow,
    AtlasTurnContextPackRecentResourceRow,
    AtlasTurnContextPackReleaseRow,
    AtlasTurnContextPackRow,
    AtlasTurnContextSummaryRow,
    AtlasTurnContextSummarySourceResourceRow,
    AtlasTurnContextSummarySourceRow,
    AtlasTurnInputProjectionRow,
)
from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationIdempotencyRow,
    AtlasTurnConversationMemberRow,
    AtlasTurnConversationRow,
)
from atlas_production.infrastructure.persistence.payload_policy import (
    PersistedPayloadPolicyError,
)
from atlas_production.infrastructure.persistence.retrieval import (
    AtlasTurnCatalogDocumentRow,
    AtlasTurnEvidenceIdentityRow,
    AtlasTurnKnowledgeCatalogRow,
    AtlasTurnRetrievalEvidencePackRow,
    AtlasTurnRetrievalHandleRow,
    AtlasTurnRetrievalInvocationRow,
    AtlasTurnRetrievalReleaseRow,
    AtlasTurnRetrievalResultRow,
)
from atlas_production.infrastructure.postgres_owner.authorization import (
    AuthorizationStoreConflict,
    CreateGrantInput,
    PostgresAuthorizationStore,
    ReleaseGrantInput,
)
from atlas_production.infrastructure.postgres_authorization_v1_adapter import (
    PostgresAuthorizationV1Adapter,
)
from atlas_production.infrastructure.postgres_conversation_v1_adapter import (
    PostgresConversationV1Adapter,
)
from atlas_production.modules.authorization.public import (
    CreateTurnAccessGrantV1,
    CurrentGrantAuthorizationSnapshotV1,
)
from atlas_production.modules.conversation.public import (
    AppendTurnMemberV1,
    ConversationMembershipConflict,
)
from atlas_production.infrastructure.postgres_owner.context_engineering import (
    ContextMessageInput,
    ContextStoreConflict,
    CreateInputProjectionInput,
    MaterializeContextInput,
    PostgresContextEngineeringStore,
    RecentExchangeInput,
    RecordResolverProjectionInput,
    RecordRewriteProjectionInput,
    ReleaseContextInput,
    SourceLineageInput,
    SummaryInput,
    SummarySourceInput,
)
from atlas_production.infrastructure.postgres_owner.conversation_v1 import (
    AppendTurnMemberInput,
    ArchiveConversationInput,
    ConversationStoreConflict,
    CreateConversationInput,
    PostgresConversationV1Store,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    CatalogDocumentInput,
    CreateCatalogInput,
    EvidencePackLineageInput,
    MaterializeEvidencePackInput,
    PersistInvocationResultInput,
    PostgresRetrievalV1Store,
    ReleaseCatalogInput,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.shared.public import AuditEventRecord


PREFIX = "atr020-owner-store"
NOW = datetime.now(timezone.utc)
LEGACY_TURN_TABLES = {
    "atlas_citation_viewer_access", "atlas_citation_viewer_sessions",
    "atlas_citation_viewer_items", "atlas_conversations",
    "atlas_conversation_summaries", "atlas_conversation_turns",
    "atlas_response_segment_records", "atlas_claim_records",
    "atlas_claim_evidence_links", "atlas_claim_support_assessments",
    "atlas_turn_requests", "atlas_runtime_attempts", "atlas_runtime_events",
    "atlas_citation_anchors", "atlas_context_packs", "atlas_conversation_plans",
    "atlas_evidence_packs", "atlas_prompt_snapshots", "atlas_tool_invocations",
}


def test_fresh_baseline_contains_no_superseded_turn_tables(
    postgres_runtime: PostgresRuntime,
) -> None:
    tables = set(inspect(postgres_runtime.engine).get_table_names())
    assert tables.isdisjoint(LEGACY_TURN_TABLES)


@pytest.fixture(autouse=True)
def clean_owner_rows(postgres_runtime: PostgresRuntime):
    tables = (
        AtlasAuditEventRow,
        AtlasTurnRetrievalReleaseRow,
        AtlasTurnRetrievalEvidencePackRow,
        AtlasTurnEvidenceIdentityRow,
        AtlasTurnRetrievalHandleRow,
        AtlasTurnRetrievalResultRow,
        AtlasTurnRetrievalInvocationRow,
        AtlasTurnCatalogDocumentRow,
        AtlasTurnKnowledgeCatalogRow,
        AtlasTurnContextPackReleaseRow,
        AtlasTurnContextLineageEdgeRow,
        AtlasTurnContextPackRecentResourceRow,
        AtlasTurnContextPackRecentExchangeRow,
        AtlasTurnContextPackRow,
        AtlasTurnContextSummarySourceResourceRow,
        AtlasTurnContextSummarySourceRow,
        AtlasTurnContextSummaryRow,
        AtlasTurnInputProjectionRow,
        AtlasTurnAccessGrantReleaseRow,
        AtlasTurnAccessGrantRow,
        AtlasAuthorizationRevisionRow,
        AtlasTurnConversationIdempotencyRow,
        AtlasTurnConversationMemberRow,
        AtlasTurnConversationRow,
    )
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))


def test_conversation_create_replay_and_ordered_membership_cas(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresConversationV1Store(postgres_runtime.session_factory)
    create = CreateConversationInput(
        f"conversation-{PREFIX}",
        f"actor-{PREFIX}",
        "Owner-local",
        "create-key",
        "en",
    )
    first = store.create(create)
    assert store.create(create) == first
    with pytest.raises(ConversationStoreConflict):
        store.create(
            CreateConversationInput(
                create.conversation_id,
                create.actor_id,
                "Changed",
                create.idempotency_key,
                create.response_language,
            )
        )

    append = AppendTurnMemberInput(
        create.conversation_id, create.actor_id, f"turn-{PREFIX}-1", f"execution-{PREFIX}-1",
        "user", 1, "turn-key-1", reasoning_mode="deep",
    )
    member = store.append_turn_member(append)
    assert store.append_turn_member(append) == member
    with pytest.raises(ConversationStoreConflict):
        store.append_turn_member(
            AppendTurnMemberInput(
                create.conversation_id, create.actor_id, f"turn-{PREFIX}-2", f"execution-{PREFIX}-2",
                "assistant", 1, "turn-key-2", reasoning_mode="standard",
            )
        )
    assert [row.ordinal for row in store.candidate_turns(create.conversation_id)] == [1]

    retry = AppendTurnMemberInput(
        create.conversation_id, create.actor_id, f"turn-{PREFIX}-retry",
        f"execution-{PREFIX}-retry", "user", 2, "turn-key-retry",
        operation="retry_turn", retry_of_turn_id=append.turn_id,
    )
    retry_member = store.append_turn_member(retry)
    assert store.append_turn_member(retry) == retry_member
    assert retry_member.retry_of_turn_id == append.turn_id
    assert store.retry_sources(create.conversation_id) == {
        retry.turn_id: append.turn_id
    }

    archive = ArchiveConversationInput(
        conversation_id=create.conversation_id,
        actor_id=create.actor_id,
        expected_next_ordinal=3,
        idempotency_key="archive-key",
    )
    audit = AuditEventRecord(
        event_id=f"audit-{PREFIX}-archive",
        event_type="conversation_archived",
        actor_id=create.actor_id,
        target_ref=f"conversation:{create.conversation_id}",
        project_id=None,
        message_code="conversation.was_archived",
        metadata={"status": "archived"},
        created_at=NOW.isoformat(),
    )
    with pytest.raises(ConversationStoreConflict):
        store.archive(
            replace(
                archive,
                expected_next_ordinal=2,
                idempotency_key="archive-stale-membership-snapshot",
            ),
            audit_event=replace(audit, event_id=f"audit-{PREFIX}-stale-archive"),
        )
    assert store.get(create.conversation_id).status == "active"  # type: ignore[union-attr]

    with pytest.raises(PersistedPayloadPolicyError):
        store.archive(
            archive,
            audit_event=replace(
                audit,
                metadata={"not_allowlisted": "must rollback archive"},
            ),
        )
    assert store.get(create.conversation_id).status == "active"  # type: ignore[union-attr]

    archived = store.archive(archive, audit_event=audit)
    assert store.archive(archive, audit_event=audit) == archived
    assert archived.conversation.status == "archived"
    assert archived.audit_event_ref == audit.event_id
    assert store.list_for_actor(create.actor_id) == ()
    assert store.list_all()[0].status == "archived"
    assert len(store.candidate_turns(create.conversation_id)) == 2
    with pytest.raises(ConversationStoreConflict):
        store.append_turn_member(
            AppendTurnMemberInput(
                create.conversation_id,
                create.actor_id,
                f"turn-{PREFIX}-after-archive",
                f"execution-{PREFIX}-after-archive",
                "user",
                3,
                "turn-key-after-archive",
                reasoning_mode="standard",
            )
        )
    adapter = PostgresConversationV1Adapter(postgres_runtime.session_factory)
    with pytest.raises(ConversationMembershipConflict):
        adapter.append_turn_member(
            actor_id=create.actor_id,
            command=AppendTurnMemberV1(
                conversation_id=create.conversation_id,
                turn_id=f"turn-{PREFIX}-adapter-after-archive",
                execution_id=f"execution-{PREFIX}-adapter-after-archive",
                role="user",
                idempotency_key="turn-key-adapter-after-archive",
                operation="create_turn",
                reasoning_mode="standard",
            ),
        )
    with postgres_runtime.session_factory() as session:
        persisted_audit = session.get(AtlasAuditEventRow, audit.event_id)
        assert persisted_audit is not None
        assert persisted_audit.actor_id == create.actor_id
        assert persisted_audit.target_ref == f"conversation:{create.conversation_id}"


def test_authorization_grant_and_release_are_exact_replays(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresAuthorizationStore(postgres_runtime.session_factory)
    create = CreateGrantInput(
        grant_ref=f"grant-{PREFIX}", execution_id=f"execution-{PREFIX}", actor_id=f"actor-{PREFIX}",
        conversation_id=f"conversation-{PREFIX}", authorization_revision=1,
        authority_digest="a" * 64, deadline_at=NOW + timedelta(hours=1), idempotency_key="grant-key",
    )
    grant = store.create_grant(create)
    assert store.create_grant(create) == grant
    with pytest.raises(AuthorizationStoreConflict):
        store.create_grant(replace(create, conversation_id="changed"))
    release = ReleaseGrantInput("release-grant-1", create.execution_id, create.grant_ref, "release-key")
    assert store.release_grant(release) == store.release_grant(release)
    with pytest.raises(AuthorizationStoreConflict):
        store.release_grant(ReleaseGrantInput("release-grant-2", create.execution_id, create.grant_ref, "release-key"))


def test_authorization_public_replay_precedes_changed_or_revoked_current_acl(
    postgres_runtime: PostgresRuntime,
) -> None:
    class CurrentAuthority:
        revision = 1
        authorized = True
        calls = 0

        def current_grant_authorization(self, *, actor_id, conversation_id, deadline_at=None):
            self.calls += 1
            return CurrentGrantAuthorizationSnapshotV1(
                actor_id=actor_id,
                conversation_id=conversation_id,
                authorization_revision=self.revision,
                snapshot_ref=f"authority-snapshot-{self.revision}",
                authorized=self.authorized,
            )

        def current_resource_authorizations(self, **_kwargs):
            return ()

    current = CurrentAuthority()
    adapter = PostgresAuthorizationV1Adapter(
        PostgresAuthorizationStore(postgres_runtime.session_factory), current
    )
    command = CreateTurnAccessGrantV1(
        execution_id=f"execution-{PREFIX}-public",
        actor_id=f"actor-{PREFIX}-public",
        conversation_id=f"conversation-{PREFIX}-public",
        deadline_at=NOW + timedelta(hours=1),
        idempotency_key="grant-public-key",
    )
    first = adapter.create_grant(command)
    current.revision = 2
    current.authorized = False
    assert adapter.create_grant(command) == first
    assert current.calls == 1
    with pytest.raises(AuthorizationStoreConflict, match="public command changed"):
        adapter.create_grant(command.model_copy(update={"execution_id": "changed"}))


def test_context_pack_materializes_summary_sources_and_multi_resource_lineage(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresContextEngineeringStore(postgres_runtime.session_factory)
    projection_ref = f"input-projection-{PREFIX}-context"
    execution_id = f"execution-{PREFIX}"
    store.create_input_projection(
        CreateInputProjectionInput(
            projection_ref=projection_ref,
            execution_id=execution_id,
            original_user_input="compare them",
        )
    )
    store.record_resolver_projection(
        RecordResolverProjectionInput(
            execution_id=execution_id,
            resolver_output="the two authorized documents",
            resolver_invocation_ref=f"resolver-invocation-{PREFIX}",
            failure_code=None,
        )
    )
    store.record_rewrite_projection(
        RecordRewriteProjectionInput(
            execution_id=execution_id,
            rewritten_user_input="compare",
            rewrite_invocation_ref=f"rewrite-invocation-{PREFIX}",
            failure_code=None,
        )
    )
    command = MaterializeContextInput(
        context_pack_ref=f"context-pack-{PREFIX}", execution_id=execution_id,
        input_projection_ref=projection_ref,
        conversation_id=f"conversation-{PREFIX}", dependent_turn_id=f"turn-{PREFIX}-current",
        model_user_input="compare", recent_tail=(
            RecentExchangeInput(
                f"turn-{PREFIX}-recent-root",
                f"turn-{PREFIX}-recent",
                "1" * 64,
                ContextMessageInput("user", "question"),
                ContextMessageInput("assistant", "recent", "verified"),
            ),
        ),
        summary=SummaryInput(
            f"summary-{PREFIX}",
            None,
            "older user summary",
            "older assistant summary",
            4,
            (
                SummarySourceInput(
                    f"turn-{PREFIX}-old-root-1", f"turn-{PREFIX}-old-1", "2" * 64
                ),
                SummarySourceInput(
                    f"turn-{PREFIX}-old-root-2",
                    f"turn-{PREFIX}-old-2",
                    "3" * 64,
                    ("document-1",),
                ),
            ),
        ),
        source_lineage=(
            SourceLineageInput(f"turn-{PREFIX}-recent", None, "turn", "recent_turn"),
            SourceLineageInput(f"turn-{PREFIX}-old-1", f"summary-{PREFIX}", "summary", "summary_source"),
            SourceLineageInput(f"turn-{PREFIX}-old-2", f"summary-{PREFIX}", "summary", "summary_source"),
            SourceLineageInput(
                f"turn-{PREFIX}-old-2", "document-version-1", "document", "knowledge_hint",
                2, "document-version-1", "index-generation-1",
            ),
            SourceLineageInput(
                f"turn-{PREFIX}-old-2", "evidence-ref-1", "evidence", "knowledge_hint",
                2, "document-version-1", "index-generation-1",
            ),
        ), token_budget=16000, idempotency_key="context-key",
    )
    pack = store.materialize(command)
    assert store.materialize(command) == pack
    assert pack.summary is not None
    assert pack.summary.historical_user_context == "older user summary"
    assert (
        pack.summary.assistant_pending_verification_context
        == "older assistant summary"
    )
    assert [source.representative_turn_id for source in pack.summary.sources] == [
        f"turn-{PREFIX}-old-1", f"turn-{PREFIX}-old-2"
    ]
    graph = store.lineage_graph([command.dependent_turn_id])
    assert {edge.source_resource_kind for edge in graph.edges} == {"turn", "summary", "document", "evidence"}
    release = ReleaseContextInput(
        release_ref=f"context-release-{PREFIX}",
        execution_id=command.execution_id,
        context_pack_ref=command.context_pack_ref,
        idempotency_key="context-release-key",
    )
    assert store.release(release) == store.release(release)
    with pytest.raises(ContextStoreConflict, match="release replay changed"):
        store.release(replace(release, context_pack_ref="changed-context"))
    with pytest.raises(ContextStoreConflict):
        store.materialize(replace(command, model_user_input="changed"))
    with pytest.raises(ValueError, match="recent exchanges require exact"):
        store.materialize(
            replace(
                command,
                context_pack_ref=f"context-pack-{PREFIX}-missing-recent",
                execution_id=f"execution-{PREFIX}-missing-recent",
                source_lineage=command.source_lineage[1:],
            )
        )
    with pytest.raises(ValueError, match="summary sources require exact"):
        store.materialize(
            replace(
                command,
                context_pack_ref=f"context-pack-{PREFIX}-missing-summary",
                execution_id=f"execution-{PREFIX}-missing-summary",
                source_lineage=tuple(
                    edge
                    for edge in command.source_lineage
                    if not (
                        edge.source_turn_id == f"turn-{PREFIX}-old-2"
                        and edge.source_resource_kind == "summary"
                    )
                ),
            )
        )


def test_input_projection_persists_monotonic_resolver_and_rewrite_audit(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresContextEngineeringStore(postgres_runtime.session_factory)
    create = CreateInputProjectionInput(
        projection_ref=f"input-projection-{PREFIX}",
        execution_id=f"execution-{PREFIX}-projection",
        original_user_input="它跟上一個差在哪裡？",
    )
    original = store.create_input_projection(create)
    assert store.create_input_projection(create) == original
    assert original.resolver_output is None
    with pytest.raises(ContextStoreConflict, match="replay payload changed"):
        store.create_input_projection(
            replace(create, original_user_input="changed original")
        )

    resolver = RecordResolverProjectionInput(
        execution_id=create.execution_id,
        resolver_output="使用者指的是文件 A 與文件 B 的差異。",
        resolver_invocation_ref="invocation-resolver-1",
        failure_code=None,
    )
    resolved = store.record_resolver_projection(resolver)
    assert store.record_resolver_projection(resolver) == resolved
    assert resolved.updated_at >= original.updated_at
    with pytest.raises(ContextStoreConflict, match="resolver projection replay changed"):
        store.record_resolver_projection(
            replace(resolver, resolver_output="changed resolver")
        )

    rewrite = RecordRewriteProjectionInput(
        execution_id=create.execution_id,
        rewritten_user_input="文件 B 與文件 A 有哪些差異？",
        rewrite_invocation_ref="invocation-rewrite-1",
        failure_code=None,
    )
    projected = store.record_rewrite_projection(rewrite)
    assert store.record_rewrite_projection(rewrite) == projected
    assert projected.original_user_input == create.original_user_input
    assert projected.resolver_output == resolver.resolver_output
    assert projected.rewritten_user_input == rewrite.rewritten_user_input


def test_input_projection_concurrent_exact_create_replays_single_row(
    postgres_runtime: PostgresRuntime,
) -> None:
    command = CreateInputProjectionInput(
        projection_ref=f"input-projection-{PREFIX}-concurrent",
        execution_id=f"execution-{PREFIX}-projection-concurrent",
        original_user_input="它和前一份文件有什麼不同？",
    )
    barrier = Barrier(2)

    def create_projection(_: int):
        store = PostgresContextEngineeringStore(postgres_runtime.session_factory)
        barrier.wait()
        return store.create_input_projection(command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        projections = tuple(pool.map(create_projection, range(2)))

    assert projections[0] == projections[1]
    assert projections[0].projection_ref == command.projection_ref
    assert projections[0].execution_id == command.execution_id
    assert projections[0].original_user_input == command.original_user_input

    stored = PostgresContextEngineeringStore(
        postgres_runtime.session_factory
    ).get_input_projection(command.execution_id)
    assert stored == projections[0]


def test_input_projection_records_terminal_stage_failure_without_rewrite(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresContextEngineeringStore(postgres_runtime.session_factory)
    create = CreateInputProjectionInput(
        projection_ref=f"input-projection-{PREFIX}-failed",
        execution_id=f"execution-{PREFIX}-projection-failed",
        original_user_input="What does it mean?",
    )
    store.create_input_projection(create)
    failed = store.record_resolver_projection(
        RecordResolverProjectionInput(
            execution_id=create.execution_id,
            resolver_output=None,
            resolver_invocation_ref="invocation-resolver-failed",
            failure_code="provider_refused",
        )
    )
    assert failed.resolver_failure_code == "provider_refused"
    with pytest.raises(ContextStoreConflict, match="successful resolver"):
        store.record_rewrite_projection(
            RecordRewriteProjectionInput(
                execution_id=create.execution_id,
                rewritten_user_input="Should not persist",
                rewrite_invocation_ref="invocation-rewrite-invalid",
                failure_code=None,
            )
        )


def _catalog(execution_suffix: str = "1") -> CreateCatalogInput:
    return CreateCatalogInput(
        catalog_ref=f"catalog-{PREFIX}-{execution_suffix}",
        execution_id=f"execution-{PREFIX}-{execution_suffix}",
        grant_ref=f"grant-{PREFIX}-{execution_suffix}", authorization_revision=7,
        generation_retention_ref=f"retention-{PREFIX}-{execution_suffix}",
        retrieval_generation_ref=f"retrieval-generation-{execution_suffix}",
        documents=(CatalogDocumentInput(
            document_handle=f"document-handle-{execution_suffix}", lifecycle_epoch=3,
            document_version_ref=f"document-version-{execution_suffix}", generation_ref=f"generation-{execution_suffix}",
            processing_generation_ref=f"processing-{execution_suffix}",
            processing_revision_ref=f"revision-{execution_suffix}",
            index_generation_ref=f"index-{execution_suffix}",
            manifest_digest=(execution_suffix[-1] if execution_suffix[-1] in "abcdef" else "b") * 64,
            descriptor={"display_name": f"Document {execution_suffix}", "media_type": "application/pdf", "modalities": ["text"]},
        ),), idempotency_key=f"catalog-key-{execution_suffix}",
    )


def test_retrieval_exact_pins_replay_scope_evidence_pack_release_and_rollback(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresRetrievalV1Store(postgres_runtime.session_factory)
    catalog_input = _catalog()
    catalog = store.create_catalog(catalog_input)
    assert store.create_catalog(catalog_input) == catalog
    assert catalog.documents[0].index_generation_ref == "index-1"
    assert catalog.documents[0].processing_generation_ref == "processing-1"

    invocation = PersistInvocationResultInput(
        invocation_id=f"invocation-{PREFIX}-1", result_ref=f"result-{PREFIX}-1",
        execution_id=catalog.execution_id, catalog_ref=catalog.catalog_ref, invocation_ordinal=1,
        action="search_knowledge", schema_version="search-knowledge-v1",
        canonical_arguments={
            "action": "search_knowledge",
            "query_text": "alpha",
            "document_handles": [catalog.documents[0].document_handle],
        },
        result_type="knowledge_search_result",
        observation={"result_type": "knowledge_search_result", "evidence": [], "next_cursor": None},
        error_code=None,
        handles=(
            ResultHandleInput(
                f"evidence-handle-{PREFIX}-1", "evidence", f"evidence-ref-{PREFIX}-1",
                f"identity-{PREFIX}-1", catalog.documents[0].document_handle,
            ),
            ResultHandleInput(
                f"page-handle-{PREFIX}-1", "page", f"page-ref-{PREFIX}-1",
                document_handle=catalog.documents[0].document_handle,
            ),
            ResultHandleInput(
                f"visual-handle-{PREFIX}-1", "visual", f"visual-ref-{PREFIX}-1",
                document_handle=catalog.documents[0].document_handle,
            ),
        ),
    )
    first = store.persist_invocation_result(invocation)
    assert store.count_page_and_visual_handles(
        execution_id=catalog.execution_id,
        catalog_ref=catalog.catalog_ref,
    ) == 2
    replay = store.persist_invocation_result(
        replace(
            invocation,
            invocation_id="ignored-replay-invocation",
            result_ref="ignored-replay-result",
            invocation_ordinal=9,
        )
    )
    assert replay.replayed and replay.invocation_id == first.invocation_id
    with pytest.raises(RetrievalStoreConflict):
        store.persist_invocation_result(
            replace(
                invocation,
                invocation_id="changed-result-invocation",
                result_ref="changed-result-ref",
                observation={
                    "result_type": "knowledge_search_result",
                    "evidence": [],
                    "next_cursor": "changed",
                },
            )
        )
    changed_schema = store.persist_invocation_result(
        replace(
            invocation,
            invocation_id="new-schema-invocation",
            result_ref="new-schema-result",
            invocation_ordinal=2,
            schema_version="search-knowledge-v2",
        )
    )
    assert not changed_schema.replayed
    assert changed_schema.schema_version == "search-knowledge-v2"

    injected_document = replace(
        invocation,
        invocation_id="catalog-expansion-invocation",
        result_ref="catalog-expansion-result",
        invocation_ordinal=3,
        schema_version="search-knowledge-v3",
        handles=(
            ResultHandleInput(
                "document-handle-outside-catalog",
                "document",
                "document-version-outside-catalog",
            ),
        ),
    )
    with pytest.raises(RetrievalStoreConflict, match="cannot create a document handle"):
        store.persist_invocation_result(injected_document)
    with postgres_runtime.session_factory() as session:
        assert session.get(AtlasTurnRetrievalInvocationRow, injected_document.invocation_id) is None
        assert session.get(AtlasTurnRetrievalResultRow, injected_document.result_ref) is None
        assert session.get(AtlasTurnRetrievalHandleRow, "document-handle-outside-catalog") is None

    second_catalog = store.create_catalog(_catalog("2"))
    with pytest.raises(RetrievalStoreConflict):
        store.resolve_handles(
            execution_id=second_catalog.execution_id, catalog_ref=second_catalog.catalog_ref,
            handles=(f"evidence-handle-{PREFIX}-1",),
        )
    resolved = store.resolve_handles(
        execution_id=catalog.execution_id, catalog_ref=catalog.catalog_ref,
        handles=(f"evidence-handle-{PREFIX}-1",),
    )[0]
    assert resolved.evidence_identity == f"identity-{PREFIX}-1"
    assert resolved.document_handle == catalog.documents[0].document_handle
    assert resolved.source_result_ref == first.result_ref
    assert resolved.source_invocation_ordinal == first.invocation_ordinal
    tolerant = store.resolve_claimed_handles(
        execution_id=catalog.execution_id,
        catalog_ref=catalog.catalog_ref,
        handles=(
            f"evidence-handle-{PREFIX}-1",
            "unknown-handle",
            f"evidence-handle-{PREFIX}-1",
        ),
    )
    assert tolerant[0] is not None
    assert tolerant[1] is None
    assert tolerant[2] == tolerant[0]
    assert store.resolve_claimed_handles(
        execution_id=second_catalog.execution_id,
        catalog_ref=second_catalog.catalog_ref,
        handles=(f"evidence-handle-{PREFIX}-1",),
    ) == (None,)

    pack_command = MaterializeEvidencePackInput(
        f"evidence-pack-{PREFIX}", catalog.execution_id, catalog.catalog_ref,
        (EvidencePackLineageInput(
            evidence_handle=resolved.handle,
            evidence_ref=resolved.resource_ref,
            evidence_digest=hashlib.sha256(json.dumps(
                {
                    "evidence_ref": resolved.resource_ref,
                    "evidence_identity": resolved.evidence_identity,
                    "document_handle": resolved.document_handle,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()).hexdigest(),
            resource_ref="resource-1",
            document_version_ref="document-version-1",
            processing_revision_ref="revision-1",
            index_generation_ref="index-1",
            page_artifact_ref=None,
            result_ref=resolved.source_result_ref,
            invocation_ordinal=resolved.source_invocation_ordinal,
        ),),
    )
    pack = store.materialize_evidence_pack(pack_command)
    assert store.materialize_evidence_pack(pack_command) == pack
    release = ReleaseCatalogInput(
        f"release-catalog-{PREFIX}", catalog.execution_id, catalog.catalog_ref, "release-key"
    )
    assert store.release_catalog(release) == store.release_catalog(release)

    invalid = PersistInvocationResultInput(
        invocation_id=f"invocation-{PREFIX}-rollback", result_ref=f"result-{PREFIX}-rollback",
        execution_id=second_catalog.execution_id, catalog_ref=second_catalog.catalog_ref, invocation_ordinal=1,
        action="search_knowledge", schema_version="search-knowledge-v1",
        canonical_arguments={"action": "search_knowledge", "query_text": "rollback"},
        result_type="knowledge_search_result",
        observation={"result_type": "knowledge_search_result", "evidence": [], "next_cursor": None},
        error_code=None,
        handles=(ResultHandleInput(
            f"evidence-handle-{PREFIX}-rollback", "evidence", f"evidence-ref-{PREFIX}-rollback",
            f"identity-{PREFIX}-rollback", "document-handle-not-in-catalog",
        ),),
    )
    with pytest.raises(RetrievalStoreConflict):
        store.persist_invocation_result(invalid)
    with postgres_runtime.session_factory() as session:
        assert session.get(AtlasTurnRetrievalInvocationRow, invalid.invocation_id) is None
        assert session.get(AtlasTurnRetrievalResultRow, invalid.result_ref) is None
