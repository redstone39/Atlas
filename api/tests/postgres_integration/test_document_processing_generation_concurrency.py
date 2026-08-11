from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier, Event, current_thread
from typing import Any

import pytest
from sqlalchemy import delete, func, literal_column, or_, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import (
    artifact_storage as artifact_rows,
)
from atlas_production.infrastructure.persistence import (
    processing_pipeline as processing_rows,
)
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasArtifactScopeBindingRow,
    AtlasArtifactStorageControlRow,
    AtlasArtifactStorageTargetRow,
    AtlasArtifactWriteAttemptRow,
    AtlasStorageBlobRow,
)

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingBatchClaimRow,
    AtlasProcessingGenerationRetentionEntryRow,
    AtlasProcessingGenerationRetentionRow,
    AtlasProcessingCheckpointRow,
    AtlasProcessingGenerationRow,
    AtlasProcessingJobRow,
    AtlasProcessingRequestSnapshotRow,
    AtlasSearchChunkRow,
    AtlasTaskOutboxRow,
    AtlasVectorPointMappingRow,
)
from atlas_production.infrastructure.postgres_owner.generation_retention import (
    GenerationRetentionConflict,
    PostgresGenerationRetentionOwner,
)
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
    _document_row,
    _document_version_payload,
)
from atlas_production.infrastructure.postgres_owner import (
    document_processing as document_processing_owner,
)
from atlas_production.modules.processing_pipeline.public import (
    DocumentProcessingCurrentnessConflict,
)
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendCatalogDocument,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    PostgresProductionKnowledgeRowSource,
    ProductionKnowledgeRetrievalBackend,
)
from atlas_production.modules.artifact_storage.records import (
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageFence,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.processing_pipeline.records import (
    EvidencePageArtifact,
    EvidenceRecord,
)
from atlas_production.modules.processing_pipeline.public import (
    CreateGenerationRetentionV1,
    GenerationRetentionResourceV1,
    ReleaseGenerationRetentionV1,
)


DOCUMENT_ID = "document-c3-generation-concurrency"
DOCUMENT_VERSION_ID = "version-c3-generation-concurrency"
SOURCE_INDEX_GENERATION_ID = "index-c3-generation-concurrency-source"
PROJECT_ID = "project-c3-generation-concurrency"
INHERITED_TEAM_ID = "team-c3-generation-concurrency"
TARGET_ID = "target-c3-generation-concurrency"
SOURCE_ARTIFACT_ID = "artifact-c3-generation-concurrency-source"
SOURCE_ATTEMPT_ID = "attempt-c3-generation-concurrency-source"
SOURCE_BLOB_ID = "blob-c3-generation-concurrency-source"
SOURCE_PROCESSING_IDENTITY_ID = "identity-c3-generation-concurrency-source"
SOURCE_PROCESSING_REVISION_ID = "revision-c3-generation-concurrency-source"
ROOT_DIGEST = "1" * 64
SOURCE_DIGEST = "2" * 64
NOW = "2026-07-17T00:00:00+00:00"
FENCE = StorageFence(TARGET_ID, 1, ROOT_DIGEST, 2)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _fixture_outbox_record(
    *,
    task_name: str,
    queue_name: str,
    payload: dict[str, object],
    available_at: datetime,
    last_error_code: str | None = None,
    identity_salt: str | None = None,
) -> document_processing_owner.TaskOutboxRecord:
    identity_payload: dict[str, object] = {
        "task_name": task_name,
        "queue_name": queue_name,
        "payload": payload,
    }
    if identity_salt is not None:
        identity_payload["identity_salt"] = identity_salt
    identity = hashlib.sha256(
        json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return document_processing_owner.TaskOutboxRecord(
        outbox_id=f"outbox-{identity[:32]}",
        task_name=task_name,
        queue_name=queue_name,
        payload_schema_version=1,
        payload=payload,
        celery_task_id=f"task-{identity}",
        status="pending",
        claim_owner=None,
        claim_expires_at=None,
        attempts=0,
        available_at=available_at,
        last_error_code=last_error_code,
        created_at=available_at,
        dispatched_at=None,
    )


def _artifact_records(
    *,
    artifact_id: str,
    attempt_id: str,
    blob_id: str,
    artifact_class: str,
    logical_identity: str,
    checksum: str,
    byte_size: int,
    content_type: str,
    processing_generation: int | None,
    source_artifact_id: str | None,
    page_number: int | None,
) -> tuple[object, ...]:
    authorization = [["team", INHERITED_TEAM_ID]]
    attempt = ArtifactWriteAttemptRecord(
        write_attempt_id=attempt_id,
        idempotency_scope=f"document:{DOCUMENT_ID}",
        idempotency_key=f"fixture:{artifact_id}",
        request_fingerprint=_digest(f"request:{artifact_id}"),
        fence=FENCE,
        parent_resource_id=DOCUMENT_ID,
        parent_lifecycle_epoch=0,
        status="succeeded",
        lease_owner="worker-c3-generation-concurrency",
        lease_expires_at=NOW,
        attempt_generation=1,
        last_heartbeat_at=NOW,
        opaque_temp_name=f"opaque-temp/{artifact_id}",
        created_at=NOW,
        updated_at=NOW,
        intent={
            "artifact_class": artifact_class,
            "logical_identity": logical_identity,
            "content_type": content_type,
            "owner_scope_type": "project",
            "owner_scope_id": PROJECT_ID,
            "document_version_id": DOCUMENT_VERSION_ID,
            "source_artifact_id": source_artifact_id,
            "processing_generation": processing_generation,
            "pipeline_id": None,
            "pipeline_version": None,
            "generation": processing_generation if page_number is not None else None,
            "page_number": page_number,
            "block_id": None,
            "acl_policy_version": None,
            "acl_action": None,
            "authorization_bindings": authorization,
            "allowed_parent_statuses": ["active"],
        },
        blob_id=blob_id,
        byte_size=byte_size,
        checksum_sha256=checksum,
    )
    blob = StorageBlobRecord(
        blob_id=blob_id,
        opaque_ref=f"opaque/{blob_id}",
        status="committed",
        dedup_mode="none",
        checksum_algorithm="sha256",
        checksum_value=checksum,
        byte_size=byte_size,
        content_type=content_type,
        fence=FENCE,
        created_at=NOW,
        updated_at=NOW,
        write_attempt_id=attempt_id,
        committed_at=NOW,
    )
    artifact = ArtifactRecord(
        artifact_id=artifact_id,
        artifact_class=artifact_class,
        blob_id=blob_id,
        checksum_algorithm="sha256",
        checksum_value=checksum,
        byte_size=byte_size,
        content_type=content_type,
        owner_scope_type="project",
        owner_scope_id=PROJECT_ID,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
        logical_identity=logical_identity,
        source_artifact_id=source_artifact_id,
        document_version_id=DOCUMENT_VERSION_ID,
        parent_resource_id=DOCUMENT_ID,
        parent_lifecycle_epoch=0,
        processing_generation=processing_generation,
        generation=processing_generation if page_number is not None else None,
        page_number=page_number,
    )
    owner = ArtifactScopeBindingRecord(
        binding_id=f"binding-owner-{artifact_id}", artifact_id=artifact_id,
        binding_kind="owner", scope_type="project", scope_id=PROJECT_ID,
        created_at=NOW,
    )
    auth = ArtifactScopeBindingRecord(
        binding_id=f"binding-authorization-{artifact_id}", artifact_id=artifact_id,
        binding_kind="authorization", scope_type="team", scope_id=INHERITED_TEAM_ID,
        created_at=NOW,
    )
    return attempt, blob, artifact, owner, auth


def _add_artifact_graph(session: Session, records: tuple[object, ...]) -> None:
    attempt, blob, artifact, owner, authorization = records
    session.add(AtlasArtifactWriteAttemptRow(**artifact_rows._attempt_payload(attempt)))
    session.add(AtlasStorageBlobRow(**artifact_rows._flatten(blob)))
    session.flush()
    session.add(AtlasArtifactRow(**artifact_rows._artifact_payload(artifact)))
    session.flush()
    session.add(AtlasArtifactScopeBindingRow(**asdict(owner)))
    session.add(AtlasArtifactScopeBindingRow(**asdict(authorization)))


def _delete_fixture(
    runtime: PostgresRuntime,
    *,
    restore_control: dict[str, object] | None = None,
) -> None:
    with runtime.session_factory() as session:
        identity = session.scalar(
            select(processing_rows.AtlasProcessingIdentityRow).where(
                processing_rows.AtlasProcessingIdentityRow.source_artifact_id
                == SOURCE_ARTIFACT_ID
            )
        )
        if identity is not None:
            # Processing identities retain the source artifact and published
            # identities are immutable. The PostgreSQL runner gives every
            # collected node a fresh disposable schema, so database-level
            # disposal is the supported cleanup once an identity exists.
            return
    with runtime.session_factory() as session:
        job_ids = tuple(
            session.scalars(
                select(AtlasProcessingJobRow.job_id).where(
                    AtlasProcessingJobRow.document_id == DOCUMENT_ID
                )
            ).all()
        )
        if job_ids:
            session.execute(
                delete(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.payload["job_id"].as_string().in_(job_ids)
                )
            )
        audit_scope = AtlasAuditEventRow.document_id == DOCUMENT_ID
        if job_ids:
            audit_scope = or_(
                audit_scope,
                AtlasAuditEventRow.event_metadata["job_id"]
                .as_string()
                .in_(job_ids),
            )
        session.execute(delete(AtlasAuditEventRow).where(audit_scope))
        session.execute(
            delete(AtlasVectorPointMappingRow).where(
                AtlasVectorPointMappingRow.index_generation_id.in_(
                    select(AtlasIndexGenerationRow.index_generation_id).where(
                        AtlasIndexGenerationRow.document_id == DOCUMENT_ID
                    )
                )
            )
        )
        session.execute(
            delete(AtlasSearchChunkRow).where(
                AtlasSearchChunkRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(processing_rows.AtlasEvidencePageArtifactRow).where(
                processing_rows.AtlasEvidencePageArtifactRow.document_version_id
                == DOCUMENT_VERSION_ID
            )
        )
        session.execute(
            delete(processing_rows.AtlasEvidenceRow).where(
                processing_rows.AtlasEvidenceRow.document_id == DOCUMENT_ID
            )
        )
        if job_ids:
            session.execute(
                delete(AtlasProcessingCheckpointRow).where(
                    AtlasProcessingCheckpointRow.job_id.in_(job_ids)
                )
            )
        session.execute(
            delete(AtlasProcessingJobRow).where(
                AtlasProcessingJobRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasProcessingGenerationRetentionEntryRow).where(
                AtlasProcessingGenerationRetentionEntryRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasProcessingGenerationRetentionRow).where(
                ~select(AtlasProcessingGenerationRetentionEntryRow.retention_ref)
                .where(
                    AtlasProcessingGenerationRetentionEntryRow.retention_ref
                    == AtlasProcessingGenerationRetentionRow.retention_ref
                )
                .exists()
            )
        )
        session.execute(
            delete(AtlasIndexGenerationRow).where(
                AtlasIndexGenerationRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasProcessingGenerationRow).where(
                AtlasProcessingGenerationRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasDocumentVersionRow).where(
                AtlasDocumentVersionRow.document_version_id == DOCUMENT_VERSION_ID
            )
        )
        session.execute(
            delete(AtlasDocumentRow).where(
                AtlasDocumentRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(processing_rows.AtlasProcessingRevisionRow).where(
                processing_rows.AtlasProcessingRevisionRow.processing_identity_id
                == SOURCE_PROCESSING_IDENTITY_ID
            )
        )
        session.execute(
            delete(processing_rows.AtlasProcessingIdentityRow).where(
                processing_rows.AtlasProcessingIdentityRow.processing_identity_id
                == SOURCE_PROCESSING_IDENTITY_ID
            )
        )
        session.execute(
            delete(AtlasArtifactScopeBindingRow).where(
                AtlasArtifactScopeBindingRow.artifact_id.in_(
                    select(AtlasArtifactRow.artifact_id).where(
                        AtlasArtifactRow.parent_resource_id == DOCUMENT_ID
                    )
                )
            )
        )
        session.execute(
            delete(AtlasArtifactRow).where(
                AtlasArtifactRow.parent_resource_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasStorageBlobRow).where(
                AtlasStorageBlobRow.write_attempt_id.in_(
                    select(AtlasArtifactWriteAttemptRow.write_attempt_id).where(
                        AtlasArtifactWriteAttemptRow.parent_resource_id == DOCUMENT_ID
                    )
                )
            )
        )
        session.execute(
            delete(AtlasArtifactWriteAttemptRow).where(
                AtlasArtifactWriteAttemptRow.parent_resource_id == DOCUMENT_ID
            )
        )
        control = session.get(AtlasArtifactStorageControlRow, "global")
        if control is not None and control.active_target_id == TARGET_ID:
            session.delete(control)
            session.flush()
        session.execute(
            delete(AtlasArtifactStorageTargetRow).where(
                AtlasArtifactStorageTargetRow.target_id == TARGET_ID
            )
        )
        if restore_control is not None:
            session.add(AtlasArtifactStorageControlRow(**restore_control))
        session.commit()


def test_processing_owned_generation_retention_fences_cleanup_until_release(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_active_source(postgres_runtime)
    retention = PostgresGenerationRetentionOwner(postgres_runtime.session_factory)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        claim = retention.create_generation_retention(
            CreateGenerationRetentionV1(
                execution_id="execution-generation-retention",
                resources=[
                    GenerationRetentionResourceV1(
                        document_version_ref=DOCUMENT_VERSION_ID,
                        processing_generation_ref="processing-generation-1",
                        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                        manifest_digest="f" * 64,
                    )
                ],
                idempotency_key="generation-retention-create",
            )
        )
        assert retention.create_generation_retention(
            CreateGenerationRetentionV1(
                execution_id="execution-generation-retention",
                resources=[
                    GenerationRetentionResourceV1(
                        document_version_ref=DOCUMENT_VERSION_ID,
                        processing_generation_ref="processing-generation-1",
                        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                        manifest_digest="f" * 64,
                    )
                ],
                idempotency_key="generation-retention-create",
            )
        ) == claim

        with postgres_runtime.session_factory() as session, session.begin():
            generation = session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID)
            processing = session.get(AtlasProcessingGenerationRow, (DOCUMENT_ID, 1))
            document = session.get(AtlasDocumentRow, DOCUMENT_ID)
            assert generation is not None
            assert processing is not None
            assert document is not None
            generation.status = "retired"
            processing.status = "retired"
            document.active_processing_generation = 2
            document.active_index_generation_id = "new-current-index-generation"
            content_fingerprint = _digest("retained-content")
            processing_fingerprint = _digest("retained-processing")
            session.add(
                processing_rows.AtlasEvidenceRow(
                    evidence_id="evidence-generation-retention",
                    document_id=DOCUMENT_ID,
                    document_title="Generation concurrency",
                    locator_label="Page 1",
                    snippet="retained snapshot evidence",
                    content="retained snapshot evidence remains searchable",
                        document_version_id=DOCUMENT_VERSION_ID,
                        processing_generation=1,
                        processing_revision_id=SOURCE_PROCESSING_REVISION_ID,
                    status="ready",
                    source_region_id="region-generation-retention",
                    channel_id="generic_text",
                    output_contract_version="eir-draft-v1",
                    claim_support_role="claim_grounding",
                    locator_payload={"page_number": 1},
                    content_fingerprint=content_fingerprint,
                    processing_fingerprint=processing_fingerprint,
                    profile_id="source-profile",
                    profile_revision=1,
                    quality_flag_refs=[],
                )
            )
            session.add(
                AtlasSearchChunkRow(
                    chunk_id="chunk-generation-retention",
                    batch_id="batch-generation-retention",
                    document_id=DOCUMENT_ID,
                        document_version_id=DOCUMENT_VERSION_ID,
                        processing_generation=1,
                        processing_revision_id=SOURCE_PROCESSING_REVISION_ID,
                    index_generation_id=SOURCE_INDEX_GENERATION_ID,
                    evidence_id="evidence-generation-retention",
                    segment_id="segment-generation-retention",
                    window_ordinal=0,
                    normalized_text="retained snapshot evidence remains searchable",
                    locator={"page_number": 1},
                    content_fingerprint=content_fingerprint,
                    processing_fingerprint=processing_fingerprint,
                    search_vector=None,
                    status="active",
                    created_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
                )
            )

        knowledge_rows = PostgresProductionKnowledgeRowSource(
            postgres_runtime.session_factory
        )
        backend = ProductionKnowledgeRetrievalBackend(knowledge_rows)
        retained = backend.search(
            documents=(
                BackendCatalogDocument(
                    document_handle="retained-document-handle",
                    lifecycle_epoch=1,
                    document_version_ref=DOCUMENT_VERSION_ID,
                    processing_generation_ref="processing-generation-1",
                        processing_revision_ref=SOURCE_PROCESSING_REVISION_ID,
                    index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                    manifest_digest="f" * 64,
                    descriptor={},
                ),
            ),
            query_text="retained snapshot",
            required_modalities=("text",),
            facet_hints={},
            limit=20,
        )
        assert [item.snippet for item in retained] == ["retained snapshot evidence"]
        protected = knowledge_rows.read_exact_citation_evidence(
            evidence_ref=retained[0].evidence_ref,
            document_version_ref=DOCUMENT_VERSION_ID,
            processing_generation_ref="processing-generation-1",
            index_generation_ref=SOURCE_INDEX_GENERATION_ID,
        )
        assert protected is not None
        assert protected.content == "retained snapshot evidence remains searchable"

        repository.cleanup_retired_generations(limit=10)
        with postgres_runtime.session_factory() as session:
            assert session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID) is not None

        release = ReleaseGenerationRetentionV1(
            execution_id="execution-generation-retention",
            retention_ref=claim.retention_ref,
            idempotency_key="generation-retention-release",
        )
        retention.release_generation_retention(release)
        retention.release_generation_retention(release)
        repository.cleanup_retired_generations(limit=10)
        with postgres_runtime.session_factory() as session:
            assert session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID) is None
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_generation_retention_concurrent_exact_create_replays_one_claim(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_active_source(postgres_runtime)
    owner = PostgresGenerationRetentionOwner(postgres_runtime.session_factory)
    command = CreateGenerationRetentionV1(
        execution_id="execution-generation-retention-concurrent-create",
        resources=[
            GenerationRetentionResourceV1(
                document_version_ref=DOCUMENT_VERSION_ID,
                processing_generation_ref="processing-generation-1",
                index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                manifest_digest="f" * 64,
            )
        ],
        idempotency_key="generation-retention-concurrent-create",
    )
    barrier = Barrier(2)

    def create():
        barrier.wait()
        return owner.create_generation_retention(command)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: create(), range(2)))
        assert results[0] == results[1]
        with postgres_runtime.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingGenerationRetentionRow).where(
                    AtlasProcessingGenerationRetentionRow.execution_id
                    == command.execution_id
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "active"
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_generation_retention_release_racing_create_never_leaves_active_orphan(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_active_source(postgres_runtime)
    owner = PostgresGenerationRetentionOwner(postgres_runtime.session_factory)
    execution_id = "execution-generation-retention-release-race"
    create_command = CreateGenerationRetentionV1(
        execution_id=execution_id,
        resources=[
            GenerationRetentionResourceV1(
                document_version_ref=DOCUMENT_VERSION_ID,
                processing_generation_ref="processing-generation-1",
                index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                manifest_digest="f" * 64,
            )
        ],
        idempotency_key="generation-retention-release-race-create",
    )
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return owner.create_generation_retention(create_command)
        except GenerationRetentionConflict:
            return None

    def release():
        barrier.wait()
        owner.release_execution_generation_retention(
            execution_id=execution_id,
            idempotency_key="generation-retention-release-race-release",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            create_future = executor.submit(create)
            release_future = executor.submit(release)
            create_future.result()
            release_future.result()
        with postgres_runtime.session_factory() as session:
            row = session.scalar(
                select(AtlasProcessingGenerationRetentionRow).where(
                    AtlasProcessingGenerationRetentionRow.execution_id == execution_id
                )
            )
            assert row is not None
            assert row.status == "released"
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def _seed_current_source(
    runtime: PostgresRuntime,
) -> dict[str, object] | None:
    _delete_fixture(runtime)
    previous_control: dict[str, object] | None = None
    document = DocumentRecord(
        document_id=DOCUMENT_ID,
        title="Generation concurrency",
        source_digest=SOURCE_DIGEST,
        searchable_projection="source",
        intake_status="ready",
        source_kind="inline_text",
        document_format="txt",
        content_type="text/plain",
        source_byte_size=6,
        uploader_actor_id="user-c3-generation-concurrency",
        scope_type="project",
        scope_id=PROJECT_ID,
        lifecycle_status="active",
        original_artifact_id=SOURCE_ARTIFACT_ID,
        raw_sha256=SOURCE_DIGEST,
        active_processing_generation=0,
    )
    version = DocumentVersionRecord(
        document_version_id=DOCUMENT_VERSION_ID,
        document_id=DOCUMENT_ID,
        title=document.title,
        source_kind=document.source_kind,
        document_format=document.document_format,
        source_digest=document.source_digest,
        content_digest=_digest("source"),
        created_at=NOW,
        status="active",
        original_artifact_id=SOURCE_ARTIFACT_ID,
        content_type=document.content_type,
    )
    with runtime.session_factory() as session:
        current = session.get(AtlasArtifactStorageControlRow, "global")
        if current is not None:
            if current.active_target_id != TARGET_ID:
                previous_control = {
                    column.name: getattr(current, column.name)
                    for column in AtlasArtifactStorageControlRow.__table__.columns
                }
            session.delete(current)
            session.flush()
        session.add(
            AtlasArtifactStorageTargetRow(
                target_id=TARGET_ID,
                target_revision=1,
                target_kind="local",
                masked_label="c3 generation concurrency",
                config_key="c3-generation-concurrency",
                root_identity_digest=ROOT_DIGEST,
                capabilities={
                    "create_file": True,
                    "modify_file": True,
                    "remove_file": True,
                },
                status="active",
                created_at=NOW,
                updated_at=NOW,
                created_by="test-c3-generation-concurrency",
                verification_mode="full_hash",
                evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
                failure_code=None,
                registration_idempotency_key=None,
                registration_request_fingerprint=None,
            )
        )
        session.flush()
        session.add(
            AtlasArtifactStorageControlRow(
                control_id="global",
                mode="active",
                active_target_id=TARGET_ID,
                active_target_revision=1,
                root_identity_digest=ROOT_DIGEST,
                storage_epoch=2,
                updated_at=NOW,
            )
        )
        _add_artifact_graph(
            session,
            _artifact_records(
                artifact_id=SOURCE_ARTIFACT_ID,
                attempt_id=SOURCE_ATTEMPT_ID,
                blob_id=SOURCE_BLOB_ID,
                artifact_class="original_inline_source",
                logical_identity=(
                    f"document:{DOCUMENT_ID}:{DOCUMENT_VERSION_ID}:original"
                ),
                checksum=SOURCE_DIGEST,
                byte_size=6,
                content_type="text/plain",
                processing_generation=None,
                source_artifact_id=None,
                page_number=None,
            ),
        )
        session.flush()
        session.add(_document_row(document))
        session.add(
            AtlasDocumentVersionRow(
                document_version_id=version.document_version_id,
                document_id=version.document_id,
                payload=_document_version_payload(version),
            )
        )
        session.commit()
    return previous_control


def _seed_active_source(runtime: PostgresRuntime) -> dict[str, object] | None:
    previous_control = _seed_current_source(runtime)
    created_at = datetime(2026, 7, 17, tzinfo=timezone.utc)
    with runtime.session_factory() as session:
        document = session.get(AtlasDocumentRow, DOCUMENT_ID)
        assert document is not None
        identity = processing_rows.AtlasProcessingIdentityRow(
            processing_identity_id=SOURCE_PROCESSING_IDENTITY_ID,
            source_sha256=SOURCE_DIGEST,
            processing_fingerprint="3" * 64,
            processing_spec={"contract": "generation-concurrency-source"},
            source_artifact_id=SOURCE_ARTIFACT_ID,
            source_artifact_checksum_sha256=SOURCE_DIGEST,
            current_revision_id=None,
            created_at=created_at.isoformat(),
        )
        session.add(identity)
        session.flush()
        session.add(
            processing_rows.AtlasProcessingRevisionRow(
                processing_revision_id=SOURCE_PROCESSING_REVISION_ID,
                processing_identity_id=SOURCE_PROCESSING_IDENTITY_ID,
                revision_number=1,
                state="ready",
                manifest_digest="f" * 64,
                page_artifact_count=1,
                evidence_count=1,
                chunk_count=1,
                index_point_count=1,
                created_at=created_at.isoformat(),
                finalized_at=created_at.isoformat(),
            )
        )
        session.flush()
        identity.current_revision_id = SOURCE_PROCESSING_REVISION_ID
        session.add(
            AtlasProcessingGenerationRow(
                document_id=DOCUMENT_ID,
                processing_generation=1,
                document_version_id=DOCUMENT_VERSION_ID,
                profile_id="source-profile",
                profile_revision=1,
                status="active",
                expected_page_count=1,
                actual_page_count=1,
                expected_evidence_count=1,
                actual_evidence_count=1,
                expected_chunk_count=1,
                actual_chunk_count=1,
                manifest_digest="f" * 64,
                created_at=created_at,
                published_at=created_at,
            )
        )
        session.add(
            AtlasIndexGenerationRow(
                index_generation_id=SOURCE_INDEX_GENERATION_ID,
                document_id=DOCUMENT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                processing_revision_id=SOURCE_PROCESSING_REVISION_ID,
                source_processing_generation=1,
                embedding_profile_id="source-embedding",
                embedding_profile={"model": "source"},
                qdrant_collection="atlas_evidence_v1",
                status="active",
                expected_point_count=1,
                actual_point_count=1,
                expected_fts_count=1,
                actual_fts_count=1,
                manifest_digest="f" * 64,
                supersedes_index_generation_id=None,
                created_at=created_at,
                published_at=created_at,
            )
        )
        session.flush()
        document.active_processing_generation = 1
        document.active_index_generation_id = SOURCE_INDEX_GENERATION_ID
        document.processing_identity_id = SOURCE_PROCESSING_IDENTITY_ID
        session.commit()
    return previous_control


def _prepare_competing_publications(
    runtime: PostgresRuntime,
    repository: PostgresDocumentProcessingAdapter,
    *,
    idempotency_scope: str = "c3-publication-race",
    ordinals: tuple[str, ...] = ("first", "second"),
) -> dict[str, str]:
    jobs = tuple(
        repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope=idempotency_scope,
            idempotency_key=ordinal,
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )
        for ordinal in ordinals
    )
    with runtime.session_factory() as session:
        for job_record in jobs:
            assert job_record.processing_generation is not None
            job = session.get(AtlasProcessingJobRow, job_record.job_id)
            index = session.get(
                AtlasIndexGenerationRow,
                job_record.index_generation_id,
            )
            generation = session.get(
                AtlasProcessingGenerationRow,
                (DOCUMENT_ID, job_record.processing_generation),
            )
            assert job is not None
            assert index is not None
            assert generation is not None
            suffix = job_record.job_id
            batch_id = f"{suffix}:page:1"
            evidence_id = f"evidence-{suffix}"
            chunk_id = f"chunk-{suffix}"
            page_id = f"page-{suffix}"
            storage_artifact_id = f"artifact-page-{suffix}"
            attempt_id = f"attempt-page-{suffix}"
            blob_id = f"blob-page-{suffix}"
            page_digest = _digest(f"page:{suffix}")
            content_fingerprint = _digest(f"content:{suffix}")
            processing_fingerprint = _digest(f"processing:{suffix}")
            processing_identity_id = SOURCE_PROCESSING_IDENTITY_ID
            processing_revision_id = f"revision-{suffix}"
            revision_number = int(
                session.scalar(
                    select(
                        func.max(
                            processing_rows.AtlasProcessingRevisionRow.revision_number
                        )
                    ).where(
                        processing_rows.AtlasProcessingRevisionRow.processing_identity_id
                        == processing_identity_id
                    )
                )
                or 0
            ) + 1
            session.add(
                processing_rows.AtlasProcessingRevisionRow(
                    processing_revision_id=processing_revision_id,
                    processing_identity_id=processing_identity_id,
                    revision_number=revision_number,
                    state="building",
                    manifest_digest=None,
                    page_artifact_count=None,
                    evidence_count=None,
                    chunk_count=None,
                    index_point_count=None,
                    created_at=NOW,
                    finalized_at=None,
                )
            )
            session.flush()
            job.processing_identity_id = processing_identity_id
            job.processing_revision_id = processing_revision_id
            index.processing_revision_id = processing_revision_id
            job.stage = "publishing"
            job.status = "running"
            job.progress_current = 1
            generation.profile_id = "c3-profile"
            generation.profile_revision = 1
            generation.expected_page_count = 1
            generation.actual_page_count = 1
            generation.expected_evidence_count = 1
            generation.actual_evidence_count = 1
            generation.expected_chunk_count = 1
            generation.actual_chunk_count = 1
            index.expected_point_count = 1
            index.actual_point_count = 1
            index.expected_fts_count = 1
            index.actual_fts_count = 1
            session.add(
                AtlasProcessingCheckpointRow(
                    job_id=job_record.job_id,
                    unit_kind="page",
                    unit_start=1,
                    unit_end=1,
                    batch_id=batch_id,
                    claim_token=f"claim-{suffix}",
                    fence=0,
                    input_fingerprint=_digest(f"input:{suffix}"),
                    output_digest=_digest(f"output:{suffix}"),
                    evidence_count=1,
                    chunk_count=1,
                    preview_count=1,
                    committed_at=job.created_at,
                )
            )
            evidence = EvidenceRecord(
                evidence_id=evidence_id,
                document_id=DOCUMENT_ID,
                document_title="Generation concurrency",
                locator_label="Page 1",
                snippet="publication race evidence",
                content="publication race evidence",
                document_version_id=DOCUMENT_VERSION_ID,
                processing_generation=job_record.processing_generation,
                status="staged",
                source_region_id=f"region-{suffix}",
                channel_id="generic_text",
                output_contract_version="eir-draft-v1",
                claim_support_role="claim_grounding",
                locator_payload={"page_number": 1},
                content_fingerprint=content_fingerprint,
                processing_fingerprint=processing_fingerprint,
                profile_id="c3-profile",
                profile_revision=1,
                quality_flag_refs=[],
            )
            evidence_payload = asdict(evidence)
            evidence_payload["processing_revision_id"] = processing_revision_id
            session.add(processing_rows.AtlasEvidenceRow(**evidence_payload))
            page = EvidencePageArtifact(
                artifact_id=page_id,
                tenant_id=PROJECT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                source_page_index=0,
                source_page_label="1",
                artifact_kind="pdf_single_page",
                artifact_digest=page_digest,
                content_length=7,
                storage_artifact_id=storage_artifact_id,
                source_crop_box=[0.0, 0.0, 1.0, 1.0],
                source_rotation=0,
                geometry_transform_version="v1",
                renderer_version="v1",
                created_at=NOW,
                processing_generation=job_record.processing_generation,
            )
            session.add(
                processing_rows.AtlasEvidencePageArtifactRow(
                    id=page.artifact_id,
                    tenant_id=page.tenant_id,
                    document_version_id=page.document_version_id,
                    source_page_index=page.source_page_index,
                    renderer_version=page.renderer_version,
                    processing_generation=page.processing_generation,
                    processing_revision_id=processing_revision_id,
                    payload=processing_rows.evidence_page_artifact_payload(page),
                )
            )
            _add_artifact_graph(
                session,
                _artifact_records(
                    artifact_id=storage_artifact_id,
                    attempt_id=attempt_id,
                    blob_id=blob_id,
                    artifact_class="document_page_pdf",
                    logical_identity=(
                        f"document:{DOCUMENT_ID}:{DOCUMENT_VERSION_ID}:"
                        f"generation:{job_record.processing_generation}:page:1"
                    ),
                    checksum=page_digest,
                    byte_size=7,
                    content_type="application/pdf",
                    processing_generation=job_record.processing_generation,
                    source_artifact_id=SOURCE_ARTIFACT_ID,
                    page_number=1,
                ),
            )
            session.add(
                AtlasSearchChunkRow(
                    chunk_id=chunk_id,
                    batch_id=batch_id,
                    document_id=DOCUMENT_ID,
                    document_version_id=DOCUMENT_VERSION_ID,
                    processing_generation=job_record.processing_generation,
                    processing_revision_id=processing_revision_id,
                    index_generation_id=job_record.index_generation_id,
                    evidence_id=evidence_id,
                    segment_id=f"segment-{suffix}",
                    window_ordinal=0,
                    normalized_text="publication race evidence",
                    locator={"page_number": 1},
                    content_fingerprint=content_fingerprint,
                    processing_fingerprint=processing_fingerprint,
                    search_vector=None,
                    status="staged",
                    created_at=job.created_at,
                )
            )
            session.add(
                AtlasVectorPointMappingRow(
                    index_generation_id=job_record.index_generation_id,
                    point_id=f"point-{suffix}",
                    chunk_id=chunk_id,
                    payload_digest=_digest(f"payload:{suffix}"),
                    vector_digest=_digest(f"vector:{suffix}"),
                    created_at=job.created_at,
                )
            )
        session.commit()
    manifests: dict[str, str] = {}
    for job in jobs:
        manifest = repository.load_publication_manifest(
            job.job_id,
            expected_attempt=1,
        )
        assert manifest is not None
        manifests[job.job_id] = manifest.manifest_digest
    return manifests


def _add_fixture_outbox(
    session: Session,
    repository: PostgresDocumentProcessingAdapter,
    *,
    job_id: str,
    attempt: int,
    ordinal: int,
    available_at: datetime,
    status: str = "pending",
    batch_id: str | None = None,
    identity_salt: str | None = None,
) -> str:
    record = _fixture_outbox_record(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={
            "job_id": job_id,
            "batch_id": batch_id or f"{job_id}:page:{ordinal + 1}",
            "attempt": attempt,
            "schema_version": 1,
        },
        available_at=available_at,
        identity_salt=identity_salt,
    )
    payload = asdict(record)
    payload["status"] = status
    payload["dispatched_at"] = available_at if status == "dispatched" else None
    session.add(AtlasTaskOutboxRow(**payload))
    return record.outbox_id


def _versioned_rows(
    session: Session,
    row_type: type[Any],
    identity_columns: tuple[Any, ...],
    *predicates: Any,
) -> tuple[tuple[object, ...], ...]:
    xmin = literal_column(f"{row_type.__tablename__}.xmin::text")
    statement = (
        select(*identity_columns, xmin)
        .select_from(row_type)
        .where(*predicates)
        .order_by(*identity_columns)
    )
    return tuple(tuple(row) for row in session.execute(statement).all())


def _outbox_write_snapshot(
    runtime: PostgresRuntime,
    outbox_ids: tuple[str, ...],
) -> dict[str, tuple[object, ...]]:
    with runtime.session_factory() as session:
        rows = session.scalars(
            select(AtlasTaskOutboxRow)
            .where(AtlasTaskOutboxRow.outbox_id.in_(outbox_ids))
            .order_by(AtlasTaskOutboxRow.outbox_id)
        ).all()
        versions = dict(
            _versioned_rows(
                session,
                AtlasTaskOutboxRow,
                (AtlasTaskOutboxRow.outbox_id,),
                AtlasTaskOutboxRow.outbox_id.in_(outbox_ids),
            )
        )
        return {
            row.outbox_id: (
                versions[row.outbox_id],
                row.task_name,
                row.queue_name,
                row.payload_schema_version,
                dict(row.payload),
                row.celery_task_id,
                row.status,
                row.claim_owner,
                row.claim_expires_at,
                row.attempts,
                row.available_at,
                row.last_error_code,
                row.created_at,
                row.dispatched_at,
            )
            for row in rows
        }


def _publication_write_snapshot(
    runtime: PostgresRuntime,
    job_id: str,
) -> tuple[object, ...]:
    with runtime.session_factory() as session:
        job = session.get(AtlasProcessingJobRow, job_id)
        assert job is not None
        assert job.processing_generation is not None
        generation = job.processing_generation
        derived_artifact_ids = select(AtlasArtifactRow.artifact_id).where(
            AtlasArtifactRow.parent_resource_id == DOCUMENT_ID,
            AtlasArtifactRow.processing_generation == generation,
        )
        derived_blob_ids = select(AtlasArtifactRow.blob_id).where(
            AtlasArtifactRow.parent_resource_id == DOCUMENT_ID,
            AtlasArtifactRow.processing_generation == generation,
        )
        derived_attempt_ids = select(
            AtlasStorageBlobRow.write_attempt_id
        ).where(AtlasStorageBlobRow.blob_id.in_(derived_blob_ids))
        return (
            _versioned_rows(
                session,
                AtlasDocumentRow,
                (AtlasDocumentRow.document_id,),
                AtlasDocumentRow.document_id == DOCUMENT_ID,
            ),
            _versioned_rows(
                session,
                AtlasProcessingJobRow,
                (AtlasProcessingJobRow.job_id,),
                AtlasProcessingJobRow.job_id == job_id,
            ),
            _versioned_rows(
                session,
                AtlasProcessingGenerationRow,
                (
                    AtlasProcessingGenerationRow.document_id,
                    AtlasProcessingGenerationRow.processing_generation,
                ),
                AtlasProcessingGenerationRow.document_id == DOCUMENT_ID,
                AtlasProcessingGenerationRow.processing_generation == generation,
            ),
            _versioned_rows(
                session,
                AtlasIndexGenerationRow,
                (AtlasIndexGenerationRow.index_generation_id,),
                AtlasIndexGenerationRow.index_generation_id
                == job.index_generation_id,
            ),
            _versioned_rows(
                session,
                AtlasProcessingCheckpointRow,
                (
                    AtlasProcessingCheckpointRow.job_id,
                    AtlasProcessingCheckpointRow.unit_kind,
                    AtlasProcessingCheckpointRow.unit_start,
                    AtlasProcessingCheckpointRow.unit_end,
                ),
                AtlasProcessingCheckpointRow.job_id == job_id,
            ),
            _versioned_rows(
                session,
                processing_rows.AtlasEvidenceRow,
                (processing_rows.AtlasEvidenceRow.evidence_id,),
                processing_rows.AtlasEvidenceRow.document_id == DOCUMENT_ID,
                processing_rows.AtlasEvidenceRow.processing_generation == generation,
            ),
            _versioned_rows(
                session,
                processing_rows.AtlasEvidencePageArtifactRow,
                (processing_rows.AtlasEvidencePageArtifactRow.id,),
                processing_rows.AtlasEvidencePageArtifactRow.document_version_id
                == DOCUMENT_VERSION_ID,
                processing_rows.AtlasEvidencePageArtifactRow.processing_generation
                == generation,
            ),
            _versioned_rows(
                session,
                AtlasSearchChunkRow,
                (AtlasSearchChunkRow.chunk_id,),
                AtlasSearchChunkRow.document_id == DOCUMENT_ID,
                AtlasSearchChunkRow.processing_generation == generation,
            ),
            _versioned_rows(
                session,
                AtlasVectorPointMappingRow,
                (
                    AtlasVectorPointMappingRow.index_generation_id,
                    AtlasVectorPointMappingRow.point_id,
                ),
                AtlasVectorPointMappingRow.index_generation_id
                == job.index_generation_id,
            ),
            _versioned_rows(
                session,
                AtlasArtifactRow,
                (AtlasArtifactRow.artifact_id,),
                AtlasArtifactRow.artifact_id.in_(derived_artifact_ids),
            ),
            _versioned_rows(
                session,
                AtlasArtifactScopeBindingRow,
                (AtlasArtifactScopeBindingRow.binding_id,),
                AtlasArtifactScopeBindingRow.artifact_id.in_(derived_artifact_ids),
            ),
            _versioned_rows(
                session,
                AtlasStorageBlobRow,
                (AtlasStorageBlobRow.blob_id,),
                AtlasStorageBlobRow.blob_id.in_(derived_blob_ids),
            ),
            _versioned_rows(
                session,
                AtlasArtifactWriteAttemptRow,
                (AtlasArtifactWriteAttemptRow.write_attempt_id,),
                AtlasArtifactWriteAttemptRow.write_attempt_id.in_(
                    derived_attempt_ids
                ),
            ),
            _versioned_rows(
                session,
                AtlasAuditEventRow,
                (AtlasAuditEventRow.event_id,),
                AtlasAuditEventRow.document_id == DOCUMENT_ID,
            ),
        )


def test_concurrent_jobs_allocate_distinct_unpublished_processing_generations(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    start = Barrier(2)
    real_acquire_owner_locks = document_processing_owner.acquire_owner_locks

    def coordinate_allocation_locks(
        session: Session,
        *,
        domain_keys: tuple[str, ...] = (),
        identity_keys: tuple[str, ...] = (),
    ) -> None:
        if set(identity_keys) == {
            f"document:allocation:{DOCUMENT_ID}",
            f"document:document:{DOCUMENT_ID}",
        }:
            start.wait(timeout=10.0)
        real_acquire_owner_locks(
            session,
            domain_keys=domain_keys,
            identity_keys=identity_keys,
        )

    monkeypatch.setattr(
        document_processing_owner,
        "acquire_owner_locks",
        coordinate_allocation_locks,
    )

    def create_job(key: str):
        return repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="c3-generation-concurrency",
            idempotency_key=key,
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(create_job, "first"),
                pool.submit(create_job, "second"),
            )
            jobs = tuple(future.result(timeout=10.0) for future in futures)

        assert {job.processing_generation for job in jobs} == {1, 2}
        with postgres_runtime.session_factory() as session:
            document = session.get(AtlasDocumentRow, DOCUMENT_ID)
            persisted_jobs = session.scalars(
                select(AtlasProcessingJobRow).where(
                    AtlasProcessingJobRow.document_id == DOCUMENT_ID
                )
            ).all()
            generations = session.scalars(
                select(AtlasProcessingGenerationRow)
                .where(AtlasProcessingGenerationRow.document_id == DOCUMENT_ID)
                .order_by(AtlasProcessingGenerationRow.processing_generation)
            ).all()
        assert document is not None
        assert document.processing_job_id in {job.job_id for job in jobs}
        assert {row.job_id for row in persisted_jobs} == {job.job_id for job in jobs}
        assert {row.processing_generation for row in persisted_jobs} == {1, 2}
        assert [row.processing_generation for row in generations] == [1, 2]
        assert all(row.document_version_id == DOCUMENT_VERSION_ID for row in generations)
        assert all(row.status == "building" for row in generations)
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_processing_request_snapshot_survives_registry_change_and_restart(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    media_type = "application/x-atlas-t042"
    runtime_id = "runtime-t042"
    plugin_id = "plugin-t042"
    profile_id = "profile-t042"
    plugin_identity = json.dumps([plugin_id, "1.0.0"], separators=(",", ":"))
    profile_identity_v1 = json.dumps([profile_id, 1], separators=(",", ":"))
    profile_identity_v2 = json.dumps([profile_id, 2], separators=(",", ":"))
    ref = {
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "package_digest": f"platform-builtin:{plugin_id}:1.0.0",
        "runtime_profile": runtime_id,
    }

    def profile_payload(revision: int, status: str) -> dict[str, object]:
        return {
            "profile_id": profile_id,
            "revision": revision,
            "status": status,
            "accepted_media_types": [media_type],
            "base_parser_plugin_ref": ref,
            "mandatory_processor_plugin_refs": [],
            "eligible_processor_plugin_refs": [],
            "plugin_priority": [],
        }

    try:
        with postgres_runtime.session_factory() as session:
            session.merge(
                processing_rows.AtlasRuntimeProfileRow(
                    id=runtime_id,
                    payload={
                        "runtime_profile_id": runtime_id,
                        "description": "T-042 runtime",
                        "enabled": True,
                        "available_packages": {},
                    },
                )
            )
            session.merge(
                processing_rows.AtlasPluginVersionRow(
                    id=plugin_identity,
                    payload={
                        **ref,
                        "plugin_kind": "base_parser",
                        "status": "verified",
                        "trust_provenance": "platform_builtin",
                        "descriptor": {"entrypoint": "plugins:Parser"},
                    },
                )
            )
            session.merge(
                processing_rows.AtlasProcessingProfileRevisionRow(
                    id=profile_identity_v1,
                    payload=profile_payload(1, "active"),
                )
            )
            session.commit()

        first_adapter = PostgresDocumentProcessingAdapter(
            postgres_runtime.session_factory
        )
        first = first_adapter.accept_processing_job(
            media_type=media_type,
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="t042-processing-request",
            idempotency_key="first",
            created_by="user-t042",
        )

        with postgres_runtime.session_factory() as session:
            first_profile = session.get(
                processing_rows.AtlasProcessingProfileRevisionRow,
                profile_identity_v1,
            )
            assert first_profile is not None
            first_profile.payload = profile_payload(1, "deprecated")
            session.merge(
                processing_rows.AtlasProcessingProfileRevisionRow(
                    id=profile_identity_v2,
                    payload=profile_payload(2, "active"),
                )
            )
            session.commit()

        restarted_adapter = PostgresDocumentProcessingAdapter(
            postgres_runtime.session_factory
        )
        pinned = restarted_adapter.load_processing_execution(
            job_id=first.job_id,
            expected_attempt=1,
            expected_fence=0,
        )
        replay = restarted_adapter.accept_processing_job(
            media_type=media_type,
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="t042-processing-request",
            idempotency_key="first",
            created_by="user-t042",
        )
        restarted_adapter.prepare_job(
            first.job_id,
            total_units=2,
            profile_id=profile_id,
            profile_revision=1,
            expected_attempt=1,
        )
        batch_id = f"{first.job_id}:page:1"
        with postgres_runtime.session_factory() as session:
            session.add(
                AtlasProcessingCheckpointRow(
                    job_id=first.job_id,
                    unit_kind="page",
                    unit_start=1,
                    unit_end=1,
                    batch_id=batch_id,
                    claim_token="consumed-claim-t042",
                    fence=0,
                    input_fingerprint="c" * 64,
                    output_digest="d" * 64,
                    evidence_count=1,
                    chunk_count=1,
                    preview_count=1,
                    committed_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        cancelled = restarted_adapter.cancel_processing_job(first.job_id)
        with pytest.raises(DocumentProcessingCurrentnessConflict):
            restarted_adapter.load_processing_execution(
                job_id=first.job_id,
                expected_attempt=1,
                expected_fence=0,
            )
        retried = restarted_adapter.retry_terminal_job(first.job_id)
        retried_snapshot = restarted_adapter.load_processing_execution(
            job_id=retried.job_id,
            expected_attempt=retried.attempt,
            expected_fence=retried.fence,
        )
        with pytest.raises(DocumentProcessingCurrentnessConflict):
            restarted_adapter.accept_processing_job(
                media_type=media_type,
                document_id=DOCUMENT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                job_kind="reprocess",
                idempotency_scope="t042-processing-request",
                idempotency_key="second",
                created_by="user-t042",
            )

        assert pinned.profile_revision == 1
        assert replay.job_id == first.job_id
        assert cancelled.status == "cancelled"
        assert retried.job_id != first.job_id
        assert retried.attempt == 1
        assert retried_snapshot == pinned
        with postgres_runtime.session_factory() as session:
            owned = session.get(
                AtlasProcessingRequestSnapshotRow,
                first.job_id,
            )
            generation = session.get(
                AtlasProcessingGenerationRow,
                (DOCUMENT_ID, first.processing_generation),
            )
        assert owned is not None
        assert owned.processing_generation == first.processing_generation
        assert generation is not None
        assert generation.profile_revision == 1
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)
        with postgres_runtime.session_factory() as session:
            session.execute(
                delete(processing_rows.AtlasProcessingProfileRevisionRow).where(
                    processing_rows.AtlasProcessingProfileRevisionRow.id.in_(
                        (profile_identity_v1, profile_identity_v2)
                    )
                )
            )
            session.execute(
                delete(processing_rows.AtlasPluginVersionRow).where(
                    processing_rows.AtlasPluginVersionRow.id == plugin_identity
                )
            )
            session.execute(
                delete(processing_rows.AtlasRuntimeProfileRow).where(
                    processing_rows.AtlasRuntimeProfileRow.id == runtime_id
                )
            )
            session.commit()


def test_create_job_joins_outer_connection_commit_and_failure_rollback(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    job_id = None
    try:
        with postgres_runtime.engine.connect() as connection:
            outer = connection.begin()
            job = repository.create_processing_job(
                document_id=DOCUMENT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                job_kind="reprocess",
                idempotency_scope="c3-outer-connection-success",
                idempotency_key="same-outer-transaction",
                created_by="user-c3-generation-concurrency",
                connection=connection,
            )
            job_id = job.job_id
            assert outer.is_active
            visible_job = connection.execute(
                select(AtlasProcessingJobRow).where(
                    AtlasProcessingJobRow.job_id == job.job_id
                )
            ).scalar_one_or_none()
            visible_audit = connection.execute(
                select(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.event_metadata["job_id"].as_string()
                    == job.job_id
                )
            ).scalar_one_or_none()
            assert visible_job is not None
            assert visible_audit is not None
            outer.rollback()

        with postgres_runtime.session_factory() as session:
            assert session.get(AtlasProcessingJobRow, job_id) is None
            assert session.scalar(
                select(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.event_metadata["job_id"].as_string()
                    == job_id
                )
            ) is None

        with postgres_runtime.engine.connect() as connection:
            failed_outer = connection.begin()
            with pytest.raises(ValueError, match="document_version_"):
                repository.create_processing_job(
                    document_id=DOCUMENT_ID,
                    document_version_id="foreign-version",
                    job_kind="reprocess",
                    idempotency_scope="c3-outer-connection-failure",
                    idempotency_key="must-rollback-outer",
                    created_by="user-c3-generation-concurrency",
                    connection=connection,
                )
            assert not failed_outer.is_active
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_cleanup_preserves_active_publication_replay_until_pointer_moves(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_active_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        first_manifests = _prepare_competing_publications(
            postgres_runtime,
            repository,
            idempotency_scope="c3-publication-cleanup-first",
            ordinals=("first",),
        )
        ((first_job_id, first_digest),) = tuple(first_manifests.items())
        assert repository.publish_job(
            first_job_id,
            expected_attempt=1,
            verified_manifest_digest=first_digest,
        )

        repository.cleanup_staging(limit=100)
        with postgres_runtime.session_factory() as session:
            first_checkpoint_ids = tuple(
                session.scalars(
                    select(AtlasProcessingCheckpointRow.job_id).where(
                        AtlasProcessingCheckpointRow.job_id == first_job_id
                    )
                ).all()
            )
        assert first_checkpoint_ids == (first_job_id,)

        first_replay_snapshot = _publication_write_snapshot(
            postgres_runtime,
            first_job_id,
        )
        assert repository.publish_job(
            first_job_id,
            expected_attempt=1,
            verified_manifest_digest=first_digest,
        )
        assert (
            _publication_write_snapshot(postgres_runtime, first_job_id)
            == first_replay_snapshot
        )

        wrong_digest_snapshot = _publication_write_snapshot(
            postgres_runtime,
            first_job_id,
        )
        assert not repository.publish_job(
            first_job_id,
            expected_attempt=1,
            verified_manifest_digest="0" * 64,
        )
        assert (
            _publication_write_snapshot(postgres_runtime, first_job_id)
            == wrong_digest_snapshot
        )

        second_manifests = _prepare_competing_publications(
            postgres_runtime,
            repository,
            idempotency_scope="c3-publication-cleanup-second",
            ordinals=("second",),
        )
        ((second_job_id, second_digest),) = tuple(second_manifests.items())
        assert repository.publish_job(
            second_job_id,
            expected_attempt=1,
            verified_manifest_digest=second_digest,
        )

        wrong_pointer_snapshot = _publication_write_snapshot(
            postgres_runtime,
            first_job_id,
        )
        assert not repository.publish_job(
            first_job_id,
            expected_attempt=1,
            verified_manifest_digest=first_digest,
        )
        assert (
            _publication_write_snapshot(postgres_runtime, first_job_id)
            == wrong_pointer_snapshot
        )

        repository.cleanup_staging(limit=100)
        with postgres_runtime.session_factory() as session:
            remaining_checkpoint_job_ids = tuple(
                session.scalars(
                    select(AtlasProcessingCheckpointRow.job_id)
                    .where(
                        AtlasProcessingCheckpointRow.job_id.in_(
                            (first_job_id, second_job_id)
                        )
                    )
                    .order_by(AtlasProcessingCheckpointRow.job_id)
                ).all()
            )
        assert remaining_checkpoint_job_ids == (second_job_id,)

        second_replay_snapshot = _publication_write_snapshot(
            postgres_runtime,
            second_job_id,
        )
        assert repository.publish_job(
            second_job_id,
            expected_attempt=1,
            verified_manifest_digest=second_digest,
        )
        assert (
            _publication_write_snapshot(postgres_runtime, second_job_id)
            == second_replay_snapshot
        )

        with postgres_runtime.session_factory() as session:
            first_index_generation_id = session.scalar(
                select(AtlasProcessingJobRow.index_generation_id).where(
                    AtlasProcessingJobRow.job_id == first_job_id
                )
            )
        assert first_index_generation_id is not None
        retired_points = repository.retired_vector_points(limit=100)
        assert first_index_generation_id in retired_points
        repository.delete_retired_vector_points(retired_points)
        repository.cleanup_retired_generations(limit=10)
        with postgres_runtime.session_factory() as session:
            assert session.get(
                AtlasIndexGenerationRow,
                first_index_generation_id,
            ) is None
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_supported_large_job_cancel_after_dispatch_claim_cancels_complete_attempt(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="c3-large-job-cancel-race",
            idempotency_key="supported-3000-page-job",
            created_by="user-c3-generation-concurrency",
            progress_total=3_000,
        )
        available_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        unavailable_until = datetime(2100, 1, 1, tzinfo=timezone.utc)
        with postgres_runtime.session_factory() as session:
            for ordinal in range(200):
                _add_fixture_outbox(
                    session,
                    repository,
                    job_id=job.job_id,
                    attempt=1,
                    ordinal=ordinal,
                    available_at=available_at,
                )
            other_attempt_outbox_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=2,
                ordinal=0,
                available_at=unavailable_until,
            )
            session.commit()

        start = Barrier(2)
        claim_committed = Event()

        def claim_before_cancel() -> list[dict[str, object]]:
            start.wait(timeout=10.0)
            try:
                return repository.claim_pending_outbox(
                    "dispatcher-c3-generation-concurrency",
                    limit=100,
                )
            finally:
                claim_committed.set()

        def cancel_after_claim():
            start.wait(timeout=10.0)
            assert claim_committed.wait(timeout=10.0)
            return repository.cancel_processing_job(job.job_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claim_future = pool.submit(claim_before_cancel)
            cancel_future = pool.submit(cancel_after_claim)
            claimed = claim_future.result(timeout=15.0)
            cancelled = cancel_future.result(timeout=15.0)

        assert cancelled.status == "cancelled"
        assert len(claimed) == 100
        assert all(item["payload"]["job_id"] == job.job_id for item in claimed)
        assert all(item["payload"]["attempt"] == 1 for item in claimed)

        with postgres_runtime.session_factory() as session:
            persisted_job = session.get(AtlasProcessingJobRow, job.job_id)
            outboxes = session.scalars(
                select(AtlasTaskOutboxRow)
                .where(
                    AtlasTaskOutboxRow.payload["job_id"].as_string()
                    == job.job_id
                )
                .order_by(AtlasTaskOutboxRow.outbox_id)
            ).all()
        assert persisted_job is not None
        assert persisted_job.status == "cancelled"
        current_attempt = [
            row for row in outboxes if int(row.payload["attempt"]) == 1
        ]
        other_attempt = [
            row for row in outboxes if int(row.payload["attempt"]) == 2
        ]
        assert len(current_attempt) == 201
        assert all(row.status == "cancelled" for row in current_attempt)
        assert all(row.claim_owner is None for row in current_attempt)
        assert all(row.claim_expires_at is None for row in current_attempt)
        assert len(other_attempt) == 1
        assert other_attempt[0].outbox_id == other_attempt_outbox_id
        assert other_attempt[0].status == "pending"
        assert other_attempt[0].attempts == 0
        assert other_attempt[0].claim_owner is None
        assert other_attempt[0].claim_expires_at is None
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_schedule_retry_exact_payload_ignores_siblings_and_deduplicates_race(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="c3-exact-retry-history",
            idempotency_key="supported-3000-page-job",
            created_by="user-c3-generation-concurrency",
            progress_total=3_000,
        )
        archived_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        unavailable_until = datetime(2100, 1, 1, tzinfo=timezone.utc)
        target_batch_id = f"{job.job_id}:page:1"
        sibling_batch_id = f"{job.job_id}:page:2"
        exact_history_ids: list[str] = []
        with postgres_runtime.session_factory() as session:
            for ordinal in range(200):
                exact_history_ids.append(
                    _add_fixture_outbox(
                        session,
                        repository,
                        job_id=job.job_id,
                        attempt=1,
                        ordinal=ordinal,
                        available_at=archived_at,
                        status="dispatched",
                        batch_id=target_batch_id,
                        identity_salt=f"terminal-exact:{ordinal}",
                    )
                )
            sibling_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=1,
                ordinal=200,
                available_at=unavailable_until,
                batch_id=sibling_batch_id,
            )
            other_attempt_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=2,
                ordinal=200,
                available_at=unavailable_until,
                batch_id=target_batch_id,
            )
            session.commit()

        assert len(set(exact_history_ids)) == 200
        latest_history_id = max(exact_history_ids)
        immutable_ids = (*exact_history_ids, sibling_id, other_attempt_id)
        immutable_snapshot = _outbox_write_snapshot(
            postgres_runtime,
            immutable_ids,
        )
        base_payload = {
            "job_id": job.job_id,
            "batch_id": target_batch_id,
        }
        retry_payload = {
            **base_payload,
            "attempt": 1,
            "schema_version": 1,
        }
        expected_successor_id = (
            _fixture_outbox_record(
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload=retry_payload,
                available_at=archived_at,
                last_error_code="processing_dependency_unavailable",
                identity_salt=f"retry-after:{latest_history_id}",
            ).outbox_id
        )
        repository.schedule_retry(
            job.job_id,
            expected_attempt=1,
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload=base_payload,
            code="processing_dependency_unavailable",
            detail="temporary batch dependency failure",
            delay_seconds=60,
        )

        assert (
            _outbox_write_snapshot(postgres_runtime, immutable_ids)
            == immutable_snapshot
        )
        with postgres_runtime.session_factory() as session:
            persisted_job = session.get(AtlasProcessingJobRow, job.job_id)
            process_rows = session.scalars(
                select(AtlasTaskOutboxRow)
                .where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.queue_name == "atlas.processing",
                    AtlasTaskOutboxRow.payload["job_id"].as_string()
                    == job.job_id,
                    AtlasTaskOutboxRow.payload["attempt"].as_integer() == 1,
                )
                .order_by(
                    AtlasTaskOutboxRow.created_at,
                    AtlasTaskOutboxRow.outbox_id,
                )
            ).all()
            sibling = session.get(AtlasTaskOutboxRow, sibling_id)
            other_attempt = session.get(AtlasTaskOutboxRow, other_attempt_id)
        assert persisted_job is not None
        assert persisted_job.status == "retry_wait"
        assert persisted_job.attempt == 1
        assert persisted_job.failure_code == "processing_dependency_unavailable"
        assert len(process_rows) == 202
        exact_rows = [
            row for row in process_rows if dict(row.payload) == retry_payload
        ]
        terminal_exact_rows = [
            row for row in exact_rows if row.status == "dispatched"
        ]
        pending_exact_rows = [
            row for row in exact_rows if row.status == "pending"
        ]
        assert len(exact_rows) == 201
        assert len(terminal_exact_rows) == 200
        assert {row.outbox_id for row in terminal_exact_rows} == set(
            exact_history_ids
        )
        assert len(pending_exact_rows) == 1
        successor_id = pending_exact_rows[0].outbox_id
        assert successor_id == expected_successor_id
        assert successor_id not in exact_history_ids
        assert pending_exact_rows[0].last_error_code == (
            "processing_dependency_unavailable"
        )
        assert sibling is not None
        assert sibling.status == "pending"
        assert sibling.attempts == 0
        assert other_attempt is not None
        assert other_attempt.status == "pending"
        assert other_attempt.attempts == 0
        successor_snapshot = _outbox_write_snapshot(
            postgres_runtime,
            (successor_id,),
        )

        start = Barrier(2)

        def schedule_duplicate() -> str:
            start.wait(timeout=10.0)
            try:
                repository.schedule_retry(
                    job.job_id,
                    expected_attempt=1,
                    task_name="atlas.processing.process_batch",
                    queue_name="atlas.processing",
                    payload=base_payload,
                    code="processing_dependency_unavailable",
                    detail="temporary batch dependency failure",
                    delay_seconds=60,
                )
            except DocumentProcessingCurrentnessConflict:
                return "currentness_conflict"
            return "scheduled"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = tuple(pool.submit(schedule_duplicate) for _ in range(2))
            outcomes = tuple(
                future.result(timeout=15.0) for future in futures
            )

        assert "scheduled" in outcomes
        assert set(outcomes) <= {"scheduled", "currentness_conflict"}
        assert (
            _outbox_write_snapshot(postgres_runtime, immutable_ids)
            == immutable_snapshot
        )
        assert (
            _outbox_write_snapshot(postgres_runtime, (successor_id,))
            == successor_snapshot
        )
        with postgres_runtime.session_factory() as session:
            final_job = session.get(AtlasProcessingJobRow, job.job_id)
            final_process_rows = session.scalars(
                select(AtlasTaskOutboxRow)
                .where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.queue_name == "atlas.processing",
                    AtlasTaskOutboxRow.payload["job_id"].as_string()
                    == job.job_id,
                    AtlasTaskOutboxRow.payload["attempt"].as_integer() == 1,
                )
                .order_by(
                    AtlasTaskOutboxRow.created_at,
                    AtlasTaskOutboxRow.outbox_id,
                )
            ).all()
            final_sibling = session.get(AtlasTaskOutboxRow, sibling_id)
            final_other_attempt = session.get(
                AtlasTaskOutboxRow,
                other_attempt_id,
            )
        final_exact_rows = [
            row
            for row in final_process_rows
            if dict(row.payload) == retry_payload
        ]
        final_active_exact_rows = [
            row
            for row in final_exact_rows
            if row.status in {"pending", "dispatching"}
        ]
        final_terminal_exact_rows = [
            row for row in final_exact_rows if row.status == "dispatched"
        ]
        assert final_job is not None
        assert final_job.status == "retry_wait"
        assert final_job.attempt == 1
        assert len(final_process_rows) == 202
        assert len(final_exact_rows) == 201
        assert len(final_terminal_exact_rows) == 200
        assert len(final_active_exact_rows) == 1
        assert final_active_exact_rows[0].outbox_id == successor_id
        assert final_active_exact_rows[0].status == "pending"
        assert final_sibling is not None
        assert final_sibling.status == "pending"
        assert final_sibling.attempts == 0
        assert final_other_attempt is not None
        assert final_other_attempt.status == "pending"
        assert final_other_attempt.attempts == 0
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_page_retry_deduplicates_without_changing_job_or_document(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="pcr-page-retry",
            idempotency_key="page-20",
            created_by="user-c3-generation-concurrency",
            progress_total=20,
        )
        batch_id = f"{job.job_id}:page:20"
        with postgres_runtime.session_factory() as session:
            before_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            before_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )

        start = Barrier(2)

        def schedule() -> bool:
            start.wait(timeout=10.0)
            return repository.schedule_page_batch_retry(
                job.job_id,
                batch_id,
                expected_attempt=job.attempt,
                task_name="atlas.processing.process_batch",
                code="processing_batch_not_committed",
                delay_seconds=0,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _value: schedule(), range(2)))

        assert results == (True, True)
        payload = {
            "job_id": job.job_id,
            "batch_id": batch_id,
            "attempt": job.attempt,
            "schema_version": 1,
        }
        with postgres_runtime.session_factory() as session:
            after_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            after_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )
            pending = session.scalars(
                select(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.payload == payload,
                    AtlasTaskOutboxRow.status == "pending",
                )
            ).all()
        assert after_job == before_job
        assert after_document == before_document
        assert len(pending) == 1
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_index_page_claim_overlap_does_not_change_job_status(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="pcr-index-claim",
            idempotency_key="page-1",
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )
        batch_id = f"{job.job_id}:page:1"
        entered = Event()
        release = Event()

        def first_claim() -> bool:
            with repository.index_batch_execution(
                job.job_id, batch_id, expected_attempt=job.attempt
            ) as claimed:
                assert claimed is not None
                entered.set()
                assert release.wait(timeout=10.0)
                return True

        def overlapping_claim() -> bool:
            assert entered.wait(timeout=10.0)
            try:
                with repository.index_batch_execution(
                    job.job_id, batch_id, expected_attempt=job.attempt
                ) as claimed:
                    return claimed is not None
            finally:
                release.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_claim)
            second = pool.submit(overlapping_claim)
            assert second.result(timeout=15.0) is False
            assert first.result(timeout=15.0) is True

        with postgres_runtime.session_factory() as session:
            persisted = session.get(AtlasProcessingJobRow, job.job_id)
            claim = session.get(AtlasProcessingBatchClaimRow, batch_id)
        assert persisted is not None
        assert persisted.status == job.status
        assert claim is None
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_nonfinal_mapping_is_page_local_and_maintenance_recovers_only_gap(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="pcr-mapping-gap",
            idempotency_key="two-pages",
            created_by="user-c3-generation-concurrency",
            progress_total=2,
        )
        assert job.processing_generation is not None
        created_at = datetime.now(timezone.utc)
        with postgres_runtime.session_factory() as session:
            job_row = session.get(AtlasProcessingJobRow, job.job_id)
            generation = session.get(
                AtlasProcessingGenerationRow,
                (DOCUMENT_ID, job.processing_generation),
            )
            index = session.get(
                AtlasIndexGenerationRow, job.index_generation_id
            )
            assert job_row is not None
            assert generation is not None
            assert index is not None
            job_row.status = "running"
            job_row.stage = "indexing"
            job_row.progress_current = 2
            generation.expected_page_count = 2
            generation.actual_page_count = 2
            generation.expected_evidence_count = 2
            generation.actual_evidence_count = 2
            generation.expected_chunk_count = 2
            generation.actual_chunk_count = 2
            index.expected_point_count = 2
            index.actual_point_count = 0
            index.expected_fts_count = 2
            index.actual_fts_count = 2
            for page_number in (1, 2):
                batch_id = f"{job.job_id}:page:{page_number}"
                evidence_id = f"evidence-{job.job_id}-{page_number}"
                chunk_id = f"chunk-{job.job_id}-{page_number}"
                evidence = EvidenceRecord(
                    evidence_id=evidence_id,
                    document_id=DOCUMENT_ID,
                    document_title="Generation concurrency",
                    locator_label=f"Page {page_number}",
                    snippet=f"page {page_number}",
                    content=f"page {page_number}",
                    document_version_id=DOCUMENT_VERSION_ID,
                    processing_generation=job.processing_generation,
                    status="staged",
                    source_region_id=f"region-{page_number}",
                    channel_id="generic_text",
                    output_contract_version="eir-draft-v1",
                    claim_support_role="claim_grounding",
                    locator_payload={"page_number": page_number},
                    content_fingerprint=_digest(f"content:{page_number}"),
                    processing_fingerprint=_digest(f"processing:{page_number}"),
                    profile_id="c3-profile",
                    profile_revision=1,
                    quality_flag_refs=[],
                )
                session.add(processing_rows.AtlasEvidenceRow(**asdict(evidence)))
                session.add(
                    AtlasProcessingCheckpointRow(
                        job_id=job.job_id,
                        unit_kind="page",
                        unit_start=page_number,
                        unit_end=page_number,
                        batch_id=batch_id,
                        claim_token=f"claim-{page_number}",
                        fence=job.fence,
                        input_fingerprint=_digest(f"input:{page_number}"),
                        output_digest=_digest(f"output:{page_number}"),
                        evidence_count=1,
                        chunk_count=1,
                        preview_count=1,
                        committed_at=created_at,
                    )
                )
                session.add(
                    AtlasSearchChunkRow(
                        chunk_id=chunk_id,
                        batch_id=batch_id,
                        document_id=DOCUMENT_ID,
                        document_version_id=DOCUMENT_VERSION_ID,
                        processing_generation=job.processing_generation,
                        index_generation_id=job.index_generation_id,
                        evidence_id=evidence_id,
                        segment_id=f"segment-{page_number}",
                        window_ordinal=0,
                        normalized_text=f"page {page_number}",
                        locator={"page_number": page_number},
                        content_fingerprint=_digest(f"content:{page_number}"),
                        processing_fingerprint=_digest(
                            f"processing:{page_number}"
                        ),
                        search_vector=None,
                        status="staged",
                        created_at=created_at,
                    )
                )
            session.commit()

        with postgres_runtime.session_factory() as session:
            before_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            before_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )

        page_one_chunk = f"chunk-{job.job_id}-1"
        assert repository.mark_batch_indexed(
            job_id=job.job_id,
            batch_id=f"{job.job_id}:page:1",
            mappings=[
                {
                    "index_generation_id": job.index_generation_id,
                    "point_id": f"point-{job.job_id}-1",
                    "chunk_id": page_one_chunk,
                    "payload_digest": _digest("payload:1"),
                    "vector_digest": _digest("vector:1"),
                }
            ],
            expected_attempt=job.attempt,
        )
        assert repository.reconcile_incomplete_page_batches(limit=100) == 0
        with postgres_runtime.session_factory() as session:
            job_row = session.get(AtlasProcessingJobRow, job.job_id)
            assert job_row is not None
            job_row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=301)
            session.commit()
        with postgres_runtime.session_factory() as session:
            before_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
        assert repository.reconcile_incomplete_page_batches(limit=100) == 1
        assert repository.reconcile_incomplete_page_batches(limit=100) == 0

        with postgres_runtime.session_factory() as session:
            after_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            after_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )
            index = session.get(AtlasIndexGenerationRow, job.index_generation_id)
            page_two_retry = session.scalars(
                select(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.task_name == "atlas.indexing.index_batch",
                    AtlasTaskOutboxRow.payload["batch_id"].as_string()
                    == f"{job.job_id}:page:2",
                    AtlasTaskOutboxRow.status == "pending",
                )
            ).all()
        assert after_job == before_job
        assert after_document == before_document
        assert index is not None and index.actual_point_count == 1
        assert len(page_two_retry) == 1
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_external_upsert_then_worker_crash_recovers_only_expired_claim_page(
    postgres_runtime: PostgresRuntime,
) -> None:
    """An external upsert without its DB mapping is replayed after claim expiry."""

    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="pcr-expired-index-claim",
            idempotency_key="external-upsert-page-1",
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )
        assert job.processing_generation is not None
        batch_id = f"{job.job_id}:page:1"
        evidence_id = f"evidence-{job.job_id}-1"
        chunk_id = f"chunk-{job.job_id}-1"
        crashed_at = datetime.now(timezone.utc) - timedelta(seconds=301)
        with postgres_runtime.session_factory() as session:
            job_row = session.get(AtlasProcessingJobRow, job.job_id)
            generation = session.get(
                AtlasProcessingGenerationRow,
                (DOCUMENT_ID, job.processing_generation),
            )
            index = session.get(AtlasIndexGenerationRow, job.index_generation_id)
            assert job_row is not None
            assert generation is not None
            assert index is not None
            job_row.status = "running"
            job_row.stage = "indexing"
            job_row.progress_current = 1
            # A sibling page may keep the job fresh after this page's worker
            # crashes. Expired claim recovery must not depend on job staleness.
            job_row.updated_at = datetime.now(timezone.utc)
            generation.expected_page_count = 1
            generation.actual_page_count = 1
            generation.expected_evidence_count = 1
            generation.actual_evidence_count = 1
            generation.expected_chunk_count = 1
            generation.actual_chunk_count = 1
            index.expected_point_count = 1
            index.actual_point_count = 0
            index.expected_fts_count = 1
            index.actual_fts_count = 1
            evidence = EvidenceRecord(
                evidence_id=evidence_id,
                document_id=DOCUMENT_ID,
                document_title="Generation concurrency",
                locator_label="Page 1",
                snippet="page 1",
                content="page 1",
                document_version_id=DOCUMENT_VERSION_ID,
                processing_generation=job.processing_generation,
                status="staged",
                source_region_id="region-1",
                channel_id="generic_text",
                output_contract_version="eir-draft-v1",
                claim_support_role="claim_grounding",
                locator_payload={"page_number": 1},
                content_fingerprint=_digest("content:expired-index-claim"),
                processing_fingerprint=_digest("processing:expired-index-claim"),
                profile_id="c3-profile",
                profile_revision=1,
                quality_flag_refs=[],
            )
            session.add(processing_rows.AtlasEvidenceRow(**asdict(evidence)))
            session.add(
                AtlasProcessingCheckpointRow(
                    job_id=job.job_id,
                    unit_kind="page",
                    unit_start=1,
                    unit_end=1,
                    batch_id=batch_id,
                    claim_token="processing-claim-completed",
                    fence=job.fence,
                    input_fingerprint=_digest("input:expired-index-claim"),
                    output_digest=_digest("output:expired-index-claim"),
                    evidence_count=1,
                    chunk_count=1,
                    preview_count=1,
                    committed_at=crashed_at,
                )
            )
            session.add(
                AtlasSearchChunkRow(
                    chunk_id=chunk_id,
                    batch_id=batch_id,
                    document_id=DOCUMENT_ID,
                    document_version_id=DOCUMENT_VERSION_ID,
                    processing_generation=job.processing_generation,
                    index_generation_id=job.index_generation_id,
                    evidence_id=evidence_id,
                    segment_id="segment-1",
                    window_ordinal=0,
                    normalized_text="page 1",
                    locator={"page_number": 1},
                    content_fingerprint=_digest("content:expired-index-claim"),
                    processing_fingerprint=_digest(
                        "processing:expired-index-claim"
                    ),
                    search_vector=None,
                    status="staged",
                    created_at=crashed_at,
                )
            )
            # The deterministic Qdrant point was already upserted, but the worker
            # process ended before the DB mapping transaction began. The expired
            # claim and missing mapping are the durable observations of that gap.
            session.add(
                AtlasProcessingBatchClaimRow(
                    batch_id=batch_id,
                    job_id=job.job_id,
                    attempt=job.attempt,
                    claim_token="index-claim-worker-crashed",
                    unit_kind="page",
                    unit_start=1,
                    unit_end=1,
                    lease_expires_at=datetime.now(timezone.utc)
                    - timedelta(seconds=1),
                    created_at=crashed_at,
                    updated_at=crashed_at,
                )
            )
            session.commit()

        with postgres_runtime.session_factory() as session:
            before_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            before_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )

        assert repository.reconcile_incomplete_page_batches(limit=100) == 1
        assert repository.reconcile_incomplete_page_batches(limit=100) == 0

        with postgres_runtime.session_factory() as session:
            after_job = asdict(
                document_processing_owner._job_record(
                    session.get(AtlasProcessingJobRow, job.job_id)
                )
            )
            after_document = asdict(
                document_processing_owner._document_record(
                    session.get(AtlasDocumentRow, DOCUMENT_ID)
                )
            )
            retries = session.scalars(
                select(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.task_name == "atlas.indexing.index_batch",
                    AtlasTaskOutboxRow.payload["batch_id"].as_string() == batch_id,
                    AtlasTaskOutboxRow.status == "pending",
                )
            ).all()
            mappings = session.scalars(
                select(AtlasVectorPointMappingRow).where(
                    AtlasVectorPointMappingRow.index_generation_id
                    == job.index_generation_id,
                    AtlasVectorPointMappingRow.chunk_id == chunk_id,
                )
            ).all()
        assert after_job == before_job
        assert after_document == before_document
        assert len(retries) == 1
        assert mappings == []
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_recent_processing_queue_activity_suppresses_unclaimed_page_orphan(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="pcr-processing-gap",
            idempotency_key="stale-page",
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )
        recent_job_id = f"{job.job_id}-recent"
        with postgres_runtime.session_factory() as session:
            job_row = session.get(AtlasProcessingJobRow, job.job_id)
            assert job_row is not None
            job_row.status = "running"
            job_row.stage = "parsing"
            job_row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=301)
            recent_job = asdict(document_processing_owner._job_record(job_row))
            recent_job.update(
                job_id=recent_job_id,
                idempotency_scope="pcr-recent-queue-activity",
                idempotency_key="recent-job",
                request_fingerprint=_digest("recent-job"),
                progress_current=0,
                progress_total=1,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(AtlasProcessingJobRow(**recent_job))
            session.commit()

        assert repository.reconcile_incomplete_page_batches(limit=100) == 0
        with postgres_runtime.session_factory() as session:
            session.execute(
                delete(AtlasProcessingJobRow).where(
                    AtlasProcessingJobRow.job_id == recent_job_id
                )
            )
            session.commit()
        assert repository.reconcile_incomplete_page_batches(limit=100) == 1
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


@pytest.mark.parametrize(
    "completion_order",
    ("complete_before_schedule", "schedule_before_complete"),
)
def test_schedule_retry_preserves_dispatch_completion_for_both_commit_orders(
    postgres_runtime: PostgresRuntime,
    completion_order: str,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    dispatcher_id = f"dispatcher-c3-retry-{completion_order}"
    retry_code = "processing_dependency_unavailable"
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope=f"c3-dispatch-retry-{completion_order}",
            idempotency_key="supported-3000-page-job",
            created_by="user-c3-generation-concurrency",
            progress_total=3_000,
        )
        archived_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        unavailable_until = datetime(2100, 1, 1, tzinfo=timezone.utc)
        target_batch_id = f"{job.job_id}:page:1"
        base_payload = {
            "job_id": job.job_id,
            "batch_id": target_batch_id,
        }
        retry_payload = {
            **base_payload,
            "attempt": 1,
            "schema_version": 1,
        }
        with postgres_runtime.session_factory() as session:
            predecessor_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=1,
                ordinal=0,
                available_at=archived_at,
                status="dispatching",
                batch_id=target_batch_id,
                identity_salt=f"dispatching-predecessor:{completion_order}",
            )
            session.flush()
            predecessor = session.get(AtlasTaskOutboxRow, predecessor_id)
            assert predecessor is not None
            predecessor.claim_owner = dispatcher_id
            predecessor.claim_expires_at = unavailable_until
            predecessor.attempts = 1
            session.commit()

        expected_successor_id = (
            _fixture_outbox_record(
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload=retry_payload,
                available_at=archived_at,
                last_error_code=retry_code,
                identity_salt=f"retry-after:{predecessor_id}",
            ).outbox_id
        )
        dispatching_snapshot = _outbox_write_snapshot(
            postgres_runtime,
            (predecessor_id,),
        )

        def schedule() -> None:
            repository.schedule_retry(
                job.job_id,
                expected_attempt=1,
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload=base_payload,
                code=retry_code,
                detail="temporary batch dependency failure",
                delay_seconds=60,
            )

        if completion_order == "complete_before_schedule":
            repository.complete_outbox(predecessor_id, dispatcher_id)
            completed_snapshot = _outbox_write_snapshot(
                postgres_runtime,
                (predecessor_id,),
            )
            assert completed_snapshot != dispatching_snapshot
            schedule()
            assert (
                _outbox_write_snapshot(postgres_runtime, (predecessor_id,))
                == completed_snapshot
            )
        else:
            schedule()
            assert (
                _outbox_write_snapshot(postgres_runtime, (predecessor_id,))
                == dispatching_snapshot
            )
            repository.complete_outbox(predecessor_id, dispatcher_id)

        with postgres_runtime.session_factory() as session:
            persisted_job = session.get(AtlasProcessingJobRow, job.job_id)
            exact_rows = session.scalars(
                select(AtlasTaskOutboxRow)
                .where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.queue_name == "atlas.processing",
                    AtlasTaskOutboxRow.payload == retry_payload,
                )
                .order_by(
                    AtlasTaskOutboxRow.created_at,
                    AtlasTaskOutboxRow.outbox_id,
                )
            ).all()

        assert persisted_job is not None
        assert persisted_job.status == "retry_wait"
        assert persisted_job.attempt == 1
        assert persisted_job.failure_code == retry_code
        assert len(exact_rows) == 2
        persisted_predecessor = next(
            row for row in exact_rows if row.outbox_id == predecessor_id
        )
        pending_successors = [
            row for row in exact_rows if row.status == "pending"
        ]
        assert persisted_predecessor.status == "dispatched"
        assert persisted_predecessor.claim_owner is None
        assert persisted_predecessor.claim_expires_at is None
        assert persisted_predecessor.attempts == 1
        assert persisted_predecessor.dispatched_at is not None
        assert len(pending_successors) == 1
        assert pending_successors[0].outbox_id == expected_successor_id
        assert pending_successors[0].outbox_id != predecessor_id
        assert pending_successors[0].last_error_code == retry_code
        assert pending_successors[0].attempts == 0
        assert pending_successors[0].claim_owner is None
        assert pending_successors[0].claim_expires_at is None
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


