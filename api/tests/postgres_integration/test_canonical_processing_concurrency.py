from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from threading import Barrier

import pytest
from sqlalchemy import delete, select

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingJobRow,
    AtlasTaskOutboxRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    _JobTransitionSql,
)
from atlas_production.modules.processing_pipeline.public import (
    ProcessingExecutionSnapshot,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from test_document_processing_generation_concurrency import (
    DOCUMENT_ID,
    DOCUMENT_VERSION_ID,
    SOURCE_DIGEST,
    _delete_fixture,
    _seed_current_source,
)

SECOND_DOCUMENT_ID = f"{DOCUMENT_ID}-second"
SECOND_DOCUMENT_VERSION_ID = f"{DOCUMENT_VERSION_ID}-second"


def _snapshot(*, output_contract: str = "eir-draft-v1") -> ProcessingExecutionSnapshot:
    profile = {
        "profile_id": "canonical-concurrency-profile",
        "revision": 1,
        "status": "active",
        "accepted_media_types": ["text/plain"],
        "base_parser_plugin_ref": {
            "plugin_id": "atlas-plain-text",
            "plugin_version": "1.0.0",
            "package_digest": "platform-builtin:atlas-plain-text:1.0.0",
            "runtime_profile": "atlas-python-v1",
        },
        "mandatory_processor_plugin_refs": [],
        "eligible_processor_plugin_refs": [],
        "plugin_priority": [],
        "planner_enabled": False,
        "planner_model_route_id": None,
        "channel_registry_version": "kpel-registry-v0.1",
        "trait_registry_version": "kpel-registry-v0.1",
        "max_regions_per_plan": 100,
        "max_modules_per_region": 4,
        "max_total_plugin_invocations": 500,
        "planner_failure_behavior": "mandatory_only",
    }
    version = {
        "plugin_id": "atlas-plain-text",
        "plugin_version": "1.0.0",
        "package_digest": "platform-builtin:atlas-plain-text:1.0.0",
        "runtime_profile": "atlas-python-v1",
        "plugin_kind": "base_parser",
        "status": "verified",
        "descriptor": {
            "entrypoint": "atlas_plugin_runner.builtin_plugins:InlineTextPlugin",
            "output_contract_version": output_contract,
        },
    }
    runtime = {
        "runtime_profile_id": "atlas-python-v1",
        "available_packages": {"pypdf": "6.0.0"},
    }
    return ProcessingExecutionSnapshot(
        profile_id=profile["profile_id"],
        profile_revision=profile["revision"],
        profile_snapshot=profile,
        plugin_versions=(version,),
        plugin_packages=(),
        runtime_profiles=(runtime,),
        acceptance_request_digest="a" * 64,
    )


def _delete_canonical_rows(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session:
        identities = tuple(
            session.scalars(
                select(AtlasProcessingIdentityRow.processing_identity_id).where(
                    AtlasProcessingIdentityRow.source_sha256 == SOURCE_DIGEST
                )
            ).all()
        )
        if identities:
            identity_rows = session.scalars(
                select(AtlasProcessingIdentityRow).where(
                    AtlasProcessingIdentityRow.processing_identity_id.in_(identities)
                )
            ).all()
            if any(identity.current_revision_id is not None for identity in identity_rows):
                raise RuntimeError(
                    "canonical fixture cleanup requires a fresh dedicated database"
                )
            job_ids = tuple(
                session.scalars(
                    select(AtlasProcessingJobRow.job_id).where(
                        AtlasProcessingJobRow.processing_identity_id.in_(identities)
                    )
                ).all()
            )
            if job_ids:
                session.execute(
                    delete(AtlasTaskOutboxRow).where(
                        AtlasTaskOutboxRow.payload["job_id"].as_string().in_(job_ids)
                    )
                )
                session.execute(
                    delete(AtlasProcessingJobRow).where(
                        AtlasProcessingJobRow.job_id.in_(job_ids)
                    )
                )
            session.execute(
                delete(AtlasIndexGenerationRow).where(
                    AtlasIndexGenerationRow.processing_revision_id.in_(
                        select(
                            AtlasProcessingRevisionRow.processing_revision_id
                        ).where(
                            AtlasProcessingRevisionRow.processing_identity_id.in_(
                                identities
                            )
                        )
                    )
                )
            )
            session.execute(
                delete(AtlasProcessingRevisionRow).where(
                    AtlasProcessingRevisionRow.processing_identity_id.in_(identities)
                )
            )
            for document in session.scalars(
                select(AtlasDocumentRow).where(
                    AtlasDocumentRow.processing_identity_id.in_(identities)
                )
            ).all():
                document.processing_identity_id = None
            session.flush()
            session.execute(
                delete(AtlasProcessingIdentityRow).where(
                    AtlasProcessingIdentityRow.processing_identity_id.in_(identities)
                )
            )
        session.commit()


def _cleanup(runtime: PostgresRuntime, previous_control: dict | None) -> None:
    _delete_canonical_rows(runtime)
    with runtime.session_factory() as session:
        session.execute(
            delete(AtlasDocumentVersionRow).where(
                AtlasDocumentVersionRow.document_id == SECOND_DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasDocumentRow).where(
                AtlasDocumentRow.document_id == SECOND_DOCUMENT_ID
            )
        )
        session.commit()
    _delete_fixture(runtime, restore_control=previous_control)


def test_concurrent_same_material_processing_joins_one_build(
    postgres_runtime: PostgresRuntime,
) -> None:
    _delete_canonical_rows(postgres_runtime)
    previous_control = _seed_current_source(postgres_runtime)
    with postgres_runtime.session_factory() as session:
        source = session.get(AtlasDocumentRow, DOCUMENT_ID)
        version = session.get(AtlasDocumentVersionRow, DOCUMENT_VERSION_ID)
        assert source is not None
        assert version is not None
        document_values = {
            column.name: getattr(source, column.name)
            for column in AtlasDocumentRow.__table__.columns
        }
        document_values.update(
            document_id=SECOND_DOCUMENT_ID,
            processing_identity_id=None,
            processing_job_id=None,
        )
        version_payload = deepcopy(version.payload)
        version_payload.update(
            document_id=SECOND_DOCUMENT_ID,
            document_version_id=SECOND_DOCUMENT_VERSION_ID,
        )
        session.add(AtlasDocumentRow(**document_values))
        session.add(
            AtlasDocumentVersionRow(
                document_version_id=SECOND_DOCUMENT_VERSION_ID,
                document_id=SECOND_DOCUMENT_ID,
                payload=version_payload,
            )
        )
        session.commit()
    barrier = Barrier(2)

    def accept(ordinal: int):
        current_document_id = (
            DOCUMENT_ID if ordinal == 1 else SECOND_DOCUMENT_ID
        )
        current_version_id = (
            DOCUMENT_VERSION_ID
            if ordinal == 1
            else SECOND_DOCUMENT_VERSION_ID
        )
        barrier.wait()
        return _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=current_document_id,
            document_version_id=current_version_id,
            job_kind="ingest",
            idempotency_scope="canonical-concurrency",
            idempotency_key=f"request-{ordinal}",
            created_by=f"user-{ordinal}",
            execution_snapshot=_snapshot(),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(accept, (1, 2)))
        assert results[0] is not None
        assert results[1] is not None
        assert results[0].job_id == results[1].job_id
        assert results[0].processing_identity_id == results[1].processing_identity_id
        assert results[0].processing_revision_id == results[1].processing_revision_id
        replay_second = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=SECOND_DOCUMENT_ID,
            document_version_id=SECOND_DOCUMENT_VERSION_ID,
            job_kind="ingest",
            idempotency_scope="canonical-concurrency",
            idempotency_key="request-2",
            created_by="user-2",
            execution_snapshot=_snapshot(),
        )
        assert replay_second is not None
        assert replay_second.job_id == results[0].job_id

        with postgres_runtime.session_factory() as session:
            assert len(
                session.scalars(
                    select(AtlasProcessingIdentityRow).where(
                        AtlasProcessingIdentityRow.source_sha256 == SOURCE_DIGEST
                    )
                ).all()
            ) == 1
            documents = session.scalars(
                select(AtlasDocumentRow).where(
                    AtlasDocumentRow.document_id.in_(
                        (DOCUMENT_ID, SECOND_DOCUMENT_ID)
                    )
                )
            ).all()
            assert {row.processing_identity_id for row in documents} == {
                results[0].processing_identity_id
            }
            assert {row.processing_job_id for row in documents} == {
                results[0].job_id
            }
            assert len(
                session.scalars(
                    select(AtlasProcessingRevisionRow).where(
                        AtlasProcessingRevisionRow.processing_identity_id
                        == results[0].processing_identity_id
                    )
                ).all()
            ) == 1
            assert len(
                session.scalars(
                    select(AtlasProcessingJobRow).where(
                        AtlasProcessingJobRow.processing_identity_id
                        == results[0].processing_identity_id
                    )
                ).all()
            ) == 1
            assert len(
                session.scalars(
                    select(AtlasIndexGenerationRow).where(
                        AtlasIndexGenerationRow.processing_revision_id
                        == results[0].processing_revision_id
                    )
                ).all()
            ) == 1
            assert len(
                session.scalars(
                    select(AtlasTaskOutboxRow).where(
                        AtlasTaskOutboxRow.payload["job_id"].as_string()
                        == results[0].job_id
                    )
                ).all()
            ) == 1
    finally:
        _cleanup(postgres_runtime, previous_control)


