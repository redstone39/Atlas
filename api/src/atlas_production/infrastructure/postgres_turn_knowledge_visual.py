"""Private visual page renderer for production turn knowledge."""

from __future__ import annotations

from datetime import datetime
import hashlib

from sqlalchemy import select

from atlas_production.infrastructure.office_renderer_adapter import OfficeRendererAdapter
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import BackendVisualImage
from atlas_production.modules.artifact_storage.ports import ArtifactFilesystemPort

from atlas_production.infrastructure.postgres_turn_knowledge_contracts import (
    CurrentDocumentResource,
    SessionFactory,
    _apply_statement_deadline,
    _remaining_seconds,
)


class PostgresVisualPageRenderer:
    """Read one pinned single-page PDF and render the requested root bbox."""

    def __init__(
        self,
        session_factory: SessionFactory,
        filesystem: ArtifactFilesystemPort,
    ) -> None:
        self._session_factory = session_factory
        self._filesystem = filesystem

    def render(
        self,
        *,
        document: CurrentDocumentResource,
        page_number: int,
        normalized_bbox: tuple[int, int, int, int],
        deadline_at: datetime | None = None,
    ) -> BackendVisualImage:
        prefix = "processing-generation-"
        if (
            document.media_type != "application/pdf"
            or page_number < 1
            or not document.processing_generation_ref.startswith(prefix)
        ):
            raise OSError("visual_page_unavailable")
        try:
            generation = int(document.processing_generation_ref.removeprefix(prefix))
        except ValueError:
            raise OSError("visual_page_unavailable") from None
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            page = session.scalar(
                select(AtlasEvidencePageArtifactRow).where(
                    AtlasEvidencePageArtifactRow.processing_revision_id
                    == document.processing_revision_ref,
                    AtlasEvidencePageArtifactRow.source_page_index == page_number - 1,
                )
            )
            payload = dict(page.payload) if page is not None else {}
            artifact = (
                session.get(AtlasArtifactRow, payload.get("storage_artifact_id"))
                if payload.get("artifact_kind") == "pdf_single_page"
                else None
            )
            blob = (
                session.get(AtlasStorageBlobRow, artifact.blob_id)
                if artifact is not None
                else None
            )
            if (
                page is None
                or artifact is None
                or blob is None
                or artifact.artifact_class != "document_page_pdf"
                or artifact.content_type != "application/pdf"
                or artifact.lifecycle_status != "active"
                or artifact.document_version_id != page.document_version_id
                or artifact.processing_generation != generation
                or artifact.page_number != page_number
                or artifact.checksum_value != payload.get("artifact_digest")
                or artifact.byte_size != payload.get("content_length")
                or blob.status != "committed"
                or blob.checksum_value != artifact.checksum_value
                or blob.byte_size != artifact.byte_size
            ):
                raise OSError("visual_page_unavailable")
            opaque_ref = blob.opaque_ref
            expected_size = blob.byte_size
            expected_digest = blob.checksum_value
        with self._filesystem.open_read(
            opaque_ref, expected_size=expected_size
        ) as stream:
            content = stream.read(expected_size + 1)
        _remaining_seconds(deadline_at)
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_digest
        ):
            raise OSError("visual_page_integrity_failed")
        rendered = OfficeRendererAdapter().raster_pdf_page(
            content,
            normalized_bbox=normalized_bbox,
            timeout_seconds=_remaining_seconds(deadline_at),
        )
        _remaining_seconds(deadline_at)
        return BackendVisualImage(
            content=rendered.content,
            digest=rendered.sha256,
            width=rendered.width,
            height=rendered.height,
        )