@pytest.mark.parametrize(
    "recovery_order",
    ("schedule_before_reconcile", "reconcile_before_schedule"),
)
def test_dispatcher_crash_reconciliation_keeps_one_pending_retry_successor(
    postgres_runtime: PostgresRuntime,
    recovery_order: str,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    dispatcher_id = f"dispatcher-c3-retry-crash-{recovery_order}"
    retry_code = "processing_dependency_unavailable"
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope=f"c3-dispatch-retry-crash-{recovery_order}",
            idempotency_key="supported-3000-page-job",
            created_by="user-c3-generation-concurrency",
            progress_total=3_000,
        )
        expired_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        target_batch_id = f"{job.job_id}:page:1"
        base_payload = {
            "job_id": job.job_id,
            "batch_id": target_batch_id,
        }
        retry_payload = {
            **base_payload,
            "attempt": 1,
            "schema_version": 1,
        }
        with postgres_runtime.session_factory() as session:
            predecessor_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=1,
                ordinal=0,
                available_at=expired_at,
                status="dispatching",
                batch_id=target_batch_id,
                identity_salt=(
                    f"dispatching-predecessor:dispatcher-crash:{recovery_order}"
                ),
            )
            session.flush()
            predecessor = session.get(AtlasTaskOutboxRow, predecessor_id)
            assert predecessor is not None
            predecessor.claim_owner = dispatcher_id
            predecessor.claim_expires_at = expired_at
            predecessor.attempts = 1
            session.commit()

        expected_successor_id = (
            _fixture_outbox_record(
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload=retry_payload,
                available_at=expired_at,
                last_error_code=retry_code,
                identity_salt=f"retry-after:{predecessor_id}",
            ).outbox_id
        )
        dispatching_snapshot = _outbox_write_snapshot(
            postgres_runtime,
            (predecessor_id,),
        )

        def schedule() -> None:
            repository.schedule_retry(
                job.job_id,
                expected_attempt=1,
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload=base_payload,
                code=retry_code,
                detail="temporary batch dependency failure",
                delay_seconds=60,
            )

        if recovery_order == "schedule_before_reconcile":
            schedule()
            assert (
                _outbox_write_snapshot(postgres_runtime, (predecessor_id,))
                == dispatching_snapshot
            )
            successor_snapshot = _outbox_write_snapshot(
                postgres_runtime,
                (expected_successor_id,),
            )
            assert set(successor_snapshot) == {expected_successor_id}
            repository.reconcile_expired_claims(limit=200)
            expected_predecessor_status = "dispatched"
            expected_event_type = (
                "task_outbox.expired_predecessors_completed"
            )
            expected_failure_code = (
                "dispatch_claim_expired_after_retry_successor"
            )
        else:
            repository.reconcile_expired_claims(limit=200)
            successor_snapshot = _outbox_write_snapshot(
                postgres_runtime,
                (expected_successor_id,),
            )
            assert set(successor_snapshot) == {expected_successor_id}
            predecessor_after_reconcile = _outbox_write_snapshot(
                postgres_runtime,
                (predecessor_id,),
            )
            schedule()
            assert (
                _outbox_write_snapshot(postgres_runtime, (predecessor_id,))
                == predecessor_after_reconcile
            )
            expected_predecessor_status = "cancelled"
            expected_event_type = (
                "task_outbox.expired_predecessors_superseded"
            )
            expected_failure_code = "dispatch_claim_expired_superseded"

        assert (
            _outbox_write_snapshot(postgres_runtime, (expected_successor_id,))
            == successor_snapshot
        )
        with postgres_runtime.session_factory() as session:
            persisted_job = session.get(AtlasProcessingJobRow, job.job_id)
            exact_rows = session.scalars(
                select(AtlasTaskOutboxRow)
                .where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.queue_name == "atlas.processing",
                    AtlasTaskOutboxRow.payload == retry_payload,
                )
                .order_by(AtlasTaskOutboxRow.outbox_id)
            ).all()
            reconciliation_audits = session.scalars(
                select(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.event_type == expected_event_type,
                    AtlasAuditEventRow.event_metadata["job_id"].as_string()
                    == job.job_id,
                )
            ).all()

        assert persisted_job is not None
        assert persisted_job.status == "retry_wait"
        assert len(exact_rows) == 2
        persisted_predecessor = next(
            row for row in exact_rows if row.outbox_id == predecessor_id
        )
        pending_successors = [
            row for row in exact_rows if row.status == "pending"
        ]
        assert persisted_predecessor.status == expected_predecessor_status
        assert persisted_predecessor.claim_owner is None
        assert persisted_predecessor.claim_expires_at is None
        assert persisted_predecessor.attempts == 1
        if expected_predecessor_status == "dispatched":
            assert persisted_predecessor.dispatched_at is not None
            assert persisted_predecessor.last_error_code is None
        else:
            assert persisted_predecessor.dispatched_at is None
            assert persisted_predecessor.last_error_code == (
                "dispatch_claim_expired_superseded"
            )
        assert len(pending_successors) == 1
        assert pending_successors[0].outbox_id == expected_successor_id
        assert len(reconciliation_audits) == 1
        assert reconciliation_audits[0].event_metadata == {
            "operation": expected_event_type,
            "job_id": job.job_id,
            "status": (
                "dispatched"
                if expected_predecessor_status == "dispatched"
                else "cancelled"
            ),
            "failure_code": expected_failure_code,
        }
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_schedule_retry_overlaps_expired_reconciliation_under_one_lock_order(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_control = _seed_current_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        job = repository.create_processing_job(
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            job_kind="reprocess",
            idempotency_scope="c3-overlap-schedule-reconcile",
            idempotency_key="same-work-identity",
            created_by="user-c3-generation-concurrency",
            progress_total=1,
        )
        expired_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        batch_id = f"{job.job_id}:page:1"
        with postgres_runtime.session_factory() as session:
            predecessor_id = _add_fixture_outbox(
                session,
                repository,
                job_id=job.job_id,
                attempt=1,
                ordinal=0,
                available_at=expired_at,
                status="dispatching",
                batch_id=batch_id,
                identity_salt="overlap-predecessor",
            )
            session.flush()
            predecessor = session.get(AtlasTaskOutboxRow, predecessor_id)
            assert predecessor is not None
            predecessor.claim_owner = "dispatcher-overlap"
            predecessor.claim_expires_at = expired_at
            predecessor.attempts = 1
            immutable_identity = (
                predecessor.task_name,
                predecessor.queue_name,
                dict(predecessor.payload),
                predecessor.celery_task_id,
            )
            session.commit()

        retry_payload = {
            "job_id": job.job_id,
            "batch_id": batch_id,
            "attempt": 1,
            "schema_version": 1,
        }
        work_key = document_processing_owner._outbox_work_identity_owner_key(
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload=retry_payload,
        )
        schedule_holds_complete_plan = Event()
        reconcile_attempted_shared_plan = Event()
        release_schedule = Event()
        original_acquire = document_processing_owner.acquire_owner_locks

        def instrumented_acquire(session, *, domain_keys=(), identity_keys=()):
            keys = tuple(identity_keys)
            name = current_thread().name
            if work_key in keys and name.startswith("reconcile"):
                reconcile_attempted_shared_plan.set()
            original_acquire(
                session,
                domain_keys=domain_keys,
                identity_keys=keys,
            )
            if work_key in keys and name.startswith("schedule"):
                schedule_holds_complete_plan.set()
                if not release_schedule.wait(timeout=10):
                    raise TimeoutError("schedule lock-plan release timed out")

        monkeypatch.setattr(
            document_processing_owner,
            "acquire_owner_locks",
            instrumented_acquire,
        )

        def schedule() -> None:
            repository.schedule_retry(
                job.job_id,
                expected_attempt=1,
                task_name="atlas.processing.process_batch",
                queue_name="atlas.processing",
                payload={"job_id": job.job_id, "batch_id": batch_id},
                code="processing_dependency_unavailable",
                detail="overlap",
                delay_seconds=0,
            )

        def reconcile() -> None:
            repository.reconcile_expired_claims(limit=200)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="schedule") as schedule_pool, ThreadPoolExecutor(max_workers=1, thread_name_prefix="reconcile") as reconcile_pool:
            schedule_future = schedule_pool.submit(schedule)
            assert schedule_holds_complete_plan.wait(timeout=10)
            reconcile_future = reconcile_pool.submit(reconcile)
            assert reconcile_attempted_shared_plan.wait(timeout=10)
            assert not reconcile_future.done()
            release_schedule.set()
            schedule_future.result(timeout=20)
            reconcile_future.result(timeout=20)
        with postgres_runtime.session_factory() as session:
            predecessor = session.get(AtlasTaskOutboxRow, predecessor_id)
            exact_rows = session.scalars(
                select(AtlasTaskOutboxRow).where(
                    AtlasTaskOutboxRow.task_name
                    == "atlas.processing.process_batch",
                    AtlasTaskOutboxRow.queue_name == "atlas.processing",
                    AtlasTaskOutboxRow.payload == retry_payload,
                )
            ).all()
        assert predecessor is not None
        assert (
            predecessor.task_name,
            predecessor.queue_name,
            dict(predecessor.payload),
            predecessor.celery_task_id,
        ) == immutable_identity
        assert sum(row.status == "pending" for row in exact_rows) == 1
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)


