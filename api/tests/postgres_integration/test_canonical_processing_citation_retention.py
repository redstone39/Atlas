from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest
from sqlalchemy import delete

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingJobRow,
    AtlasProcessingGenerationRow,
    AtlasSearchChunkRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationScopeTagRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasEvidenceRow,
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_authorization_v1_adapter import (
    PostgresAuthorizationV1Adapter,
)
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.postgres_owner.authorization import (
    PostgresAuthorizationStore,
)
from atlas_production.infrastructure.postgres_owner.generation_retention import (
    PostgresGenerationRetentionOwner,
)
from atlas_production.infrastructure.postgres_owner.conversation_v1 import (
    CreateConversationInput,
    PostgresConversationV1Store,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    CatalogDocumentInput,
    CreateCatalogInput,
    EvidencePackLineageInput,
    MaterializeEvidencePackInput,
    PersistInvocationResultInput,
    PostgresRetrievalV1Store,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    PostgresCanonicalRetrievalLineage,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    PostgresProductionKnowledgeRowSource,
    ProductionCurrentResourceAuthorizationReader,
    _opaque_evidence_ref,
    canonical_document_resource_ref,
)
from atlas_production.modules.authorization.public import LineageResourceV1
from atlas_production.modules.processing_pipeline.public import (
    CreateGenerationRetentionV1,
    GenerationRetentionResourceV1,
    ReleaseGenerationRetentionV1,
)
from test_document_processing_generation_concurrency import (
    DOCUMENT_ID,
    DOCUMENT_VERSION_ID,
    PROJECT_ID,
    SOURCE_ARTIFACT_ID,
    SOURCE_DIGEST,
    SOURCE_INDEX_GENERATION_ID,
    _delete_fixture,
    _seed_active_source,
)


