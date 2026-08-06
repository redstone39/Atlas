from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
import tiktoken

from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    CatalogRecord,
    EvidencePackRecord,
    InvocationResultRecord,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    ExpandKnowledgeV1,
    FacetHintsV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    KnowledgeCatalogPageV1,
    ListKnowledgeDocumentsV1,
    NavigateDocumentV1,
    GovernanceEvidencePackV1,
    RetrievalInvocationEnvelopeV1,
    SearchKnowledgeV1,
    knowledge_tool_observation_schema,
)
from atlas_production.modules.processing_pipeline.public import (
    NavigationEvidenceSource,
    NavigationPageSource,
    build_document_navigation_map,
)
from atlas_production.modules.authorization.public import (
    GrantDocumentResourceSnapshotV1,
    GrantDocumentResourceV1,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendDiscoveryHit,
    BackendEvidence,
    BackendVisualImage,
    KnowledgeToolService,
)


NOW = datetime.now(timezone.utc)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class FakeGrantResources:
    def __init__(self) -> None:
        self.current_authorized = True
        self.snapshot = GrantDocumentResourceSnapshotV1(
            grant_ref="grant-1",
            authorization_revision=7,
            resources=[
                GrantDocumentResourceV1(
                    resource_ref="internal-document-17",
                    lifecycle_epoch=3,
                    document_version_ref="document-version-17",
                    processing_generation_ref="processing-generation-4",
                    index_generation_ref="index-generation-9",
                    manifest_digest="a" * 64,
                    display_name="Policy Alpha.pdf", media_type="application/pdf",
                    modalities=["text", "figure"], tags=["policy", "hr"], language="en",
                    created_at_label="2026-01-10", searchable_content="retention alpha leave",
                    version_label="2026.1",
                ),
                GrantDocumentResourceV1(
                    resource_ref="internal-document-23",
                    lifecycle_epoch=5,
                    document_version_ref="document-version-23",
                    processing_generation_ref="processing-generation-8",
                    index_generation_ref="index-generation-11",
                    manifest_digest="b" * 64,
                    display_name="Finance.csv", media_type="text/csv", modalities=["table"],
                    tags=["finance"], language="en", created_at_label="2025-12-01",
                    searchable_content="revenue forecast", version_label=None,
                ),
            ],
            digest="c" * 64,
            created_at=NOW,
        )

    def grant_document_resources(self, *, execution_id: str, grant_ref: str):
        if (execution_id, grant_ref) != ("execution-1", self.snapshot.grant_ref):
            raise RetrievalStoreConflict("grant resource snapshot is unavailable")
        return self.snapshot

    def current_grant_document_resources(
        self, *, execution_id: str, grant_ref: str, deadline_at=None
    ):
        if not self.current_authorized:
            raise PermissionError("grant revoked")
        return self.grant_document_resources(
            execution_id=execution_id, grant_ref=grant_ref
        )


class FakeStore:
    def __init__(self) -> None:
        self.catalog: CatalogRecord | None = None
        self.handles: dict[str, ResultHandleInput] = {}
        self.results: dict[tuple[str, str, str], InvocationResultRecord] = {}
        self.pack: EvidencePackRecord | None = None

    def create_catalog(self, command):
        if self.catalog is None:
            documents = tuple(
                replace(
                    document,
                    processing_revision_ref=(
                        f"processing-revision-{document.index_generation_ref}"
                    ),
                )
                for document in command.documents
            )
            self.catalog = CatalogRecord(
                command.catalog_ref, command.execution_id, command.grant_ref,
                command.generation_retention_ref,
                command.authorization_revision, command.schema_version,
                command.retrieval_generation_ref, documents,
                _digest({"catalog": command.catalog_ref}), NOW,
            )
            for document in documents:
                self.handles[document.document_handle] = ResultHandleInput(
                    document.document_handle, "document", document.document_version_ref
                )
        return self.catalog

    def get_catalog(self, *, execution_id: str, catalog_ref: str, deadline_at=None):
        if self.catalog is None or (execution_id, catalog_ref) != (
            self.catalog.execution_id, self.catalog.catalog_ref
        ):
            raise RetrievalStoreConflict("catalog does not belong to execution")
        return self.catalog

    def resolve_handles(self, *, execution_id: str, catalog_ref: str, handles: tuple[str, ...]):
        self.get_catalog(execution_id=execution_id, catalog_ref=catalog_ref)
        if any(handle not in self.handles for handle in handles):
            raise RetrievalStoreConflict("unknown or out-of-scope retrieval handle")
        return tuple(self.handles[handle] for handle in handles)

    def resolve_claimed_handles(
        self, *, execution_id: str, catalog_ref: str, handles: tuple[str, ...]
    ):
        self.get_catalog(execution_id=execution_id, catalog_ref=catalog_ref)
        return tuple(
            item
            if (item := self.handles.get(handle)) is not None
            and item.source_result_ref is not None
            else None
            for handle in handles
        )

    def replay_invocation(self, *, execution_id, catalog_ref, action, schema_version, canonical_arguments, deadline_at=None):
        self.get_catalog(execution_id=execution_id, catalog_ref=catalog_ref)
        record = self.results.get((action, schema_version, _digest(canonical_arguments)))
        return None if record is None else replace(record, replayed=True)

    def persist_invocation_result(self, command, *, deadline_at=None):
        key = (command.action, command.schema_version, _digest(command.canonical_arguments))
        existing = self.results.get(key)
        if existing:
            return replace(existing, replayed=True)
        for item in command.handles:
            if item.handle_kind == "document":
                raise RetrievalStoreConflict("tool result cannot create a document handle")
            if item.document_handle not in self.handles:
                raise RetrievalStoreConflict("evidence handle lacks catalog-scoped document lineage")
        record = InvocationResultRecord(
            command.invocation_id, command.result_ref, command.execution_id,
            command.catalog_ref, command.invocation_ordinal, command.action,
            command.schema_version, key[2], command.canonical_arguments,
            command.result_type, _digest(command.observation), command.observation,
            command.error_code, NOW, False,
        )
        for item in command.handles:
            self.handles[item.handle] = replace(
                item,
                source_result_ref=record.result_ref,
                source_result_digest=record.result_digest,
                source_invocation_ordinal=record.invocation_ordinal,
            )
        self.results[key] = record
        return record

    def materialize_evidence_pack(self, command):
        self.pack = EvidencePackRecord(
            command.evidence_pack_ref, command.execution_id, command.catalog_ref,
            command.items, _digest(command.items), NOW,
        )
        return self.pack

    def read_evidence_pack(self, evidence_pack_ref):
        return self.pack if self.pack and self.pack.evidence_pack_ref == evidence_pack_ref else None

    def read_invocation_result(self, result_ref):
        return next(
            (item for item in self.results.values() if item.result_ref == result_ref),
            None,
        )

    def release_catalog(self, command):
        return None


