from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from atlas_production.infrastructure.persistence import async_processing
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentRow
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasEvidenceRow,
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
    processing_identity_record,
    processing_identity_row,
    processing_identity_spec_payload,
    processing_revision_record,
    processing_revision_row,
)
from atlas_production.modules.processing_pipeline.canonical_processing import (
    ProcessingIdentity,
    ProcessingRevision,
    canonical_processing_spec,
    processing_fingerprint,
)


def _configuration() -> dict[str, object]:
    return {
        "schema_version": 1,
        "parser": {"plugin": "pdf-parser", "revision": 3},
        "ocr": {"engine": "tesseract", "languages": ["eng", "chi_tra"]},
        "renderer": {"engine": "poppler", "dpi": 144},
        "normalization": {"unicode": "NFC", "whitespace": "collapse"},
        "chunking": {"strategy": "semantic-v2", "max_tokens": 512},
        "embedding": {"model": "embed-v3", "dimensions": 1_024},
        "indexing": {"distance": "cosine", "fts": "simple"},
    }


@pytest.mark.parametrize(
    "field",
    [
        "parser",
        "ocr",
        "renderer",
        "normalization",
        "chunking",
        "embedding",
        "indexing",
    ],
)
def test_fingerprint_changes_for_each_material_rule_family(field: str) -> None:
    original = _configuration()
    changed = _configuration()
    rule = changed[field]
    assert isinstance(rule, dict)
    changed[field] = {**rule, "material_change": True}

    assert processing_fingerprint(changed) != processing_fingerprint(original)


@pytest.mark.parametrize(
    ("field", "first", "second"),
    [
        ("document_id", "document-a", "document-b"),
        ("document_version_id", "version-a", "version-b"),
        ("processing_generation", 1, 99),
        ("job_id", "job-a", "job-b"),
        ("actor_id", "actor-a", "actor-b"),
        ("timestamp", "2026-07-23T00:00:00Z", "2026-07-24T00:00:00Z"),
        ("created_at", "before", "after"),
        ("updated_at", "before", "after"),
        ("acl", {"project": "a"}, {"project": "b"}),
        ("tags", ["alpha"], ["beta"]),
        ("display_metadata", {"title": "A"}, {"title": "B"}),
    ],
)
def test_fingerprint_excludes_binding_and_execution_metadata(
    field: str,
    first: object,
    second: object,
) -> None:
    left = {**_configuration(), field: first}
    right = {**_configuration(), field: second}

    assert processing_fingerprint(left) == processing_fingerprint(right)
    assert field not in canonical_processing_spec(left)


def test_processing_spec_is_closed_bounded_and_canonical() -> None:
    configuration = _configuration()
    reordered = dict(reversed(list(configuration.items())))

    assert processing_fingerprint(reordered) == processing_fingerprint(configuration)
    assert processing_identity_spec_payload(configuration) == canonical_processing_spec(
        configuration
    )
    with pytest.raises(ValueError, match="processing_spec_unknown_fields"):
        canonical_processing_spec({**configuration, "tenant_id": "tenant-a"})
    with pytest.raises(ValueError, match="processing_spec_unknown_fields"):
        canonical_processing_spec(
            {
                **configuration,
                "navigation": {"contract": "document-navigation-map-v1"},
            }
        )
    with pytest.raises(ValueError, match="processing_spec_missing_fields"):
        canonical_processing_spec(
            {key: value for key, value in configuration.items() if key != "chunking"}
        )
    with pytest.raises(ValueError, match="processing_spec_string_too_large"):
        canonical_processing_spec(
            {**configuration, "parser": {"plugin": "x" * 4_097}}
        )


def test_fingerprint_recursively_excludes_binding_and_execution_metadata() -> None:
    left = _configuration()
    right = _configuration()
    left["parser"] = {
        **left["parser"],  # type: ignore[arg-type]
        "request": {
            "document_id": "document-a",
            "job_id": "job-a",
            "material_rule": "stable",
        },
    }
    right["parser"] = {
        **right["parser"],  # type: ignore[arg-type]
        "request": {
            "document_id": "document-b",
            "job_id": "job-b",
            "material_rule": "stable",
        },
    }

    assert processing_fingerprint(left) == processing_fingerprint(right)
    assert canonical_processing_spec(left)["parser"]["request"] == {
        "material_rule": "stable"
    }


