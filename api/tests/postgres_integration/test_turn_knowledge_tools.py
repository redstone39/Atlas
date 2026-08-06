from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError

from atlas_production.infrastructure.persistence.authorization import (
    AtlasAuthorizationRevisionRow,
    AtlasTurnAccessGrantRow,
    AtlasTurnGrantDocumentResourceRow,
    AtlasTurnGrantResourceSnapshotRow,
)
from atlas_production.infrastructure.persistence.retrieval import (
    AtlasTurnCatalogDocumentRow,
    AtlasTurnEvidenceIdentityRow,
    AtlasTurnKnowledgeCatalogRow,
    AtlasTurnRetrievalEvidencePackRow,
    AtlasTurnRetrievalHandleRow,
    AtlasTurnRetrievalInvocationRow,
    AtlasTurnRetrievalResultRow,
)
from atlas_production.infrastructure.postgres_owner.authorization import (
    AuthorizationStoreConflict,
    CreateGrantInput,
    PostgresAuthorizationStore,
)
from atlas_production.infrastructure.postgres_authorization_v1_adapter import (
    PostgresGrantDocumentResourceAdapter,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    PostgresRetrievalV1Store,
    _apply_statement_deadline,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    FacetHintsV1,
    ListKnowledgeDocumentsV1,
    SearchKnowledgeV1,
)
from atlas_production.modules.authorization.public import (
    GrantDocumentResourceV1,
    MaterializeGrantDocumentResourcesV1,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendDiscoveryHit,
    BackendEvidence,
    KnowledgeToolService,
)


PREFIX = "atr030-knowledge-tools"


def test_retrieval_statement_deadline_cancels_postgres_work(
    postgres_runtime: PostgresRuntime,
) -> None:
    with postgres_runtime.session_factory() as session:
        transaction = session.begin()
        try:
            _apply_statement_deadline(
                session,
                datetime.now(timezone.utc) + timedelta(milliseconds=50),
            )
            with pytest.raises(OperationalError):
                session.execute(text("SELECT pg_sleep(0.2)"))
        finally:
            transaction.rollback()


class Backend:
    def __init__(self) -> None:
        self.reads = 0

    def search(self, *, documents, query_text, required_modalities, facet_hints, limit, deadline_at=None):
        self.reads += 1
        return [BackendEvidence(
            f"evidence-ref-{PREFIX}", f"identity-{PREFIX}", documents[0].document_handle,
            "Page 1", "snippet", "content", ("text",),
        )]

    def discover_lexical(self, *, documents, query_text, limit, deadline_at=None):
        self.reads += 1
        return [
            BackendDiscoveryHit(
                match_ref=f"opaque-match-{PREFIX}",
                document_handle=documents[0].document_handle,
                preview="example policy",
                locator_label="Page 1",
                page_number=1,
            )
        ]

    def discover_vector(self, *, documents, query_text, limit, deadline_at=None):
        self.reads += 1
        return []

    def inspect(self, *, documents, evidence_refs, deadline_at=None):
        self.reads += 1
        return []

    def expand(self, *, documents, anchor_evidence_refs, direction, limit, deadline_at=None):
        self.reads += 1
        return []


@pytest.fixture(autouse=True)
def clean_rows(postgres_runtime: PostgresRuntime):
    tables = (
        AtlasTurnRetrievalEvidencePackRow, AtlasTurnEvidenceIdentityRow,
        AtlasTurnRetrievalHandleRow, AtlasTurnRetrievalResultRow,
        AtlasTurnRetrievalInvocationRow, AtlasTurnCatalogDocumentRow,
        AtlasTurnKnowledgeCatalogRow, AtlasTurnGrantDocumentResourceRow,
        AtlasTurnGrantResourceSnapshotRow, AtlasTurnAccessGrantRow,
        AtlasAuthorizationRevisionRow,
    )
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))


def _resource() -> GrantDocumentResourceV1:
    return GrantDocumentResourceV1(
        resource_ref=f"private-resource-{PREFIX}", lifecycle_epoch=4,
        document_version_ref="version-4", processing_generation_ref="processing-7",
        index_generation_ref="index-9", manifest_digest="d" * 64,
        display_name="Authorized.pdf", media_type="application/pdf",
        modalities=["text"], tags=["policy"], language="en",
        created_at_label="2026-07-20", searchable_content="example policy",
        version_label="4",
    )