class FakeBackend:
    def __init__(self) -> None:
        self.reads = 0
        self.operations: list[str] = []
        self.evidence: dict[str, BackendEvidence] = {}
        self.search_document_scopes: list[tuple[str, ...]] = []
        self.lexical_hits: list[BackendDiscoveryHit] = []
        self.vector_hits: list[BackendDiscoveryHit] = []
        self.lexical_error = False
        self.vector_error = False
        self.navigation_enabled = True

    def discover_lexical(self, *, documents, query_text, limit, deadline_at=None):
        self.reads += 1
        self.operations.append("discover_lexical")
        if self.lexical_error:
            raise ConnectionError("lexical unavailable")
        return self.lexical_hits[:limit]

    def discover_vector(self, *, documents, query_text, limit, deadline_at=None):
        self.reads += 1
        self.operations.append("discover_vector")
        if self.vector_error:
            raise OSError("vector unavailable")
        return self.vector_hits[:limit]

    def search(self, *, documents, query_text, required_modalities, facet_hints, limit, deadline_at=None):
        self.reads += 1
        self.operations.append("search")
        self.search_document_scopes.append(
            tuple(document.document_handle for document in documents)
        )
        document = documents[0]
        item = BackendEvidence(
            "evidence-ref-alpha", "identity-alpha", document.document_handle,
            "Page 2", "Alpha snippet", "Alpha full content", ("text",), 2,
        )
        self.evidence[item.evidence_ref] = item
        return [item]

    def inspect(self, *, documents, evidence_refs, deadline_at=None):
        self.reads += 1
        self.operations.append("inspect")
        return [self.evidence[ref] for ref in evidence_refs]

    def expand(self, *, documents, anchor_evidence_refs, direction, limit, deadline_at=None):
        self.reads += 1
        self.operations.append("expand")
        document = documents[0]
        item = BackendEvidence(
            f"evidence-ref-{direction}", f"identity-{direction}", document.document_handle,
            "Page 3", "Expanded snippet", "Expanded content", ("text",),
        )
        self.evidence[item.evidence_ref] = item
        return [item]

    def read_exact(self, *, documents, evidence_requests):
        self.reads += 1
        self.operations.append("read_exact")
        return [
            self.evidence[ref]
            for ref, _document_handle in evidence_requests
            if ref in self.evidence
        ]

    def render_visual(self, *, document, page_number, normalized_bbox, deadline_at=None):
        self.reads += 1
        self.operations.append("render_visual")
        content = f"image:{page_number}:{normalized_bbox}".encode()
        return BackendVisualImage(
            content=content,
            digest=hashlib.sha256(content).hexdigest(),
            width=800,
            height=600,
        )

    def navigation_map(self, *, document, deadline_at=None):
        self.reads += 1
        self.operations.append("navigation_map")
        if not self.navigation_enabled:
            return None
        return build_document_navigation_map(
            document_version_ref=document.document_version_ref,
            processing_revision_ref=document.processing_revision_ref,
            processing_generation_ref=document.processing_generation_ref,
            media_type=str(document.descriptor["media_type"]),
            pages=[
                NavigationPageSource(1, "第 1 頁", True),
                NavigationPageSource(2, "第 2 頁", True),
                NavigationPageSource(3, "第 3 頁", True),
            ],
            evidence=[
                NavigationEvidenceSource(
                    "figure-1",
                    2,
                    "Figure 1. Pin Assignments",
                    "RTL8111G pin assignments",
                    "figure",
                )
            ],
        )


def _service():
    store = FakeStore()
    backend = FakeBackend()
    service = KnowledgeToolService(
        grant_resources=FakeGrantResources(), store=store, backend=backend
    )
    catalog = service.create_catalog(
        execution_id="execution-1",
        grant_ref="grant-1",
        generation_retention_ref="retention-1",
        idempotency_key="catalog-key",
    )
    return service, store, backend, catalog


def _service_with_resources(resources: list[GrantDocumentResourceV1]):
    grants = FakeGrantResources()
    grants.snapshot = grants.snapshot.model_copy(
        update={
            "resources": resources,
            "digest": _digest([item.resource_ref for item in resources]),
        }
    )
    store = FakeStore()
    backend = FakeBackend()
    service = KnowledgeToolService(
        grant_resources=grants,
        store=store,
        backend=backend,
    )
    catalog = service.create_catalog(
        execution_id="execution-1",
        grant_ref="grant-1",
        generation_retention_ref="retention-1",
        idempotency_key="catalog-key",
    )
    return service, store, backend, catalog


def _resource(index: int, *, display_name: str, searchable_content: str) -> GrantDocumentResourceV1:
    return GrantDocumentResourceV1(
        resource_ref=f"resource-{index}",
        lifecycle_epoch=1,
        document_version_ref=f"document-version-{index}",
        processing_generation_ref=f"processing-generation-{index}",
        index_generation_ref=f"index-generation-{index}",
        manifest_digest=hashlib.sha256(f"manifest-{index}".encode()).hexdigest(),
        display_name=display_name,
        media_type="application/pdf",
        modalities=["text"],
        tags=["manual"],
        language="en",
        created_at_label="2026-01-01",
        searchable_content=searchable_content,
        version_label="V1.0",
    )


def _first_document_handle(store: FakeStore) -> str:
    assert store.catalog is not None
    return store.catalog.documents[0].document_handle