def test_typed_identity_requires_matching_material_fingerprint_and_artifact_pin() -> None:
    configuration = _configuration()
    identity = ProcessingIdentity(
        processing_identity_id="identity-1",
        source_sha256="a" * 64,
        processing_fingerprint=processing_fingerprint(configuration),
        processing_spec=configuration,
        source_artifact_id="artifact-source",
        source_artifact_checksum_sha256="a" * 64,
        created_at="2026-07-23T00:00:00Z",
    )

    assert identity.processing_spec == canonical_processing_spec(configuration)
    assert processing_identity_record(processing_identity_row(identity)) == identity
    with pytest.raises(ValueError, match="processing_fingerprint_mismatch"):
        ProcessingIdentity(
            processing_identity_id="identity-1",
            source_sha256="a" * 64,
            processing_fingerprint="b" * 64,
            processing_spec=configuration,
            source_artifact_id="artifact-source",
            source_artifact_checksum_sha256="a" * 64,
            created_at="2026-07-23T00:00:00Z",
        )
    with pytest.raises(ValueError, match="source_artifact_checksum_mismatch"):
        ProcessingIdentity(
            processing_identity_id="identity-1",
            source_sha256="a" * 64,
            processing_fingerprint=processing_fingerprint(configuration),
            processing_spec=configuration,
            source_artifact_id="artifact-source",
            source_artifact_checksum_sha256="b" * 64,
            created_at="2026-07-23T00:00:00Z",
        )


def test_typed_revision_enforces_terminal_manifest_contract() -> None:
    ready = ProcessingRevision(
        processing_revision_id="revision-1",
        processing_identity_id="identity-1",
        revision_number=1,
        state="ready",
        manifest_digest="c" * 64,
        page_artifact_count=2,
        evidence_count=4,
        chunk_count=6,
        index_point_count=6,
        created_at="2026-07-23T00:00:00Z",
        finalized_at="2026-07-23T00:01:00Z",
    )

    assert ready.state == "ready"
    assert processing_revision_record(processing_revision_row(ready)) == ready
    with pytest.raises(ValueError, match="ready_revision_manifest_incomplete"):
        ProcessingRevision(
            processing_revision_id="revision-2",
            processing_identity_id="identity-1",
            revision_number=2,
            state="ready",
            created_at="2026-07-23T00:00:00Z",
            finalized_at="2026-07-23T00:01:00Z",
        )
    with pytest.raises(ValueError, match="non_ready_revision_manifest_not_allowed"):
        ProcessingRevision(
            processing_revision_id="revision-3",
            processing_identity_id="identity-1",
            revision_number=3,
            state="failed",
            manifest_digest="c" * 64,
            created_at="2026-07-23T00:00:00Z",
            finalized_at="2026-07-23T00:01:00Z",
        )


def _constraint_names(table: object, constraint_type: type) -> set[str | None]:
    return {
        constraint.name
        for constraint in table.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, constraint_type)
    }


def test_orm_schema_exposes_canonical_identity_revision_and_nullable_lineage() -> None:
    identity = AtlasProcessingIdentityRow.__table__
    revision = AtlasProcessingRevisionRow.__table__

    assert "uq_atlas_processing_identity_key" in _constraint_names(
        identity, UniqueConstraint
    )
    assert "fk_atlas_processing_identity_current_revision" in _constraint_names(
        identity, ForeignKeyConstraint
    )
    assert "uq_atlas_processing_revision_number" in _constraint_names(
        revision, UniqueConstraint
    )
    assert "ck_atlas_processing_revision_terminal_metadata" in _constraint_names(
        revision, CheckConstraint
    )
    assert any(
        isinstance(item, Index)
        and item.name == "ux_atlas_processing_revision_building"
        and item.unique
        for item in revision.indexes
    )

    lineage_tables = (
        AtlasDocumentRow.__table__,
        async_processing.AtlasProcessingJobRow.__table__,
        async_processing.AtlasIndexGenerationRow.__table__,
        AtlasEvidenceRow.__table__,
        AtlasEvidencePageArtifactRow.__table__,
        async_processing.AtlasSearchChunkRow.__table__,
    )
    for table in lineage_tables:
        column = (
            table.c.processing_identity_id
            if table.name == "atlas_documents"
            else table.c.processing_revision_id
        )
        assert column.nullable is True
        assert column.foreign_keys

    assert any(
        index.name == "ux_atlas_processing_job_active_identity" and index.unique
        for index in async_processing.AtlasProcessingJobRow.__table__.indexes
    )
    assert "fk_atlas_processing_job_revision_identity" in _constraint_names(
        async_processing.AtlasProcessingJobRow.__table__, ForeignKeyConstraint
    )
    assert "uq_atlas_index_generation_processing_revision" in _constraint_names(
        async_processing.AtlasIndexGenerationRow.__table__, UniqueConstraint
    )


def test_development_baseline_contains_canonical_constraints_and_immutability() -> None:
    baseline = importlib.import_module(
        "atlas_production.migrations.versions.20260711_0001_development_baseline"
    )
    source = Path(baseline.__file__).read_text(encoding="utf-8")

    for marker in (
        "atlas_processing_identities",
        "atlas_processing_revisions",
        "uq_atlas_processing_identity_key",
        "ux_atlas_processing_revision_building",
        "ux_atlas_processing_job_active_identity",
        "atlas_processing_revision_immutable",
        "atlas_processing_identity_immutable",
        "current processing revision cannot be cleared",
        "fk_atlas_processing_identity_source_artifact",
        "fk_atlas_processing_identity_current_revision",
    ):
        assert marker in source
