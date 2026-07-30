from __future__ import annotations

from types import SimpleNamespace

from atlas_production.routes.knowledge_library import (
    _has_active_knowledge_generation,
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