def _discovery_hit(
    document_handle: str,
    suffix: str,
    *,
    preview: str | None = None,
) -> BackendDiscoveryHit:
    return BackendDiscoveryHit(
        match_ref=f"opaque-match-{suffix}",
        document_handle=document_handle,
        preview=preview or f"preview {suffix}",
        locator_label=f"Page {suffix}",
        page_number=1,
    )


def test_navigation_overview_search_around_replay_and_evidence_boundary() -> None:
    service, store, backend, catalog = _service()
    document_handle = _first_document_handle(store)
    overview_action = NavigateDocumentV1(
        action="navigate_document",
        mode="overview",
        document_handle=document_handle,
        navigation_handle=None,
        query_text=None,
        relation=None,
        cursor=None,
        limit=2,
        max_output_tokens=16_000,
    )
    overview = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=overview_action,
    )

    assert overview.observation.result_type == "document_navigation_result"
    assert overview.observation.mode == "overview"
    assert len(overview.observation.targets) == 2
    assert overview.evidence_lineage == []
    first_target = overview.observation.targets[0]
    assert store.handles[first_target.navigation_handle].handle_kind == "navigation"
    assert first_target.page_handle is not None
    assert store.handles[first_target.page_handle].handle_kind == "page"

    reads = backend.reads
    replay = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=99,
        action=overview_action,
    )
    assert replay.replayed
    assert replay.observation == overview.observation
    assert backend.reads == reads

    searched = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=2,
        action=NavigateDocumentV1(
            action="navigate_document",
            mode="search",
            document_handle=document_handle,
            navigation_handle=None,
            query_text="pin assignments",
            relation=None,
            cursor=None,
            limit=10,
            max_output_tokens=16_000,
        ),
    )
    assert searched.search_rounds == 1
    assert any(item.kind == "figure" for item in searched.observation.targets)
    figure = next(
        item for item in searched.observation.targets if item.kind == "figure"
    )

    nearby = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=3,
        action=NavigateDocumentV1(
            action="navigate_document",
            mode="around",
            document_handle=None,
            navigation_handle=figure.navigation_handle,
            query_text=None,
            relation="parent",
            cursor=None,
            limit=10,
            max_output_tokens=16_000,
        ),
    )
    assert [item.page_number for item in nearby.observation.targets] == [2]
    assert nearby.evidence_lineage == []

    declared = service.read_claimed_evidence_lineage(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        handles=[figure.navigation_handle],
    )
    assert declared[0].resolution_status == "unresolved"
    assert declared[0].evidence_ref is None


def test_navigation_unavailable_and_mode_contracts_are_typed() -> None:
    service, store, backend, catalog = _service()
    backend.navigation_enabled = False
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=NavigateDocumentV1(
            action="navigate_document",
            mode="overview",
            document_handle=_first_document_handle(store),
            navigation_handle=None,
            query_text=None,
            relation=None,
            cursor=None,
            limit=10,
            max_output_tokens=16_000,
        ),
    )

    assert result.observation.result_type == "knowledge_tool_error"
    assert result.observation.error_code == "navigation_unavailable"
    assert not result.observation.retryable
    with pytest.raises(ValidationError):
        NavigateDocumentV1(
            action="navigate_document",
            mode="around",
            document_handle=_first_document_handle(store),
            navigation_handle=None,
            query_text=None,
            relation="next",
            cursor=None,
            limit=10,
            max_output_tokens=16_000,
        )


def test_discover_hybrid_ranking_trace_replay_and_model_safe_projection() -> None:
    resources = [
        _resource(index, display_name=f"Document {index}", searchable_content="content")
        for index in range(1, 4)
    ]
    service, store, backend, catalog = _service_with_resources(resources)
    assert store.catalog is not None
    first, second, third = [
        document.document_handle for document in store.catalog.documents
    ]
    backend.lexical_hits = [
        _discovery_hit(first, "lexical-first", preview="lexical preview wins"),
        _discovery_hit(first, "lexical-duplicate"),
        _discovery_hit(second, "lexical-second"),
        _discovery_hit(third, "lexical-third"),
    ]
    backend.vector_hits = [
        _discovery_hit(second, "vector-first", preview="vector preview"),
        _discovery_hit(first, "vector-second"),
    ]
    action = DiscoverRelevantDocumentsV1(
        action="discover_relevant_documents",
        query_text="natural language policy question",
        limit=3,
    )
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=action,
        max_output_bytes=16_000,
    )
    tied = sorted((first, second))
    assert [item.document_handle for item in result.observation.candidates] == [
        *tied,
        third,
    ]
    by_handle = {
        item.document_handle: item for item in result.observation.candidates
    }
    assert by_handle[first].preview == "lexical preview wins"
    assert result.observation.ranking_contract == "equal-reciprocal-rank-v1"
    assert result.observation.channels == ["lexical", "vector"]
    assert not result.observation.degraded
    assert result.observation.vector_coverage == 3
    assert result.evidence_lineage == []
    assert set(store.handles) == {first, second, third}

    provider_payload = result.observation.model_dump_json()
    for internal in (
        "natural language policy question",
        "opaque-match",
        "component",
        "processing_revision",
        "fused_score",
    ):
        assert internal not in provider_payload
    persisted = next(iter(store.results.values())).observation
    trace = persisted["discovery_trace"]
    assert trace["query_text"] == action.query_text
    assert trace["ranked_candidates"][0]["components"]
    assert trace["ranked_candidates"][0]["lineage"]["document_version_ref"]
    assert trace["ranked_candidates"][0]["fused_score"] == "3/2"
    component_for_second = next(
        item
        for item in trace["ranked_candidates"]
        if item["document_handle"] == second
    )
    assert component_for_second["components"]["lexical"]["rank"] == 2

    reads = backend.reads
    replay = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=99,
        action=action,
        max_output_bytes=16_000,
    )
    assert replay.replayed and replay.result_ref == result.result_ref
    assert backend.reads == reads
    assert replay.observation == result.observation


