from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendCatalogDocument,
)
from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    CurrentDiscoveryMatch,
    CurrentDocumentResource,
    CurrentEvidenceResource,
    CurrentResourceState,
    GrantAuthorityState,
    GrantResourceSnapshot,
    ProductionAuthorizedGrantResourceSource,
    ProductionCurrentResourceAuthorizationReader,
    ProductionKnowledgeRetrievalBackend,
    PostgresProductionKnowledgeRowSource,
    canonical_document_resource_ref,
)


DOCUMENT = CurrentDocumentResource(
    document_id="internal-document-17",
    resource_ref=canonical_document_resource_ref("internal-document-17"),
    lifecycle_epoch=4,
    document_version_ref="version-17",
    processing_identity_ref="identity-17",
    processing_revision_ref="revision-17",
    source_artifact_ref="artifact-17",
    source_artifact_checksum_sha256="f" * 64,
    processing_generation_ref="processing-generation-9",
    index_generation_ref="index-17",
    manifest_digest="a" * 64,
    display_name="Policy Alpha.pdf",
    media_type="application/pdf",
    searchable_content="retention alpha policy",
    uploaded_at="2026-07-20",
)
OTHER = replace(
    DOCUMENT,
    document_id="internal-document-99",
    resource_ref=canonical_document_resource_ref("internal-document-99"),
    document_version_ref="version-99",
    index_generation_ref="index-99",
    display_name="Unauthorized.pdf",
)
EVIDENCE_A = CurrentEvidenceResource(
    evidence_id="internal-evidence-a",
    evidence_ref="evidence-resource-a",
    document_id=DOCUMENT.document_id,
    document_version_ref=DOCUMENT.document_version_ref,
    processing_revision_ref=DOCUMENT.processing_revision_ref,
    processing_generation_ref=DOCUMENT.processing_generation_ref,
    index_generation_ref=DOCUMENT.index_generation_ref,
    manifest_digest=DOCUMENT.manifest_digest,
    locator_label="p. 2",
    snippet="alpha retention",
    content="Alpha retention is seven years.",
    modality="text",
    page_number=2,
    page_artifact_ref="page-artifact-2",
    content_fingerprint="b" * 64,
)
EVIDENCE_B = replace(
    EVIDENCE_A,
    evidence_id="internal-evidence-b",
    evidence_ref="evidence-resource-b",
    locator_label="p. 3",
    snippet="next page",
    content="Alpha appendix.",
    page_number=3,
    content_fingerprint="c" * 64,
)
EVIDENCE_VISUAL = replace(
    EVIDENCE_A,
    evidence_id="internal-evidence-visual",
    evidence_ref="evidence-resource-visual",
    locator_label="p. 4 figure",
    snippet="visual layout",
    content="Visual relationship.",
    modality="figure",
    page_number=4,
    content_fingerprint="d" * 64,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _NavigationSession:
    def __init__(self, *, revision_identity: str = DOCUMENT.processing_identity_ref):
        self._revision = SimpleNamespace(
            processing_identity_id=revision_identity,
            state="ready",
            manifest_digest=DOCUMENT.manifest_digest,
        )
        self._page_rows = [
            SimpleNamespace(
                source_page_index=0,
                payload={
                    "source_page_label": "第 1 頁",
                    "artifact_kind": "pdf_single_page",
                },
            )
        ]
        self._evidence_rows = [
            SimpleNamespace(
                evidence_id="legacy-evidence-1",
                locator_payload={
                    "page_number": 1,
                    "evidence_modality": "figure",
                },
                locator_label="Figure 1. Pin Assignments",
                content="RTL8111G pin assignments",
            )
        ]
        self._scalar_results = iter((self._page_rows, self._evidence_rows))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _row_type, _key):
        return self._revision

    def scalars(self, _statement):
        return _Scalars(next(self._scalar_results))

    def rollback(self):
        for row in (*self._page_rows, *self._evidence_rows):
            row.__dict__.clear()
        return None


def test_navigation_map_projects_existing_ready_generation_without_identity_spec() -> None:
    source = PostgresProductionKnowledgeRowSource(lambda: _NavigationSession())

    navigation_map = source.navigation_map(document=DOCUMENT)

    assert navigation_map is not None
    assert navigation_map.processing_revision_ref == DOCUMENT.processing_revision_ref
    assert [node.kind for node in navigation_map.nodes] == ["page", "figure"]


def test_navigation_map_rejects_revision_from_foreign_identity() -> None:
    source = PostgresProductionKnowledgeRowSource(
        lambda: _NavigationSession(revision_identity="identity-foreign")
    )

    assert source.navigation_map(document=DOCUMENT) is None