def test_final_publisher_moves_source_pointer_and_replays_exactly(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_active_source(postgres_runtime)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    try:
        manifests = _prepare_competing_publications(
            postgres_runtime,
            repository,
            ordinals=("publisher",),
        )
        ((winner_id, winner_digest),) = tuple(manifests.items())
        assert repository.publish_job(
            winner_id,
            expected_attempt=1,
            verified_manifest_digest=winner_digest,
        )

        with postgres_runtime.session_factory() as session:
            document = session.get(AtlasDocumentRow, DOCUMENT_ID)
            winner = session.get(AtlasProcessingJobRow, winner_id)
            indexes = session.scalars(
                select(AtlasIndexGenerationRow)
                .where(AtlasIndexGenerationRow.document_id == DOCUMENT_ID)
                .order_by(AtlasIndexGenerationRow.index_generation_id)
            ).all()
            generations = session.scalars(
                select(AtlasProcessingGenerationRow)
                .where(AtlasProcessingGenerationRow.document_id == DOCUMENT_ID)
                .order_by(AtlasProcessingGenerationRow.processing_generation)
            ).all()

        assert document is not None
        assert winner is not None
        assert document.active_index_generation_id == winner.index_generation_id
        assert document.active_processing_generation == winner.processing_generation
        assert winner.status == "succeeded"
        assert [row.status for row in indexes].count("active") == 1
        assert [row.status for row in generations].count("active") == 1
        assert next(
            row
            for row in indexes
            if row.index_generation_id == winner.index_generation_id
        ).status == "active"
        assert next(
            row
            for row in generations
            if row.processing_generation == winner.processing_generation
        ).status == "active"
        assert next(
            row for row in indexes if row.index_generation_id == SOURCE_INDEX_GENERATION_ID
        ).status == "retired"
        assert next(
            row for row in generations if row.processing_generation == 1
        ).status == "retired"

        assert repository.publish_job(
            winner_id,
            expected_attempt=1,
            verified_manifest_digest=winner_digest,
        )

        protected_visual = PostgresProductionKnowledgeRowSource(
            postgres_runtime.session_factory
        ).read_exact_citation_evidence(
            evidence_ref=(
                "visual|kh_document_current|1|1000,2000,9000,8000|"
                + "a" * 64
            ),
            document_version_ref=DOCUMENT_VERSION_ID,
            processing_generation_ref=(
                f"processing-generation-{winner.processing_generation}"
            ),
            index_generation_ref=winner.index_generation_id,
        )
        assert protected_visual is not None
        assert protected_visual.locator_label == (
            "Page 1 bbox [1000,2000,9000,8000]"
        )
    finally:
        _delete_fixture(postgres_runtime, restore_control=previous_control)