def test_discover_channel_failures_empty_and_budget_truncation() -> None:
    service, store, backend, catalog = _service()
    assert store.catalog is not None
    handles = [item.document_handle for item in store.catalog.documents]
    action = DiscoverRelevantDocumentsV1(
        action="discover_relevant_documents",
        query_text="retention",
        limit=20,
    )
    backend.lexical_error = True
    backend.vector_hits = [_discovery_hit(handles[0], "vector")]
    degraded = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=action,
        max_output_bytes=16_000,
    )
    assert degraded.observation.degraded
    assert degraded.observation.channels == ["vector"]
    assert degraded.observation.vector_coverage == 2

    empty_service, _empty_store, _empty_backend, empty_catalog = _service()
    empty = empty_service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=empty_catalog.catalog_ref,
        invocation_ordinal=1,
        action=action,
        max_output_bytes=16_000,
    )
    assert empty.observation.candidates == []
    assert not empty.observation.degraded

    failed_service, _failed_store, failed_backend, failed_catalog = _service()
    failed_backend.lexical_error = failed_backend.vector_error = True
    failed = failed_service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=failed_catalog.catalog_ref,
        invocation_ordinal=1,
        action=action,
        max_output_bytes=16_000,
    )
    assert failed.observation.result_type == "knowledge_tool_error"
    assert failed.observation.retryable
    assert failed.observation.message_code == "retrieval_backend_unavailable"

    truncated_service, truncated_store, truncated_backend, truncated_catalog = _service()
    assert truncated_store.catalog is not None
    truncated_handles = [
        item.document_handle for item in truncated_store.catalog.documents
    ]
    truncated_backend.lexical_hits = [
        _discovery_hit(handle, f"large-{index}", preview="x" * 400)
        for index, handle in enumerate(truncated_handles, start=1)
    ]
    truncated = truncated_service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=truncated_catalog.catalog_ref,
        invocation_ordinal=1,
        action=action,
        max_output_bytes=950,
    )
    assert truncated.observation.result_type == "relevant_document_discovery_result"
    assert len(truncated.observation.candidates) == 1
    assert truncated.observation.truncated_by_budget
    persisted = next(iter(truncated_store.results.values())).observation
    assert len(persisted["discovery_trace"]["ranked_candidates"]) == 2
    assert persisted["discovery_trace"]["truncated_by_budget"]


def test_discover_input_and_provider_schema_have_no_search_only_contracts() -> None:
    with pytest.raises(ValidationError):
        DiscoverRelevantDocumentsV1(
            action="discover_relevant_documents",
            query_text="x" * 4001,
            limit=1,
        )
    with pytest.raises(ValidationError):
        DiscoverRelevantDocumentsV1(
            action="discover_relevant_documents",
            query_text="query",
            limit=21,
        )
    action_schema = DiscoverRelevantDocumentsV1.model_json_schema()
    assert "required_modalities" not in action_schema["properties"]
    assert "max_output_tokens" not in action_schema["properties"]
    observation_schema = json.dumps(knowledge_tool_observation_schema())
    assert "relevant_document_discovery_result" in observation_schema
    assert "match_ref" not in observation_schema
    assert "component_rank" not in observation_schema


def test_catalog_list_find_search_inspect_expand_replay_and_evidence_pack() -> None:
    service, store, backend, catalog = _service()
    listed = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=ListKnowledgeDocumentsV1(
            action="list_knowledge_documents", cursor=None, page_size=10,
            max_output_tokens=16000,
        ),
    )
    assert isinstance(listed, RetrievalInvocationEnvelopeV1)
    payload = listed.model_dump_json()
    assert "internal-document-17" not in payload and "document-version-17" not in payload
    assert [item.display_name for item in listed.observation.documents] == [
        "Policy Alpha.pdf", "Finance.csv"
    ]

    found = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=2,
        max_output_bytes=16_000,
        action=FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword="policy",
            cursor=None,
        ),
    )
    document_handle = found.observation.documents[0].document_handle
    search_action = SearchKnowledgeV1(
        action="search_knowledge", query_text="alpha", document_handles=[document_handle],
        required_modalities=["text"],
        facet_hints=FacetHintsV1(
            document_types=[], date_from=None, date_to=None, languages=[], tags=[]
        ),
        limit=20,
        max_output_tokens=16000,
    )
    searched = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=3, action=search_action,
    )
    assert [item.evidence_identity for item in searched.evidence_lineage] == ["identity-alpha"]
    assert backend.search_document_scopes == [(document_handle,)]
    assert "identity-alpha" not in searched.observation.model_dump_json()
    evidence_handle = searched.observation.evidence[0].evidence_handle
    declared = service.read_claimed_evidence_lineage(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        handles=[
            evidence_handle,
            document_handle,
            "kh_unknown_handle",
            evidence_handle,
        ],
    )
    assert [item.resolution_status for item in declared] == [
        "resolved",
        "unresolved",
        "unresolved",
        "resolved",
    ]
    assert declared[0].evidence_ref == "evidence-ref-alpha"
    assert declared[0].document_ref == "internal-document-17"
    assert declared[0].document_version_ref == "document-version-17"
    assert declared[0].processing_generation_ref == "processing-generation-4"
    assert declared[0].index_generation_ref == "index-generation-9"
    assert declared[0].document_display_name == "Policy Alpha.pdf"
    assert declared[0].document_version_label == "2026.1"
    assert declared[0].page_number == 2
    assert declared[0].locator_label == "Page 2"
    assert declared[3].duplicate_of_position == 1
    replay = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=99, action=search_action,
    )
    assert replay.replayed and replay.result_ref == searched.result_ref and backend.reads == 1

    inspected = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=4,
        action=InspectKnowledgeV1(
            action="inspect_knowledge", handles=[evidence_handle], max_output_tokens=16000
        ),
    )
    assert inspected.observation.items[0].content == "Alpha full content"
    expanded = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=5,
        action=ExpandKnowledgeV1(
            action="expand_knowledge", anchor_handles=[evidence_handle], direction="next_page",
            limit=20, max_output_tokens=16000,
        ),
    )
    assert expanded.observation.direction == "next_page"
    pack = service.materialize_evidence_pack(
        execution_id="execution-1", catalog_ref=catalog.catalog_ref,
        evidence_handles=[evidence_handle], idempotency_key="pack-key",
    )
    assert [item.evidence_ref for item in pack.items] == ["evidence-ref-alpha"]
    runtime_lineage = searched.evidence_lineage[0]
    assert pack.items[0].evidence_handle == runtime_lineage.evidence_handle
    assert pack.items[0].evidence_digest == runtime_lineage.evidence_digest
    assert pack.items[0].result_ref == runtime_lineage.result_ref
    assert pack.items[0].invocation_ordinal == runtime_lineage.invocation_ordinal
    handles_before_governance = set(store.handles)
    operations_before_governance = list(backend.operations)
    governance_pack = service.read_governance_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_pack_ref=pack.evidence_pack_ref,
        evidence_pack_digest=pack.digest,
    )
    assert isinstance(governance_pack, GovernanceEvidencePackV1)
    assert governance_pack.items[0].evidence_handle == runtime_lineage.evidence_handle
    assert governance_pack.items[0].content == "Alpha full content"
    assert backend.reads == 4  # search, inspect, expand, exact governance read
    assert backend.operations == operations_before_governance + ["read_exact"]
    assert set(store.handles) == handles_before_governance
    assert store.catalog is not None
    first = store.catalog.documents[0]
    assert (
        store.catalog.authorization_revision, first.lifecycle_epoch,
        first.document_version_ref, first.processing_generation_ref,
        first.index_generation_ref, first.manifest_digest,
    ) == (
        7, 3, "document-version-17", "processing-generation-4",
        "index-generation-9", "a" * 64,
    )