class FakeRows:
    current = True
    def current_scope(self, *, actor_id: str):
        assert actor_id == "actor-1"
        return frozenset({("team", "team-a"), ("project", "project-b")})


    def grant_authority(self, *, actor_id: str, conversation_id: str, deadline_at=None):
        return GrantAuthorityState(actor_id, conversation_id, True, "authority-snapshot", 7)

    def grant_resources(self, *, actor_id: str, conversation_id: str):
        return GrantResourceSnapshot(
            self.grant_authority(actor_id=actor_id, conversation_id=conversation_id),
            (DOCUMENT,),
        )

    def authorized_documents(self, *, actor_id: str):
        assert actor_id == "actor-1"
        return (DOCUMENT,)

    def authorized_resource_refs(self, *, actor_id: str):
        assert actor_id == "actor-1"
        return frozenset({DOCUMENT.resource_ref})

    def resources(self, *, resource_refs: tuple[str, ...]):
        values = {DOCUMENT.resource_ref: DOCUMENT, OTHER.resource_ref: OTHER}
        return tuple(values[ref] for ref in resource_refs if ref in values and self.current)

    def resource_authorizations(self, *, actor_id: str, resource_refs: tuple[str, ...], deadline_at=None):
        assert actor_id == "actor-1"
        values = {DOCUMENT.resource_ref: DOCUMENT, OTHER.resource_ref: OTHER}
        return tuple(
            CurrentResourceState(
                ref,
                ref == DOCUMENT.resource_ref,
                values.get(ref) if self.current else None,
            )
            for ref in dict.fromkeys(resource_refs)
        )

    def pinned_documents(self, *, pins, deadline_at=None):
        expected = (
            DOCUMENT.document_version_ref,
            DOCUMENT.processing_generation_ref,
            DOCUMENT.index_generation_ref,
            DOCUMENT.manifest_digest,
        )
        return (DOCUMENT,) if self.current and expected in pins else ()

    def evidence(self, *, documents, deadline_at=None):
        assert documents == (DOCUMENT,)
        return (EVIDENCE_B, EVIDENCE_A, EVIDENCE_VISUAL)

    def lexical_discovery(self, *, documents, query_text, limit, deadline_at=None):
        assert documents == (DOCUMENT,)
        assert query_text == "alpha retention"
        return (CurrentDiscoveryMatch("chunk-a", EVIDENCE_A),)[:limit]

    def vector_discovery(self, *, documents, chunk_ids, deadline_at=None):
        assert documents == (DOCUMENT,)
        return tuple(
            CurrentDiscoveryMatch(chunk_id, EVIDENCE_B)
            for chunk_id in chunk_ids
            if chunk_id == "chunk-b"
        )


def _backend_document() -> BackendCatalogDocument:
    return BackendCatalogDocument(
        document_handle="opaque-document-handle",
        lifecycle_epoch=DOCUMENT.lifecycle_epoch,
        document_version_ref=DOCUMENT.document_version_ref,
        processing_generation_ref=DOCUMENT.processing_generation_ref,
        processing_revision_ref=DOCUMENT.processing_revision_ref,
        index_generation_ref=DOCUMENT.index_generation_ref,
        manifest_digest=DOCUMENT.manifest_digest,
        descriptor={"display_name": DOCUMENT.display_name},
    )


def test_production_adapter_is_read_only_and_has_no_owner_repository_calls() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/atlas_production/infrastructure/postgres_turn_knowledge_production.py"
    ).read_text()
    assert "postgres_owner" not in source
    assert "with_for_update" not in source
    assert "pg_advisory" not in source
    assert "session.add(" not in source
    assert "session.execute(delete" not in source
    assert "internal-document-17" not in canonical_document_resource_ref(
        "internal-document-17"
    )


def test_current_reader_is_exact_fail_closed_and_does_not_expand_acl() -> None:
    rows = FakeRows()
    reader = ProductionCurrentResourceAuthorizationReader(rows)
    grant = reader.current_grant_authorization(actor_id="actor-1", conversation_id="conversation-1")
    assert grant.authorized and grant.authorization_revision == 7

    snapshots = reader.current_resource_authorizations(
        actor_id="actor-1",
        resource_refs=(DOCUMENT.resource_ref, OTHER.resource_ref, "unknown-resource"),
    )
    assert [snapshot.authorized for snapshot in snapshots] == [True, False, False]
    assert snapshots[0].lifecycle_epoch == DOCUMENT.lifecycle_epoch
    assert snapshots[0].version_ref == DOCUMENT.document_version_ref
    assert snapshots[0].processing_generation_ref == DOCUMENT.processing_generation_ref
    assert snapshots[0].index_generation_ref == DOCUMENT.index_generation_ref
    assert snapshots[1].active and not snapshots[1].authorized
    assert not snapshots[2].active