ACTOR_ID = "user-cpr003-citation-retention"
IDENTITY_ID = "processing-identity-cpr003"
OLD_REVISION_ID = "processing-revision-cpr003-old"
NEW_REVISION_ID = "processing-revision-cpr003-new"
NEW_INDEX_ID = "index-cpr003-new"
BINDING_B_ID = "document-cpr003-binding-b"
BINDING_B_VERSION_ID = "version-cpr003-binding-b"
EVIDENCE_ID = "evidence-cpr003-old"
OLD_MANIFEST = "f" * 64
NEW_MANIFEST = "e" * 64
PROCESSING_FINGERPRINT = "d" * 64
NOW_TEXT = "2026-07-23T00:00:00+00:00"
PERMISSION_GRANT_ID = "grant-cpr003-project-reader"
DEFAULT_CONVERSATION_ID = "conversation-cpr003-default-all"
SELECTED_CONVERSATION_ID = "conversation-cpr003-selected"
NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _seed_canonical_fixture(runtime: PostgresRuntime) -> dict[str, object] | None:
    # The legacy helper predates canonical identity FKs. Make its fixed source
    # artifact reusable even if a prior interrupted canonical test left rows.
    with runtime.session_factory() as session, session.begin():
        identity_ids = [
            row.processing_identity_id
            for row in session.query(AtlasProcessingIdentityRow)
            .filter(
                AtlasProcessingIdentityRow.source_artifact_id == SOURCE_ARTIFACT_ID
            )
            .all()
        ]
        if identity_ids:
            session.query(AtlasDocumentRow).filter(
                AtlasDocumentRow.processing_identity_id.in_(identity_ids)
            ).update(
                {AtlasDocumentRow.processing_identity_id: None},
                synchronize_session=False,
            )
            session.query(AtlasProcessingJobRow).filter(
                AtlasProcessingJobRow.processing_identity_id.in_(identity_ids)
            ).update(
                {
                    AtlasProcessingJobRow.processing_identity_id: None,
                    AtlasProcessingJobRow.processing_revision_id: None,
                },
                synchronize_session=False,
            )
            revision_ids = [
                row.processing_revision_id
                for row in session.query(AtlasProcessingRevisionRow)
                .filter(
                    AtlasProcessingRevisionRow.processing_identity_id.in_(identity_ids)
                )
                .all()
            ]
            session.query(AtlasProcessingIdentityRow).filter(
                AtlasProcessingIdentityRow.processing_identity_id.in_(identity_ids)
            ).update(
                {AtlasProcessingIdentityRow.current_revision_id: None},
                synchronize_session=False,
            )
            if revision_ids:
                session.query(AtlasIndexGenerationRow).filter(
                    AtlasIndexGenerationRow.processing_revision_id.in_(revision_ids)
                ).update(
                    {AtlasIndexGenerationRow.processing_revision_id: None},
                    synchronize_session=False,
                )
                session.query(AtlasEvidenceRow).filter(
                    AtlasEvidenceRow.processing_revision_id.in_(revision_ids)
                ).update(
                    {AtlasEvidenceRow.processing_revision_id: None},
                    synchronize_session=False,
                )
                session.query(AtlasSearchChunkRow).filter(
                    AtlasSearchChunkRow.processing_revision_id.in_(revision_ids)
                ).update(
                    {AtlasSearchChunkRow.processing_revision_id: None},
                    synchronize_session=False,
                )
                session.query(AtlasEvidencePageArtifactRow).filter(
                    AtlasEvidencePageArtifactRow.processing_revision_id.in_(revision_ids)
                ).update(
                    {AtlasEvidencePageArtifactRow.processing_revision_id: None},
                    synchronize_session=False,
                )
                session.execute(
                    delete(AtlasProcessingRevisionRow).where(
                        AtlasProcessingRevisionRow.processing_revision_id.in_(
                            revision_ids
                        )
                    )
                )
            session.execute(
                delete(AtlasProcessingIdentityRow).where(
                    AtlasProcessingIdentityRow.processing_identity_id.in_(identity_ids)
                )
            )
    previous_control = _seed_active_source(runtime)
    with runtime.session_factory() as session, session.begin():
        session.add(
            AtlasProjectRow(
                project_id=PROJECT_ID,
                name="CPR-003 canonical citation retention",
                policy_profile_id="policy-default",
            )
        )
        session.add(
            AtlasDocumentTagRow(
                document_id=DOCUMENT_ID,
                tag_type="project",
                tag_id=PROJECT_ID,
                created_at=NOW_TEXT,
            )
        )
        session.add(
            AtlasUserRow(
                actor_id=ACTOR_ID,
                display_name="CPR-003 reader",
                email=None,
                system_role="user",
                password_digest=None,
                active=True,
                actor_type="user",
                created_at=NOW_TEXT,
            )
        )
        session.add(
            AtlasPermissionGrantRow(
                grant_id=PERMISSION_GRANT_ID,
                project_id=PROJECT_ID,
                subject_type="user",
                subject_id=ACTOR_ID,
                role="viewer",
                effect="allow",
                status="active",
                created_at=NOW_TEXT,
                revoked_at=None,
            )
        )
        identity = AtlasProcessingIdentityRow(
            processing_identity_id=IDENTITY_ID,
            source_sha256=SOURCE_DIGEST,
            processing_fingerprint=PROCESSING_FINGERPRINT,
            processing_spec={"contract": "cpr-003"},
            source_artifact_id=SOURCE_ARTIFACT_ID,
            source_artifact_checksum_sha256=SOURCE_DIGEST,
            current_revision_id=None,
            created_at=NOW_TEXT,
        )
        session.add(identity)
        session.flush()
        session.add(
            AtlasProcessingRevisionRow(
                processing_revision_id=OLD_REVISION_ID,
                processing_identity_id=IDENTITY_ID,
                revision_number=1,
                state="ready",
                manifest_digest=OLD_MANIFEST,
                page_artifact_count=0,
                evidence_count=1,
                chunk_count=1,
                index_point_count=1,
                created_at=NOW_TEXT,
                finalized_at=NOW_TEXT,
            )
        )
        session.flush()
        identity.current_revision_id = OLD_REVISION_ID
        document = session.get(AtlasDocumentRow, DOCUMENT_ID)
        index = session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID)
        assert document is not None
        assert index is not None
        document.processing_identity_id = IDENTITY_ID
        index.processing_revision_id = OLD_REVISION_ID
        session.add(
            AtlasEvidenceRow(
                evidence_id=EVIDENCE_ID,
                document_id=DOCUMENT_ID,
                document_title="Canonical retained citation",
                locator_label="Page 1",
                snippet="old retained citation",
                content="old retained citation remains exact",
                status="ready",
                document_version_id=DOCUMENT_VERSION_ID,
                processing_generation=1,
                processing_revision_id=OLD_REVISION_ID,
                source_region_id="region-cpr003-old",
                channel_id="generic_text",
                output_contract_version="eir-draft-v1",
                claim_support_role="claim_grounding",
                locator_payload={},
                content_fingerprint="c" * 64,
                processing_fingerprint=PROCESSING_FINGERPRINT,
                profile_id="source-profile",
                profile_revision=1,
                promotion_decision_id=None,
                quality_flag_refs=[],
                trace_ref=None,
                supersedes_evidence_id=None,
                evidence_artifact_id=None,
            )
        )
        session.add(
            AtlasSearchChunkRow(
                chunk_id="chunk-cpr003-old",
                batch_id="batch-cpr003-old",
                document_id=DOCUMENT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                processing_generation=1,
                processing_revision_id=OLD_REVISION_ID,
                index_generation_id=SOURCE_INDEX_GENERATION_ID,
                evidence_id=EVIDENCE_ID,
                segment_id="segment-cpr003-old",
                window_ordinal=0,
                normalized_text="old retained citation remains exact",
                locator={},
                content_fingerprint="c" * 64,
                processing_fingerprint=PROCESSING_FINGERPRINT,
                search_vector=None,
                status="active",
                created_at=NOW,
            )
        )
    return previous_control