def test_identity_find_ignores_content_and_paginates_ten_with_keyword_bound_cursor() -> None:
    resources = [
        _resource(
            0,
            display_name="RTL8111G(S) Layout Guide V1.0",
            searchable_content="four MDI pairs",
        ),
        _resource(
            1,
            display_name="RTL8106E Layout Guide V1.0",
            searchable_content="mentions RTL8111G only in body text",
        ),
        *[
            _resource(
                index,
                display_name=f"Manual {index:02d}",
                searchable_content=f"body {index}",
            )
            for index in range(2, 25)
        ],
    ]
    service, _store, _backend, catalog = _service_with_resources(resources)

    identity = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        max_output_bytes=16_000,
        action=FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword="ＲＴＬ８１１１Ｇ",
            cursor=None,
        ),
    )
    assert [item.display_name for item in identity.observation.documents] == [
        "RTL8111G(S) Layout Guide V1.0"
    ]

    first = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=2,
        max_output_bytes=16_000,
        action=FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword="manual",
            cursor=None,
        ),
    )
    assert len(first.observation.documents) == 10
    assert first.observation.next_cursor is not None
    second = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=3,
        max_output_bytes=16_000,
        action=FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword="MANUAL",
            cursor=first.observation.next_cursor,
        ),
    )
    third = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=4,
        max_output_bytes=16_000,
        action=FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword="manual",
            cursor=second.observation.next_cursor,
        ),
    )
    assert [len(page.observation.documents) for page in (first, second, third)] == [
        10,
        10,
        5,
    ]
    assert third.observation.next_cursor is None
    handles = [
        item.document_handle
        for page in (first, second, third)
        for item in page.observation.documents
    ]
    assert len(handles) == len(set(handles)) == 25

    with pytest.raises(RetrievalStoreConflict, match="cursor"):
        service.invoke(
            execution_id="execution-1",
            grant_ref="grant-1",
            catalog_ref=catalog.catalog_ref,
            invocation_ordinal=5,
            max_output_bytes=16_000,
            action=FindKnowledgeDocumentsV1(
                action="find_knowledge_documents",
                keyword="finance",
                cursor=first.observation.next_cursor,
            ),
        )


@pytest.mark.parametrize("action_name", ["find", "list"])
def test_catalog_pagination_does_not_skip_candidates_when_output_is_bounded(
    action_name: str,
) -> None:
    resources = [
        _resource(
            index,
            display_name=f"Manual {index:02d}",
            searchable_content=f"body {index}",
        )
        for index in range(25)
    ]
    service, _store, _backend, catalog = _service_with_resources(resources)
    cursor = None
    disclosed: list[str] = []

    for invocation_ordinal in range(1, 26):
        action = (
            FindKnowledgeDocumentsV1(
                action="find_knowledge_documents",
                keyword="manual",
                cursor=cursor,
            )
            if action_name == "find"
            else ListKnowledgeDocumentsV1(
                action="list_knowledge_documents",
                cursor=cursor,
                page_size=10,
                max_output_tokens=1200,
            )
        )
        page = service.invoke(
            execution_id="execution-1",
            grant_ref="grant-1",
            catalog_ref=catalog.catalog_ref,
            invocation_ordinal=invocation_ordinal,
            action=action,
            max_output_bytes=1200,
        )
        assert isinstance(page.observation, KnowledgeCatalogPageV1)
        assert 1 <= len(page.observation.documents) <= 10
        disclosed.extend(
            item.display_name for item in page.observation.documents
        )
        cursor = page.observation.next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("bounded catalog pagination did not terminate")

    assert disclosed == [f"Manual {index:02d}" for index in range(25)]
    assert len(disclosed) == len(set(disclosed))


def test_each_search_records_actual_query_handles_result_and_never_expands_scope() -> None:
    service, store, backend, catalog = _service()
    assert store.catalog is not None
    handles = [document.document_handle for document in store.catalog.documents]
    first = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="first query",
            document_handles=[handles[0]],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=16000,
        ),
    )
    second = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=2,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="second query",
            document_handles=[handles[0], handles[1]],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=16000,
        ),
    )

    assert backend.search_document_scopes == [
        (handles[0],),
        (handles[0], handles[1]),
    ]
    persisted = sorted(store.results.values(), key=lambda item: item.invocation_ordinal)
    assert [item.canonical_arguments["query_text"] for item in persisted] == [
        "first query",
        "second query",
    ]
    assert [item.canonical_arguments["document_handles"] for item in persisted] == [
        [handles[0]],
        [handles[0], handles[1]],
    ]
    assert [item.result_ref for item in persisted] == [first.result_ref, second.result_ref]
    assert all(item.observation["result_type"] == "knowledge_search_result" for item in persisted)