def test_authorized_grant_resource_source_uses_opaque_ref_and_exact_pins() -> None:
    resources = ProductionAuthorizedGrantResourceSource(FakeRows()).authorized_document_resources(
        actor_id="actor-1"
    )
    assert len(resources) == 1
    resource = resources[0]
    assert resource.resource_ref == DOCUMENT.resource_ref
    assert "internal-document-17" not in resource.model_dump_json()
    assert resource.document_version_ref == "version-17"
    assert resource.processing_generation_ref == "processing-generation-9"
    assert resource.index_generation_ref == "index-17"
    assert resource.modalities == ["text", "figure"]


def test_authorized_grant_source_delegates_current_scope() -> None:
    source = ProductionAuthorizedGrantResourceSource(FakeRows())

    assert source.current_scope(actor_id="actor-1") == frozenset(
        {("team", "team-a"), ("project", "project-b")}
    )


def test_selected_scope_empty_intersection_never_falls_back_to_all(
    monkeypatch,
) -> None:
    observed = []

    def _scope(_session, **facts):
        observed.append(facts["requested_scope"])
        return set()

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_turn_knowledge_production.read_effective_document_scope",
        _scope,
    )
    source = PostgresProductionKnowledgeRowSource(lambda: SimpleNamespace())

    assert source._authorized_documents_in_session(
        SimpleNamespace(),
        actor_id="actor-1",
        requested_scope={("team", "team-revoked")},
    ) == ()
    assert observed == [{("team", "team-revoked")}]


def test_backend_is_deterministic_exact_current_and_model_safe() -> None:
    rows = FakeRows()
    backend = ProductionKnowledgeRetrievalBackend(rows)
    document = _backend_document()
    first = backend.search(
        documents=(document,), query_text="alpha retention", required_modalities=("text",),
        facet_hints={}, limit=20,
    )
    second = backend.search(
        documents=(document,), query_text="alpha retention", required_modalities=("text",),
        facet_hints={}, limit=20,
    )
    assert first == second
    assert [item.evidence_ref for item in first] == ["evidence-resource-a", "evidence-resource-b"]
    assert all(item.document_handle == "opaque-document-handle" for item in first)
    assert "internal-document-17" not in repr(first)
    assert "internal-evidence-a" not in repr(first)

    inspected = backend.inspect(
        documents=(document,), evidence_refs=("evidence-resource-a", "not-authorized")
    )
    assert [item.evidence_ref for item in inspected] == ["evidence-resource-a"]
    expanded = backend.expand(
        documents=(document,), anchor_evidence_refs=("evidence-resource-a",),
        direction="next_page", limit=20,
    )
    assert [item.evidence_ref for item in expanded] == ["evidence-resource-b"]

    rows.current = False
    assert backend.search(
        documents=(document,), query_text="alpha", required_modalities=(), facet_hints={}, limit=20
    ) == ()


def test_backend_discovery_uses_exact_pins_and_vector_pairs() -> None:
    class FakeVector:
        def __init__(self):
            self.pairs = None

        def search_hits(
            self, query_text, *, limit, revision_index_pairs, timeout_seconds=None
        ):
            from atlas_production.async_runtime.vector_index import VectorSearchHit

            assert query_text == "alpha retention"
            assert limit == 20
            self.pairs = revision_index_pairs
            return [
                VectorSearchHit(
                    chunk_id="chunk-b",
                    processing_revision_id=DOCUMENT.processing_revision_ref,
                    index_generation_id=DOCUMENT.index_generation_ref,
                )
            ]

    rows = FakeRows()
    vector = FakeVector()
    backend = ProductionKnowledgeRetrievalBackend(
        rows,
        vector_index=vector,  # type: ignore[arg-type]
    )
    document = _backend_document()
    lexical = backend.discover_lexical(
        documents=(document,),
        query_text="alpha retention",
        limit=20,
    )
    vector_hits = backend.discover_vector(
        documents=(document,),
        query_text="alpha retention",
        limit=20,
    )
    assert [item.match_ref for item in lexical] == ["evidence-resource-a"]
    assert [item.match_ref for item in vector_hits] == ["evidence-resource-b"]
    assert vector.pairs == {
        (DOCUMENT.processing_revision_ref, DOCUMENT.index_generation_ref)
    }
    assert all(
        item.document_handle == document.document_handle
        for item in (*lexical, *vector_hits)
    )

    rows.current = False
    assert backend.discover_lexical(
        documents=(document,),
        query_text="alpha retention",
        limit=20,
    ) == ()
