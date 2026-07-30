from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import logging
from uuid import uuid4
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Mapping
import unicodedata

from huggingface_hub import snapshot_download
from sqlalchemy import select
from sqlalchemy.orm import Session
from tokenizers import Tokenizer
from atlas_processing_sdk.contracts import validate_preview_region

from atlas_production.infrastructure.artifact_storage_config_adapter import RootOnlyStorageTargetConfig
from atlas_production.infrastructure.artifact_storage_filesystem_adapter import LocalArtifactFilesystemAdapter
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
    PostgresProcessingExecutionAdapter,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    DocumentProcessingCurrentnessConflict,
)
from atlas_production.infrastructure.postgres_model_routing_adapter import (
    PostgresModelRoutingAdapter,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasArtifactScopeBindingRow,
    AtlasArtifactStorageTargetRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentRow
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasPluginPackageRow,
    AtlasPluginVersionRow,
    AtlasProcessingProfileRevisionRow,
)
from atlas_production.infrastructure.processing_plugin_artifact_adapter import (
    LocalProcessingPluginArtifactStore,
)
from atlas_production.infrastructure.processing_runner_adapter import (
    ProcessingRunnerError,
    default_processing_runner,
)
from atlas_production.infrastructure.bounded_artifact_writer import BoundedArtifactWriter
from atlas_production.infrastructure.bounded_artifact_writer import (
    DocumentPreparationArtifactFence,
)
from atlas_production.infrastructure.bounded_artifact_writer import ProcessingArtifactFence
from atlas_production.async_runtime.vector_index import MODEL_NAME, MODEL_REVISION, VectorIndex
from atlas_production.infrastructure.office_renderer_adapter import (
    OfficeRendererAdapter,
    OfficeRendererError,
    OfficeRenderResult,
    PdfPageRasterResult,
)
from atlas_production.infrastructure.pdf_preview_adapter import (
    PdfPreviewAdapter,
    PdfPreviewError,
)
from atlas_production.modules.document_intake.formats import (
    INLINE_PREVIEW_MIME_TYPES,
    LEGACY_OFFICE_MIME_TYPES,
    OFFICE_MIME_TYPES,
)
from atlas_production.modules.model_routing.service import ModelRoutingService
from atlas_production.providers import (
    default_provider_adapter_factory,
)
from atlas_production.shared.png import (
    validated_rgb_png_dimensions,
)
from atlas_production.worker_composition import (
    IndexingTaskPort,
    WorkerPortFactories,
    configure_worker_port_factories,
)


logger = logging.getLogger(__name__)

_DEFAULT_PROCESSING_PLUGIN_TIMEOUT_SECONDS = 60
_MAX_PROCESSING_PLUGIN_TIMEOUT_SECONDS = 600
_DOCLING_LAYOUT_TIMEOUT_ENV = "ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS"


def _read_docling_layout_timeout_seconds(
    environment: Mapping[str, str],
) -> int:
    raw_value = environment.get(_DOCLING_LAYOUT_TIMEOUT_ENV)
    if raw_value is None:
        return _DEFAULT_PROCESSING_PLUGIN_TIMEOUT_SECONDS
    try:
        timeout_seconds = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"{_DOCLING_LAYOUT_TIMEOUT_ENV} must be an integer from 1 to "
            f"{_MAX_PROCESSING_PLUGIN_TIMEOUT_SECONDS}"
        ) from exc
    if not 1 <= timeout_seconds <= _MAX_PROCESSING_PLUGIN_TIMEOUT_SECONDS:
        raise RuntimeError(
            f"{_DOCLING_LAYOUT_TIMEOUT_ENV} must be an integer from 1 to "
            f"{_MAX_PROCESSING_PLUGIN_TIMEOUT_SECONDS}"
        )
    return timeout_seconds


_DOCLING_LAYOUT_TIMEOUT_SECONDS = _read_docling_layout_timeout_seconds(
    os.environ
)


def _processor_invocation_limits(
    processor: Mapping[str, object],
    *,
    inherited_deadline: datetime,
    now: datetime | None = None,
) -> tuple[int, datetime]:
    if processor.get("plugin_id") != "atlas-docling-layout":
        return _DEFAULT_PROCESSING_PLUGIN_TIMEOUT_SECONDS, inherited_deadline
    timeout_seconds = _DOCLING_LAYOUT_TIMEOUT_SECONDS
    started_at = now or datetime.now(timezone.utc)
    return timeout_seconds, started_at + timedelta(seconds=timeout_seconds)


def _processing_runner_failure_is_transient(
    error: ProcessingRunnerError,
) -> bool:
    return error.safe_code in {
        "plugin_cancelled",
        "plugin_crashed",
        "plugin_interrupted",
        "plugin_runner_unavailable",
        "plugin_timeout",
    } or (
        error.safe_code == "plugin_execution_failed"
        and error.safe_type == "KeyboardInterrupt"
    )


_worker_runtime: PostgresRuntime | None = None


def configure_postgres_worker_runtime(
    runtime: PostgresRuntime | None = None,
) -> PostgresRuntime:
    """Bind lazy role factories to PostgreSQL without constructing an API app."""

    global _worker_runtime
    if runtime is None and _worker_runtime is not None:
        return _worker_runtime
    selected = runtime or PostgresRuntime.from_environment()
    selected.bootstrap_schema()
    _worker_runtime = selected

    def job():
        return PostgresDocumentProcessingAdapter(selected.session_factory)

    def indexing():
        return _LazyIndexingTaskRuntime(
            lambda: _PostgresIndexingTaskRuntime(job(), VectorIndex())
        )

    configure_worker_port_factories(
        WorkerPortFactories(
            job=job,
            artifact=lambda: BoundedArtifactWriter(selected.engine),
            processing=lambda: _PostgresProcessingTaskRuntime(job(), indexing()),
            execution=lambda: PostgresProcessingExecutionAdapter(
                selected.session_factory
            ),
            model_routing=lambda: ModelRoutingService(
                PostgresModelRoutingAdapter(
                    selected.session_factory,
                    default_provider_adapter_factory,
                )
            ),
            indexing=indexing,
        )
    )
    return selected


def worker_runtime() -> PostgresRuntime:
    return configure_postgres_worker_runtime()


def worker_engine():
    """Compatibility seam for bounded SQL/byte helpers; never creates Store."""

    return worker_runtime().engine


def worker_repository() -> PostgresDocumentProcessingAdapter:
    return PostgresDocumentProcessingAdapter(worker_runtime().session_factory)


@dataclass(frozen=True, slots=True)
class _PostgresIndexingTaskRuntime:
    repository: PostgresDocumentProcessingAdapter
    index: VectorIndex

    def index_batch(self, job_id: str, batch_id: str, *, attempt: int) -> bool:
        with self.repository.index_batch_execution(
            job_id, batch_id, expected_attempt=attempt
        ) as claimed_job:
            if claimed_job is None:
                return False
            try:
                job, chunks = self.repository.chunks_for_batch(job_id, batch_id)
                if job.status in {"succeeded", "failed", "cancelled"}:
                    return True
                if not self.repository.set_embedding_profile(
                    job_id,
                    job.index_generation_id,
                    self.index.profile,
                    expected_attempt=attempt,
                ):
                    return False
                mappings = (
                    self.index.upsert(job.index_generation_id, chunks)
                    if chunks
                    else []
                )
                return self.repository.mark_batch_indexed(
                    job_id=job_id,
                    batch_id=batch_id,
                    mappings=mappings,
                    expected_attempt=attempt,
                )
            except DocumentProcessingCurrentnessConflict:
                return False

    def reindex_generation(
        self,
        job_id: str,
        batch_id: str,
        *,
        attempt: int,
    ) -> bool:
        existing = self.repository.get_job(job_id)
        if existing is None or existing.status in {"succeeded", "failed", "cancelled"}:
            return True
        if not self.repository.stage_reindex_batch(
            job_id,
            batch_id,
            expected_attempt=attempt,
        ):
            return False
        return self.index_batch(job_id, batch_id, attempt=attempt)

    def cleanup_old_index(self, *, limit: int = 100) -> None:
        retired = self.repository.retired_vector_points(limit=limit)
        for point_ids in retired.values():
            self.index.delete_points(point_ids)
        self.repository.delete_retired_vector_points(retired)
        self.repository.cleanup_retired_generations(limit=min(limit, 10))

    def verify_generation(
        self,
        *,
        collection_name: str,
        index_generation_id: str,
        processing_revision_id: str,
        expected_points: dict[str, str],
    ) -> bool:
        return self.index.verify_generation(
            collection_name=collection_name,
            index_generation_id=index_generation_id,
            processing_revision_id=processing_revision_id,
            expected_points=expected_points,
        )