def test_retrieval_owner_rejects_constructed_empty_search_before_backend() -> None:
    service, _store, backend, catalog = _service()
    invalid = SearchKnowledgeV1.model_construct(
        action="search_knowledge",
        query_text="must not run",
        document_handles=[],
        required_modalities=[],
        facet_hints=FacetHintsV1(
            document_types=[], date_from=None, date_to=None, languages=[], tags=[]
        ),
        limit=1,
        max_output_tokens=16000,
    )

    with pytest.raises(
        RetrievalStoreConflict,
        match="requires at least one selected document handle",
    ):
        service.invoke(
            execution_id="execution-1",
            grant_ref="grant-1",
            catalog_ref=catalog.catalog_ref,
            invocation_ordinal=1,
            action=invalid,
        )

    assert backend.reads == 0


def test_inspect_visual_supports_direct_rect_recursive_crop_replay_and_governance() -> None:
    service, store, backend, catalog = _service()
    search = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None,
                languages=[], tags=[],
            ),
            limit=1,
            max_output_tokens=16_000,
        ),
    )
    page_handle = search.observation.evidence[0].page_handle
    assert page_handle is not None
    direct = InspectVisualV1(
        action="inspect_visual",
        handle=page_handle,
        scope="rect",
        bbox={"left": 1_000, "top": 2_000, "right": 9_000, "bottom": 8_000},
    )
    first = service.invoke(
        execution_id="execution-1", grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref, invocation_ordinal=2, action=direct,
        max_output_bytes=16_000,
    )

    assert first.visual_image is not None
    assert first.observation.bbox.model_dump() == {
        "left": 1_000, "top": 2_000, "right": 9_000, "bottom": 8_000,
    }
    assert first.evidence_lineage[0].evidence_handle == first.observation.visual_handle

    second = service.invoke(
        execution_id="execution-1", grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref, invocation_ordinal=3,
        max_output_bytes=16_000,
        action=InspectVisualV1(
            action="inspect_visual", handle=first.observation.visual_handle,
            scope="rect",
            bbox={"left": 2_500, "top": 2_500, "right": 7_500, "bottom": 7_500},
        ),
    )
    assert second.observation.bbox.model_dump() == {
        "left": 3_000, "top": 3_500, "right": 7_000, "bottom": 6_500,
    }
    declared_visual = service.read_claimed_evidence_lineage(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        handles=[second.observation.visual_handle],
    )[0]
    assert declared_visual.resolution_status == "resolved"
    assert declared_visual.handle_kind == "visual"
    assert declared_visual.evidence_ref == second.evidence_lineage[0].evidence_ref
    assert declared_visual.result_ref == second.result_ref
    assert declared_visual.invocation_ordinal == 3
    assert declared_visual.page_number == 2
    assert declared_visual.locator_label == (
        "Page 2 bbox [3000,3500,7000,6500]"
    )

    replay = service.invoke(
        execution_id="execution-1", grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref, invocation_ordinal=4, action=direct,
        max_output_bytes=16_000,
    )
    assert replay.replayed is True
    assert replay.observation == first.observation
    assert replay.visual_image is not None
    assert backend.operations.count("render_visual") == 3

    pack = service.materialize_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_handles=[second.observation.visual_handle],
        idempotency_key="visual-pack",
    )
    governance = service.read_governance_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_pack_ref=pack.evidence_pack_ref,
        evidence_pack_digest=pack.digest,
    )
    assert governance.items[0].locator_label == (
        "Page 2 bbox [3000,3500,7000,6500]"
    )
    assert len(governance.visual_images) == 1
    assert governance.visual_images[0].visual_handle == (
        second.observation.visual_handle
    )
    assert governance.visual_images[0].image_digest == (
        second.observation.image_digest
    )
    assert backend.operations.count("render_visual") == 4

    service._grant_resources.current_authorized = False
    renders_before_revoke = backend.operations.count("render_visual")
    with pytest.raises(PermissionError, match="revoked"):
        service.read_governance_evidence_pack(
            execution_id="execution-1",
            catalog_ref=catalog.catalog_ref,
            evidence_pack_ref=pack.evidence_pack_ref,
            evidence_pack_digest=pack.digest,
        )
    assert backend.operations.count("render_visual") == renders_before_revoke


@pytest.mark.parametrize(
    "scope,bbox",
    [
        ("full", {"left": 0, "top": 0, "right": 10_000, "bottom": 10_000}),
        ("rect", None),
        ("rect", {"left": 5_000, "top": 0, "right": 5_000, "bottom": 1}),
        ("rect", {"left": -1, "top": 0, "right": 1, "bottom": 1}),
    ],
)
def test_inspect_visual_rejects_invalid_scope_bbox_contract(scope, bbox) -> None:
    with pytest.raises(ValidationError):
        InspectVisualV1(
            action="inspect_visual",
            handle="kh_page_contract",
            scope=scope,
            bbox=bbox,
        )


def test_inspect_visual_rechecks_current_acl_before_render_and_replay() -> None:
    service, store, backend, catalog = _service()
    search = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None,
                languages=[], tags=[],
            ),
            limit=1,
            max_output_tokens=16_000,
        ),
    )
    page_handle = search.observation.evidence[0].page_handle
    assert page_handle is not None
    action = InspectVisualV1(
        action="inspect_visual", handle=page_handle, scope="full", bbox=None
    )
    first = service.invoke(
        execution_id="execution-1", grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref, invocation_ordinal=2, action=action,
        max_output_bytes=16_000,
    )
    assert first.visual_image is not None
    renders_before_revoke = backend.operations.count("render_visual")

    service._grant_resources.current_authorized = False
    with pytest.raises(PermissionError, match="revoked"):
        service.invoke(
            execution_id="execution-1", grant_ref="grant-1",
            catalog_ref=catalog.catalog_ref, invocation_ordinal=3, action=action,
            max_output_bytes=16_000,
        )
    assert backend.operations.count("render_visual") == renders_before_revoke