def test_page_preparation_persists_job_revision_and_rejects_lineage_replay(
    postgres_runtime: PostgresRuntime,
) -> None:
    _delete_canonical_rows(postgres_runtime)
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    page_id: str | None = None
    try:
        job = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="page-preparation-lineage",
            idempotency_key="page-1",
            created_by="user-canonical-page-lineage",
            progress_total=1,
            execution_snapshot=_snapshot(),
        )
        assert job is not None
        assert job.processing_generation is not None
        assert job.processing_revision_id is not None
        batch_id = f"{job.job_id}:page:1"
        page_id = f"epa-{hashlib.sha256(batch_id.encode()).hexdigest()[:32]}"
        page_record = {
            "artifact_id": page_id,
            "tenant_id": "project-canonical-page-lineage",
            "document_version_id": DOCUMENT_VERSION_ID,
            "source_page_index": 0,
            "source_page_label": "1",
            "artifact_kind": "pdf_single_page",
            "artifact_digest": hashlib.sha256(b"prepared-page-1").hexdigest(),
            "content_length": 7,
            "storage_artifact_id": "artifact-prepared-page-1",
            "source_crop_box": [0.0, 0.0, 1.0, 1.0],
            "source_rotation": 0,
            "geometry_transform_version": "v1",
            "renderer_version": "v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "processing_generation": job.processing_generation,
        }

        with repository.preparation_execution(
            job.job_id,
            expected_attempt=job.attempt,
        ) as claimed:
            assert claimed is not None
            assert claimed.batch_claim_token is not None
            with repository.transaction() as connection:
                repository.finalize_document_page_preparation(
                    connection,
                    job_id=job.job_id,
                    expected_attempt=claimed.attempt,
                    claim_fence=claimed.fence,
                    claim_token=claimed.batch_claim_token,
                    page_record=page_record,
                )

            with postgres_runtime.session_factory() as session:
                persisted = session.get(AtlasEvidencePageArtifactRow, page_id)
                assert persisted is not None
                assert (
                    persisted.processing_revision_id
                    == job.processing_revision_id
                )
                prepared = repository.prepared_page_artifact(
                    job.job_id,
                    batch_id,
                )
                assert prepared["artifact_id"] == page_id
                assert (
                    prepared["storage_artifact_id"]
                    == page_record["storage_artifact_id"]
                )
                persisted.processing_revision_id = None
                session.commit()

            with pytest.raises(
                ValueError,
                match="document_page_preparation_identity_conflict",
            ):
                with repository.transaction() as connection:
                    repository.finalize_document_page_preparation(
                        connection,
                        job_id=job.job_id,
                        expected_attempt=claimed.attempt,
                        claim_fence=claimed.fence,
                        claim_token=claimed.batch_claim_token,
                        page_record=page_record,
                    )

        with pytest.raises(ValueError, match="document_page_source_invalid"):
            repository.prepared_page_artifact(job.job_id, batch_id)
    finally:
        if page_id is not None:
            with postgres_runtime.session_factory() as session:
                session.execute(
                    delete(AtlasEvidencePageArtifactRow).where(
                        AtlasEvidencePageArtifactRow.id == page_id
                    )
                )
                session.commit()
        _cleanup(postgres_runtime, previous_control)