def _publish_new_revision(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session, session.begin():
        identity = session.get(AtlasProcessingIdentityRow, IDENTITY_ID)
        document = session.get(AtlasDocumentRow, DOCUMENT_ID)
        old_index = session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID)
        old_generation = session.get(AtlasProcessingGenerationRow, (DOCUMENT_ID, 1))
        assert identity is not None
        assert document is not None
        assert old_index is not None
        assert old_generation is not None
        old_index.status = "retired"
        old_generation.status = "retired"
        session.add(
            AtlasProcessingRevisionRow(
                processing_revision_id=NEW_REVISION_ID,
                processing_identity_id=IDENTITY_ID,
                revision_number=2,
                state="ready",
                manifest_digest=NEW_MANIFEST,
                page_artifact_count=0,
                evidence_count=0,
                chunk_count=0,
                index_point_count=0,
                created_at=NOW_TEXT,
                finalized_at=NOW_TEXT,
            )
        )
        session.flush()
        session.add(
            AtlasProcessingGenerationRow(
                document_id=DOCUMENT_ID,
                processing_generation=2,
                document_version_id=DOCUMENT_VERSION_ID,
                profile_id="source-profile",
                profile_revision=1,
                status="active",
                expected_page_count=0,
                actual_page_count=0,
                expected_evidence_count=0,
                actual_evidence_count=0,
                expected_chunk_count=0,
                actual_chunk_count=0,
                manifest_digest=NEW_MANIFEST,
                created_at=NOW,
                published_at=NOW,
            )
        )
        session.add(
            AtlasIndexGenerationRow(
                index_generation_id=NEW_INDEX_ID,
                document_id=DOCUMENT_ID,
                document_version_id=DOCUMENT_VERSION_ID,
                processing_revision_id=NEW_REVISION_ID,
                source_processing_generation=2,
                embedding_profile_id="source-embedding",
                embedding_profile={"model": "source"},
                qdrant_collection="atlas_evidence_v1",
                status="active",
                expected_point_count=0,
                actual_point_count=0,
                expected_fts_count=0,
                actual_fts_count=0,
                manifest_digest=NEW_MANIFEST,
                supersedes_index_generation_id=SOURCE_INDEX_GENERATION_ID,
                created_at=NOW,
                published_at=NOW,
            )
        )
        identity.current_revision_id = NEW_REVISION_ID
        document.active_processing_generation = 2
        document.active_index_generation_id = NEW_INDEX_ID


