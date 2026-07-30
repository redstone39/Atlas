from types import SimpleNamespace

from atlas_production.async_runtime.vector_index import (
    COLLECTION_NAME,
    VectorIndex,
    _point_payload,
)
from atlas_production.async_runtime.workflows import (
    _PostgresProcessingTaskRuntime,
)


def test_qdrant_payload_and_search_filter_use_exact_revision_index_pairs() -> None:
    payload = _point_payload(
        "index-a",
        {
            "chunk_id": "chunk-a",
            "document_id": "document-a",
            "processing_revision_id": "revision-a",
            "content_fingerprint": "a" * 64,
            "segment_id": "segment-a",
            "locator": {},
        },
    )
    assert payload["processing_revision_id"] == "revision-a"
    assert payload["index_generation_id"] == "index-a"

    class _Embedding:
        def query_embed(self, _values):
            yield SimpleNamespace(tolist=lambda: [0.1, 0.2])

    class _Client:
        def __init__(self):
            self.query_filter = None

        def collection_exists(self, name):
            return name == COLLECTION_NAME

        def query_points(self, **kwargs):
            self.query_filter = kwargs["query_filter"]
            return SimpleNamespace(points=[])

    index = VectorIndex.__new__(VectorIndex)
    index.client = _Client()
    index._ensure_embedding = lambda: _Embedding()  # type: ignore[method-assign]

    assert index.search(
        "policy",
        limit=10,
        revision_index_pairs={
            ("revision-a", "index-a"),
            ("revision-b", "index-b"),
        },
    ) == []

    dumped = index.client.query_filter.model_dump(exclude_none=True)
    assert "must" not in dumped
    pairs = {
        tuple(
            condition["match"]["value"]
            for condition in branch["must"]
        )
        for branch in dumped["should"]
    }
    assert pairs == {
        ("revision-a", "index-a"),
        ("revision-b", "index-b"),
    }
    assert ("revision-a", "index-b") not in pairs
    assert ("revision-b", "index-a") not in pairs


def test_verify_generation_rejects_single_but_wrong_processing_revision() -> None:
    class _Client:
        def collection_exists(self, name):
            return name == COLLECTION_NAME

        def count(self, **_kwargs):
            return SimpleNamespace(count=1)

        def retrieve(self, **_kwargs):
            return [
                SimpleNamespace(
                    id="point-a",
                    payload={
                        "processing_revision_id": "revision-wrong",
                        "index_generation_id": "index-a",
                        "chunk_id": "chunk-a",
                    },
                )
            ]

    index = VectorIndex.__new__(VectorIndex)
    index.client = _Client()

    assert not index.verify_generation(
        collection_name=COLLECTION_NAME,
        index_generation_id="index-a",
        processing_revision_id="revision-expected",
        expected_points={"point-a": "chunk-a"},
    )


def test_qdrant_search_hits_exclude_foreign_or_stale_payloads() -> None:
    class _Embedding:
        def query_embed(self, _values):
            yield SimpleNamespace(tolist=lambda: [0.1, 0.2])

    class _Client:
        def collection_exists(self, name):
            return name == COLLECTION_NAME

        def query_points(self, **_kwargs):
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        payload={
                            "chunk_id": "chunk-exact",
                            "processing_revision_id": "revision-a",
                            "index_generation_id": "index-a",
                        }
                    ),
                    SimpleNamespace(
                        payload={
                            "chunk_id": "chunk-stale",
                            "processing_revision_id": "revision-old",
                            "index_generation_id": "index-a",
                        }
                    ),
                    SimpleNamespace(
                        payload={
                            "chunk_id": "chunk-foreign",
                            "processing_revision_id": "revision-a",
                            "index_generation_id": "index-foreign",
                        }
                    ),
                ]
            )

    index = VectorIndex.__new__(VectorIndex)
    index.client = _Client()
    index._ensure_embedding = lambda: _Embedding()  # type: ignore[method-assign]
    hits = index.search_hits(
        "policy",
        limit=20,
        revision_index_pairs={("revision-a", "index-a")},
    )
    assert [hit.chunk_id for hit in hits] == ["chunk-exact"]


def test_existing_vector_search_treats_missing_collection_as_empty() -> None:
    class _Client:
        def collection_exists(self, _name):
            return False

    index = VectorIndex.__new__(VectorIndex)
    index.client = _Client()
    assert index.search(
        "policy",
        limit=20,
        revision_index_pairs={("revision-a", "index-a")},
    ) == []


def test_finalize_generation_passes_manifest_revision_authority_to_verifier() -> None:
    class _Repository:
        def load_publication_manifest(self, _job_id, *, expected_attempt):
            assert expected_attempt == 3
            return SimpleNamespace(
                qdrant_collection=COLLECTION_NAME,
                index_generation_id="index-a",
                processing_revision_id="revision-authority",
                points=(SimpleNamespace(point_id="point-a", chunk_id="chunk-a"),),
                manifest_digest="a" * 64,
            )

        def publish_job(
            self,
            _job_id,
            *,
            expected_attempt,
            verified_manifest_digest,
        ):
            assert expected_attempt == 3
            assert verified_manifest_digest == "a" * 64
            return True

    class _Indexing:
        def verify_generation(self, **kwargs):
            assert kwargs == {
                "collection_name": COLLECTION_NAME,
                "index_generation_id": "index-a",
                "processing_revision_id": "revision-authority",
                "expected_points": {"point-a": "chunk-a"},
            }
            return True

    runtime = _PostgresProcessingTaskRuntime(
        repository=_Repository(),  # type: ignore[arg-type]
        indexing=_Indexing(),  # type: ignore[arg-type]
    )

    assert runtime.finalize_generation("job-a", attempt=3) == "published"
