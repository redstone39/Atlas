from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from atlas_production.async_runtime.vector_index import VectorIndex
from atlas_production.async_runtime.public import best_effort_dispatch
from atlas_production.infrastructure.artifact_storage_config_adapter import (
    RootOnlyStorageTargetConfig,
)
from atlas_production.infrastructure.artifact_storage_filesystem_adapter import (
    LocalArtifactFilesystemAdapter,
)
from atlas_production.infrastructure.persistence import artifact_storage as rows
from atlas_production.infrastructure.postgres_agent_adapter import (
    build_postgres_agent_access,
)
from atlas_production.infrastructure.processing_jobs_authorization import (
    RbacProcessingJobsAuthorization,
)
from atlas_production.infrastructure.postgres_audit_adapter import (
    build_postgres_audit_adapter,
)
from atlas_production.infrastructure.postgres_document_artifact_provider import (
    PostgresDocumentRestoreProofProvider,
    PostgresDocumentUploadJourneyProvider,
)
from atlas_production.infrastructure.postgres_document_intake_adapter import (
    PostgresDocumentIntakeAdapter,
)
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.postgres_document_upload import (
    NewDocumentUploadCommand,
    NewDocumentUploadJourneyCommand,
    NewDocumentUploadRequestBoundaryCommand,
)
from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher
from atlas_production.infrastructure.ldap_directory_gateway import (
    LdapDirectoryGateway,
    validate_directory_filter,
)
from atlas_production.infrastructure.postgres_identity_adapter import (
    PostgresCurrentPrincipal,
    PostgresIdentityAccessRepository,
    PostgresInviteScopeGrantAdapter,
)
from atlas_production.infrastructure.postgres_model_routing_adapter import PostgresModelRoutingAdapter
from atlas_production.infrastructure.postgres_ops_adapter import PostgresOpsAdapter
from atlas_production.infrastructure.postgres_owner.ops import PostgresOpsReadinessRepository
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
)
from atlas_production.infrastructure.postgres_processing_adapter import PostgresProcessingAdapter
from atlas_production.infrastructure.postgres_project_adapter import build_postgres_project_governance
from atlas_production.infrastructure.postgres_authorization_v1_adapter import (
    PostgresAuthorizationV1Adapter,
)
from atlas_production.infrastructure.postgres_context_engineering_v3_adapter import (
    PostgresContextEngineeringV3Adapter,
)
from atlas_production.infrastructure.postgres_conversation_v1_adapter import (
    PostgresConversationV1Adapter,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    KnowledgeToolService,
    PostgresCanonicalRetrievalLineage,
)
from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    PostgresProductionKnowledgeRowSource,
    PostgresVisualPageRenderer,
    ProductionAuthorizedGrantResourceSource,
    ProductionCurrentResourceAuthorizationReader,
    ProductionKnowledgeRetrievalBackend,
)
from atlas_production.infrastructure.strict_turn_model_adapter import StrictProviderTurnModel
from atlas_production.infrastructure.context_compaction import (
    ProviderContextSummaryGenerator,
    SynchronousContextCompactor,
)
from atlas_production.infrastructure.conversation_token_usage import (
    PostgresConversationTokenUsageReader,
)
from atlas_production.infrastructure.strict_posthoc_claim_evaluator import (
    StrictPostHocClaimEvaluator,
)
from atlas_production.infrastructure.thread_turn_carrier import (
    ThreadTurnCarrier,
    TurnLeaseFailureSweeper,
)
from atlas_production.infrastructure.turn_execution_orchestrator import (
    StatelessTurnExecutionOrchestrator,
)
from atlas_production.infrastructure.turn_model_input_adapter import (
    PublicOwnerTurnModelInputSource,
)
from atlas_production.infrastructure.turn_input_projection import (
    ProviderTurnInputProjector,
)
from atlas_production.infrastructure.turn_release_reconciler import (
    TurnResourceReleaseReconciler,
)
from atlas_production.infrastructure.postgres_owner.authorization import PostgresAuthorizationStore
from atlas_production.infrastructure.postgres_owner.context_engineering import (
    PostgresContextEngineeringStore,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import PostgresRetrievalV1Store
from atlas_production.infrastructure.postgres_owner.generation_retention import (
    PostgresGenerationRetentionOwner,
)
from atlas_production.infrastructure.postgres_owner.result_governance_v1 import (
    PostgresResultGovernanceV1Store,
)
from atlas_production.infrastructure.postgres_owner.citation_v1 import PostgresCitationV1Store
from atlas_production.modules.citation_preview.protected_read import (
    ProtectedCitationReadService,
    ProtectedDeclaredEvidenceReadService,
)
from atlas_production.infrastructure.postgres_owner.audit_v1 import PostgresAuditV1Store
from atlas_production.infrastructure.postgres_owner.turn_runtime import PostgresTurnRuntimeOwner
from atlas_production.infrastructure.postgres_owner.answer_behavior import (
    PostgresAnswerBehaviorOwner,
)
from atlas_production.infrastructure.notes_collaboration_client import (
    HttpNotesCollaborationClient,
)
from atlas_production.infrastructure.postgres_owner.notes import PostgresNotesOwner
from atlas_production.infrastructure.postgres_notes_attachments import (
    PostgresNotesAttachmentProvider,
)
from atlas_production.infrastructure.bounded_artifact_writer import BoundedArtifactWriter
from atlas_production.infrastructure.postgres_team_adapter import build_postgres_team_access
from atlas_production.infrastructure.processing_plugin_artifact_adapter import LocalProcessingPluginArtifactStore
from atlas_production.infrastructure.processing_runner_adapter import default_processing_runner
from atlas_production.infrastructure.provider_key_cipher import AesGcmCredentialCipher, CredentialCryptoError
from atlas_production.infrastructure.postgres_artifact_journeys import (
    ArtifactTargetFacts,
    ArtifactTargetJourneyBuilder,
    PostgresProtectedOriginalJourneyProvider,
)
from atlas_production.infrastructure.postgres_artifact_storage_adapter import (
    PostgresArtifactStorageAdapter,
)
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_owner.artifact import (
    BeginArtifactWriteCommand,
    ClaimArtifactReconciliationCommand,
    FinalizeArtifactReconciliationCommand,
    FinalizeArtifactWriteCommand,
    HeartbeatArtifactWriteCommand,
    ProtectedArtifactOpenCommand,
    TargetControlCommand,
)
from atlas_production.modules.audit.public import AdminAuditEventReadService
from atlas_production.modules.agent_runtime.public import AgentRuntimeApplication
from atlas_production.modules.conversation_audit.service import ConversationAuditService
from atlas_production.modules.model_routing.service import ModelRoutingService
from atlas_production.modules.ops.public import OpsReadinessService
from atlas_production.modules.identity_access.directory_service import DirectoryIdentityService
from atlas_production.modules.identity_access.agent_service import AgentAccessService
from atlas_production.modules.identity_access.service import IdentityAccessService
from atlas_production.modules.identity_access.team_service import TeamAccessService
from atlas_production.modules.document_intake.public import (
    DocumentLibraryApplication,
    DocumentLibraryExceptionTypes,
    DocumentLifecycleRequestInput,
    DocumentUploadAccessDenied,
    DocumentUploadReplayConflict,
    DocumentUploadUnauthenticated,
)
from atlas_production.modules.processing_pipeline.public import (
    DocumentLifecycleDenied,
    DocumentLifecycleProcessingAcceptance,
    DocumentProcessingCurrentnessConflict,
    ProcessingJobsApplication,
    ProcessingRegistryService,
)
from atlas_production.modules.project_governance.service import ProjectGovernanceService
from atlas_production.modules.notes.service import NotesApplicationService
from atlas_production.modules.workspace_turn.public import WorkspaceTurnApplication
from atlas_production.modules.answer_behavior.public import AnswerBehaviorService
from atlas_production.providers import default_provider_adapter_factory
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.artifact_storage.records import (
    StorageBlobRecord,
    StorageControlRecord,
    StorageFence,
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)
from atlas_production.shared.public import utc_now_iso


@dataclass(frozen=True, slots=True)
class ApiComposition:
    """Immutable request-runtime bundle; routes resolve only this object."""

    current_principal: PostgresCurrentPrincipal
    identity_access: IdentityAccessService
    directory_identity: DirectoryIdentityService
    agent_access: AgentAccessService
    agent_runtime: AgentRuntimeApplication
    team_access: TeamAccessService
    project_governance: ProjectGovernanceService
    workspace_scope_authority: ActionAwareAclAuthority
    processing_registry: ProcessingRegistryService
    document_intake: PostgresDocumentIntakeAdapter
    document_processing: PostgresDocumentProcessingAdapter
    document_library: DocumentLibraryApplication
    processing_jobs: ProcessingJobsApplication
    model_routing: ModelRoutingService
    answer_behavior: AnswerBehaviorService
    notes: NotesApplicationService
    ops_readiness: OpsReadinessService
    conversation_audit: ConversationAuditService
    workspace_turn: WorkspaceTurnApplication
    admin_audit_events: AdminAuditEventReadService
    artifact_storage: PostgresArtifactStorageAdapter
    protected_originals: PostgresProtectedOriginalJourneyProvider
    document_uploads: PostgresDocumentUploadJourneyProvider
    document_restore_proofs: PostgresDocumentRestoreProofProvider
    turn_execution_carrier: ThreadTurnCarrier
    turn_resource_release_reconciler: TurnResourceReleaseReconciler
    turn_lease_failure_sweeper: TurnLeaseFailureSweeper


@dataclass(frozen=True, slots=True)
class _ProcessingRunnerProbe:
    def available(self) -> bool:
        return bool(os.environ.get("ATLAS_PLUGIN_RUNNER_URL"))


@dataclass(frozen=True, slots=True)
class _CredentialEncryptionProbe:
    def available(self) -> bool:
        try:
            AesGcmCredentialCipher.from_environment()
            return True
        except CredentialCryptoError:
            return False

class _EnvironmentDirectoryCipher:
    def encrypt(self, **kwargs):
        return AesGcmEnvelopeCipher.from_environment().encrypt(**kwargs)

    def decrypt(self, secret, **kwargs):
        return AesGcmEnvelopeCipher.from_environment().decrypt(secret, **kwargs)


@dataclass(frozen=True, slots=True)
class _ArtifactReadinessProbe:
    runtime: PostgresRuntime

    def readiness_available(self) -> bool:
        try:
            _active_artifact_filesystem(self.runtime)
            return True
        except Exception:
            return False


def _artifact_allowlisted_parents() -> tuple[Path, ...]:
    raw = os.environ.get("ATLAS_ARTIFACT_ALLOWED_PARENTS")
    if not raw:
        raise RuntimeError("ATLAS_ARTIFACT_ALLOWED_PARENTS is required")
    values = tuple(Path(item).expanduser() for item in raw.split(os.pathsep) if item)
    if not values:
        raise RuntimeError("ATLAS_ARTIFACT_ALLOWED_PARENTS is empty")
    return values


def _active_artifact_filesystem(
    runtime: PostgresRuntime,
) -> LocalArtifactFilesystemAdapter:
    config_path = os.environ.get("ATLAS_ARTIFACT_TARGET_CONFIG")
    if not config_path:
        raise RuntimeError("ATLAS_ARTIFACT_TARGET_CONFIG is required")
    with runtime.session_factory() as session:
        control = session.get(rows.AtlasArtifactStorageControlRow, "global")
        target = (
            session.get(
                rows.AtlasArtifactStorageTargetRow,
                (control.active_target_id, control.active_target_revision),
            )
            if control is not None
            and control.active_target_id is not None
            and control.active_target_revision is not None
            else None
        )
        if (
            control is None
            or control.mode != "active"
            or control.root_identity_digest is None
            or target is None
            or target.status != "active"
        ):
            raise RuntimeError("artifact storage is not active")
        target_id = target.target_id
        target_revision = target.target_revision
        config_key = target.config_key
        expected_root = control.root_identity_digest
    configured = RootOnlyStorageTargetConfig(config_path).load().get(target_id)
    if (
        configured is None
        or configured["revision"] != target_revision
        or configured["config_key"] != config_key
    ):
        raise RuntimeError("artifact target configuration is unavailable")
    filesystem = LocalArtifactFilesystemAdapter(
        configured["raw_path"],
        allowlisted_parents=_artifact_allowlisted_parents(),
        create_layout=False,
    )
    if filesystem.root_identity_digest != expected_root:
        raise RuntimeError("artifact storage root identity changed")
    return filesystem


def _artifact_adapter(
    runtime: PostgresRuntime,
    filesystem: LocalArtifactFilesystemAdapter,
) -> PostgresArtifactStorageAdapter:
    session_factory = runtime.session_factory
    return PostgresArtifactStorageAdapter(
        ProtectedArtifactOpenCommand(session_factory),
        BeginArtifactWriteCommand(session_factory),
        FinalizeArtifactWriteCommand(session_factory),
        TargetControlCommand(session_factory),
        ClaimArtifactReconciliationCommand(session_factory),
        FinalizeArtifactReconciliationCommand(session_factory),
        filesystem,
        HeartbeatArtifactWriteCommand(session_factory),
    )


def build_api_composition(
    runtime: PostgresRuntime | None = None,
) -> ApiComposition:
    """Build the one immutable Production API graph from typed PostgreSQL owners."""

    selected = runtime or PostgresRuntime.from_environment()
    selected.bootstrap_schema()
    session_factory = selected.session_factory
    filesystem = _active_artifact_filesystem(selected)
    artifact_storage = _artifact_adapter(selected, filesystem)

    identity_repository = PostgresIdentityAccessRepository(session_factory)
    current_principal = PostgresCurrentPrincipal(identity_repository)
    directory_cipher = _EnvironmentDirectoryCipher()

    def directory_custom_ca(connection_id: str) -> str | None:
        secret = identity_repository.get_directory_secret(
            connection_id, "custom_ca"
        )
        if secret is None:
            return None
        return directory_cipher.decrypt(
            secret,
            domain="identity_directory_custom_ca",
            owner_id=connection_id,
            owner_kind="directory_connection",
        )

    directory_identity = DirectoryIdentityService(
        identity_repository,
        LdapDirectoryGateway(custom_ca_resolver=directory_custom_ca),
        directory_cipher,
        validate_directory_filter,
    )
    identity_access = IdentityAccessService(
        identity_repository,
        PostgresInviteScopeGrantAdapter(identity_repository),
        directory_identity,
    )
    agent_access, agent_query_authority = build_postgres_agent_access(
        session_factory
    )
    team_access = build_postgres_team_access(
        session_factory,
        directory_identity,
        identity_repository,
    )
    project_governance = build_postgres_project_governance(
        session_factory,
        directory_identity,
        identity_repository,
        identity_repository.acl_authority,
    )
    notes_notifier = HttpNotesCollaborationClient.from_environment()
    notes_artifact_writer = BoundedArtifactWriter(selected.engine)
    notes_owner = PostgresNotesOwner(session_factory, notes_artifact_writer)
    notes = NotesApplicationService(
        notes_owner,
        notes_notifier,
        PostgresNotesAttachmentProvider(
            notes_owner,
            notes_artifact_writer,
        ),
    )
    audit_reader, audit_writer = build_postgres_audit_adapter(session_factory)
    agent_runtime = AgentRuntimeApplication(agent_query_authority, audit_writer)

    processing_registry = ProcessingRegistryService(
        PostgresProcessingAdapter(session_factory),
        LocalProcessingPluginArtifactStore(),
        default_processing_runner(),
    )
    document_intake = PostgresDocumentIntakeAdapter(session_factory)
    document_processing = PostgresDocumentProcessingAdapter(session_factory)
    processing_jobs = ProcessingJobsApplication(
        document_processing,
        document_intake,
        RbacProcessingJobsAuthorization(),
        best_effort_dispatch,
    )
    model_routing = ModelRoutingService(
        PostgresModelRoutingAdapter(
            session_factory,
            default_provider_adapter_factory,
        )
    )

    # Strict-turn owner graph.  Every collaborator closes its own transaction;
    # the execution coordinator carries no durable state and is never resumed.
    strict_runtime = PostgresTurnRuntimeOwner(session_factory)
    answer_behavior_owner = PostgresAnswerBehaviorOwner(session_factory)
    answer_behavior = AnswerBehaviorService(answer_behavior_owner)
    # A prior process/host disappearance cannot be resumed or claimed. Startup
    # only terminalizes expired non-terminal executions.
    turn_lease_failure_sweeper = TurnLeaseFailureSweeper(strict_runtime)
    turn_lease_failure_sweeper.start()
    knowledge_rows = PostgresProductionKnowledgeRowSource(
        session_factory,
        filesystem,
    )
    authorization_store = PostgresAuthorizationStore(session_factory)
    strict_authorization = PostgresAuthorizationV1Adapter(
        authorization_store,
        ProductionCurrentResourceAuthorizationReader(knowledge_rows),
    )
    strict_contexts = PostgresContextEngineeringV3Adapter(
        PostgresContextEngineeringStore(session_factory)
    )
    retrieval_lineage = PostgresCanonicalRetrievalLineage(session_factory)
    strict_retrieval = KnowledgeToolService(
        grant_resources=strict_authorization,
        store=PostgresRetrievalV1Store(
            session_factory,
            canonicalize_catalog=retrieval_lineage.canonicalize_catalog,
            canonicalize_evidence_pack=(
                retrieval_lineage.canonicalize_evidence_pack
            ),
        ),
        backend=ProductionKnowledgeRetrievalBackend(
            knowledge_rows,
            PostgresVisualPageRenderer(session_factory, filesystem),
            VectorIndex(),
        ),
    )
    generation_retention = PostgresGenerationRetentionOwner(session_factory)
    strict_results = PostgresResultGovernanceV1Store(session_factory)
    strict_citations = PostgresCitationV1Store(session_factory)
    protected_citations = ProtectedCitationReadService(
        bindings=strict_citations,
        evidence=knowledge_rows,
    )
    strict_audit = PostgresAuditV1Store(session_factory)
    protected_declared_evidence = ProtectedDeclaredEvidenceReadService(
        declarations=strict_audit,
        evidence_packs=strict_retrieval,
        evidence=knowledge_rows,
        pages=knowledge_rows,
    )
    strict_turn_model = StrictProviderTurnModel(model_routing)
    strict_orchestrator = StatelessTurnExecutionOrchestrator(
        runtime=strict_runtime,
        model=strict_turn_model,
        model_inputs=PublicOwnerTurnModelInputSource(
            contexts=strict_contexts,
            grant_resources=strict_authorization,
            answer_behavior=answer_behavior_owner,
        ),
        retrieval=strict_retrieval,
        result_governance=strict_results,
        citation=strict_citations,
        audit=strict_audit,
        evaluator=StrictPostHocClaimEvaluator(model_routing, strict_runtime),
        reasoning_model=strict_turn_model,
    )
    turn_execution_carrier = ThreadTurnCarrier(strict_orchestrator, strict_runtime)
    conversations = PostgresConversationV1Adapter(session_factory)
    workspace_turn = WorkspaceTurnApplication(
        conversations=conversations,
        retry_lineage=conversations,
        authorization=strict_authorization,
        knowledge_source=ProductionAuthorizedGrantResourceSource(knowledge_rows),
        contexts=strict_contexts,
        input_projections=strict_contexts,
        retrieval=strict_retrieval,
        generation_retention=generation_retention,
        runtime=strict_runtime,
        results=strict_results,
        citations=strict_citations,
        audits=strict_audit,
        citation_reader=protected_citations,
        declared_evidence_reader=protected_declared_evidence,
        carrier=turn_execution_carrier,
        model_routes=model_routing,
        answer_behavior=answer_behavior_owner,
        context_preparer=SynchronousContextCompactor(
            turn_model=strict_turn_model,
            summary_generator=ProviderContextSummaryGenerator(
                model_routing, strict_runtime
            ),
            input_projector=ProviderTurnInputProjector(
                model_routing, strict_contexts, strict_runtime
            ),
            answer_behavior=answer_behavior_owner,
        ),
        conversation_usage=PostgresConversationTokenUsageReader(
            session_factory
        ),
    )
    turn_resource_release_reconciler = TurnResourceReleaseReconciler(
        runtime=strict_runtime,
        authorization=strict_authorization,
        retrieval=strict_retrieval,
        generation_retention=generation_retention,
        contexts=strict_contexts,
    )
    turn_resource_release_reconciler.start()

    conversation_audit = ConversationAuditService(workspace_turn, audit_writer)

    document_uploads = PostgresDocumentUploadJourneyProvider(
        session_factory,
        NewDocumentUploadJourneyCommand(
            artifact_storage,
            NewDocumentUploadRequestBoundaryCommand(session_factory),
            NewDocumentUploadCommand(session_factory),
            lambda _job: None,
        ),
    )
    document_restore_proofs = PostgresDocumentRestoreProofProvider(
        session_factory, filesystem
    )
    document_library = DocumentLibraryApplication(
        document_intake,
        document_processing,
        document_uploads,
        document_restore_proofs,
        DocumentLifecycleRequestInput,
        DocumentLifecycleProcessingAcceptance,
        DocumentLibraryExceptionTypes(
            DocumentUploadAccessDenied,
            DocumentUploadUnauthenticated,
            DocumentUploadReplayConflict,
            DocumentLifecycleDenied,
            DocumentProcessingCurrentnessConflict,
        ),
        best_effort_dispatch,
    )
    ops_readiness = OpsReadinessService(
        PostgresOpsAdapter(
            PostgresOpsReadinessRepository(
                session_factory,
                _ProcessingRunnerProbe(),
                _CredentialEncryptionProbe(),
            )
        ),
        _ArtifactReadinessProbe(selected),
        notes_notifier,
    )

    return ApiComposition(
        current_principal=current_principal,
        identity_access=identity_access,
        agent_access=agent_access,
        directory_identity=directory_identity,
        agent_runtime=agent_runtime,
        team_access=team_access,
        project_governance=project_governance,
        workspace_scope_authority=identity_repository.acl_authority,
        processing_registry=processing_registry,
        document_intake=document_intake,
        document_processing=document_processing,
        processing_jobs=processing_jobs,
        model_routing=model_routing,
        answer_behavior=answer_behavior,
        notes=notes,
        ops_readiness=ops_readiness,
        conversation_audit=conversation_audit,
        workspace_turn=workspace_turn,
        admin_audit_events=AdminAuditEventReadService(audit_reader, audit_writer),
        artifact_storage=artifact_storage,
        protected_originals=PostgresProtectedOriginalJourneyProvider(
            session_factory
        ),
        document_uploads=document_uploads,
        document_restore_proofs=document_restore_proofs,
        turn_execution_carrier=turn_execution_carrier,
        turn_resource_release_reconciler=turn_resource_release_reconciler,
        document_library=document_library,
        turn_lease_failure_sweeper=turn_lease_failure_sweeper,
    )


@dataclass(frozen=True, slots=True)
class ArtifactStorageComposition:
    """Operator-facing artifact control composition without aggregate state."""

    _local_pilot: Callable[..., Mapping[str, object]]
    _offline: Callable[..., Mapping[str, object]]
    _portainer: Callable[..., Mapping[str, object]]

    def initialize_local_pilot_target(
        self,
        *,
        raw_path: str = "/srv/atlas-artifacts",
    ) -> Mapping[str, object]:
        return self._local_pilot(raw_path=raw_path)

    def configure_offline_target(
        self,
        *,
        target_id: str,
        target_revision: int,
        masked_label: str,
        operator_id: str,
        change_id: str,
        verification_mode: str = "full_hash",
        risk_acknowledgement: str | None = None,
    ) -> Mapping[str, object]:
        return self._offline(
            target_id=target_id,
            target_revision=target_revision,
            masked_label=masked_label,
            operator_id=operator_id,
            change_id=change_id,
            verification_mode=verification_mode,
            risk_acknowledgement=risk_acknowledgement,
        )

    def configure_portainer_target(
        self,
        *,
        generation: int,
        switch_mode: str,
        risk_acknowledgement: str,
        raw_path: str = "/srv/atlas-artifacts",
    ) -> Mapping[str, object]:
        return self._portainer(
            generation=generation,
            switch_mode=switch_mode,
            risk_acknowledgement=risk_acknowledgement,
            raw_path=raw_path,
        )


def _control(row: rows.AtlasArtifactStorageControlRow | None) -> StorageControlRecord | None:
    if row is None:
        return None
    return StorageControlRecord(
        control_id=row.control_id,
        mode=row.mode,
        active_target_id=row.active_target_id,
        active_target_revision=row.active_target_revision,
        root_identity_digest=row.root_identity_digest,
        storage_epoch=row.storage_epoch,
        updated_at=row.updated_at,
    )


def _blob(row: rows.AtlasStorageBlobRow) -> StorageBlobRecord:
    return StorageBlobRecord(
        blob_id=row.blob_id,
        opaque_ref=row.opaque_ref,
        status=row.status,
        dedup_mode=row.dedup_mode,
        dedup_scope_type=row.dedup_scope_type,
        dedup_scope_id=row.dedup_scope_id,
        checksum_algorithm=row.checksum_algorithm,
        checksum_value=row.checksum_value,
        byte_size=row.byte_size,
        content_type=row.content_type,
        fence=StorageFence(
            row.target_id,
            row.target_revision,
            row.root_identity_digest,
            row.storage_epoch,
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        write_attempt_id=row.write_attempt_id,
        committed_at=row.committed_at,
        failure_code=row.failure_code,
        failure_detail_summary=row.failure_detail_summary,
        reconciliation_required_at=row.reconciliation_required_at,
        reconciled_at=row.reconciled_at,
        reconciled_by=row.reconciled_by,
    )


@dataclass(frozen=True, slots=True)
class _ArtifactTargetRuntime:
    runtime: PostgresRuntime
    config: RootOnlyStorageTargetConfig
    allowlisted_parents: tuple[Path, ...]

    def local_pilot(self, *, raw_path: str) -> Mapping[str, object]:
        target_id = "target-local-pilot"
        target_revision = 1
        config_key = self.config.put_target(
            target_id=target_id,
            revision=target_revision,
            kind="local",
            raw_path=raw_path,
        )
        filesystem = LocalArtifactFilesystemAdapter(
            raw_path,
            allowlisted_parents=self.allowlisted_parents,
        )
        with self.runtime.session_factory() as session:
            current = session.get(rows.AtlasArtifactStorageControlRow, "global")
            target = session.get(
                rows.AtlasArtifactStorageTargetRow,
                (target_id, target_revision),
            )
        if (
            current is not None
            and current.mode == "active"
            and current.active_target_id == target_id
            and current.active_target_revision == target_revision
            and current.root_identity_digest == filesystem.root_identity_digest
            and target is not None
            and target.status == "active"
            and target.config_key == config_key
            and target.root_identity_digest == filesystem.root_identity_digest
        ):
            return {
                "target_id": target_id,
                "target_revision": target_revision,
                "storage_epoch": current.storage_epoch,
                "replayed": True,
            }
        if target is not None:
            raise ValueError("local pilot artifact target state conflicts")
        receipt = self.offline(
            target_id=target_id,
            target_revision=target_revision,
            masked_label="Local pilot",
            operator_id="local-deployment-bootstrap",
            change_id="local-pilot-target-v1",
            verification_mode="full_hash",
            risk_acknowledgement=None,
        )
        return {
            **receipt,
            "target_id": target_id,
            "target_revision": target_revision,
            "replayed": False,
        }

    def _facts(
        self,
        *,
        target_id: str,
        target_revision: int,
        masked_label: str,
        operator_id: str,
        change_id: str,
        verification_mode: str,
    ) -> tuple[ArtifactTargetFacts, PostgresArtifactStorageAdapter]:
        configured = self.config.load().get(target_id)
        if configured is None or configured["revision"] != target_revision:
            raise ValueError("artifact target configuration is unavailable")
        filesystem = LocalArtifactFilesystemAdapter(
            configured["raw_path"],
            allowlisted_parents=self.allowlisted_parents,
        )
        capabilities = filesystem.probe_capabilities()
        if capabilities != {
            "create_file": True,
            "modify_file": True,
            "remove_file": True,
        }:
            raise ValueError("artifact target capabilities are invalid")
        with self.runtime.session_factory() as session:
            current = _control(
                session.get(rows.AtlasArtifactStorageControlRow, "global")
            )
            committed = tuple(
                _blob(row)
                for row in session.scalars(
                    select(rows.AtlasStorageBlobRow)
                    .where(rows.AtlasStorageBlobRow.status == "committed")
                    .order_by(rows.AtlasStorageBlobRow.blob_id)
                ).all()
            )
        evidence_claim = (
            "TARGET_COPY_CHECKSUM_VERIFIED"
            if verification_mode == "full_hash"
            else "OPERATOR_ACCEPTED_UNVERIFIED_TARGET"
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "change_id": change_id,
                    "target_id": target_id,
                    "target_revision": target_revision,
                    "target_kind": configured["kind"],
                    "masked_label": masked_label,
                    "root_identity_digest": filesystem.root_identity_digest,
                    "verification_mode": verification_mode,
                    "evidence_claim": evidence_claim,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        observed_at = utc_now_iso()
        facts = ArtifactTargetFacts(
            expected_control=current,
            committed_blobs=committed,
            target_id=target_id,
            target_revision=target_revision,
            target_kind=configured["kind"],
            masked_label=masked_label,
            config_key=configured["config_key"],
            root_identity_digest=filesystem.root_identity_digest,
            capabilities=capabilities,
            created_by=operator_id,
            operation_id=f"op-{uuid4().hex[:20]}",
            idempotency_scope="offline_storage_change",
            idempotency_key=change_id,
            request_fingerprint=fingerprint,
            observed_at=observed_at,
            audit_events=(
                build_audit_event(
                    event_type="artifact_storage_target_configured",
                    actor_id=operator_id,
                    target_ref=f"artifact-target:{target_id}:{target_revision}",
                    project_id=None,
                    message_code="storage.target_configuration_was_committed",
                    metadata={
                        "change_id": change_id,
                        "verification_mode": verification_mode,
                    },
                ),
            ),
            verification_mode=verification_mode,
            evidence_claim=evidence_claim,
        )
        session_factory = self.runtime.session_factory
        adapter = PostgresArtifactStorageAdapter(
            ProtectedArtifactOpenCommand(session_factory),
            BeginArtifactWriteCommand(session_factory),
            FinalizeArtifactWriteCommand(session_factory),
            TargetControlCommand(session_factory),
            ClaimArtifactReconciliationCommand(session_factory),
            FinalizeArtifactReconciliationCommand(session_factory),
            filesystem,
            HeartbeatArtifactWriteCommand(session_factory),
        )
        return facts, adapter

    def offline(self, **kwargs) -> Mapping[str, object]:
        verification_mode = kwargs.pop("verification_mode")
        risk_acknowledgement = kwargs.pop("risk_acknowledgement")
        if verification_mode != "full_hash" or risk_acknowledgement is not None:
            raise ValueError("offline target requires full_hash verification")
        facts, adapter = self._facts(
            verification_mode=verification_mode,
            **kwargs,
        )
        return adapter.configure_offline_target(
            ArtifactTargetJourneyBuilder().offline(facts)
        )

    def portainer(self, **kwargs) -> Mapping[str, object]:
        generation = kwargs["generation"]
        switch_mode = kwargs["switch_mode"]
        acknowledgement = kwargs["risk_acknowledgement"]
        raw_path = kwargs["raw_path"]
        if generation < 1:
            raise ValueError("Portainer generation must be positive")
        if acknowledgement != UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT:
            raise ValueError("Portainer risk acknowledgement is invalid")
        if switch_mode not in {"explicit", "operator_accepted_unverified"}:
            raise ValueError("Portainer switch mode is invalid")
        target_id = f"target-portainer-smb-g{generation}"
        self.config.put_target(
            target_id=target_id,
            revision=generation,
            kind="smb_mount",
            raw_path=raw_path,
        )
        facts, adapter = self._facts(
            target_id=target_id,
            target_revision=generation,
            masked_label="Portainer SMB",
            operator_id="portainer-environment-admin",
            change_id=f"portainer-smb-g{generation}",
            verification_mode="operator_accepted_unverified",
        )
        request = ArtifactTargetJourneyBuilder().portainer(
            facts,
            generation_prefix="target-portainer-smb-g",
            switch_mode="explicit",
            risk_acknowledgement=acknowledgement,
        )
        return adapter.configure_portainer_target(request)


def build_artifact_storage_composition(
    runtime: PostgresRuntime | None = None,
) -> ArtifactStorageComposition:
    selected = runtime or PostgresRuntime.from_environment()
    selected.bootstrap_schema()
    config_path = os.environ.get("ATLAS_ARTIFACT_TARGET_CONFIG")
    allowlist_raw = os.environ.get("ATLAS_ARTIFACT_ALLOWED_PARENTS")
    if not config_path or not allowlist_raw:
        raise RuntimeError(
            "ATLAS_ARTIFACT_TARGET_CONFIG and ATLAS_ARTIFACT_ALLOWED_PARENTS are required"
        )
    target_runtime = _ArtifactTargetRuntime(
        runtime=selected,
        config=RootOnlyStorageTargetConfig(config_path),
        allowlisted_parents=tuple(
            Path(item).expanduser()
            for item in allowlist_raw.split(os.pathsep)
            if item
        ),
    )
    return ArtifactStorageComposition(
        _local_pilot=target_runtime.local_pilot,
        _offline=target_runtime.offline,
        _portainer=target_runtime.portainer,
    )


__all__ = [
    "ApiComposition",
    "ArtifactStorageComposition",
    "build_api_composition",
    "build_artifact_storage_composition",
]