def test_cross_scope_and_document_expand_reject_before_backend_read() -> None:
    service, _store, backend, catalog = _service()
    document_handle = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=ListKnowledgeDocumentsV1(
            action="list_knowledge_documents", cursor=None, page_size=1,
            max_output_tokens=16000,
        ),
    ).observation.documents[0].document_handle
    before = backend.reads
    with pytest.raises(RetrievalStoreConflict, match="grant"):
        service.invoke(
            execution_id="execution-1", grant_ref="grant-other", catalog_ref=catalog.catalog_ref,
            invocation_ordinal=2,
            action=SearchKnowledgeV1(
                action="search_knowledge", query_text="x",
                document_handles=[document_handle],
                required_modalities=[],
                facet_hints=FacetHintsV1(
                    document_types=[], date_from=None, date_to=None, languages=[], tags=[]
                ),
                limit=20,
                max_output_tokens=16000,
            ),
        )
    with pytest.raises(RetrievalStoreConflict, match="obtained evidence"):
        service.invoke(
            execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
            invocation_ordinal=3,
            action=ExpandKnowledgeV1(
                action="expand_knowledge", anchor_handles=[document_handle], direction="next_page",
                limit=20, max_output_tokens=16000,
            ),
        )
    with pytest.raises(RetrievalStoreConflict, match="out-of-scope"):
        service.invoke(
            execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
            invocation_ordinal=4,
            action=InspectKnowledgeV1(
                action="inspect_knowledge", handles=["kh_evidence_cross_execution"],
                max_output_tokens=16000,
            ),
        )
    assert backend.reads == before


def test_governance_exact_pack_reader_fails_closed_before_content_read_on_authority_conflict() -> None:
    service, store, backend, catalog = _service()
    searched = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=16000,
        ),
    )
    handle = searched.observation.evidence[0].evidence_handle
    pack = service.materialize_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_handles=[handle],
        idempotency_key="authority-pack",
    )
    before = backend.reads
    base = dict(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_pack_ref=pack.evidence_pack_ref,
        evidence_pack_digest=pack.digest,
    )
    for changed in (
        {"execution_id": "execution-other"},
        {"catalog_ref": "catalog-other"},
        {"evidence_pack_ref": "pack-unknown"},
        {"evidence_pack_digest": "f" * 64},
    ):
        with pytest.raises(RetrievalStoreConflict):
            service.read_governance_evidence_pack(**{**base, **changed})
    assert backend.reads == before

    service._grant_resources.snapshot = service._grant_resources.snapshot.model_copy(
        update={"authorization_revision": 8}
    )
    with pytest.raises(RetrievalStoreConflict, match="authorization changed"):
        service.read_governance_evidence_pack(**base)
    assert backend.reads == before


def test_empty_governance_pack_does_not_read_any_evidence_content() -> None:
    service, _store, backend, catalog = _service()
    pack = service.materialize_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_handles=[],
        idempotency_key="empty-authority-pack",
    )
    before = backend.reads

    exact = service.read_governance_evidence_pack(
        execution_id="execution-1",
        catalog_ref=catalog.catalog_ref,
        evidence_pack_ref=pack.evidence_pack_ref,
        evidence_pack_digest=pack.digest,
    )

    assert exact.items == []
    assert backend.reads == before

def test_new_public_contracts_are_closed_and_reject_internal_id_fields() -> None:
    with pytest.raises(ValidationError):
        GrantDocumentResourceV1.model_validate({
            "resource_ref": "resource-opaque", "lifecycle_epoch": 1,
            "document_version_ref": "version-1", "processing_generation_ref": "processing-1",
            "index_generation_ref": "index-1", "manifest_digest": "a" * 64,
            "display_name": "Safe.pdf", "media_type": "application/pdf",
            "modalities": ["text"], "tags": [], "language": None,
            "created_at_label": None, "searchable_content": "safe", "version_label": None,
            "document_id": "internal-secret",
        })
    service, _store, _backend, catalog = _service()
    envelope = service.invoke(
        execution_id="execution-1", grant_ref="grant-1", catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=ListKnowledgeDocumentsV1(
            action="list_knowledge_documents", cursor=None, page_size=1,
            max_output_tokens=16000,
        ),
    )
    with pytest.raises(ValidationError):
        RetrievalInvocationEnvelopeV1.model_validate({
            **envelope.model_dump(mode="json"), "authorization_revision": 7,
        })


def test_tool_output_cap_is_enforced_before_handles_are_persisted() -> None:
    service, store, backend, catalog = _service()
    document_handle = _first_document_handle(store)
    backend.search = lambda **_kwargs: [
        BackendEvidence(
            "evidence-ref-large",
            "identity-large",
            document_handle,
            "Page 2",
            "測" * 2_000,
            "content",
            ("text",),
            2,
        )
    ]
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[document_handle],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )
    assert result.observation.result_type == "knowledge_tool_error"
    assert result.observation.error_code == "budget_exhausted"
    assert result.tool_tokens <= 256
    assert all(item.handle_kind == "document" for item in store.handles.values())