def test_fresh_postgres_grant_catalog_exact_pins_replay_and_rollback(
    postgres_runtime: PostgresRuntime,
) -> None:
    authorization = PostgresAuthorizationStore(postgres_runtime.session_factory)
    grant = authorization.create_grant(CreateGrantInput(
        grant_ref=f"grant-{PREFIX}", execution_id=f"execution-{PREFIX}",
        actor_id=f"actor-{PREFIX}", conversation_id=f"conversation-{PREFIX}",
        authorization_revision=12, authority_digest="e" * 64,
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
        idempotency_key="grant-key",
    ))
    grant_resources = PostgresGrantDocumentResourceAdapter(authorization)
    materialize = MaterializeGrantDocumentResourcesV1(
        execution_id=grant.execution_id, grant_ref=grant.grant_ref,
        authorization_revision=grant.authorization_revision,
        resources=[_resource()], idempotency_key="resource-key",
    )
    snapshot = grant_resources.materialize_grant_document_resources(materialize)
    assert grant_resources.materialize_grant_document_resources(materialize) == snapshot
    with pytest.raises(AuthorizationStoreConflict, match="replay payload changed"):
        grant_resources.materialize_grant_document_resources(
            materialize.model_copy(
                update={"resources": [_resource().model_copy(update={"lifecycle_epoch": 99})]}
            )
        )
    assert grant_resources.grant_document_resources(
        execution_id=grant.execution_id, grant_ref=grant.grant_ref
    ).resources == [_resource()]

    backend = Backend()
    service = KnowledgeToolService(
        grant_resources=grant_resources,
        store=PostgresRetrievalV1Store(postgres_runtime.session_factory),
        backend=backend,
    )
    catalog = service.create_catalog(
        execution_id=grant.execution_id,
        grant_ref=grant.grant_ref,
        generation_retention_ref=f"retention-{PREFIX}",
        idempotency_key="catalog-key",
    )
    listed = service.invoke(
        execution_id=grant.execution_id,
        grant_ref=grant.grant_ref,
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=ListKnowledgeDocumentsV1(
            action="list_knowledge_documents",
            cursor=None,
            page_size=10,
            max_output_tokens=16000,
        ),
    )
    action = SearchKnowledgeV1(
        action="search_knowledge", query_text="retention",
        document_handles=[listed.observation.documents[0].document_handle],
        required_modalities=["text"], facet_hints=FacetHintsV1(
            document_types=[], date_from=None, date_to=None, languages=[], tags=[]
        ),
        limit=20,
        max_output_tokens=16000,
    )
    first = service.invoke(
        execution_id=grant.execution_id, grant_ref=grant.grant_ref,
        catalog_ref=catalog.catalog_ref, invocation_ordinal=2, action=action,
    )
    replay = service.invoke(
        execution_id=grant.execution_id, grant_ref=grant.grant_ref,
        catalog_ref=catalog.catalog_ref, invocation_ordinal=20, action=action,
    )
    assert replay.replayed and replay.result_ref == first.result_ref and backend.reads == 1
    with postgres_runtime.session_factory() as session:
        document = session.get(
            AtlasTurnCatalogDocumentRow,
            (catalog.catalog_ref, first.document_candidate_handles[0]),
        )
        assert document is not None
        assert (
            document.lifecycle_epoch, document.document_version_ref,
            document.processing_generation_ref, document.index_generation_ref,
            document.manifest_digest,
        ) == (4, "version-4", "processing-7", "index-9", "d" * 64)

    discovered = service.invoke(
        execution_id=grant.execution_id,
        grant_ref=grant.grant_ref,
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=3,
        action=DiscoverRelevantDocumentsV1(
            action="discover_relevant_documents",
            query_text="example policy",
            limit=20,
        ),
        max_output_bytes=16_000,
    )
    assert discovered.observation.candidates[0].document_handle == (
        listed.observation.documents[0].document_handle
    )
    with postgres_runtime.session_factory() as session:
        invocation = session.scalar(
            select(AtlasTurnRetrievalInvocationRow).where(
                AtlasTurnRetrievalInvocationRow.action
                == "discover_relevant_documents"
            )
        )
        assert invocation is not None
        result_row = session.scalar(
            select(AtlasTurnRetrievalResultRow).where(
                AtlasTurnRetrievalResultRow.invocation_id
                == invocation.invocation_id
            )
        )
        assert result_row is not None
        assert invocation.canonical_arguments["query_text"] == "example policy"
        assert result_row.observation["discovery_trace"]["ranked_candidates"][0][
            "lineage"
        ]["index_generation_ref"] == "index-9"
        assert "opaque-match" not in result_row.observation["provider_observation"]
        evidence_pack_constraint = session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conname = 'ck_atlas_turn_retrieval_evidence_pack_count'"
            )
        )
        # The unified model-visible-item limit is enforced by Turn Runtime;
        # Retrieval does not duplicate it as an evidence-pack row constraint.
        assert evidence_pack_constraint is None

    def postgres_timed_out_search(**kwargs):
        with postgres_runtime.session_factory() as session:
            _apply_statement_deadline(session, kwargs["deadline_at"])
            session.execute(text("SELECT pg_sleep(0.2)"))
        return []

    backend.search = postgres_timed_out_search
    timed_out = service.invoke(
        execution_id=grant.execution_id,
        grant_ref=grant.grant_ref,
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=4,
        deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=50),
        action=action.model_copy(update={"query_text": "deadline cancellation"}),
    )
    assert timed_out.observation.result_type == "knowledge_tool_error"
    assert timed_out.observation.message_code == "retrieval_tool_timeout"
    with postgres_runtime.session_factory() as session:
        timed_out_row = session.get(
            AtlasTurnRetrievalResultRow,
            timed_out.result_ref,
        )
        assert timed_out_row is not None
        assert timed_out_row.observation["message_code"] == "retrieval_tool_timeout"

    traces = service.read_discovery_traces(
        execution_id=grant.execution_id,
        catalog_ref=catalog.catalog_ref,
    )
    assert len(traces) == 1
    assert traces[0].query_text == "example policy"
    assert traces[0].invocation_ordinal == 3
    assert traces[0].result_ref == discovered.result_ref
    assert traces[0].candidates[0].position == 1
    assert traces[0].candidates[0].document_version_ref == "version-4"
    assert traces[0].candidates[0].processing_generation_ref == "processing-7"
    assert traces[0].candidates[0].index_generation_ref == "index-9"
    assert traces[0].candidates[0].locator_label is not None