def _cleanup_canonical_fixture(
    runtime: PostgresRuntime, *, restore_control: dict[str, object] | None
) -> None:
    with runtime.session_factory() as session:
        identity = session.get(AtlasProcessingIdentityRow, IDENTITY_ID)
        if identity is not None and identity.current_revision_id is not None:
            # Published canonical identities are intentionally immutable.
            # Like the provider concurrency suite, this test therefore runs
            # against a fresh dedicated database and leaves published evidence
            # for database-level disposal.
            return
    with runtime.session_factory() as session, session.begin():
        document = session.get(AtlasDocumentRow, DOCUMENT_ID)
        identity = session.get(AtlasProcessingIdentityRow, IDENTITY_ID)
        if document is not None:
            document.processing_identity_id = None
        if identity is not None:
            identity.current_revision_id = None
        session.flush()
        session.execute(
            delete(AtlasSearchChunkRow).where(
                AtlasSearchChunkRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(
            delete(AtlasEvidenceRow).where(AtlasEvidenceRow.document_id == DOCUMENT_ID)
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
            delete(AtlasProcessingRevisionRow).where(
                AtlasProcessingRevisionRow.processing_identity_id == IDENTITY_ID
            )
        )
        session.execute(
            delete(AtlasProcessingIdentityRow).where(
                AtlasProcessingIdentityRow.processing_identity_id == IDENTITY_ID
            )
        )
        session.execute(
            delete(AtlasDocumentTagRow).where(
                AtlasDocumentTagRow.document_id == DOCUMENT_ID
            )
        )
        session.execute(delete(AtlasUserRow).where(AtlasUserRow.actor_id == ACTOR_ID))
        session.execute(
            delete(AtlasPermissionGrantRow).where(
                AtlasPermissionGrantRow.grant_id == PERMISSION_GRANT_ID
            )
        )
        session.execute(
            delete(AtlasProjectRow).where(AtlasProjectRow.project_id == PROJECT_ID)
        )
    _delete_fixture(runtime, restore_control=restore_control)


def test_old_exact_citation_survives_current_switch_and_retention_fences_cleanup(
    postgres_runtime: PostgresRuntime,
) -> None:
    previous_control = _seed_canonical_fixture(postgres_runtime)
    retention = PostgresGenerationRetentionOwner(postgres_runtime.session_factory)
    repository = PostgresDocumentProcessingAdapter(postgres_runtime.session_factory)
    rows = PostgresProductionKnowledgeRowSource(postgres_runtime.session_factory)
    resource_ref = canonical_document_resource_ref(DOCUMENT_ID)
    authorization = PostgresAuthorizationV1Adapter(
        PostgresAuthorizationStore(postgres_runtime.session_factory),
        ProductionCurrentResourceAuthorizationReader(rows),
    )
    lineage = LineageResourceV1(
        resource_ref=resource_ref,
        resource_kind="document",
        lifecycle_epoch=1,
        version_ref=DOCUMENT_VERSION_ID,
        generation_ref=SOURCE_INDEX_GENERATION_ID,
        processing_generation_ref="processing-generation-1",
        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
    )
    conversations = PostgresConversationV1Store(postgres_runtime.session_factory)
    default_conversation = conversations.create(
        CreateConversationInput(
            conversation_id=DEFAULT_CONVERSATION_ID,
            actor_id=ACTOR_ID,
            title="Default all",
            idempotency_key="create-cpr003-default-all",
            response_language="en",
        )
    )
    selected_conversation = conversations.create(
        CreateConversationInput(
            conversation_id=SELECTED_CONVERSATION_ID,
            actor_id=ACTOR_ID,
            title="Selected project",
            idempotency_key="create-cpr003-selected",
            response_language="en",
            tag_refs=(("project", PROJECT_ID),),
        )
    )
    assert default_conversation.conversation_id == DEFAULT_CONVERSATION_ID
    assert selected_conversation.conversation_id == SELECTED_CONVERSATION_ID
    assert {
        item.resource_ref
        for item in rows.grant_resources(
            actor_id=ACTOR_ID,
            conversation_id=DEFAULT_CONVERSATION_ID,
        ).documents
    } == {resource_ref}
    assert {
        item.resource_ref
        for item in rows.grant_resources(
            actor_id=ACTOR_ID,
            conversation_id=SELECTED_CONVERSATION_ID,
        ).documents
    } == {resource_ref}
    try:
        claim = retention.create_generation_retention(
            CreateGenerationRetentionV1(
                execution_id="execution-cpr003-retention",
                resources=[
                    GenerationRetentionResourceV1(
                        document_version_ref=DOCUMENT_VERSION_ID,
                        processing_generation_ref="processing-generation-1",
                        processing_revision_ref=OLD_REVISION_ID,
                        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                        manifest_digest=OLD_MANIFEST,
                    )
                ],
                idempotency_key="create-cpr003-retention",
            )
        )
        canonical_lineage = PostgresCanonicalRetrievalLineage(
            postgres_runtime.session_factory
        )
        with postgres_runtime.session_factory() as session, session.begin():
            binding_a = session.get(AtlasDocumentRow, DOCUMENT_ID)
            version_a = session.get(AtlasDocumentVersionRow, DOCUMENT_VERSION_ID)
            assert binding_a is not None
            assert version_a is not None
            binding_values = {
                column.name: getattr(binding_a, column.name)
                for column in AtlasDocumentRow.__table__.columns
            }
            binding_values.update(
                document_id=BINDING_B_ID,
                title="CPR-003 binding B",
                active_index_generation_id=None,
                active_processing_generation=0,
                processing_job_id=None,
            )
            version_payload = deepcopy(version_a.payload)
            version_payload.update(
                document_id=BINDING_B_ID,
                document_version_id=BINDING_B_VERSION_ID,
            )
            session.add(AtlasDocumentRow(**binding_values))
            session.add(
                AtlasDocumentVersionRow(
                    document_version_id=BINDING_B_VERSION_ID,
                    document_id=BINDING_B_ID,
                    payload=version_payload,
                )
            )
        with pytest.raises(
            RetrievalStoreConflict,
            match="catalog document revision pin is unavailable",
        ):
            canonical_lineage.canonicalize_catalog(
                CreateCatalogInput(
                    catalog_ref="catalog-cpr003-binding-mismatch",
                    execution_id="execution-cpr003-binding-mismatch",
                    grant_ref="grant-cpr003-binding-a",
                    generation_retention_ref=claim.retention_ref,
                    authorization_revision=1,
                    retrieval_generation_ref="retrieval-cpr003-binding-mismatch",
                    documents=(
                        CatalogDocumentInput(
                            document_handle="document-handle-cpr003-mismatch",
                            resource_ref=resource_ref,
                            lifecycle_epoch=1,
                            document_version_ref=BINDING_B_VERSION_ID,
                            generation_ref=SOURCE_INDEX_GENERATION_ID,
                            processing_generation_ref="processing-generation-1",
                            processing_revision_ref=None,
                            index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                            manifest_digest=OLD_MANIFEST,
                            descriptor={},
                        ),
                    ),
                    idempotency_key="catalog-cpr003-binding-mismatch",
                )
            )
        retrieval = PostgresRetrievalV1Store(
            postgres_runtime.session_factory,
            canonicalize_catalog=canonical_lineage.canonicalize_catalog,
            canonicalize_evidence_pack=(
                canonical_lineage.canonicalize_evidence_pack
            ),
        )
        catalog = retrieval.create_catalog(
            CreateCatalogInput(
                catalog_ref="catalog-cpr003",
                execution_id="execution-cpr003-catalog",
                grant_ref="grant-cpr003",
                generation_retention_ref=claim.retention_ref,
                authorization_revision=1,
                retrieval_generation_ref="retrieval-generation-cpr003",
                documents=(
                    CatalogDocumentInput(
                        document_handle="document-handle-cpr003",
                        resource_ref=resource_ref,
                        lifecycle_epoch=1,
                        document_version_ref=DOCUMENT_VERSION_ID,
                        generation_ref=SOURCE_INDEX_GENERATION_ID,
                        processing_generation_ref="processing-generation-1",
                        processing_revision_ref=None,
                        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                        manifest_digest=OLD_MANIFEST,
                        descriptor={
                            "display_name": "Canonical retained citation",
                            "media_type": "text/plain",
                            "modalities": ["text"],
                        },
                    ),
                ),
                idempotency_key="catalog-cpr003",
            )
        )
        assert catalog.documents[0].processing_revision_ref == OLD_REVISION_ID
        result = retrieval.persist_invocation_result(
            PersistInvocationResultInput(
                invocation_id="invocation-cpr003",
                result_ref="result-cpr003",
                execution_id=catalog.execution_id,
                catalog_ref=catalog.catalog_ref,
                invocation_ordinal=1,
                action="search_knowledge",
                schema_version="search-knowledge-v1",
                canonical_arguments={
                    "action": "search_knowledge",
                    "query_text": "old retained",
                },
                result_type="knowledge_search_result",
                observation={"evidence": []},
                error_code=None,
                handles=(
                    ResultHandleInput(
                        handle="evidence-handle-cpr003",
                        handle_kind="evidence",
                        resource_ref=_opaque_evidence_ref(EVIDENCE_ID),
                        evidence_identity="evidence-identity-cpr003",
                        document_handle="document-handle-cpr003",
                    ),
                ),
            )
        )
        evidence_digest = hashlib.sha256(
            json.dumps(
                {
                    "evidence_ref": _opaque_evidence_ref(EVIDENCE_ID),
                    "evidence_identity": "evidence-identity-cpr003",
                    "document_handle": "document-handle-cpr003",
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        pack = retrieval.materialize_evidence_pack(
            MaterializeEvidencePackInput(
                evidence_pack_ref="evidence-pack-cpr003",
                execution_id=catalog.execution_id,
                catalog_ref=catalog.catalog_ref,
                items=(
                    EvidencePackLineageInput(
                        evidence_handle="evidence-handle-cpr003",
                        evidence_ref=_opaque_evidence_ref(EVIDENCE_ID),
                        evidence_digest=evidence_digest,
                        resource_ref=resource_ref,
                        document_version_ref=DOCUMENT_VERSION_ID,
                        processing_revision_ref=OLD_REVISION_ID,
                        index_generation_ref=SOURCE_INDEX_GENERATION_ID,
                        page_artifact_ref="caller-value-must-not-survive",
                        result_ref=result.result_ref,
                        invocation_ordinal=result.invocation_ordinal,
                    ),
                ),
            )
        )
        assert pack.items[0].processing_revision_ref == OLD_REVISION_ID
        assert pack.items[0].index_generation_ref == SOURCE_INDEX_GENERATION_ID
        assert pack.items[0].page_artifact_ref is None
        _publish_new_revision(postgres_runtime)

        exact = rows.read_exact_citation_evidence(
            evidence_ref=_opaque_evidence_ref(EVIDENCE_ID),
            document_version_ref=DOCUMENT_VERSION_ID,
            processing_generation_ref="processing-generation-1",
            processing_revision_ref=OLD_REVISION_ID,
            index_generation_ref=SOURCE_INDEX_GENERATION_ID,
        )
        assert exact is not None
        assert exact.content == "old retained citation remains exact"
        assert authorization.current_visibility(
            actor_id=ACTOR_ID, resources=[lineage]
        )[0].decision == "visible"

        repository.cleanup_retired_generations(limit=10)
        with postgres_runtime.session_factory() as session:
            assert (
                session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID)
                is not None
            )

        with postgres_runtime.session_factory() as session, session.begin():
            grant = session.get(AtlasPermissionGrantRow, PERMISSION_GRANT_ID)
            assert grant is not None
            grant.status = "revoked"
            grant.revoked_at = NOW_TEXT
        assert rows.grant_resources(
            actor_id=ACTOR_ID,
            conversation_id=DEFAULT_CONVERSATION_ID,
        ).documents == ()
        assert rows.grant_resources(
            actor_id=ACTOR_ID,
            conversation_id=SELECTED_CONVERSATION_ID,
        ).documents == ()
        with postgres_runtime.session_factory() as session:
            assert session.get(
                AtlasTurnConversationScopeTagRow,
                (SELECTED_CONVERSATION_ID, "project", PROJECT_ID),
            ) is not None
        with postgres_runtime.session_factory() as session, session.begin():
            actor = session.get(AtlasUserRow, ACTOR_ID)
            assert actor is not None
            actor.active = False
        revoked = authorization.current_visibility(
            actor_id=ACTOR_ID, resources=[lineage]
        )[0]
        assert revoked.decision == "hidden"
        assert revoked.reason == "access_revoked"

        retention.release_generation_retention(
            ReleaseGenerationRetentionV1(
                execution_id="execution-cpr003-retention",
                retention_ref=claim.retention_ref,
                idempotency_key="release-cpr003-retention",
            )
        )
        repository.cleanup_retired_generations(limit=10)
        with postgres_runtime.session_factory() as session:
            assert (
                session.get(AtlasIndexGenerationRow, SOURCE_INDEX_GENERATION_ID)
                is None
            )
        assert (
            rows.read_exact_citation_evidence(
                evidence_ref=_opaque_evidence_ref(EVIDENCE_ID),
                document_version_ref=DOCUMENT_VERSION_ID,
                processing_generation_ref="processing-generation-1",
                processing_revision_ref=OLD_REVISION_ID,
                index_generation_ref=SOURCE_INDEX_GENERATION_ID,
            )
            is None
        )
    finally:
        _cleanup_canonical_fixture(
            postgres_runtime, restore_control=previous_control
        )