class _LazyIndexingTaskRuntime:
    """Delay embedding/Qdrant setup until a task actually needs the index."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self._runtime: _PostgresIndexingTaskRuntime | None = None

    def _get(self) -> _PostgresIndexingTaskRuntime:
        if self._runtime is None:
            self._runtime = self._factory()
        return self._runtime

    def index_batch(self, job_id: str, batch_id: str, *, attempt: int) -> bool:
        return self._get().index_batch(job_id, batch_id, attempt=attempt)

    def reindex_generation(
        self,
        job_id: str,
        batch_id: str,
        *,
        attempt: int,
    ) -> bool:
        return self._get().reindex_generation(job_id, batch_id, attempt=attempt)

    def cleanup_old_index(self, *, limit: int = 100) -> None:
        self._get().cleanup_old_index(limit=limit)

    def verify_generation(
        self,
        *,
        collection_name: str,
        index_generation_id: str,
        processing_revision_id: str,
        expected_points: dict[str, str],
    ) -> bool:
        return self._get().verify_generation(
            collection_name=collection_name,
            index_generation_id=index_generation_id,
            processing_revision_id=processing_revision_id,
            expected_points=expected_points,
        )


@dataclass(frozen=True, slots=True)
class _PostgresProcessingTaskRuntime:
    repository: PostgresDocumentProcessingAdapter
    indexing: IndexingTaskPort

    def prepare(self, job_id: str, *, attempt: int) -> None:
        prepare(job_id, attempt)

    def process_batch(self, job_id: str, batch_id: str, *, attempt: int) -> bool:
        try:
            return process_batch(job_id, batch_id, attempt)
        except DocumentProcessingCurrentnessConflict:
            return False

    def finalize_generation(
        self,
        job_id: str,
        *,
        attempt: int,
    ) -> Literal[
        "published",
        "generation_manifest_not_ready",
        "qdrant_generation_manifest_mismatch",
        "generation_manifest_changed",
    ]:
        manifest = self.repository.load_publication_manifest(
            job_id,
            expected_attempt=attempt,
        )
        if manifest is None:
            return "generation_manifest_not_ready"
        if manifest.qdrant_collection is not None and not self.indexing.verify_generation(
            collection_name=manifest.qdrant_collection,
            index_generation_id=manifest.index_generation_id,
            processing_revision_id=manifest.processing_revision_id,
            expected_points={
                point.point_id: point.chunk_id for point in manifest.points
            },
        ):
            return "qdrant_generation_manifest_mismatch"
        if not self.repository.publish_job(
            job_id,
            expected_attempt=attempt,
            verified_manifest_digest=manifest.manifest_digest,
        ):
            return "generation_manifest_changed"
        return "published"


def _active_processing_profile(media_type: str) -> dict:
    with Session(worker_engine()) as session:
        profiles = session.scalars(select(AtlasProcessingProfileRevisionRow)).all()
    matches = [
        row.payload
        for row in profiles
        if row.payload.get("status") == "active"
        and media_type in row.payload.get("accepted_media_types", [])
    ]
    if len(matches) != 1:
        raise ValueError("processing_profile_unavailable")
    return matches[0]


def _processing_profile(profile_id: str, revision: int) -> dict:
    identity = json.dumps([profile_id, revision], separators=(",", ":"))
    with Session(worker_engine()) as session:
        row = session.get(AtlasProcessingProfileRevisionRow, identity)
    if row is None:
        raise ValueError("processing_profile_unavailable")
    return row.payload


def _plugin_identity(ref: dict) -> tuple[str, str, str]:
    return (
        ref["plugin_id"],
        ref["plugin_version"],
        ref["package_digest"],
    )


def _plugin_invocation_index(refs: list[dict]) -> dict[tuple[str, str, str], dict]:
    requested = {_plugin_identity(ref): ref for ref in refs}
    row_ids = [
        json.dumps([plugin_id, plugin_version], separators=(",", ":"))
        for plugin_id, plugin_version, _ in requested
    ]
    with Session(worker_engine()) as session:
        versions = session.scalars(
            select(AtlasPluginVersionRow).where(AtlasPluginVersionRow.id.in_(row_ids))
        ).all()
        version_payloads = {
            (row.payload.get("plugin_id"), row.payload.get("plugin_version")): row.payload
            for row in versions
        }
        needs_package = any(
            payload.get("trust_provenance") != "platform_builtin"
            for payload in version_payloads.values()
        )
        packages = (
            session.scalars(select(AtlasPluginPackageRow)).all()
            if needs_package
            else []
        )
    package_payloads = {
        (
            row.payload.get("plugin_id"),
            row.payload.get("plugin_version"),
            row.payload.get("package_digest"),
        ): row.payload
        for row in packages
    }
    result: dict[tuple[str, str, str], dict] = {}
    for identity, ref in requested.items():
        payload = version_payloads.get(identity[:2])
        if payload is None or payload.get("package_digest") != ref["package_digest"]:
            continue
        descriptor = payload.get("descriptor") or {}
        package = None
        if payload.get("trust_provenance") != "platform_builtin":
            package_row = package_payloads.get(identity)
            if package_row is None:
                continue
            try:
                package = LocalProcessingPluginArtifactStore().get(
                    package_row["artifact_ref"]
                )
            except (OSError, KeyError):
                continue
        try:
            result[identity] = {
                "plugin_id": ref["plugin_id"],
                "plugin_version": ref["plugin_version"],
                "runtime_profile": ref["runtime_profile"],
                "kind": payload["plugin_kind"],
                "entrypoint": descriptor["entrypoint"],
                "accepted_region_kinds": tuple(
                    descriptor.get("accepted_region_kinds", [])
                ),
                "accepted_content_kind_hints": tuple(
                    descriptor.get("accepted_content_kind_hints", [])
                ),
                "accepted_element_kind_hints": tuple(
                    descriptor.get("accepted_element_kind_hints", [])
                ),
                "package": package,
            }
        except KeyError:
            continue
    return result


def _plugin_invocation(ref: dict) -> dict:
    invocation = _plugin_invocation_index([ref]).get(_plugin_identity(ref))
    if invocation is None:
        identity = json.dumps(
            [ref["plugin_id"], ref["plugin_version"]], separators=(",", ":")
        )
        with Session(worker_engine()) as session:
            version = session.get(AtlasPluginVersionRow, identity)
            packages = (
                session.scalars(select(AtlasPluginPackageRow)).all()
                if version is not None
                and version.payload.get("trust_provenance") != "platform_builtin"
                else []
            )
        if (
            version is None
            or version.payload.get("package_digest") != ref["package_digest"]
        ):
            raise ValueError("processing_plugin_revision_unavailable")
        package_row = next(
            (
                row.payload
                for row in packages
                if row.payload.get("plugin_id") == ref["plugin_id"]
                and row.payload.get("plugin_version") == ref["plugin_version"]
                and row.payload.get("package_digest") == ref["package_digest"]
            ),
            None,
        )
        if version.payload.get("trust_provenance") != "platform_builtin":
            if package_row is None:
                raise ValueError("processing_plugin_package_unavailable")
            LocalProcessingPluginArtifactStore().get(package_row["artifact_ref"])
        descriptor = version.payload.get("descriptor") or {}
        if "entrypoint" not in descriptor or "plugin_kind" not in version.payload:
            raise KeyError("processing plugin descriptor is incomplete")
        raise ValueError("processing_plugin_revision_unavailable")
    return invocation


def _ordered_mandatory_processors(
    profile: dict,
    invocation_index: dict[tuple[str, str, str], dict] | None = None,
) -> list[dict]:
    mandatory = profile.get("mandatory_processor_plugin_refs", [])
    if invocation_index is None:
        invocation_index = _plugin_invocation_index(mandatory)
    by_identity = {
        (item["plugin_id"], item["plugin_version"], item["package_digest"]): item
        for item in mandatory
    }
    ordered: list[dict] = []
    for item in profile.get("plugin_priority", []):
        identity = (item["plugin_id"], item["plugin_version"], item["package_digest"])
        if identity in by_identity:
            ref = by_identity.pop(identity)
            invocation = invocation_index.get(_plugin_identity(ref))
            if invocation is not None:
                ordered.append(invocation)
    for item in by_identity.values():
        invocation = invocation_index.get(_plugin_identity(item))
        if invocation is not None:
            ordered.append(invocation)
    return ordered


def _processor_accepts(processor: dict, source: dict) -> bool:
    return (
        source.get("region_kind") in processor["accepted_region_kinds"]
        and source.get("content_kind_hint")
        in processor["accepted_content_kind_hints"]
        and (
            not processor["accepted_element_kind_hints"]
            or source.get("element_kind_hint")
            in processor["accepted_element_kind_hints"]
        )
    )


def _merge_processor_output(
    processed: dict,
    *,
    invocation_id: str,
    combined_assets: dict[str, str],
) -> list[dict]:
    output_assets = processed.get("assets", {})
    prefix = hashlib.sha256(invocation_id.encode()).hexdigest()[:16]
    ref_map = {
        ref: f"runner-result:{prefix}-{ordinal}"
        for ordinal, ref in enumerate(output_assets, start=1)
    }
    combined_assets.update({
        ref_map[ref]: value for ref, value in output_assets.items()
    })
    merged: list[dict] = []
    scalar_ref_fields = (
        "candidate_payload_ref", "structured_content_ref",
        "native_artifact_ref", "content_rendition_ref",
    )
    list_ref_fields = ("table_asset_refs", "figure_asset_refs")
    for raw in processed.get("drafts", []):
        draft = dict(raw)
        for field in scalar_ref_fields:
            if draft.get(field) in ref_map:
                draft[field] = ref_map[draft[field]]
        for field in list_ref_fields:
            draft[field] = [
                ref_map.get(ref, ref) for ref in draft.get(field, [])
            ]
        merged.append(draft)
    return merged


def _processor_warning_code(processor: dict) -> str | None:
    plugin_id = processor.get("plugin_id")
    if plugin_id == "atlas-rapidocr":
        return "image_ocr_failed"
    if plugin_id == "atlas-docling-layout":
        # Text remains usable, but layout failure removes a visual precision
        # channel and must not be presented as fully ready.
        return "layout_detection_failed"
    # Generic-text enrichment is a best-effort precision channel. Base-parser
    # text remains complete without it.
    return None


def _validated_pdf_drafts(
    drafts: list[dict],
    *,
    page_number: int,
) -> list[dict]:
    """Keep every text-bearing candidate while treating geometry as optional.

    Plugins only propose preview geometry. Core validates the complete SDK
    contract plus the current batch page, removes invalid proposals, and records
    a bounded quality flag instead of rejecting otherwise useful extracted text.
    """
    validated: list[dict] = []
    for raw in drafts:
        draft = dict(raw)
        flags = list(dict.fromkeys(draft.get("quality_flag_refs") or []))
        preview = draft.get("preview_region")
        if preview is None:
            flags.append("pdf_preview_region_missing")
        else:
            try:
                validate_preview_region(preview)
                if preview["page_number"] != page_number:
                    raise ValueError("preview_region page does not match the batch")
            except (KeyError, TypeError, ValueError):
                draft.pop("preview_region", None)
                flags.append("pdf_preview_region_invalid")
        draft["quality_flag_refs"] = list(dict.fromkeys(flags))
        validated.append(draft)
    return validated


def _pdf_draft_output_fingerprint(
    draft: dict,
    *,
    assets: dict[str, str],
) -> str | None:
    """Fingerprint the retrieval output, not its runner-local asset reference."""

    is_candidate = "candidate_payload_ref" in draft
    text_ref = (
        draft.get("candidate_payload_ref")
        if is_candidate
        else draft.get("normalized_text_ref")
    )
    encoded_text = assets.get(text_ref) if isinstance(text_ref, str) else None
    if not isinstance(encoded_text, str):
        return None
    try:
        text_digest = hashlib.sha256(
            base64.b64decode(encoded_text, validate=True)
        ).hexdigest()
    except ValueError:
        return None

    structured_ref = draft.get("structured_content_ref")
    encoded_structured = (
        assets.get(structured_ref) if isinstance(structured_ref, str) else None
    )
    try:
        structured_digest = (
            hashlib.sha256(
                base64.b64decode(encoded_structured, validate=True)
            ).hexdigest()
            if isinstance(encoded_structured, str)
            else None
        )
    except ValueError:
        return None
    semantic_output = {
        "text_digest": text_digest,
        "structured_digest": structured_digest,
        "channel_id": draft.get("channel_id") or "generic_text",
        "content_kind_hint": draft.get("content_kind_hint"),
        "element_kind_hint": draft.get("element_kind_hint"),
        "table_grid": draft.get("table_grid"),
        "cell_bboxes": draft.get("cell_bboxes"),
    }
    return hashlib.sha256(
        json.dumps(
            semantic_output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _pdf_source_span(
    draft: dict,
    *,
    source_by_identity: dict[str, dict],
) -> str | None:
    """Return an exact, replayable span identity for PDF duplicate checks."""

    if "candidate_payload_ref" in draft:
        raw_ids = draft.get("source_region_ids")
        if not isinstance(raw_ids, list) or not raw_ids or not all(
            isinstance(value, str) for value in raw_ids
        ):
            return None
        source_ids = raw_ids
        source_locators: list[dict] = []
        for source_id in source_ids:
            source = source_by_identity.get(source_id)
            locator = source.get("locator_draft") if source is not None else None
            if not isinstance(locator, dict):
                return None
            source_locators.append(locator)
        preview = draft.get("preview_region")
        if preview is not None and not isinstance(preview, dict):
            return None
    else:
        source_id = draft.get("source_region_identity")
        locator = draft.get("locator_draft")
        if not isinstance(source_id, str) or not isinstance(locator, dict):
            return None
        source_ids = [source_id]
        source_locators = [locator]
        preview = None
    return json.dumps(
        {
            "source_region_ids": source_ids,
            "source_locators": source_locators,
            "preview_region": preview,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_pdf_drafts(
    source_drafts: list[dict],
    candidate_drafts: list[dict],
    *,
    assets: dict[str, str],
) -> list[dict]:
    """Keep base parsing and suppress only exact candidate copies of it.

    Candidate-to-candidate output is deliberately not collapsed: two processors
    may carry distinct provenance even when their visible text happens to match.
    A candidate is redundant only when both its resolved source span (including
    optional preview geometry) and semantic output fingerprint equal an existing
    base-parser draft.
    """

    source_by_identity = {
        source["source_region_identity"]: source
        for source in source_drafts
        if isinstance(source.get("source_region_identity"), str)
    }
    source_keys = {
        (span, fingerprint)
        for source in source_drafts
        if (
            span := _pdf_source_span(
                source, source_by_identity=source_by_identity
            )
        )
        and (
            fingerprint := _pdf_draft_output_fingerprint(
                source, assets=assets
            )
        )
    }
    retained_candidates: list[dict] = []
    for candidate in candidate_drafts:
        span = _pdf_source_span(
            candidate, source_by_identity=source_by_identity
        )
        fingerprint = _pdf_draft_output_fingerprint(candidate, assets=assets)
        if span is not None and fingerprint is not None and (
            span, fingerprint
        ) in source_keys:
            continue
        retained_candidates.append(candidate)
    return [*source_drafts, *retained_candidates]


def _normalized_alignment_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _contains_alignment_span(page_text: str, needle: str) -> bool:
    haystack = _normalized_alignment_text(page_text)
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        left_ok = (
            start == 0
            or not (haystack[start - 1].isalnum() and needle[0].isalnum())
        )
        right_ok = (
            end == len(haystack)
            or not (haystack[end].isalnum() and needle[-1].isalnum())
        )
        if left_ok and right_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _aligned_office_page(
    locator: dict,
    text_value: str,
    rendered: OfficeRenderResult | None,
) -> int | None:
    if rendered is None:
        return None
    slide_number = locator.get("slide_number")
    needle = _normalized_alignment_text(text_value)
    if (
        isinstance(slide_number, int)
        and not isinstance(slide_number, bool)
        and 1 <= slide_number <= len(rendered.pages)
    ):
        declared_page = rendered.pages[slide_number - 1]
        selector_kind = locator.get("selector_kind")
        if selector_kind == "powerpoint_image":
            geometry = [
                locator.get("left"), locator.get("top"),
                locator.get("width"), locator.get("height"),
                locator.get("slide_width"), locator.get("slide_height"),
            ]
            if all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in geometry
            ):
                left, top, width, height, slide_width, slide_height = geometry
                if (
                    left >= 0 and top >= 0 and width > 0 and height > 0
                    and slide_width > 0 and slide_height > 0
                    and left + width <= slide_width
                    and top + height <= slide_height
                ):
                    return slide_number
            return None
        if needle and _contains_alignment_span(declared_page.normalized_text, needle):
            return slide_number
        return None
    if slide_number is not None:
        return None
    alignment_anchors = locator.get("alignment_anchors")
    if isinstance(alignment_anchors, list):
        anchors = [
            _normalized_alignment_text(value)
            for value in alignment_anchors
            if isinstance(value, str) and _normalized_alignment_text(value)
        ]
        if anchors:
            page_sets = [
                {
                    page.page_number
                    for page in rendered.pages
                    if _contains_alignment_span(page.normalized_text, anchor)
                }
                for anchor in anchors
            ]
            confirmed = set.intersection(*page_sets) if page_sets else set()
            return next(iter(confirmed)) if len(confirmed) == 1 else None
    if not needle:
        return None
    matches = [
        page.page_number
        for page in rendered.pages
        if _contains_alignment_span(page.normalized_text, needle)
    ]
    return matches[0] if len(matches) == 1 else None


def _page_label(
    document_format: str,
    page_number: int | None,
    *,
    locator: dict | None = None,
    ordinal: int | None = None,
) -> str:
    """Return the user-facing locator used by citation cards and the Viewer."""

    normalized_format = document_format.casefold()
    if page_number is not None:
        if normalized_format in {"ppt", "pptx"}:
            return f"投影片 {page_number}"
        if normalized_format in {"xls", "xlsx"}:
            sheet_name = (locator or {}).get("sheet_name")
            prefix = (
                f"{sheet_name} · "
                if isinstance(sheet_name, str) and sheet_name.strip()
                else ""
            )
            return f"{prefix}預覽第 {page_number} 頁"
        return f"第 {page_number} 頁"
    return f"{document_format.upper()} 證據 {ordinal or 1}"


def _alignment_method(locator: dict) -> str:
    selector_kind = locator.get("selector_kind")
    if selector_kind == "powerpoint_image":
        return "image_region"
    if isinstance(locator.get("slide_number"), int):
        return "slide_identity"
    if any(
        value is not None
        for value in (
            locator.get("table_index"),
            locator.get("table_name"),
            locator.get("cell_range"),
        )
    ):
        return "table_text_exact"
    if isinstance(locator.get("alignment_anchors"), list):
        return "normalized_text_exact"
    return "normalized_text_exact"


def _artifact_scope(document) -> tuple[str, str | None, tuple[tuple[str, str], ...]]:
    owner_scope_type = document.scope_type or "system"
    owner_scope_id = document.scope_id
    owner = (owner_scope_type, owner_scope_id)
    with Session(worker_engine()) as session:
        binding_rows = session.execute(select(
            AtlasArtifactScopeBindingRow.scope_type,
            AtlasArtifactScopeBindingRow.scope_id,
        ).where(
            AtlasArtifactScopeBindingRow.artifact_id == document.original_artifact_id,
            AtlasArtifactScopeBindingRow.binding_kind.in_(
                ("owner", "authorization", "inherited")
            ),
        )).all()
    authorization_bindings = tuple(sorted({
        (scope_type, scope_id)
        for scope_type, scope_id in binding_rows
        if scope_type in {"team", "project"}
        and scope_id is not None
        and (scope_type, scope_id) != owner
    }))
    return owner_scope_type, owner_scope_id, authorization_bindings


def _pdf_page_logical_identity(job, page_number: int) -> str:
    return (
        f"{job.document_version_id}:generation:{job.processing_generation}:"
        f"page:{page_number}"
    )


def _pdf_page_projection(
    *,
    rendered,
    page_number: int,
    document,
    job,
    batch_id: str,
    storage_artifact_id: str,
    created_at: str,
) -> dict:
    preview_digest = hashlib.sha256(rendered.content).hexdigest()
    page_projection_id = f"epa-{hashlib.sha256(batch_id.encode()).hexdigest()[:32]}"
    return {
        "artifact_id": page_projection_id,
        "tenant_id": "atlas-production",
        "document_version_id": job.document_version_id,
        "source_page_index": page_number - 1,
        "source_page_label": _page_label(document.document_format, page_number),
        "artifact_kind": "pdf_single_page",
        "artifact_digest": preview_digest,
        "content_length": len(rendered.content),
        "storage_artifact_id": storage_artifact_id,
        "source_crop_box": list(rendered.crop_box),
        "source_rotation": rendered.rotation,
        "geometry_transform_version": "rotated-cropbox-top-left-v1",
        "renderer_version": rendered.renderer_version,
        "quality_flag_refs": [],
        "created_at": created_at,
        "processing_generation": job.processing_generation,
    }


def _store_prepared_pdf_page(
    *,
    repository: PostgresDocumentProcessingAdapter,
    rendered,
    page_number: int,
    document,
    job,
    preparation_fence: DocumentPreparationArtifactFence,
    artifact_scope: tuple[str, str | None, tuple[tuple[str, str], ...]],
) -> dict:
    batch_id = f"{job.job_id}:page:{page_number}"
    owner_scope_type, owner_scope_id, authorization_bindings = artifact_scope
    published: dict[str, dict] = {}

    def finalize(connection, artifact) -> None:
        page_record = _pdf_page_projection(
            rendered=rendered,
            page_number=page_number,
            document=document,
            job=job,
            batch_id=batch_id,
            storage_artifact_id=artifact.artifact_id,
            created_at=artifact.created_at,
        )
        repository.finalize_document_page_preparation(
            connection,
            job_id=job.job_id,
            expected_attempt=preparation_fence.attempt,
            claim_fence=preparation_fence.claim_fence,
            claim_token=preparation_fence.claim_token,
            page_record=page_record,
        )
        published["page"] = page_record

    preview = BoundedArtifactWriter(worker_engine()).write(
        content=rendered.content,
        artifact_class="document_page_pdf",
        logical_identity=_pdf_page_logical_identity(job, page_number),
        content_type="application/pdf",
        owner_scope_type=owner_scope_type,
        owner_scope_id=owner_scope_id,
        parent_resource_id=job.document_id,
        parent_lifecycle_epoch=document.resource_lifecycle_epoch,
        document_version_id=job.document_version_id,
        source_artifact_id=document.original_artifact_id,
        processing_generation=job.processing_generation,
        pipeline_id="knowledge-processing",
        pipeline_version="document-page-preparation-v2",
        generation=job.processing_generation,
        page_number=page_number,
        authorization_bindings=authorization_bindings,
        allowed_parent_statuses=("active", "restoring"),
        processing_fence=preparation_fence,
        finalize=finalize,
    )
    if preview.replayed:
        page_record = _pdf_page_projection(
            rendered=rendered,
            page_number=page_number,
            document=document,
            job=job,
            batch_id=batch_id,
            storage_artifact_id=preview.artifact.artifact_id,
            created_at=preview.artifact.created_at,
        )
        with repository.transaction() as connection:
            assert connection is not None
            repository.finalize_document_page_preparation(
                connection,
                job_id=job.job_id,
                expected_attempt=preparation_fence.attempt,
                claim_fence=preparation_fence.claim_fence,
                claim_token=preparation_fence.claim_token,
                page_record=page_record,
            )
        published["page"] = page_record
    if "page" not in published:
        raise RuntimeError("document_page_preparation_publication_failed")
    return published["page"]


def _pdf_page_image_artifact(
    *,
    content: bytes,
    page_number: int,
    prepared_page: dict,
    document,
    job,
    batch_id: str,
    processing_fence: ProcessingArtifactFence,
) -> dict:
    rendered = OfficeRendererAdapter().raster_pdf_page(content)
    if (
        rendered.normalized_bbox != (0, 0, 10_000, 10_000)
        or rendered.content_type != "image/png"
        or hashlib.sha256(rendered.content).hexdigest() != rendered.sha256
        or len(rendered.content) != rendered.byte_length
        or validated_rgb_png_dimensions(rendered.content)
        != (rendered.width, rendered.height)
    ):
        raise ValueError("pdf_page_image_invalid")
    owner_scope_type, owner_scope_id, authorization_bindings = _artifact_scope(document)
    stored = BoundedArtifactWriter(worker_engine()).write(
        content=rendered.content,
        artifact_class="page_image",
        logical_identity=(
            f"{job.document_version_id}:generation:{job.processing_generation}:"
            f"page-image:{page_number}:{rendered.sha256}"
        ),
        content_type="image/png",
        owner_scope_type=owner_scope_type,
        owner_scope_id=owner_scope_id,
        parent_resource_id=job.document_id,
        parent_lifecycle_epoch=document.resource_lifecycle_epoch,
        document_version_id=job.document_version_id,
        source_artifact_id=prepared_page["storage_artifact_id"],
        processing_generation=job.processing_generation,
        pipeline_id="knowledge-processing",
        pipeline_version="pdf-page-image-v1",
        generation=job.processing_generation,
        page_number=page_number,
        authorization_bindings=authorization_bindings,
        allowed_parent_statuses=("active", "restoring"),
        processing_fence=processing_fence,
    )
    projection_id = "epa-" + hashlib.sha256(
        f"{batch_id}:pdf-page-image:{page_number}".encode()
    ).hexdigest()[:32]
    return {
        "artifact_id": projection_id,
        "tenant_id": "atlas-production",
        "document_version_id": job.document_version_id,
        "source_page_index": page_number - 1,
        "source_page_label": _page_label(document.document_format, page_number),
        "artifact_kind": "page_image",
        "artifact_digest": rendered.sha256,
        "content_length": rendered.byte_length,
        "storage_artifact_id": stored.artifact.artifact_id,
        "source_crop_box": list(prepared_page["source_crop_box"]),
        "source_rotation": prepared_page["source_rotation"],
        "geometry_transform_version": "normalized-top-left-10000-v1",
        "renderer_version": rendered.renderer_version,
        "width": rendered.width,
        "height": rendered.height,
        "render_config_revision": rendered.renderer_config_digest,
        "normalized_bbox": [0, 0, 10_000, 10_000],
        "quality_flag_refs": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "processing_generation": job.processing_generation,
    }


def _remap_single_page_pdf_draft(draft: dict, page_number: int) -> dict:
    """Restore original source-page identity after parsing a one-page rendition."""

    result = dict(draft)
    for field in ("source_region_identity", "parent_region_identity"):
        value = result.get(field)
        if isinstance(value, str) and (value == "page:1" or value.startswith("page:1:")):
            result[field] = f"page:{page_number}{value[len('page:1') :]}"
    source_ids = result.get("source_region_ids")
    if isinstance(source_ids, list):
        result["source_region_ids"] = [
            f"page:{page_number}{value[len('page:1') :]}"
            if isinstance(value, str)
            and (value == "page:1" or value.startswith("page:1:"))
            else value
            for value in source_ids
        ]
    locator = result.get("locator_draft")
    if isinstance(locator, dict) and locator.get("page_number") == 1:
        result["locator_draft"] = {**locator, "page_number": page_number}
    preview = result.get("preview_region")
    if isinstance(preview, dict) and preview.get("page_number") == 1:
        result["preview_region"] = {**preview, "page_number": page_number}
    return result


def _office_page_artifacts(
    *,
    rendered: OfficeRenderResult,
    document,
    job,
    batch_id: str,
    processing_fence: ProcessingArtifactFence,
) -> list[dict]:
    owner_scope_type, owner_scope_id, authorization_bindings = _artifact_scope(document)
    rows: list[dict] = []
    for page in rendered.pages:
        projection_id = (
            "epa-" + hashlib.sha256(
                f"{batch_id}:office-page:{page.page_number}".encode()
            ).hexdigest()[:32]
        )
        stored = BoundedArtifactWriter(worker_engine()).write(
            content=page.content,
            artifact_class="page_image",
            logical_identity=(
                f"{document.original_artifact_id}:page-image:{page.page_number}:"
                f"generation:{job.processing_generation}:{page.sha256}"
            ),
            content_type="image/png",
            owner_scope_type=owner_scope_type,
            owner_scope_id=owner_scope_id,
            parent_resource_id=job.document_id,
            parent_lifecycle_epoch=document.resource_lifecycle_epoch,
            document_version_id=job.document_version_id,
            source_artifact_id=document.original_artifact_id,
            processing_generation=job.processing_generation,
            pipeline_id="knowledge-processing",
            pipeline_version="celery-office-v1",
            generation=job.processing_generation,
            page_number=page.page_number,
            authorization_bindings=authorization_bindings,
            allowed_parent_statuses=("active", "restoring"),
            processing_fence=processing_fence,
        )
        rows.append({
            "artifact_id": projection_id,
            "tenant_id": "atlas-production",
            "document_version_id": job.document_version_id,
            "source_page_index": page.page_number - 1,
            "source_page_label": _page_label(
                document.document_format, page.page_number
            ),
            "artifact_kind": "page_image",
            "artifact_digest": page.sha256,
            "content_length": len(page.content),
            "storage_artifact_id": stored.artifact.artifact_id,
            "source_crop_box": [0.0, 0.0, float(page.width), float(page.height)],
            "source_rotation": 0,
            "geometry_transform_version": "image-normalized-top-left-v1",
            "renderer_version": rendered.renderer_version,
            "width": page.width,
            "height": page.height,
            "render_config_revision": (
                rendered.renderer_config_digest
            ),
            "quality_flag_refs": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "processing_generation": job.processing_generation,
        })
    return rows


@lru_cache(maxsize=1)
def _embedding_tokenizer() -> tuple[Tokenizer, int]:
    cache_dir = os.getenv("ATLAS_FASTEMBED_CACHE", "/var/lib/atlas-fastembed")
    model_path = Path(snapshot_download(
        repo_id=MODEL_NAME,
        revision=MODEL_REVISION,
        cache_dir=cache_dir,
        allow_patterns=["tokenizer.json", "tokenizer_config.json"],
        local_files_only=os.getenv("ATLAS_EMBEDDING_OFFLINE") == "true",
    ))
    tokenizer = Tokenizer.from_file(str(model_path / "tokenizer.json"))
    tokenizer.no_truncation()
    config = json.loads((model_path / "tokenizer_config.json").read_text())
    max_context = int(config.get("model_max_length", config.get("max_length")))
    prefix_tokens = len(tokenizer.encode("passage: ", add_special_tokens=True).ids)
    content_token_budget = max_context - prefix_tokens
    if content_token_budget <= 0:
        raise ValueError("embedding_token_budget_invalid")
    return tokenizer, content_token_budget


def _text_windows(
    value: str,
    *,
    max_characters: int = 4096,
    tokenizer_profile: tuple[Tokenizer, int] | None = None,
) -> list[str]:
    """Return deterministic, lossless bounded projections.

    Evidence rows are deliberately bounded for retrieval. Oversized parser
    elements become multiple immutable evidence/chunk windows instead of being
    truncated; joining the windows recreates the normalized parser output.
    """
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")
    tokenizer, token_budget = tokenizer_profile or _embedding_tokenizer()
    windows: list[str] = []
    for offset in range(0, len(value), max_characters):
        segment = value[offset:offset + max_characters]
        encoding = tokenizer.encode(segment, add_special_tokens=False)
        if len(encoding.ids) <= token_budget:
            windows.append(segment)
            continue
        cursor = 0
        for token_offset in range(token_budget, len(encoding.ids), token_budget):
            end = encoding.offsets[token_offset - 1][1]
            if end <= cursor:
                raise ValueError("embedding_token_offsets_invalid")
            windows.append(segment[cursor:end])
            cursor = end
        if cursor < len(segment):
            windows.append(segment[cursor:])
    return windows


@dataclass(frozen=True, slots=True)
class _ProcessingSource:
    repository: PostgresDocumentProcessingAdapter
    job: object
    document: object
    media_type: str
    blob: object
    adapter: LocalArtifactFilesystemAdapter

    def read_all(self) -> bytes:
        with self.adapter.open_read(
            self.blob.opaque_ref, expected_size=self.blob.byte_size
        ) as stream:
            content = stream.read()
        if hashlib.sha256(content).hexdigest() != self.blob.checksum_value:
            raise RuntimeError("artifact_checksum_mismatch")
        return content


def _processing_document(job_id: str, *, job=None):
    repository = worker_repository()
    job = job or repository.get_job(job_id)
    if job is None:
        raise ValueError("processing_job_not_found")
    with Session(worker_engine()) as session:
        document_row = session.execute(
            select(AtlasDocumentRow).where(
                AtlasDocumentRow.document_id == job.document_id,
                AtlasDocumentRow.lifecycle_status.in_(("active", "restoring")),
            )
        ).scalar_one_or_none()
    if document_row is None:
        raise ValueError("processing_source_unavailable")
    document = SimpleNamespace(**{
        name: getattr(document_row, name)
        for name in AtlasDocumentRow.__table__.columns.keys()
    })
    return repository, job, document, document.content_type or "text/plain"


def _source_descriptor(job_id: str, *, job=None) -> _ProcessingSource:
    repository, job, document, media_type = _processing_document(
        job_id, job=job
    )
    with Session(worker_engine()) as session:
        row = session.execute(
            select(
                AtlasArtifactRow,
                AtlasStorageBlobRow,
                AtlasArtifactStorageTargetRow,
            )
            .join(
                AtlasStorageBlobRow,
                AtlasStorageBlobRow.blob_id == AtlasArtifactRow.blob_id,
            )
            .join(
                AtlasArtifactStorageTargetRow,
                (AtlasArtifactStorageTargetRow.target_id == AtlasStorageBlobRow.target_id)
                & (
                    AtlasArtifactStorageTargetRow.target_revision
                    == AtlasStorageBlobRow.target_revision
                ),
            )
            .where(
                AtlasArtifactRow.artifact_id == document.original_artifact_id,
                AtlasArtifactRow.lifecycle_status == "active",
                AtlasStorageBlobRow.status == "committed",
            )
        ).one_or_none()
    if row is None:
        raise ValueError("processing_source_unavailable")
    _artifact, blob, target = row
    config = RootOnlyStorageTargetConfig(os.environ["ATLAS_ARTIFACT_TARGET_CONFIG"])
    configured = config.load().get(target.target_id)
    if (
        configured is None
        or configured["revision"] != target.target_revision
        or configured["config_key"] != target.config_key
    ):
        raise RuntimeError("storage_target_configuration_unavailable")
    allowlist = tuple(
        Path(value) for value in os.environ["ATLAS_ARTIFACT_ALLOWED_PARENTS"].split(os.pathsep)
        if value
    )
    adapter = LocalArtifactFilesystemAdapter(
        configured["raw_path"], allowlisted_parents=allowlist, create_layout=False
    )
    return _ProcessingSource(
        repository=repository,
        job=job,
        document=document,
        media_type=media_type,
        blob=blob,
        adapter=adapter,
    )


def _source(job_id: str):
    source = _source_descriptor(job_id)
    return (
        source.repository,
        source.job,
        source.document,
        source.media_type,
        source.read_all(),
    )


def prepare(job_id: str, attempt: int) -> int:
    repository = worker_repository()
    job = repository.get_job(job_id)
    if job is None:
        raise ValueError("processing_job_not_found")
    if job.job_kind == "reindex":
        return repository.prepare_reindex(job_id, expected_attempt=attempt)
    with repository.preparation_execution(
        job_id, expected_attempt=attempt
    ) as claimed_job:
        if claimed_job is None:
            current = repository.get_job(job_id)
            if current is None or current.attempt != attempt or current.status in {
                "succeeded", "failed", "cancelled"
            }:
                return 0
            raise RuntimeError("processing_prepare_claim_unavailable")
        if claimed_job.attempt != attempt:
            return 0
        source = _source_descriptor(job_id, job=claimed_job)
        if claimed_job.processing_generation is None:
            profile = _active_processing_profile(source.media_type)
        else:
            profile_pin = repository.get_processing_profile_pin(
                document_id=claimed_job.document_id,
                processing_generation=claimed_job.processing_generation,
            )
            profile = _processing_profile(
                profile_pin.profile_id, profile_pin.profile_revision
            )
        if source.media_type != "application/pdf":
            source.read_all()
            repository.prepare_job(
                job_id,
                total_units=1,
                profile_id=profile["profile_id"],
                profile_revision=int(profile["revision"]),
                expected_attempt=attempt,
            )
            return 1

        max_pages = min(int(os.getenv("ATLAS_PDF_MAX_PAGES", "3000")), 3000)
        if max_pages <= 0:
            raise RuntimeError("pdf_page_limit_invalid")
        if claimed_job.processing_generation is None:
            raise ValueError("processing_generation_unavailable")
        if claimed_job.batch_claim_token is None:
            raise RuntimeError("processing_prepare_claim_unavailable")
        artifact_scope = _artifact_scope(source.document)
        total_pages = 0

        def on_document(page_count: int) -> None:
            nonlocal total_pages
            total_pages = page_count
            repository.prepare_job(
                job_id,
                total_units=page_count,
                profile_id=profile["profile_id"],
                profile_revision=int(profile["revision"]),
                expected_attempt=attempt,
                enqueue_batches=False,
            )

        def on_page(page_index: int, rendered) -> None:
            page_number = page_index + 1
            preparation_fence = DocumentPreparationArtifactFence(
                job_id=job_id,
                attempt=attempt,
                claim_fence=claimed_job.fence,
                claim_token=str(claimed_job.batch_claim_token),
                document_id=claimed_job.document_id,
                document_version_id=claimed_job.document_version_id,
                processing_generation=claimed_job.processing_generation or 0,
                batch_id=f"{job_id}:prepare",
                page_number=page_number,
                parent_lifecycle_epoch=source.document.resource_lifecycle_epoch,
            )
            _store_prepared_pdf_page(
                repository=repository,
                rendered=rendered,
                page_number=page_number,
                document=source.document,
                job=claimed_job,
                preparation_fence=preparation_fence,
                artifact_scope=artifact_scope,
            )

        try:
            with source.adapter.open_read(
                source.blob.opaque_ref, expected_size=source.blob.byte_size
            ) as stream:
                PdfPreviewAdapter().render_document(
                    stream,
                    expected_size=source.blob.byte_size,
                    expected_sha256=source.blob.checksum_value,
                    max_pages=max_pages,
                    on_document=on_document,
                    on_page=on_page,
                )
        except PdfPreviewError as exc:
            if exc.code == "artifact_too_large":
                raise ValueError("artifact_too_large") from exc
            if exc.code in {
                "pdf_preview_source_invalid",
                "pdf_preview_input_invalid",
                "artifact_checksum_mismatch",
                "processing_source_empty",
            }:
                raise ValueError("source_pdf_invalid") from exc
            raise ValueError("pdf_page_preparation_failed") from exc
        return total_pages


def _ensure_index_delivery(
    repository: PostgresDocumentProcessingAdapter,
    job_id: str,
    batch_id: str,
    *,
    attempt: int,
) -> None:
    try:
        queued = repository.enqueue_index_batch(
            job_id, batch_id, expected_attempt=attempt
        )
    except DocumentProcessingCurrentnessConflict:
        queued = False
    if not queued:
        repository.schedule_page_batch_retry(
            job_id,
            batch_id,
            expected_attempt=attempt,
            task_name="atlas.indexing.index_batch",
            code="index_batch_not_queued",
        )


def process_batch(job_id: str, batch_id: str, attempt: int) -> bool:
    repository = worker_repository()

    with repository.batch_execution(job_id, batch_id) as claimed_job:
        # A duplicate delivery that overlaps the active batch is not complete.
        # Its caller persists a same-attempt delayed retry so a later crash of
        # the current lock owner cannot leave this batch without a delivery.
        if claimed_job is None:
            return False
        # A terminal-failure retry may deliberately omit already-checkpointed
        # processing messages from the new attempt. Fence those stale messages
        # before loading or parsing the source, even when no same-identity
        # attempt-N outbox exists for the task-level preflight to compare.
        if claimed_job.attempt != attempt:
            return True
        repository, job, document, media_type = _processing_document(
            job_id, job=claimed_job
        )
        if repository.checkpoint_for_batch(job_id, batch_id) is not None:
            _ensure_index_delivery(
                repository, job_id, batch_id, attempt=attempt
            )
            return True
        try:
            unit_start = unit_end = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_batch_id") from exc
        if job.status in {"succeeded", "failed", "cancelled"}:
            return True
        try:
            prepared_page = (
                repository.prepared_page_artifact(job_id, batch_id)
                if media_type == "application/pdf"
                else None
            )
        except ValueError as exc:
            raise ValueError("pdf_page_preparation_failed") from exc
        content = None
        if media_type == "application/pdf":
            if prepared_page is None:
                # PDF page tasks have exactly one source: the document-level
                # prepared page artifact. A missing publication fails before
                # the original descriptor is opened; there is no full-source
                # fallback on either success or failure paths.
                raise ValueError("pdf_page_preparation_failed")
        else:
            content = _source_descriptor(job_id, job=job).read_all()
        return _process_claimed_batch(
            repository=repository,
            job=job,
            document=document,
            media_type=media_type,
            content=content,
            prepared_page=prepared_page,
            job_id=job_id,
            batch_id=batch_id,
            unit_start=unit_start,
            unit_end=unit_end,
            attempt=attempt,
            claim_fence=claimed_job.fence,
            claim_token=str(claimed_job.batch_claim_token),
        )


def _process_claimed_batch(
    *,
    repository: PostgresDocumentProcessingAdapter,
    job,
    document,
    media_type: str,
    content: bytes | None,
    prepared_page: dict | None,
    job_id: str,
    batch_id: str,
    unit_start: int,
    unit_end: int,
    attempt: int,
    claim_fence: int,
    claim_token: str,
) -> bool:
    invocation_id = f"inv-{hashlib.sha256(batch_id.encode()).hexdigest()[:24]}"
    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=_DEFAULT_PROCESSING_PLUGIN_TIMEOUT_SECONDS
    )
    if job.processing_generation is None:
        raise ValueError("processing_generation_unavailable")
    processing_fence = ProcessingArtifactFence(
        job_id=job_id,
        attempt=attempt,
        claim_fence=claim_fence,
        claim_token=claim_token,
        document_id=job.document_id,
        document_version_id=job.document_version_id,
        processing_generation=job.processing_generation,
        batch_id=batch_id,
        unit_start=unit_start,
        unit_end=unit_end,
        parent_lifecycle_epoch=document.resource_lifecycle_epoch,
    )
    profile_pin = repository.get_processing_profile_pin(
        document_id=job.document_id,
        processing_generation=job.processing_generation,
    )
    profile = _processing_profile(
        profile_pin.profile_id, profile_pin.profile_revision
    )
    rendered_office: OfficeRenderResult | None = None
    if content is None and prepared_page is None:
        raise ValueError("processing_source_unavailable")
    parser_content = content or b""
    processing_warning_codes: list[str] = []
    page_artifact_rows: list[dict] = []
    page_scoped_pdf = False
    if media_type == "application/pdf":
        try:
            if prepared_page is None:
                raise ValueError("document_page_source_unavailable")
            parser_content = BoundedArtifactWriter(worker_engine()).read_processing(
                prepared_page["storage_artifact_id"],
                expected_content_type="application/pdf",
                expected_artifact_class="document_page_pdf",
                expected_logical_identity=_pdf_page_logical_identity(
                    job, unit_start
                ),
                fence=processing_fence,
            )
            if (
                len(parser_content) != prepared_page["content_length"]
                or hashlib.sha256(parser_content).hexdigest()
                != prepared_page["artifact_digest"]
            ):
                raise ValueError("document_page_source_invalid")
        except ValueError as exc:
            raise ValueError("pdf_page_preparation_failed") from exc
        page_scoped_pdf = True
        page_artifact_rows.append(dict(prepared_page))
        try:
            _pdf_page_image_artifact(
                content=parser_content,
                page_number=unit_start,
                prepared_page=prepared_page,
                document=document,
                job=job,
                batch_id=batch_id,
                processing_fence=processing_fence,
            )
        except (OfficeRendererError, ValueError) as exc:
            logger.warning(
                "PDF page image unavailable for page=%s code=%s",
                unit_start,
                str(exc),
            )
            processing_warning_codes.append("pdf_page_image_unavailable")
    if media_type in OFFICE_MIME_TYPES:
        try:
            rendered_office = OfficeRendererAdapter().render(content, media_type)
        except OfficeRendererError as exc:
            if str(exc) == "artifact_too_large":
                raise ValueError("artifact_too_large") from exc
            if media_type in LEGACY_OFFICE_MIME_TYPES:
                raise ValueError("legacy_converter_unavailable") from exc
            processing_warning_codes.append("office_preview_unavailable")
        if media_type in LEGACY_OFFICE_MIME_TYPES:
            if (
                rendered_office is None
                or rendered_office.converted_document is None
                or rendered_office.converted_mime is None
            ):
                raise ValueError("legacy_converter_unavailable")
            parser_content = rendered_office.converted_document
    request = {
        "run_id": job_id,
        "invocation_id": invocation_id,
        "document_id": job.document_id,
        "document_version_id": job.document_version_id,
        "artifact_ref": f"artifact:{job.document_version_id}",
        "media_type": media_type,
        "profile_id": profile_pin.profile_id,
        "profile_revision": profile_pin.profile_revision,
        "policy_snapshot_ref": f"job:{job_id}",
        "deadline_at": deadline.isoformat(),
        "plugin_config": (
            {"artifact_page_number": 1, "source_page_number": unit_start}
            if page_scoped_pdf
            else {}
        ),
        "batch_id": batch_id,
        "unit_start": 1 if page_scoped_pdf else unit_start,
        "unit_end": 1 if page_scoped_pdf else unit_end,
        "resume_cursor": None,
    }
    plugin_refs = [
        profile["base_parser_plugin_ref"],
        *profile.get("mandatory_processor_plugin_refs", []),
    ]
    plugin_invocations = _plugin_invocation_index(plugin_refs)
    base_parser = plugin_invocations.get(
        _plugin_identity(profile["base_parser_plugin_ref"])
    )
    if base_parser is None:
        base_parser = _plugin_invocation(profile["base_parser_plugin_ref"])
    runner = default_processing_runner()
    try:
        result = runner.invoke(
            {
                "invocation_id": invocation_id,
                "runtime_profile": base_parser["runtime_profile"],
                "kind": "base_parser",
                "entrypoint": base_parser["entrypoint"],
                "request": request,
                "artifact": parser_content,
                "package": base_parser["package"],
                "input_assets": {},
                "timeout_seconds": _DEFAULT_PROCESSING_PLUGIN_TIMEOUT_SECONDS,
            }
        )
    except ProcessingRunnerError as exc:
        logger.warning(
            "base parser failed for document=%s generation=%s page=%s code=%s type=%s",
            job.document_id,
            job.processing_generation,
            unit_start,
            exc.safe_code,
            exc.safe_type,
        )
        if _processing_runner_failure_is_transient(exc):
            # Let the task boundary schedule a same-attempt infrastructure
            # retry. These outcomes do not prove a deterministic source or
            # parser failure, so the active generation must remain usable.
            raise RuntimeError("base_parser_temporarily_unavailable") from exc
        raise ValueError("base_parser_failed") from exc
    source_drafts = result.get("drafts", [])
    if page_scoped_pdf:
        source_drafts = [
            _remap_single_page_pdf_draft(draft, unit_start)
            for draft in source_drafts
        ]
    combined_assets = dict(result.get("assets", {}))
    candidate_drafts: list[dict] = []
    image_processor_cache: dict[tuple[str, str, str], list[dict]] = {}
    for processor_ordinal, processor in enumerate(
        _ordered_mandatory_processors(profile, plugin_invocations), start=1
    ):
        for source_ordinal, source in enumerate(source_drafts, start=1):
            if not _processor_accepts(processor, source):
                continue
            cache_key: tuple[str, str, str] | None = None
            native_ref = source.get("native_artifact_ref")
            encoded_native = (
                combined_assets.get(native_ref)
                if isinstance(native_ref, str)
                else None
            )
            if (
                source.get("region_kind") == "image_region"
                and isinstance(encoded_native, str)
            ):
                cache_key = (
                    str(processor.get("plugin_id")),
                    str(processor.get("plugin_version")),
                    hashlib.sha256(
                        base64.b64decode(encoded_native, validate=True)
                    ).hexdigest(),
                )
                cached = image_processor_cache.get(cache_key)
                if cached is not None:
                    candidate_drafts.extend([
                        {
                            **candidate,
                            "source_region_ids": [
                                source["source_region_identity"]
                            ],
                        }
                        for candidate in cached
                    ])
                    continue
            processor_invocation_id = (
                f"{invocation_id}:processor:{processor_ordinal}:source:{source_ordinal}"
            )
            processor_request = {
                key: value
                for key, value in request.items()
                if key
                not in {
                    "batch_id", "unit_start", "unit_end", "resume_cursor"
                }
            }
            processor_request.update({
                "invocation_id": processor_invocation_id,
                "region_id": source["source_region_identity"],
                "region_kind": source["region_kind"],
                "content_kind_hint": source["content_kind_hint"],
                "element_kind_hint": source.get("element_kind_hint"),
                "normalized_text_ref": source.get("normalized_text_ref"),
                "structured_content_ref": source.get("structured_content_ref"),
                "native_artifact_ref": source.get("native_artifact_ref"),
                "locator_draft": source["locator_draft"],
                "active_trait_hints": [],
            })
            processor_input_assets = {
                ref: combined_assets[ref]
                for ref in (
                    source.get("normalized_text_ref"),
                    source.get("structured_content_ref"),
                    source.get("native_artifact_ref"),
                )
                if isinstance(ref, str) and ref in combined_assets
            }
            processor_timeout_seconds, processor_deadline = (
                _processor_invocation_limits(
                    processor,
                    inherited_deadline=deadline,
                )
            )
            processor_request["deadline_at"] = processor_deadline.isoformat()
            try:
                processed = runner.invoke({
                    "invocation_id": processor_invocation_id,
                    "runtime_profile": processor["runtime_profile"],
                    "kind": "region_processor",
                    "entrypoint": processor["entrypoint"],
                    "request": processor_request,
                    "artifact": parser_content,
                    "package": processor["package"],
                    "input_assets": processor_input_assets,
                    "timeout_seconds": processor_timeout_seconds,
                })
            except ProcessingRunnerError as exc:
                logger.warning(
                    "region processor failed for document=%s generation=%s page=%s plugin=%s code=%s type=%s",
                    job.document_id,
                    job.processing_generation,
                    unit_start,
                    processor["plugin_id"],
                    exc.safe_code,
                    exc.safe_type,
                )
                # Region precision is explicitly best effort. The verified page
                # and base-parser text remain the fail-open citation path.
                warning_code = _processor_warning_code(processor)
                if warning_code is not None:
                    processing_warning_codes.append(warning_code)
                continue
            merged = _merge_processor_output(
                processed,
                invocation_id=processor_invocation_id,
                combined_assets=combined_assets,
            )
            for candidate in merged:
                candidate.setdefault("processor_id", processor["plugin_id"])
                candidate.setdefault(
                    "processor_revision", processor["plugin_version"]
                )
                if processor["plugin_id"] == "atlas-rapidocr":
                    candidate.setdefault("processor_engine", "rapidocr")
                    candidate.setdefault("processor_engine_revision", "3.9.1")
                if candidate.get("channel_id") == "visual_semantics":
                    continue
                candidate_drafts.append(candidate)
            if cache_key is not None:
                image_processor_cache[cache_key] = [
                    dict(candidate)
                    for candidate in merged
                    if candidate.get("channel_id") != "visual_semantics"
                ]

    source_by_identity = {
        source["source_region_identity"]: source for source in source_drafts
    }
    candidate_source_ids = {
        source_id
        for candidate in candidate_drafts
        for source_id in candidate.get("source_region_ids", [])
        if isinstance(source_id, str)
    }
    ocr_text_unavailable = media_type == "application/pdf" and any(
        source.get("content_kind_hint") == "unknown"
        and not source.get("normalized_text_ref")
        and source.get("source_region_identity") not in candidate_source_ids
        for source in source_drafts
    )
    if media_type == "application/pdf":
        selected_drafts = _merge_pdf_drafts(
            source_drafts,
            candidate_drafts,
            assets=combined_assets,
        )
        selected_drafts = _validated_pdf_drafts(
            selected_drafts,
            page_number=unit_start,
        )
        if ocr_text_unavailable:
            selected_drafts = [
                {
                    **draft,
                    "quality_flag_refs": list(dict.fromkeys([
                        *draft.get("quality_flag_refs", []),
                        "pdf_ocr_text_unavailable",
                    ])),
                }
                for draft in selected_drafts
            ]
    else:
        # Office enrichment is additive: OCR and visual inference must not
        # replace native paragraphs, tables, and slide text.
        selected_drafts = (
            [*source_drafts, *candidate_drafts]
            if media_type in OFFICE_MIME_TYPES
            else candidate_drafts or source_drafts
        )
    if media_type != "application/pdf" and rendered_office is not None:
        page_artifact_rows.extend(_office_page_artifacts(
            rendered=rendered_office,
            document=document,
            job=job,
            batch_id=batch_id,
            processing_fence=processing_fence,
        ))
    page_artifacts_by_number = {
        row["source_page_index"] + 1: row for row in page_artifact_rows
    }
    evidence_rows: list[dict] = []
    chunk_rows: list[dict] = []
    for ordinal, draft in enumerate(selected_drafts, start=1):
        is_candidate = "candidate_payload_ref" in draft
        ref = (
            draft.get("candidate_payload_ref")
            if is_candidate
            else draft.get("normalized_text_ref")
        )
        encoded = combined_assets.get(ref)
        if not isinstance(encoded, str):
            continue
        text_value = base64.b64decode(encoded, validate=True).decode("utf-8").strip()
        if not text_value:
            continue
        if is_candidate:
            source_ids = draft.get("source_region_ids") or []
            source = source_by_identity.get(source_ids[0]) if source_ids else None
            locator = dict(source.get("locator_draft") or {}) if source else {}
            if isinstance(draft.get("preview_region"), dict):
                locator["preview_region"] = draft["preview_region"]
            source_region_identity = (
                source_ids[0] if source_ids else "source:unknown"
            )
            preview = draft.get("preview_region") or {}
            candidate_identity = preview.get("source_element_id") or ordinal
            source_identity = (
                f"{source_region_identity}:candidate:{candidate_identity}"
            )
            channel_id = draft.get("channel_id") or "generic_text"
        else:
            locator = dict(draft.get("locator_draft") or {})
            source_identity = draft.get("source_region_identity") or f"source:{ordinal}"
            channel_id = "generic_text"
        page_number = (
            unit_start
            if media_type == "application/pdf"
            else _aligned_office_page(locator, text_value, rendered_office)
        )
        if page_number is not None:
            locator["page_number"] = page_number
        else:
            locator.pop("page_number", None)
            if media_type in OFFICE_MIME_TYPES and rendered_office is not None:
                processing_warning_codes.append(
                    "office_preview_page_mapping_missing"
                )
        locator["document_format"] = document.document_format
        locator["preview_kind"] = (
            "pdf_page" if media_type == "application/pdf"
            else "page_image" if page_number is not None and rendered_office is not None
            else "inline_text" if media_type in INLINE_PREVIEW_MIME_TYPES
            else "unavailable"
        )
        evidence_modality = (
            "image_ocr" if draft.get("element_kind_hint") == "ocr_text"
            else "table_text" if draft.get("content_kind_hint") == "table"
            else "document_text"
        )
        locator["evidence_modality"] = evidence_modality
        preview_artifact = page_artifacts_by_number.get(page_number)
        locator["parser_id"] = base_parser["plugin_id"]
        locator["parser_revision"] = base_parser["plugin_version"]
        locator["profile_id"] = profile_pin.profile_id
        locator["profile_revision"] = profile_pin.profile_revision
        for field in (
            "processor_id",
            "processor_revision",
            "processor_engine",
            "processor_engine_revision",
        ):
            value = draft.get(field)
            if value is not None:
                locator[field] = value
        if evidence_modality == "image_ocr":
            locator["processor_source_type"] = "image_ocr_extract"
        if preview_artifact is not None:
            locator.update({
                "preview_artifact_ref": preview_artifact["artifact_id"],
                "preview_artifact_id": preview_artifact["artifact_id"],
                "preview_artifact_digest": preview_artifact["artifact_digest"],
                "preview_page_number": page_number,
                "preview_source_kind": (
                    "office_page_image"
                    if media_type in OFFICE_MIME_TYPES
                    else "pdf_single_page"
                ),
                "preview_image_width": preview_artifact.get("width"),
                "preview_image_height": preview_artifact.get("height"),
                "preview_renderer_revision": preview_artifact["renderer_version"],
                "preview_render_config_revision": preview_artifact.get(
                    "render_config_revision"
                ),
            })
            if media_type in OFFICE_MIME_TYPES:
                locator.update({
                    "alignment_method": _alignment_method(locator),
                    "alignment_version": "office-page-image-alignment-v1",
                })
            locator = {
                key: value for key, value in locator.items() if value is not None
            }
        locator_label = _page_label(
            document.document_format,
            page_number,
            locator=locator,
            ordinal=ordinal,
        )
        for window_ordinal, projection in enumerate(_text_windows(text_value)):
            content_fingerprint = hashlib.sha256(
                projection.encode("utf-8")
            ).hexdigest()
            stable = hashlib.sha256(
                f"{job_id}:{batch_id}:{source_identity}:{ordinal}:{window_ordinal}".encode()
            ).hexdigest()
            evidence_id = f"evidence-{stable[:32]}"
            chunk_id = f"chunk-{stable[:32]}"
            processing_fingerprint = hashlib.sha256(
                f"{job.document_version_id}:{job.processing_generation}:{content_fingerprint}".encode()
            ).hexdigest()
            evidence_rows.append({
                "evidence_id": evidence_id,
                "document_id": job.document_id,
                "document_title": document.title,
                "locator_label": locator_label,
                "snippet": projection,
                "content": projection,
                "document_version_id": job.document_version_id,
                "processing_generation": job.processing_generation,
                "source_region_id": f"{source_identity}:window:{window_ordinal}",
                "channel_id": channel_id,
                "output_contract_version": "eir-v1",
                "claim_support_role": "claim_grounding",
                "locator_payload": locator,
                "content_fingerprint": content_fingerprint,
                "processing_fingerprint": processing_fingerprint,
                "profile_id": request["profile_id"],
                "profile_revision": profile_pin.profile_revision,
                "quality_flag_refs": list(dict.fromkeys([
                    *list(draft.get("quality_flag_refs") or []),
                    *processing_warning_codes,
                ])),
                "trace_ref": f"job:{job_id}:batch:{batch_id}",
                "evidence_artifact_id": (
                    preview_artifact["artifact_id"]
                    if preview_artifact is not None
                    else f"evidence-projection:{evidence_id}"
                    if media_type in INLINE_PREVIEW_MIME_TYPES
                    or document.source_kind == "inline_text"
                    else None
                ),
            })
            chunk_rows.append({
                "chunk_id": chunk_id,
                "batch_id": batch_id,
                "document_id": job.document_id,
                "document_version_id": job.document_version_id,
                "processing_generation": job.processing_generation,
                "index_generation_id": job.index_generation_id,
                "evidence_id": evidence_id,
                "segment_id": source_identity,
                "window_ordinal": window_ordinal,
                "normalized_text": projection,
                "locator": locator,
                "content_fingerprint": content_fingerprint,
                "processing_fingerprint": processing_fingerprint,
                "status": "staged",
                "created_at": datetime.now(timezone.utc),
            })
    output_digest = hashlib.sha256(
        json.dumps(
            [(row["evidence_id"], row["content_fingerprint"]) for row in evidence_rows],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    committed = repository.commit_checkpoint(
        job_id=job_id,
        attempt=attempt,
        claim_fence=claim_fence,
        claim_token=claim_token,
        batch_id=batch_id,
        unit_start=unit_start,
        unit_end=unit_end,
        input_fingerprint=hashlib.sha256(parser_content).hexdigest(),
        output_digest=output_digest,
        evidence_rows=evidence_rows,
        chunk_rows=chunk_rows,
        page_artifact_rows=(
            [] if media_type == "application/pdf" else page_artifact_rows
        ),
        preview_count=len(page_artifact_rows),
        warning_codes=processing_warning_codes,
    )
    if committed:
        _ensure_index_delivery(repository, job_id, batch_id, attempt=attempt)
    return committed
