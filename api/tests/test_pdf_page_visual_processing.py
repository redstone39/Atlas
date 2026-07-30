from __future__ import annotations

import hashlib
import inspect
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from atlas_production.async_runtime import workflows
from atlas_production.async_runtime.workflows import _pdf_page_image_artifact
from atlas_production.infrastructure.office_renderer_adapter import PdfPageRasterResult


def raster(content: bytes | None = None) -> PdfPageRasterResult:
    if content is None:
        output = BytesIO()
        Image.new("RGB", (12, 16), (20, 40, 60)).save(output, format="PNG")
        content = output.getvalue()
    return PdfPageRasterResult(
        renderer_version="pypdfium2-pdf-page-raster-v2",
        renderer_config_digest="1" * 64,
        source_sha256="2" * 64,
        source_byte_length=123,
        render_dpi=144,
        normalized_bbox=(0, 0, 10_000, 10_000),
        width=12,
        height=16,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_length=len(content),
        content_type="image/png",
        content=content,
    )


def test_prepare_uses_only_the_accepted_processing_profile_pin() -> None:
    source = inspect.getsource(workflows.prepare)
    generation_branch = source[source.index("if claimed_job.processing_generation") :]

    assert "repository.get_processing_profile_pin(" in generation_branch
    assert "_processing_profile(" in generation_branch
    assert "visual_route_snapshot" not in generation_branch


def test_pdf_page_image_is_rendered_from_exact_single_page_pdf_and_persisted(
    monkeypatch,
) -> None:
    source = b"sanitized-page-seven"
    rendered = raster()
    raster_calls: list[bytes] = []
    writes: list[dict] = []

    monkeypatch.setattr(
        "atlas_production.async_runtime.workflows.OfficeRendererAdapter.raster_pdf_page",
        lambda _self, content: raster_calls.append(content) or rendered,
    )
    monkeypatch.setattr(
        workflows,
        "_artifact_scope",
        lambda _document: ("project", "project-1", ()),
    )

    class Writer:
        def __init__(self, _engine):
            pass

        def write(self, **kwargs):
            writes.append(kwargs)
            return SimpleNamespace(
                artifact=SimpleNamespace(artifact_id="artifact-page-image-7")
            )

    monkeypatch.setattr(workflows, "BoundedArtifactWriter", Writer)
    monkeypatch.setattr(workflows, "worker_engine", lambda: object())
    document = SimpleNamespace(
        document_format="pdf",
        resource_lifecycle_epoch=2,
    )
    job = SimpleNamespace(
        document_version_id="version-1",
        processing_generation=3,
        document_id="document-1",
    )
    prepared_page = {
        "storage_artifact_id": "artifact-single-page-pdf-7",
        "source_crop_box": [10.0, 20.0, 610.0, 820.0],
        "source_rotation": 0,
    }

    result = _pdf_page_image_artifact(
        content=source,
        page_number=7,
        prepared_page=prepared_page,
        document=document,
        job=job,
        batch_id="job-1:page:7",
        processing_fence=SimpleNamespace(),
    )

    assert raster_calls == [source]
    assert writes[0]["content"] == rendered.content
    assert writes[0]["artifact_class"] == "page_image"
    assert writes[0]["source_artifact_id"] == "artifact-single-page-pdf-7"
    assert result["artifact_kind"] == "page_image"
    assert result["artifact_digest"] == rendered.sha256
    assert result["normalized_bbox"] == [0, 0, 10_000, 10_000]
    assert result["storage_artifact_id"] == "artifact-page-image-7"


def test_processing_batch_has_no_ingestion_time_model_visual_call() -> None:
    source = inspect.getsource(workflows._process_claimed_batch)

    assert "_routed_visual_payload" not in source
    assert "worker_model_routing" not in source
    assert "visual_route_snapshot" not in source