def test_tool_output_above_legacy_four_thousand_uses_configured_request_max() -> None:
    service, store, backend, catalog = _service()

    def broad_search(**_kwargs):
        document_handle = _first_document_handle(store)
        return [
            BackendEvidence(
                "evidence-ref-broad",
                "identity-broad",
                document_handle,
                "Page 2",
                "測" * 4_096,
                "Broad exact content",
                ("text",),
                2,
            )
        ]

    backend.search = broad_search
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="broad",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=16_000,
        ),
        max_output_tokens=16_000,
        tokenizer_profile="cl100k_base",
    )

    assert result.observation.result_type == "knowledge_search_result"
    expected_tokens = len(
        tiktoken.get_encoding("cl100k_base").encode(
            json.dumps(
                result.observation.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
    )
    assert result.tool_tokens == expected_tokens
    assert result.tool_tokens > 4_000
    assert result.tool_tokens <= 16_000


def test_known_backend_timeout_is_persisted_as_typed_tool_result() -> None:
    service, store, backend, catalog = _service()

    def unavailable(**_kwargs):
        raise TimeoutError("backend timed out")

    backend.search = unavailable
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )
    assert result.observation.result_type == "knowledge_tool_error"
    assert result.observation.error_code == "tool_failed"
    assert result.observation.message_code == "retrieval_backend_unavailable"
    assert next(iter(store.results.values())).error_code == "tool_failed"


class _PostgresStatementTimeout(Exception):
    sqlstate = "57014"


def _postgres_statement_timeout() -> OperationalError:
    return OperationalError("SELECT", {}, _PostgresStatementTimeout())


def test_backend_postgres_statement_timeout_is_typed_tool_timeout() -> None:
    service, store, backend, catalog = _service()

    def timed_out(**_kwargs):
        raise _postgres_statement_timeout()

    backend.search = timed_out
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert result.observation.message_code == "retrieval_tool_timeout"
    persisted = next(iter(store.results.values())).observation
    assert persisted["message_code"] == (
        "retrieval_tool_timeout"
    )


def test_discovery_postgres_statement_timeout_is_not_degraded() -> None:
    service, store, backend, catalog = _service()

    def timed_out(**_kwargs):
        raise _postgres_statement_timeout()

    backend.discover_lexical = timed_out
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        action=DiscoverRelevantDocumentsV1(
            action="discover_relevant_documents",
            query_text="natural language policy question",
            limit=3,
        ),
    )

    assert result.observation.result_type == "knowledge_tool_error"
    assert result.observation.message_code == "retrieval_tool_timeout"
    persisted = next(iter(store.results.values())).observation
    assert persisted["provider_observation"]["message_code"] == (
        "retrieval_tool_timeout"
    )


def test_tool_deadline_rejects_late_normal_result_and_persists_retryable_error() -> None:
    service, store, backend, catalog = _service()
    current = [datetime(2026, 8, 6, tzinfo=timezone.utc)]
    deadline = current[0] + timedelta(seconds=7)
    service._clock = lambda: current[0]
    original_search = backend.search
    handles_before = dict(store.handles)

    def late_search(**kwargs):
        result = original_search(**kwargs)
        current[0] = deadline
        return result

    backend.search = late_search
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=deadline,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert result.observation.result_type == "knowledge_tool_error"
    assert result.observation.error_code == "tool_failed"
    assert result.observation.message_code == "retrieval_tool_timeout"
    assert result.observation.retryable is True
    persisted = next(iter(store.results.values()))
    assert persisted.result_type == "knowledge_tool_error"
    assert persisted.observation["message_code"] == "retrieval_tool_timeout"
    assert store.handles == handles_before


def test_tool_deadline_allows_result_completed_before_deadline() -> None:
    service, store, _backend, catalog = _service()
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    service._clock = lambda: now
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=now + timedelta(seconds=9),
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert result.observation.result_type == "knowledge_search_result"
    assert next(iter(store.results.values())).result_type == "knowledge_search_result"


def test_elapsed_tool_deadline_at_service_entry_persists_retryable_error() -> None:
    service, store, backend, catalog = _service()
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    service._clock = lambda: now

    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=now,
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert backend.operations == []
    assert result.observation.message_code == "retrieval_tool_timeout"
    assert result.observation.retryable is True
    assert next(iter(store.results.values())).error_code == "tool_failed"


def test_persistence_deadline_rolls_normal_result_into_persisted_timeout() -> None:
    service, store, _backend, catalog = _service()
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    service._clock = lambda: now
    original_persist = store.persist_invocation_result
    handles_before = dict(store.handles)

    def deadline_fenced_persist(command, *, deadline_at=None):
        if deadline_at is not None:
            raise TimeoutError("result transaction crossed tool deadline")
        return original_persist(command)

    store.persist_invocation_result = deadline_fenced_persist
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=now + timedelta(seconds=5),
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert result.observation.message_code == "retrieval_tool_timeout"
    assert next(iter(store.results.values())).result_type == "knowledge_tool_error"
    assert store.handles == handles_before


def test_catalog_deadline_is_converted_to_persisted_timeout() -> None:
    service, store, _backend, catalog = _service()
    original_get_catalog = store.get_catalog

    def deadline_fenced_catalog(*, execution_id, catalog_ref, deadline_at=None):
        if deadline_at is not None:
            raise TimeoutError("catalog read crossed tool deadline")
        return original_get_catalog(execution_id=execution_id, catalog_ref=catalog_ref)

    store.get_catalog = deadline_fenced_catalog
    result = service.invoke(
        execution_id="execution-1",
        grant_ref="grant-1",
        catalog_ref=catalog.catalog_ref,
        invocation_ordinal=1,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        action=SearchKnowledgeV1(
            action="search_knowledge",
            query_text="alpha",
            document_handles=[_first_document_handle(store)],
            required_modalities=[],
            facet_hints=FacetHintsV1(
                document_types=[], date_from=None, date_to=None, languages=[], tags=[]
            ),
            limit=1,
            max_output_tokens=256,
        ),
    )

    assert result.observation.message_code == "retrieval_tool_timeout"
    assert next(iter(store.results.values())).error_code == "tool_failed"


def test_catalog_connection_failure_before_deadline_remains_fail_closed() -> None:
    service, store, _backend, catalog = _service()

    def unavailable_catalog(**_kwargs):
        raise ConnectionError("catalog store unavailable")

    store.get_catalog = unavailable_catalog
    with pytest.raises(ConnectionError, match="catalog store unavailable"):
        service.invoke(
            execution_id="execution-1",
            grant_ref="grant-1",
            catalog_ref=catalog.catalog_ref,
            invocation_ordinal=1,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
            action=SearchKnowledgeV1(
                action="search_knowledge",
                query_text="alpha",
                document_handles=[_first_document_handle(store)],
                required_modalities=[],
                facet_hints=FacetHintsV1(
                    document_types=[], date_from=None, date_to=None, languages=[], tags=[]
                ),
                limit=1,
                max_output_tokens=256,
            ),
        )

    assert store.results == {}


def test_retrieval_adapter_uses_authorization_public_contract_only() -> None:
    adapter_path = (
        Path(__file__).parents[1]
        / "src/atlas_production/infrastructure/postgres_retrieval_v1_adapter.py"
    )
    imports = {
        node.module
        for node in ast.walk(ast.parse(adapter_path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "atlas_production.modules.authorization.public" in imports
    assert "atlas_production.infrastructure.postgres_owner.authorization" not in imports
