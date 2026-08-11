from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas_production.routes.knowledge_library import (
    _has_active_knowledge_generation,
    _knowledge_document_summary,
)


def _document(**overrides):
    values = {
        "lifecycle_status": "active",
        "intake_status": "ready",
        "active_processing_generation": 1,
        "active_index_generation_id": "index-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_refresh_processing_keeps_the_active_generation_discoverable() -> None:
    assert _has_active_knowledge_generation(
        _document(intake_status="processing")
    )
    assert _has_active_knowledge_generation(
        _document(intake_status="failed")
    )


def test_unpublished_or_disabled_documents_are_not_discoverable() -> None:
    assert not _has_active_knowledge_generation(
        _document(active_processing_generation=0)
    )
    assert not _has_active_knowledge_generation(
        _document(active_index_generation_id=None)
    )
    assert not _has_active_knowledge_generation(
        _document(lifecycle_status="disabled")
    )



@pytest.mark.parametrize("download_available", (True, False))
def test_knowledge_summary_consumes_projected_download_capability(
    download_available: bool,
) -> None:
    document = _document(
        document_id="document-1",
        title="Manual",
        description="Scope-owned manual",
        document_format="pdf",
        source_filename="manual.pdf",
        source_byte_size=128,
        uploaded_at="2026-07-18T00:00:00+00:00",
    )
    item = SimpleNamespace(
        scope_labels=(("project", "project-1", "Project One"),),
        download_available=download_available,
    )
    tag = SimpleNamespace(tag_type="project", tag_id="project-1")

    summary = _knowledge_document_summary(item, document, [tag])

    assert summary.download_available is download_available