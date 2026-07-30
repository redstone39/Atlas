from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    CatalogDocumentInput,
    CreateCatalogInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    PostgresCanonicalRetrievalLineage,
)
from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    PostgresProductionKnowledgeRowSource,
    canonical_document_resource_ref,
)


class _Rows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _PinSession:
    def __init__(self) -> None:
        document_a = SimpleNamespace(
            document_id="document-a",
            lifecycle_status="active",
            processing_identity_id="identity-shared",
            raw_sha256="a" * 64,
            original_artifact_id="binding-artifact-a",
        )
        document_b = SimpleNamespace(
            document_id="document-b",
            lifecycle_status="active",
            processing_identity_id="identity-shared",
            raw_sha256="a" * 64,
            original_artifact_id="binding-artifact-b",
        )
        version_a = SimpleNamespace(
            document_id="document-a",
            document_version_id="version-a",
            payload={
                "status": "active",
                "source_digest": "a" * 64,
                "original_artifact_id": "binding-artifact-a",
            },
        )
        version_b = SimpleNamespace(
            document_id="document-b",
            document_version_id="version-b",
            payload={
                "status": "staged",
                "source_digest": "a" * 64,
                "original_artifact_id": "binding-artifact-b",
            },
        )
        identity = SimpleNamespace(
            processing_identity_id="identity-shared",
            source_sha256="a" * 64,
            source_artifact_id="canonical-artifact",
            source_artifact_checksum_sha256="a" * 64,
            current_revision_id="revision-old-ready",
        )
        revision = SimpleNamespace(
            processing_revision_id="revision-old-ready",
            processing_identity_id="identity-shared",
            state="ready",
            manifest_digest="b" * 64,
        )
        index = SimpleNamespace(
            index_generation_id="index-old-ready",
            processing_revision_id="revision-old-ready",
            source_processing_generation=1,
            status="active",
            manifest_digest="b" * 64,
        )
        self._batches = iter(
            (
                [document_a, document_b],
                [version_a, version_b],
                [identity],
                [revision],
                [index],
            )
        )
        self.scalar_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalars(self, _statement):
        self.scalar_calls += 1
        return _Rows(next(self._batches))


def test_two_authorized_bindings_share_one_current_ready_read_and_keep_old_current() -> None:
    session = _PinSession()
    source = PostgresProductionKnowledgeRowSource(lambda: session)

    pins = source.current_ready_pins({"document-a", "document-b"})

    assert session.scalar_calls == 5
    assert [pin.document_binding_id for pin in pins] == [
        "document-a",
        "document-b",
    ]
    assert {pin.processing_identity_id for pin in pins} == {"identity-shared"}
    assert {pin.processing_revision_id for pin in pins} == {
        "revision-old-ready"
    }
    assert {pin.index_generation_id for pin in pins} == {"index-old-ready"}
    assert {pin.document_version_ref for pin in pins} == {
        "version-a",
        "version-b",
    }


class _CatalogPinSession:
    def __init__(self) -> None:
        self._rows = {
            (AtlasDocumentVersionRow, "version-b"): SimpleNamespace(
                document_id="document-b"
            ),
            (AtlasDocumentRow, "document-b"): SimpleNamespace(
                document_id="document-b",
                processing_identity_id="identity-shared",
                resource_lifecycle_epoch=0,
            ),
            (AtlasIndexGenerationRow, "index-shared"): SimpleNamespace(
                processing_revision_id="revision-shared",
                manifest_digest="b" * 64,
                source_processing_generation=1,
            ),
            (AtlasProcessingRevisionRow, "revision-shared"): SimpleNamespace(
                processing_revision_id="revision-shared",
                processing_identity_id="identity-shared",
                manifest_digest="b" * 64,
                state="ready",
            ),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, row_type, key):
        return self._rows.get((row_type, key))

    def scalar(self, _statement):
        return "retention-shared"


@pytest.mark.parametrize(
    ("resource_ref", "lifecycle_epoch"),
    (
        (canonical_document_resource_ref("document-a"), 1),
        (canonical_document_resource_ref("document-b"), 2),
    ),
)
def test_catalog_rejects_binding_resource_or_lifecycle_mismatch(
    resource_ref: str,
    lifecycle_epoch: int,
) -> None:
    resolver = PostgresCanonicalRetrievalLineage(lambda: _CatalogPinSession())
    command = CreateCatalogInput(
        catalog_ref="catalog-shared",
        execution_id="execution-shared",
        grant_ref="grant-shared",
        generation_retention_ref="retention-shared",
        authorization_revision=1,
        retrieval_generation_ref="retrieval-shared",
        documents=(
            CatalogDocumentInput(
                document_handle="handle-shared",
                resource_ref=resource_ref,
                lifecycle_epoch=lifecycle_epoch,
                document_version_ref="version-b",
                generation_ref="index-shared",
                processing_generation_ref="processing-generation-1",
                processing_revision_ref=None,
                index_generation_ref="index-shared",
                manifest_digest="b" * 64,
                descriptor={},
            ),
        ),
        idempotency_key="catalog-shared",
    )

    with pytest.raises(
        RetrievalStoreConflict,
        match="catalog document revision pin is unavailable",
    ):
        resolver.canonicalize_catalog(command)