def test_material_fingerprint_change_allocates_separate_identity(
    postgres_runtime: PostgresRuntime,
) -> None:
    _delete_canonical_rows(postgres_runtime)
    previous_control = _seed_current_source(postgres_runtime)
    try:
        first = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="ingest",
            idempotency_scope="canonical-fingerprint",
            idempotency_key="first",
            created_by="user-1",
            execution_snapshot=_snapshot(),
        )
        assert first is not None
        with postgres_runtime.session_factory() as session:
            document = session.get(AtlasDocumentRow, DOCUMENT_ID)
            assert document is not None
            document.processing_identity_id = None
            session.commit()
        changed = _snapshot(output_contract="eir-v2")
        second = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="ingest",
            idempotency_scope="canonical-fingerprint",
            idempotency_key="second",
            created_by="user-1",
            execution_snapshot=deepcopy(changed),
        )
        assert second is not None
        assert first.processing_identity_id != second.processing_identity_id
    finally:
        _cleanup(postgres_runtime, previous_control)


def test_sequential_current_hit_creates_no_second_job_or_outbox(
    postgres_runtime: PostgresRuntime,
) -> None:
    _delete_canonical_rows(postgres_runtime)
    previous_control = _seed_current_source(postgres_runtime)
    try:
        first = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="ingest",
            idempotency_scope="canonical-current-hit",
            idempotency_key="first",
            created_by="user-1",
            execution_snapshot=_snapshot(),
        )
        assert first is not None
        with postgres_runtime.session_factory() as session:
            revision = session.get(
                AtlasProcessingRevisionRow,
                first.processing_revision_id,
            )
            identity = session.get(
                AtlasProcessingIdentityRow,
                first.processing_identity_id,
            )
            job = session.get(AtlasProcessingJobRow, first.job_id)
            assert revision is not None
            assert identity is not None
            assert job is not None
            revision.state = "ready"
            revision.manifest_digest = "f" * 64
            revision.page_artifact_count = 1
            revision.evidence_count = 1
            revision.chunk_count = 1
            revision.index_point_count = 1
            revision.finalized_at = "2026-07-23T00:00:00+00:00"
            job.status = "succeeded"
            job.stage = "completed"
            job.updated_at = datetime.now(timezone.utc)
            session.flush()
            identity.current_revision_id = revision.processing_revision_id
            session.commit()

        current_hit = _JobTransitionSql(
            postgres_runtime.session_factory
        ).create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="ingest",
            idempotency_scope="canonical-current-hit",
            idempotency_key="second",
            created_by="user-2",
            execution_snapshot=_snapshot(),
        )
        assert current_hit is None
        with postgres_runtime.session_factory() as session:
            jobs = session.scalars(
                select(AtlasProcessingJobRow).where(
                    AtlasProcessingJobRow.processing_identity_id
                    == first.processing_identity_id
                )
            ).all()
            outbox = session.scalars(
                select(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.payload["job_id"].as_string()
                    == first.job_id
                )
            ).all()
            document = session.get(AtlasDocumentRow, DOCUMENT_ID)
            assert len(jobs) == 1
            assert len(outbox) == 1
            assert document is not None
            assert document.intake_status == "ready"
            assert document.processing_job_id is None
    finally:
        # A published identity is intentionally immutable. The dedicated
        # database runner restores the baseline schema after this file.
        pass
