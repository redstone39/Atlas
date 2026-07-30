from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import hashlib
import json
import unicodedata
from typing import Callable, Mapping, Protocol, Sequence

from pydantic import TypeAdapter
import tiktoken
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingGenerationRetentionEntryRow,
    AtlasProcessingGenerationRetentionRow,
    AtlasSearchChunkRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasEvidenceRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.retrieval import (
    AtlasTurnCatalogDocumentRow,
)

from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY,
    PROCESSING_REVISION_REF_DESCRIPTOR_KEY,
    CatalogDocumentInput,
    CatalogRecord,
    CreateCatalogInput,
    EvidencePackLineageInput,
    EvidencePackRecord,
    MaterializeEvidencePackInput,
    PersistInvocationResultInput,
    PostgresRetrievalV1Store,
    ReleaseCatalogInput,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.modules.authorization.public import (
    GrantDocumentResourceOwner,
)
from atlas_production.modules.processing_pipeline.public import (
    DocumentNavigationMapV1,
)
from atlas_production.modules.retrieval.public import (
    ClaimedEvidenceLineageV1,
    DeclaredEvidenceItemV1,
    DeclaredEvidenceMappingV1,
    DeclaredEvidenceSubsetV1,
    DiscoveryCandidateComponentV1,
    DiscoveryCandidateLineageV1,
    DiscoveryChannelTraceV1,
    DiscoverRelevantDocumentsV1,
    DocumentNavigationResultV1,
    EvidenceDescriptorV1,
    EvidencePackLineageItemV1,
    EvidencePackRefV1,
    GovernanceEvidenceItemV1,
    GovernanceEvidencePackV1,
    ExpandKnowledgeV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    KnowledgeCatalogPageV1,
    KnowledgeDocumentDescriptorV1,
    KnowledgeExpansionResultV1,
    KnowledgeInspectionItemV1,
    KnowledgeInspectionResultV1,
    KnowledgeSearchResultV1,
    KnowledgeToolActionV1,
    KnowledgeToolErrorV1,
    KnowledgeToolObservationV1,
    ListKnowledgeDocumentsV1,
    ModelVisibleEvidenceObservationV1,
    NavigateDocumentV1,
    NavigationTargetV1,
    RelevantDocumentCandidateV1,
    RelevantDocumentDiscoveryResultV1,
    RelevantDocumentDiscoveryTraceV1,
    RetrievalInvocationEnvelopeV1,
    RetrievalEvidenceLineageV1,
    SearchKnowledgeV1,
    VisualImagePayloadV1,
    VisualInspectionResultV1,
)


_OBSERVATION_ADAPTER = TypeAdapter(KnowledgeToolObservationV1)
SessionFactory = Callable[[], Session]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _opaque(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return f"kh_{kind}_{digest[:32]}"


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in normalized
        ).split()
    )


def _page_resource(document_handle: str, page_number: int) -> str:
    return f"page|{document_handle}|{page_number}"


def _visual_resource(
    document_handle: str,
    page_number: int,
    bbox: tuple[int, int, int, int],
    image_digest: str,
) -> str:
    return (
        f"visual|{document_handle}|{page_number}|"
        f"{','.join(str(value) for value in bbox)}|{image_digest}"
    )


def _parse_visual_resource(
    kind: str, resource_ref: str
) -> tuple[str, int, tuple[int, int, int, int]]:
    parts = resource_ref.split("|")
    if kind == "page" and len(parts) == 3 and parts[0] == "page":
        document_handle, raw_page = parts[1:]
        raw_bbox = "0,0,10000,10000"
    elif kind == "visual" and len(parts) == 5 and parts[0] == "visual":
        document_handle, raw_page, raw_bbox = parts[1:4]
    else:
        raise RetrievalStoreConflict("visual handle lineage is invalid")
    try:
        page_number = int(raw_page)
        bbox = tuple(int(value) for value in raw_bbox.split(","))
    except ValueError:
        raise RetrievalStoreConflict("visual handle lineage is invalid") from None
    if (
        page_number < 1
        or len(bbox) != 4
        or any(value < 0 or value > 10_000 for value in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise RetrievalStoreConflict("visual handle lineage is invalid")
    return document_handle, page_number, bbox  # type: ignore[return-value]


def _compose_bbox(
    parent: tuple[int, int, int, int],
    child: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = parent
    width = right - left
    height = bottom - top
    return (
        left + width * child[0] // 10_000,
        top + height * child[1] // 10_000,
        left + width * child[2] // 10_000,
        top + height * child[3] // 10_000,
    )


@dataclass(frozen=True, slots=True)
class BackendCatalogDocument:
    document_handle: str
    lifecycle_epoch: int
    document_version_ref: str
    processing_generation_ref: str
    processing_revision_ref: str
    index_generation_ref: str
    manifest_digest: str
    descriptor: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BackendEvidence:
    evidence_ref: str
    evidence_identity: str
    document_handle: str
    locator_label: str
    snippet: str
    content: str
    modalities: tuple[str, ...]
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class BackendDiscoveryHit:
    match_ref: str
    document_handle: str
    preview: str
    locator_label: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class BackendVisualImage:
    content: bytes
    digest: str
    width: int
    height: int


class KnowledgeRetrievalBackend(Protocol):
    def discover_lexical(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
    ) -> Sequence[BackendDiscoveryHit]: ...

    def discover_vector(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
    ) -> Sequence[BackendDiscoveryHit]: ...

    def search(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        required_modalities: tuple[str, ...],
        facet_hints: Mapping[str, object],
        limit: int,
    ) -> Sequence[BackendEvidence]: ...

    def inspect(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        evidence_refs: tuple[str, ...],
    ) -> Sequence[BackendEvidence]: ...

    def expand(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        anchor_evidence_refs: tuple[str, ...],
        direction: str,
        limit: int,
    ) -> Sequence[BackendEvidence]: ...

    def read_exact(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        evidence_requests: tuple[tuple[str, str], ...],
    ) -> Sequence[BackendEvidence]: ...

    def render_visual(
        self,
        *,
        document: BackendCatalogDocument,
        page_number: int,
        normalized_bbox: tuple[int, int, int, int],
    ) -> BackendVisualImage: ...

    def navigation_map(
        self, *, document: BackendCatalogDocument
    ) -> DocumentNavigationMapV1 | None: ...


def _opaque_evidence_ref(evidence_id: str) -> str:
    return f"evidence-resource-{_digest(['evidence-resource-v1', evidence_id])}"


def _canonical_document_resource_ref(document_id: str) -> str:
    return f"document-resource-{_digest(['document-resource-v1', document_id])}"


def _visual_page_number(evidence_ref: str) -> int | None:
    parts = evidence_ref.split("|")
    if len(parts) != 5 or parts[0] != "visual":
        return None
    try:
        value = int(parts[2])
    except ValueError:
        return None
    return value if value > 0 else None


class PostgresCanonicalRetrievalLineage:
    """Resolve cross-owner canonical lineage before Retrieval-owned writes."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def canonicalize_catalog(self, command: CreateCatalogInput) -> CreateCatalogInput:
        exact_documents: list[CatalogDocumentInput] = []
        with self._session_factory() as session:
            for document in command.documents:
                index = session.get(
                    AtlasIndexGenerationRow, document.index_generation_ref
                )
                revision = (
                    session.get(AtlasProcessingRevisionRow, index.processing_revision_id)
                    if index is not None and index.processing_revision_id is not None
                    else None
                )
                retained = session.scalar(
                    select(AtlasProcessingGenerationRetentionEntryRow.retention_ref)
                    .join(
                        AtlasProcessingGenerationRetentionRow,
                        AtlasProcessingGenerationRetentionRow.retention_ref
                        == AtlasProcessingGenerationRetentionEntryRow.retention_ref,
                    )
                    .where(
                        AtlasProcessingGenerationRetentionEntryRow.retention_ref
                        == command.generation_retention_ref,
                        AtlasProcessingGenerationRetentionEntryRow.index_generation_id
                        == document.index_generation_ref,
                        AtlasProcessingGenerationRetentionRow.status == "active",
                    )
                    .limit(1)
                )
                binding_version = session.get(
                    AtlasDocumentVersionRow, document.document_version_ref
                )
                binding = (
                    session.get(AtlasDocumentRow, binding_version.document_id)
                    if binding_version is not None
                    else None
                )
                if (
                    index is None
                    or revision is None
                    or revision.state != "ready"
                    or retained is None
                    or binding is None
                    or document.resource_ref
                    != _canonical_document_resource_ref(binding.document_id)
                    or document.lifecycle_epoch
                    != binding.resource_lifecycle_epoch + 1
                    or binding.processing_identity_id
                    != revision.processing_identity_id
                    or index.processing_revision_id
                    != revision.processing_revision_id
                    or index.manifest_digest != document.manifest_digest
                    or revision.manifest_digest != document.manifest_digest
                    or document.processing_generation_ref
                    != f"processing-generation-{index.source_processing_generation}"
                    or (
                        document.processing_revision_ref is not None
                        and document.processing_revision_ref
                        != revision.processing_revision_id
                    )
                ):
                    raise RetrievalStoreConflict(
                        "catalog document revision pin is unavailable"
                    )
                exact_documents.append(
                    replace(
                        document,
                        processing_revision_ref=revision.processing_revision_id,
                    )
                )
        return replace(command, documents=tuple(exact_documents))

    def canonicalize_evidence_pack(
        self,
        command: MaterializeEvidencePackInput,
        resolved: tuple[ResultHandleInput, ...],
    ) -> MaterializeEvidencePackInput:
        exact_items: list[EvidencePackLineageInput] = []
        with self._session_factory() as session:
            catalog_documents = {
                row.document_handle: row
                for row in session.scalars(
                    select(AtlasTurnCatalogDocumentRow).where(
                        AtlasTurnCatalogDocumentRow.catalog_ref == command.catalog_ref
                    )
                ).all()
            }
            for proposed, actual in zip(command.items, resolved, strict=True):
                catalog_document = catalog_documents.get(
                    actual.document_handle or ""
                )
                revision_ref = (
                    catalog_document.descriptor.get(
                        PROCESSING_REVISION_REF_DESCRIPTOR_KEY
                    )
                    if catalog_document is not None
                    else None
                )
                binding_version = session.get(
                    AtlasDocumentVersionRow, proposed.document_version_ref
                )
                binding = (
                    session.get(AtlasDocumentRow, binding_version.document_id)
                    if binding_version is not None
                    else None
                )
                revision = session.get(
                    AtlasProcessingRevisionRow, proposed.processing_revision_ref
                )
                index = session.get(
                    AtlasIndexGenerationRow, proposed.index_generation_ref
                )
                if (
                    catalog_document is None
                    or catalog_document.descriptor.get(
                        AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY
                    )
                    != proposed.resource_ref
                    or revision_ref != proposed.processing_revision_ref
                    or catalog_document.document_version_ref
                    != proposed.document_version_ref
                    or catalog_document.index_generation_ref
                    != proposed.index_generation_ref
                    or binding is None
                    or revision is None
                    or binding.processing_identity_id
                    != revision.processing_identity_id
                    or revision.state != "ready"
                    or index is None
                    or index.processing_revision_id
                    != proposed.processing_revision_ref
                ):
                    raise RetrievalStoreConflict(
                        "evidence pack document revision lineage changed"
                    )

                page_number = _visual_page_number(proposed.evidence_ref)
                if page_number is None:
                    evidence_rows = session.execute(
                        select(AtlasEvidenceRow, AtlasSearchChunkRow)
                        .join(
                            AtlasSearchChunkRow,
                            AtlasSearchChunkRow.evidence_id
                            == AtlasEvidenceRow.evidence_id,
                        )
                        .where(
                            AtlasEvidenceRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasSearchChunkRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasSearchChunkRow.index_generation_id
                            == proposed.index_generation_ref,
                        )
                    ).all()
                    evidence = next(
                        (
                            row
                            for row, _chunk in evidence_rows
                            if _opaque_evidence_ref(row.evidence_id)
                            == proposed.evidence_ref
                        ),
                        None,
                    )
                    if evidence is None:
                        raise RetrievalStoreConflict(
                            "evidence pack evidence revision lineage changed"
                        )
                    raw_page = evidence.locator_payload.get("page_number")
                    page_number = raw_page if isinstance(raw_page, int) else None

                page_artifact_ref = None
                if page_number is not None:
                    page = session.scalar(
                        select(AtlasEvidencePageArtifactRow).where(
                            AtlasEvidencePageArtifactRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasEvidencePageArtifactRow.source_page_index
                            == page_number - 1,
                        )
                    )
                    if page is None:
                        raise RetrievalStoreConflict(
                            "evidence pack page artifact lineage changed"
                        )
                    page_artifact_ref = page.id
                exact_items.append(
                    replace(proposed, page_artifact_ref=page_artifact_ref)
                )
        return replace(command, items=tuple(exact_items))


class KnowledgeToolService:
    """Grant-pinned catalog and execution-local retrieval tool provider.

    Collaborator calls happen only before or after store methods. Each store
    method owns and closes its own short transaction.
    """

    def __init__(
        self,
        *,
        grant_resources: GrantDocumentResourceOwner,
        store: PostgresRetrievalV1Store,
        backend: KnowledgeRetrievalBackend,
    ) -> None:
        self._grant_resources = grant_resources
        self._store = store
        self._backend = backend

    def create_catalog(
        self,
        *,
        execution_id: str,
        grant_ref: str,
        generation_retention_ref: str,
        idempotency_key: str,
    ):
        snapshot = self._grant_resources.grant_document_resources(
            execution_id=execution_id, grant_ref=grant_ref
        )
        documents = tuple(
            CatalogDocumentInput(
                document_handle=_opaque("document", execution_id, grant_ref, resource.resource_ref),
                resource_ref=resource.resource_ref,
                lifecycle_epoch=resource.lifecycle_epoch,
                document_version_ref=resource.document_version_ref,
                generation_ref=resource.index_generation_ref,
                processing_generation_ref=resource.processing_generation_ref,
                processing_revision_ref=None,
                index_generation_ref=resource.index_generation_ref,
                manifest_digest=resource.manifest_digest,
                descriptor={
                    "display_name": resource.display_name,
                    "media_type": resource.media_type,
                    "modalities": list(resource.modalities),
                    "tags": list(resource.tags),
                    "language": resource.language,
                    "created_at_label": resource.created_at_label,
                    "searchable_content": resource.searchable_content,
                    "version_label": resource.version_label,
                },
            )
            for resource in snapshot.resources
        )
        pin_digest = _digest(
            [
                {
                    "resource_ref": resource.resource_ref,
                    "lifecycle_epoch": resource.lifecycle_epoch,
                    "document_version_ref": resource.document_version_ref,
                    "processing_generation_ref": resource.processing_generation_ref,
                    "index_generation_ref": resource.index_generation_ref,
                    "manifest_digest": resource.manifest_digest,
                }
                for resource in snapshot.resources
            ]
        )
        catalog_ref = _opaque("catalog", execution_id, grant_ref, idempotency_key)
        record = self._store.create_catalog(
            CreateCatalogInput(
                catalog_ref=catalog_ref,
                execution_id=execution_id,
                grant_ref=grant_ref,
                generation_retention_ref=generation_retention_ref,
                authorization_revision=snapshot.authorization_revision,
                retrieval_generation_ref=f"retrieval-generation-{pin_digest}",
                documents=documents,
                idempotency_key=idempotency_key,
            )
        )
        from atlas_production.modules.retrieval.public import KnowledgeCatalogSnapshotRefV1

        return KnowledgeCatalogSnapshotRefV1(
            catalog_ref=record.catalog_ref,
            grant_ref=record.grant_ref,
            generation_retention_ref=record.generation_retention_ref,
            retrieval_generation_ref=record.retrieval_generation_ref,
            document_count=len(record.documents),
            digest=record.digest,
            created_at=record.created_at,
        )

    def invoke(
        self,
        *,
        execution_id: str,
        grant_ref: str,
        catalog_ref: str,
        invocation_ordinal: int,
        action: KnowledgeToolActionV1,
        max_output_tokens: int | None = None,
        tokenizer_profile: str = "cl100k_base",
        max_output_bytes: int = 262_144,
    ) -> RetrievalInvocationEnvelopeV1:
        catalog = self._store.get_catalog(execution_id=execution_id, catalog_ref=catalog_ref)
        if catalog.grant_ref != grant_ref:
            raise RetrievalStoreConflict("catalog does not belong to grant")
        if isinstance(action, (InspectVisualV1, NavigateDocumentV1)):
            current = self._grant_resources.current_grant_document_resources(
                execution_id=execution_id, grant_ref=grant_ref
            )
            if (
                current.authorization_revision != catalog.authorization_revision
                or {
                    (
                        item.resource_ref,
                        item.lifecycle_epoch,
                        item.document_version_ref,
                        item.processing_generation_ref,
                        item.index_generation_ref,
                        item.manifest_digest,
                    )
                    for item in current.resources
                }
                != {
                    (
                        item.resource_ref,
                        item.lifecycle_epoch,
                        item.document_version_ref,
                        item.processing_generation_ref,
                        item.index_generation_ref,
                        item.manifest_digest,
                    )
                    for item in catalog.documents
                }
            ):
                raise RetrievalStoreConflict(
                    "visual catalog authorization is not current"
                )
        effective_output_tokens = max_output_tokens
        if effective_output_tokens is None:
            effective_output_tokens = getattr(action, "max_output_tokens", 16_000)
        if effective_output_tokens <= 0 or max_output_bytes <= 0:
            raise RetrievalStoreConflict("runtime tool output budgets are required")
        try:
            tokenizer = tiktoken.get_encoding(tokenizer_profile)
        except Exception as exc:
            raise RetrievalStoreConflict("runtime tokenizer profile is unsupported") from exc
        arguments = action.model_dump(mode="json")
        if isinstance(action, (FindKnowledgeDocumentsV1, DiscoverRelevantDocumentsV1)):
            arguments["runtime_max_output_tokens"] = effective_output_tokens
            arguments["tokenizer_profile"] = tokenizer_profile
        schema_version = f"{action.action.replace('_', '-')}-v1"
        resolved = self._validate_action_handles(catalog, action)
        replay = self._store.replay_invocation(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            action=action.action,
            schema_version=schema_version,
            canonical_arguments=arguments,
        )
        if replay is not None:
            observation = _OBSERVATION_ADAPTER.validate_python(
                self._provider_observation(replay.observation)
            )
            return self._envelope(
                catalog,
                observation,
                replay,
                resolved=resolved,
                visual_image=self._replay_visual_image(catalog, observation),
                tokenizer=tokenizer,
            )

        trace: dict[str, object] | None = None
        try:
            if isinstance(action, DiscoverRelevantDocumentsV1):
                observation, trace = self._execute_discovery(catalog, action)
                handles = ()
                visual_image = None
            else:
                observation, handles, visual_image = self._execute(
                    catalog, action, resolved
                )
        except (TimeoutError, ConnectionError, OSError, OperationalError):
            # Known backend availability failures are model-visible tool
            # outcomes.  Contract, authorization and immutable-lineage
            # conflicts deliberately continue to fail closed.
            observation = KnowledgeToolErrorV1(
                result_type="knowledge_tool_error",
                error_code="tool_failed",
                message_code="retrieval_backend_unavailable",
                retryable=True,
            )
            handles = ()
            visual_image = None
            if isinstance(action, DiscoverRelevantDocumentsV1):
                trace = self._discovery_trace(
                    catalog=catalog,
                    action=action,
                    channels=[
                        {
                            "channel": "lexical",
                            "status": "failed",
                            "component_document_count": 0,
                        },
                        {
                            "channel": "vector",
                            "status": "failed",
                            "component_document_count": 0,
                        },
                    ],
                    ranked=(),
                    vector_coverage=0,
                )
        observation, handles = self._bounded_observation(
            observation,
            handles,
            max_output_tokens=effective_output_tokens,
            max_output_bytes=max_output_bytes,
            tokenizer=tokenizer,
            catalog_ref=catalog_ref,
            action=action,
        )
        if not isinstance(observation, VisualInspectionResultV1):
            visual_image = None
        arguments_digest = _digest(arguments)
        invocation_id = _opaque("invocation", execution_id, catalog_ref, str(invocation_ordinal))
        result_ref = _opaque("result", execution_id, catalog_ref, action.action, arguments_digest)
        persisted_observation: Mapping[str, object] = observation.model_dump(mode="json")
        if trace is not None:
            trace = {
                **trace,
                "provider_candidate_order": [
                    item.document_handle
                    for item in getattr(observation, "candidates", ())
                ],
                "truncated_by_budget": bool(
                    getattr(observation, "truncated_by_budget", False)
                ),
                "provider_result_type": observation.result_type,
            }
            persisted_observation = {
                "provider_observation": observation.model_dump(mode="json"),
                "discovery_trace": trace,
            }
        record = self._store.persist_invocation_result(
            PersistInvocationResultInput(
                invocation_id=invocation_id,
                result_ref=result_ref,
                execution_id=execution_id,
                catalog_ref=catalog_ref,
                invocation_ordinal=invocation_ordinal,
                action=action.action,
                schema_version=schema_version,
                canonical_arguments=arguments,
                result_type=observation.result_type,
                observation=persisted_observation,
                error_code=(observation.error_code if observation.result_type == "knowledge_tool_error" else None),
                handles=handles,
            )
        )
        return self._envelope(
            catalog,
            observation,
            record,
            resolved=resolved,
            visual_image=visual_image,
            tokenizer=tokenizer,
        )

    def _bounded_observation(
        self,
        observation,
        handles,
        *,
        max_output_tokens: int,
        max_output_bytes: int,
        tokenizer,
        catalog_ref: str,
        action: KnowledgeToolActionV1,
    ):
        """Keep a stable prefix within token admission and byte payload safety."""

        def fits(candidate) -> bool:
            payload = _canonical(candidate.model_dump(mode="json"))
            return (
                len(payload) <= max_output_bytes
                and len(tokenizer.encode(payload.decode("utf-8"))) <= max_output_tokens
            )

        if fits(observation):
            return observation, handles
        field = {
            "knowledge_catalog_page": "documents",
            "relevant_document_discovery_result": "candidates",
            "knowledge_search_result": "evidence",
            "knowledge_inspection_result": "items",
            "knowledge_expansion_result": "evidence",
            "document_navigation_result": "targets",
        }.get(observation.result_type)
        if field is not None:
            original_items = list(getattr(observation, field))
            for size in range(len(original_items) - 1, 0, -1):
                update = {field: original_items[:size]}
                if observation.result_type == "relevant_document_discovery_result":
                    update["truncated_by_budget"] = True
                if observation.result_type == "knowledge_catalog_page":
                    cursor_scope = (
                        f"find:{_normalize_identity(action.keyword)}"
                        if isinstance(action, FindKnowledgeDocumentsV1)
                        else "list"
                    )
                    offset = self._cursor_offset(
                        catalog_ref,
                        cursor_scope,
                        action.cursor,
                    )
                    update["next_cursor"] = self._cursor(
                        catalog_ref,
                        cursor_scope,
                        offset + size,
                    )
                bounded = observation.model_copy(update=update)
                if fits(bounded):
                    allowed = {
                        handle
                        for item in original_items[:size]
                        for handle in (
                            getattr(item, "evidence_handle", None),
                            getattr(item, "navigation_handle", None),
                            getattr(item, "page_handle", None),
                        )
                        if handle is not None
                    }
                    bounded_handles = tuple(
                        item for item in handles if getattr(item, "handle", None) in allowed
                    )
                    return bounded, bounded_handles
        return KnowledgeToolErrorV1(
            result_type="knowledge_tool_error",
            error_code="budget_exhausted",
            message_code="tool_output_limit_exceeded",
            retryable=True,
        ), ()

    @staticmethod
    def _provider_observation(observation: Mapping[str, object]) -> Mapping[str, object]:
        provider = observation.get("provider_observation")
        if isinstance(provider, Mapping):
            return provider
        return observation

    def _execute_discovery(
        self,
        catalog: CatalogRecord,
        action: DiscoverRelevantDocumentsV1,
    ) -> tuple[
        RelevantDocumentDiscoveryResultV1 | KnowledgeToolErrorV1,
        dict[str, object],
    ]:
        documents = self._backend_documents(catalog)
        # Fetch enough ranked chunks to select the best hit per document while
        # remaining bounded for the current catalog-sized pilot.
        channel_limit = min(500, max(action.limit, len(documents) * 5))
        channel_hits: dict[str, Sequence[BackendDiscoveryHit]] = {}
        channel_trace: list[dict[str, object]] = []
        for channel, operation in (
            ("lexical", self._backend.discover_lexical),
            ("vector", self._backend.discover_vector),
        ):
            try:
                channel_hits[channel] = operation(
                    documents=documents,
                    query_text=action.query_text,
                    limit=channel_limit,
                )
                channel_trace.append(
                    {
                        "channel": channel,
                        "status": "completed",
                        "component_document_count": 0,
                    }
                )
            except (TimeoutError, ConnectionError, OSError, OperationalError):
                channel_trace.append(
                    {
                        "channel": channel,
                        "status": "failed",
                        "component_document_count": 0,
                    }
                )

        completed_channels = [
            item["channel"]
            for item in channel_trace
            if item["status"] == "completed"
        ]
        if not completed_channels:
            trace = self._discovery_trace(
                catalog=catalog,
                action=action,
                channels=channel_trace,
                ranked=(),
                vector_coverage=0,
            )
            return KnowledgeToolErrorV1(
                result_type="knowledge_tool_error",
                error_code="tool_failed",
                message_code="retrieval_backend_unavailable",
                retryable=True,
            ), trace

        documents_by_handle = {
            document.document_handle: document for document in documents
        }
        component: dict[str, dict[str, tuple[int, BackendDiscoveryHit]]] = {
            "lexical": {},
            "vector": {},
        }
        for channel, hits in channel_hits.items():
            for hit in hits:
                if hit.document_handle not in documents_by_handle:
                    raise RetrievalStoreConflict(
                        "discovery backend returned a catalog-external document"
                    )
                if hit.document_handle in component[channel]:
                    continue
                component[channel][hit.document_handle] = (
                    len(component[channel]) + 1,
                    hit,
                )
            for channel_item in channel_trace:
                if channel_item["channel"] == channel:
                    channel_item["component_document_count"] = len(component[channel])

        ranked: list[
            tuple[
                Fraction,
                int,
                str,
                dict[str, tuple[int, BackendDiscoveryHit]],
            ]
        ] = []
        for document_handle in set(component["lexical"]) | set(component["vector"]):
            parts = {
                channel: values[document_handle]
                for channel, values in component.items()
                if document_handle in values
            }
            score = sum(
                (Fraction(1, rank) for rank, _hit in parts.values()),
                start=Fraction(0, 1),
            )
            best_rank = min(rank for rank, _hit in parts.values())
            ranked.append((score, best_rank, document_handle, parts))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))

        candidates: list[RelevantDocumentCandidateV1] = []
        for _score, _best_rank, document_handle, parts in ranked[: action.limit]:
            selected = parts.get("lexical") or parts["vector"]
            hit = selected[1]
            descriptor = documents_by_handle[document_handle].descriptor
            raw_modalities = descriptor.get("modalities")
            if not isinstance(raw_modalities, (list, tuple)):
                raise RetrievalStoreConflict(
                    "discovery catalog descriptor modalities are invalid"
                )
            candidates.append(
                RelevantDocumentCandidateV1(
                    document_handle=document_handle,
                    document_display_name=str(descriptor["display_name"]),
                    media_type=str(descriptor["media_type"]),
                    modalities=list(raw_modalities),
                    preview=hit.preview[:1000],
                    locator_label=hit.locator_label,
                    page_number=hit.page_number,
                )
            )
        vector_coverage = (
            len(documents)
            if "vector" in completed_channels
            else 0
        )
        observation = RelevantDocumentDiscoveryResultV1(
            result_type="relevant_document_discovery_result",
            candidates=candidates,
            ranking_contract="equal-reciprocal-rank-v1",
            channels=completed_channels,
            degraded=len(completed_channels) != 2,
            vector_coverage=vector_coverage,
            catalog_document_count=len(documents),
            truncated_by_budget=False,
        )
        trace = self._discovery_trace(
            catalog=catalog,
            action=action,
            channels=channel_trace,
            ranked=ranked,
            vector_coverage=vector_coverage,
        )
        return observation, trace

    @staticmethod
    def _discovery_trace(
        *,
        catalog: CatalogRecord,
        action: DiscoverRelevantDocumentsV1,
        channels: Sequence[Mapping[str, object]],
        ranked: Sequence[
            tuple[
                Fraction,
                int,
                str,
                dict[str, tuple[int, BackendDiscoveryHit]],
            ]
        ],
        vector_coverage: int,
    ) -> dict[str, object]:
        documents = {
            document.document_handle: document for document in catalog.documents
        }
        return {
            "schema_version": "relevant-document-discovery-trace-v1",
            "execution_id": catalog.execution_id,
            "catalog_ref": catalog.catalog_ref,
            "query_text": action.query_text,
            "requested_limit": action.limit,
            "ranking_contract": "equal-reciprocal-rank-v1",
            "channels": [dict(item) for item in channels],
            "catalog_document_count": len(catalog.documents),
            "vector_coverage": vector_coverage,
            "ranked_candidates": [
                {
                    "order": order,
                    "document_handle": document_handle,
                    "fused_score": f"{score.numerator}/{score.denominator}",
                    "best_component_rank": best_rank,
                    "components": {
                        channel: {
                            "rank": rank,
                            "match_ref": hit.match_ref,
                            "locator_label": hit.locator_label,
                            "page_number": hit.page_number,
                        }
                        for channel, (rank, hit) in sorted(parts.items())
                    },
                    "lineage": {
                        "lifecycle_epoch": documents[document_handle].lifecycle_epoch,
                        "document_version_ref": documents[
                            document_handle
                        ].document_version_ref,
                        "processing_generation_ref": documents[
                            document_handle
                        ].processing_generation_ref,
                        "processing_revision_ref": documents[
                            document_handle
                        ].processing_revision_ref,
                        "index_generation_ref": documents[
                            document_handle
                        ].index_generation_ref,
                        "manifest_digest": documents[document_handle].manifest_digest,
                    },
                }
                for order, (
                    score,
                    best_rank,
                    document_handle,
                    parts,
                ) in enumerate(ranked, start=1)
            ],
        }

    def materialize_evidence_pack(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
        evidence_handles: list[str],
        idempotency_key: str,
    ) -> EvidencePackRefV1:
        catalog = self._store.get_catalog(
            execution_id=execution_id, catalog_ref=catalog_ref
        )
        documents_by_handle = {
            item.document_handle: item for item in catalog.documents
        }
        resolved = self._store.resolve_handles(
            execution_id=execution_id, catalog_ref=catalog_ref, handles=tuple(evidence_handles)
        )
        if any(item.handle_kind not in {"evidence", "visual"} for item in resolved):
            raise RetrievalStoreConflict(
                "evidence pack accepts only obtained evidence or visual handles"
            )
        if any(
            item.source_result_ref is None
            or item.source_result_digest is None
            or item.source_invocation_ordinal is None
            for item in resolved
        ):
            raise RetrievalStoreConflict("evidence pack source lineage is incomplete")
        items_list: list[EvidencePackLineageInput] = []
        for item in resolved:
            document = documents_by_handle.get(item.document_handle or "")
            if (
                document is None
                or document.resource_ref is None
                or document.processing_revision_ref is None
                or item.source_result_ref is None
                or item.source_invocation_ordinal is None
            ):
                raise RetrievalStoreConflict(
                    "evidence pack exact revision lineage is incomplete"
                )
            items_list.append(
                EvidencePackLineageInput(
                    evidence_handle=item.handle,
                    evidence_ref=item.resource_ref,
                    evidence_digest=self._evidence_digest(item),
                    resource_ref=document.resource_ref,
                    document_version_ref=document.document_version_ref,
                    processing_revision_ref=document.processing_revision_ref,
                    index_generation_ref=document.index_generation_ref,
                    page_artifact_ref=None,
                    result_ref=item.source_result_ref,
                    invocation_ordinal=item.source_invocation_ordinal,
                )
            )
        items = tuple(items_list)
        record = self._store.materialize_evidence_pack(
            MaterializeEvidencePackInput(
                evidence_pack_ref=_opaque("evidence_pack", execution_id, catalog_ref, idempotency_key),
                execution_id=execution_id,
                catalog_ref=catalog_ref,
                items=items,
            )
        )
        return EvidencePackRefV1(
            **self._evidence_pack_payload(record),
        )

    def read_evidence_pack(self, evidence_pack_ref: str) -> EvidencePackRefV1 | None:
        record = self._store.read_evidence_pack(evidence_pack_ref)
        if record is None:
            return None
        return EvidencePackRefV1(**self._evidence_pack_payload(record))

    def read_claimed_evidence_lineage(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
        handles: list[str],
    ) -> list[ClaimedEvidenceLineageV1]:
        """Resolve declarations without treating them as verified evidence."""

        catalog = self._store.get_catalog(
            execution_id=execution_id, catalog_ref=catalog_ref
        )
        documents = {
            document.document_handle: document for document in catalog.documents
        }
        resolved = self._store.resolve_claimed_handles(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            handles=tuple(handles),
        )
        first_positions: dict[str, int] = {}
        items: list[ClaimedEvidenceLineageV1] = []
        for position, (raw_handle, handle) in enumerate(
            zip(handles, resolved, strict=True), start=1
        ):
            duplicate_of_position = first_positions.setdefault(raw_handle, position)
            duplicate_of_position = (
                None if duplicate_of_position == position else duplicate_of_position
            )
            if (
                handle is None
                or handle.handle_kind not in {"evidence", "visual"}
                or handle.document_handle is None
                or handle.source_result_ref is None
                or handle.source_invocation_ordinal is None
            ):
                items.append(
                    ClaimedEvidenceLineageV1(
                        position=position,
                        handle=raw_handle,
                        resolution_status="unresolved",
                        duplicate_of_position=duplicate_of_position,
                    )
                )
                continue
            document = documents.get(handle.document_handle)
            result = self._store.read_invocation_result(handle.source_result_ref)
            if (
                document is None
                or document.resource_ref is None
                or result is None
                or result.execution_id != execution_id
                or result.catalog_ref != catalog_ref
                or result.invocation_ordinal != handle.source_invocation_ordinal
                or result.result_digest != handle.source_result_digest
            ):
                items.append(
                    ClaimedEvidenceLineageV1(
                        position=position,
                        handle=raw_handle,
                        resolution_status="unresolved",
                        duplicate_of_position=duplicate_of_position,
                    )
                )
                continue
            observation = _OBSERVATION_ADAPTER.validate_python(result.observation)
            page_number: int | None = None
            locator_label: str | None = None
            if handle.handle_kind == "visual":
                if (
                    not isinstance(observation, VisualInspectionResultV1)
                    or observation.visual_handle != raw_handle
                    or observation.document_handle != handle.document_handle
                    or _visual_resource(
                        observation.document_handle,
                        observation.page_number,
                        (
                            observation.bbox.left,
                            observation.bbox.top,
                            observation.bbox.right,
                            observation.bbox.bottom,
                        ),
                        observation.image_digest,
                    )
                    != handle.resource_ref
                ):
                    items.append(
                        ClaimedEvidenceLineageV1(
                            position=position,
                            handle=raw_handle,
                            resolution_status="unresolved",
                            duplicate_of_position=duplicate_of_position,
                        )
                    )
                    continue
                bbox_label = (
                    f"{observation.bbox.left},{observation.bbox.top},"
                    f"{observation.bbox.right},{observation.bbox.bottom}"
                )
                page_number = observation.page_number
                locator_label = (
                    f"Page {observation.page_number} bbox [{bbox_label}]"
                )
            else:
                descriptors = (
                    observation.evidence
                    if isinstance(
                        observation,
                        (KnowledgeSearchResultV1, KnowledgeExpansionResultV1),
                    )
                    else []
                )
                descriptor = next(
                    (
                        candidate
                        for candidate in descriptors
                        if candidate.evidence_handle == raw_handle
                        and candidate.document_handle == handle.document_handle
                    ),
                    None,
                )
                if descriptor is None:
                    items.append(
                        ClaimedEvidenceLineageV1(
                            position=position,
                            handle=raw_handle,
                            resolution_status="unresolved",
                            duplicate_of_position=duplicate_of_position,
                        )
                    )
                    continue
                page_number = descriptor.page_number
                locator_label = descriptor.locator_label
            items.append(
                ClaimedEvidenceLineageV1(
                    position=position,
                    handle=raw_handle,
                    resolution_status="resolved",
                    duplicate_of_position=duplicate_of_position,
                    handle_kind=handle.handle_kind,
                    evidence_ref=handle.resource_ref,
                    result_ref=result.result_ref,
                    invocation_ordinal=result.invocation_ordinal,
                    document_ref=document.resource_ref,
                    document_handle=document.document_handle,
                    lifecycle_epoch=document.lifecycle_epoch,
                    document_version_ref=document.document_version_ref,
                    processing_revision_ref=document.processing_revision_ref,
                    processing_generation_ref=document.processing_generation_ref,
                    index_generation_ref=document.index_generation_ref,
                    document_display_name=str(document.descriptor["display_name"]),
                    document_version_label=document.descriptor.get("version_label"),
                    page_number=page_number,
                    locator_label=locator_label,
                )
            )
        return items

    def read_discovery_traces(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
    ) -> list[RelevantDocumentDiscoveryTraceV1]:
        """Read exact persisted discovery paths without applying authorization."""

        catalog = self._store.get_catalog(
            execution_id=execution_id, catalog_ref=catalog_ref
        )
        documents = {
            document.document_handle: document for document in catalog.documents
        }
        records = self._store.read_invocation_results(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            action="discover_relevant_documents",
        )
        projected: list[RelevantDocumentDiscoveryTraceV1] = []
        for record in records:
            raw_trace = record.observation.get("discovery_trace")
            raw_provider = record.observation.get("provider_observation")
            if not isinstance(raw_trace, Mapping) or not isinstance(
                raw_provider, Mapping
            ):
                raise RetrievalStoreConflict("discovery trace payload is incomplete")
            provider_candidates = raw_provider.get("candidates", [])
            if not isinstance(provider_candidates, list):
                raise RetrievalStoreConflict(
                    "discovery provider candidate payload is invalid"
                )
            provider_by_handle = {
                item["document_handle"]: item
                for item in provider_candidates
                if isinstance(item, Mapping)
                and isinstance(item.get("document_handle"), str)
            }
            raw_channels = raw_trace.get("channels")
            raw_ranked = raw_trace.get("ranked_candidates")
            if not isinstance(raw_channels, list) or not isinstance(
                raw_ranked, list
            ):
                raise RetrievalStoreConflict("discovery trace shape is invalid")
            channels = [
                DiscoveryChannelTraceV1(
                    channel=item["channel"],
                    status=item["status"],
                )
                for item in raw_channels
                if isinstance(item, Mapping)
            ]
            candidates: list[DiscoveryCandidateLineageV1] = []
            for raw_candidate in raw_ranked:
                if not isinstance(raw_candidate, Mapping):
                    raise RetrievalStoreConflict(
                        "discovery ranked candidate is invalid"
                    )
                document_handle = raw_candidate.get("document_handle")
                document = documents.get(
                    document_handle if isinstance(document_handle, str) else ""
                )
                raw_components = raw_candidate.get("components")
                if (
                    document is None
                    or document.resource_ref is None
                    or not isinstance(raw_components, Mapping)
                ):
                    raise RetrievalStoreConflict(
                        "discovery candidate lineage is incomplete"
                    )
                components = [
                    DiscoveryCandidateComponentV1(
                        channel=channel,
                        rank=value["rank"],
                        match_ref=value["match_ref"],
                        locator_label=value["locator_label"],
                        page_number=value.get("page_number"),
                    )
                    for channel, value in sorted(raw_components.items())
                    if isinstance(value, Mapping)
                ]
                visible_candidate = provider_by_handle.get(document.document_handle)
                candidates.append(
                    DiscoveryCandidateLineageV1(
                        position=raw_candidate["order"],
                        document_handle=document.document_handle,
                        fused_score=raw_candidate["fused_score"],
                        best_component_rank=raw_candidate["best_component_rank"],
                        components=components,
                        document_ref=document.resource_ref,
                        lifecycle_epoch=document.lifecycle_epoch,
                        document_version_ref=document.document_version_ref,
                        processing_revision_ref=document.processing_revision_ref,
                        processing_generation_ref=document.processing_generation_ref,
                        index_generation_ref=document.index_generation_ref,
                        document_display_name=str(
                            document.descriptor["display_name"]
                        ),
                        document_version_label=(
                            str(document.descriptor["version_label"])
                            if document.descriptor.get("version_label") is not None
                            else None
                        ),
                        preview=(
                            str(visible_candidate["preview"])
                            if visible_candidate is not None
                            else None
                        ),
                        locator_label=(
                            str(visible_candidate["locator_label"])
                            if visible_candidate is not None
                            else None
                        ),
                        page_number=(
                            visible_candidate.get("page_number")
                            if visible_candidate is not None
                            else None
                        ),
                    )
                )
            completed_channels = sum(
                item.status == "completed" for item in channels
            )
            projected.append(
                RelevantDocumentDiscoveryTraceV1(
                    invocation_id=record.invocation_id,
                    result_ref=record.result_ref,
                    invocation_ordinal=record.invocation_ordinal,
                    query_text=raw_trace["query_text"],
                    requested_limit=raw_trace["requested_limit"],
                    ranking_contract=raw_trace["ranking_contract"],
                    channels=channels,
                    degraded=completed_channels != 2,
                    failure_code=record.error_code,
                    candidates=candidates,
                )
            )
        return projected

    def read_governance_evidence_pack(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
        evidence_pack_ref: str,
        evidence_pack_digest: str,
    ) -> GovernanceEvidencePackV1:
        """Read only content already named by one exact execution evidence pack."""

        record = self._store.read_evidence_pack(evidence_pack_ref)
        if record is None:
            raise RetrievalStoreConflict("governance evidence pack is unknown")
        if (
            record.execution_id != execution_id
            or record.catalog_ref != catalog_ref
            or record.digest != evidence_pack_digest
        ):
            raise RetrievalStoreConflict("governance evidence pack authority changed")
        catalog = self._store.get_catalog(
            execution_id=execution_id, catalog_ref=catalog_ref
        )
        current = self._grant_resources.current_grant_document_resources(
            execution_id=execution_id, grant_ref=catalog.grant_ref
        )
        if current.authorization_revision != catalog.authorization_revision:
            raise RetrievalStoreConflict("governance catalog authorization changed")
        current_by_ref = {item.resource_ref: item for item in current.resources}
        for document in catalog.documents:
            authorized = current_by_ref.get(document.resource_ref)
            if authorized is None or (
                authorized.lifecycle_epoch,
                authorized.document_version_ref,
                authorized.processing_generation_ref,
                authorized.index_generation_ref,
                authorized.manifest_digest,
            ) != (
                document.lifecycle_epoch,
                document.document_version_ref,
                document.processing_generation_ref,
                document.index_generation_ref,
                document.manifest_digest,
            ):
                raise RetrievalStoreConflict("governance evidence authority is not current")
        resolved = self._store.resolve_handles(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            handles=tuple(item.evidence_handle for item in record.items),
        )
        for lineage, handle in zip(record.items, resolved, strict=True):
            if (
                handle.handle_kind not in {"evidence", "visual"}
                or handle.resource_ref != lineage.evidence_ref
                or self._evidence_digest(handle) != lineage.evidence_digest
                or handle.source_result_ref != lineage.result_ref
                or handle.source_invocation_ordinal != lineage.invocation_ordinal
            ):
                raise RetrievalStoreConflict("governance evidence lineage changed")
        if not record.items:
            evidence: Sequence[BackendEvidence] = ()
        else:
            evidence = self._backend.read_exact(
                documents=self._backend_documents(catalog),
                evidence_requests=tuple(
                    (lineage.evidence_ref, handle.document_handle or "")
                    for lineage, handle in zip(record.items, resolved, strict=True)
                    if handle.handle_kind == "evidence"
                ),
            )
        if len({item.evidence_ref for item in evidence}) != len(evidence):
            raise RetrievalStoreConflict(
                "governance evidence reader returned duplicates"
            )
        by_ref: dict[str, BackendEvidence] = {
            item.evidence_ref: item for item in evidence
        }
        for lineage, handle in zip(record.items, resolved, strict=True):
            if handle.handle_kind != "visual":
                continue
            result = self._store.read_invocation_result(lineage.result_ref)
            if result is None:
                raise RetrievalStoreConflict("visual evidence result is unavailable")
            observation = _OBSERVATION_ADAPTER.validate_python(result.observation)
            if (
                not isinstance(observation, VisualInspectionResultV1)
                or observation.visual_handle != handle.handle
                or observation.document_handle != handle.document_handle
                or _visual_resource(
                    observation.document_handle,
                    observation.page_number,
                    (
                        observation.bbox.left,
                        observation.bbox.top,
                        observation.bbox.right,
                        observation.bbox.bottom,
                    ),
                    observation.image_digest,
                )
                != lineage.evidence_ref
            ):
                raise RetrievalStoreConflict("visual evidence lineage changed")
            bbox_label = (
                f"{observation.bbox.left},{observation.bbox.top},"
                f"{observation.bbox.right},{observation.bbox.bottom}"
            )
            content = (
                f"Inspected PDF page {observation.page_number}, normalized bbox "
                f"[{bbox_label}], image digest {observation.image_digest}."
            )
            by_ref[lineage.evidence_ref] = BackendEvidence(
                evidence_ref=lineage.evidence_ref,
                evidence_identity=lineage.evidence_ref,
                document_handle=observation.document_handle,
                locator_label=(
                    f"Page {observation.page_number} bbox [{bbox_label}]"
                ),
                snippet=content,
                content=content,
                modalities=("figure",),
                page_number=observation.page_number,
            )
        if set(by_ref) != {item.evidence_ref for item in record.items}:
            raise RetrievalStoreConflict("governance evidence content is unavailable")
        documents_by_handle = {item.document_handle: item for item in catalog.documents}
        items: list[GovernanceEvidenceItemV1] = []
        visual_images: list[VisualImagePayloadV1] = []
        for lineage, handle in zip(record.items, resolved, strict=True):
            exact = by_ref[lineage.evidence_ref]
            if (
                exact.document_handle != handle.document_handle
                or exact.document_handle not in documents_by_handle
            ):
                raise RetrievalStoreConflict("governance evidence document lineage changed")
            items.append(
                GovernanceEvidenceItemV1(
                    evidence_handle=lineage.evidence_handle,
                    evidence_ref=lineage.evidence_ref,
                    evidence_digest=lineage.evidence_digest,
                    result_ref=lineage.result_ref,
                    invocation_ordinal=lineage.invocation_ordinal,
                    locator_label=exact.locator_label[:500],
                    snippet=exact.snippet[:4096],
                    content=exact.content[:12000],
                    modalities=list(exact.modalities),
                )
            )
            if handle.handle_kind == "visual":
                result = self._store.read_invocation_result(lineage.result_ref)
                if result is None:
                    raise RetrievalStoreConflict(
                        "visual evidence result is unavailable"
                    )
                observation = _OBSERVATION_ADAPTER.validate_python(
                    result.observation
                )
                if not isinstance(observation, VisualInspectionResultV1):
                    raise RetrievalStoreConflict(
                        "visual evidence observation is unavailable"
                    )
                carrier = self._replay_visual_image(catalog, observation)
                if carrier is None:
                    raise RetrievalStoreConflict(
                        "visual evidence image is unavailable"
                    )
                visual_images.append(carrier)
        return GovernanceEvidencePackV1(
            evidence_pack_ref=record.evidence_pack_ref,
            evidence_pack_digest=record.digest,
            execution_id=record.execution_id,
            catalog_ref=record.catalog_ref,
            items=items,
            visual_images=visual_images,
        )

    def read_declared_evidence_subset(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
        handles: list[str],
        visual_images: list[VisualImagePayloadV1],
    ) -> DeclaredEvidenceSubsetV1:
        """Project only declared handles from observations already shown to the model."""

        catalog = self._store.get_catalog(
            execution_id=execution_id, catalog_ref=catalog_ref
        )
        if len({image.visual_handle for image in visual_images}) != len(
            visual_images
        ):
            raise ValueError("model-visible visual carriers must be unique")
        visible_image_by_handle = {
            image.visual_handle: image for image in visual_images
        }
        first_positions: dict[str, int] = {}
        unique_handles: list[str] = []
        for position, handle in enumerate(handles, start=1):
            if handle not in first_positions:
                first_positions[handle] = position
                unique_handles.append(handle)
        resolved = self._store.resolve_claimed_handles(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            handles=tuple(unique_handles),
        )
        resolved_by_handle = dict(zip(unique_handles, resolved, strict=True))

        observations_by_handle: dict[
            str, list[ModelVisibleEvidenceObservationV1]
        ] = {handle: [] for handle in unique_handles}
        visual_carriers: dict[str, VisualImagePayloadV1] = {}
        records = sorted(
            (
                record
                for action in (
                    "search_knowledge",
                    "inspect_knowledge",
                    "expand_knowledge",
                    "inspect_visual",
                )
                for record in self._store.read_invocation_results(
                    execution_id=execution_id,
                    catalog_ref=catalog_ref,
                    action=action,
                )
            ),
            key=lambda item: item.invocation_ordinal,
        )
        for record in records:
            observation = _OBSERVATION_ADAPTER.validate_python(
                self._provider_observation(record.observation)
            )
            if isinstance(
                observation, (KnowledgeSearchResultV1, KnowledgeExpansionResultV1)
            ):
                for evidence in observation.evidence:
                    if evidence.evidence_handle not in observations_by_handle:
                        continue
                    observations_by_handle[evidence.evidence_handle].append(
                        ModelVisibleEvidenceObservationV1(
                            result_ref=record.result_ref,
                            result_digest=record.result_digest,
                            invocation_ordinal=record.invocation_ordinal,
                            result_type=observation.result_type,
                            content_kind="snippet",
                            locator_label=evidence.locator_label,
                            model_visible_content=evidence.snippet,
                            modalities=evidence.modalities,
                        )
                    )
            elif isinstance(observation, KnowledgeInspectionResultV1):
                for evidence in observation.items:
                    if evidence.evidence_handle not in observations_by_handle:
                        continue
                    observations_by_handle[evidence.evidence_handle].append(
                        ModelVisibleEvidenceObservationV1(
                            result_ref=record.result_ref,
                            result_digest=record.result_digest,
                            invocation_ordinal=record.invocation_ordinal,
                            result_type=observation.result_type,
                            content_kind="content",
                            locator_label=evidence.locator_label,
                            model_visible_content=evidence.content,
                            modalities=evidence.modalities,
                        )
                    )
            elif (
                isinstance(observation, VisualInspectionResultV1)
                and observation.visual_handle in observations_by_handle
            ):
                bbox = observation.bbox
                observations_by_handle[observation.visual_handle].append(
                    ModelVisibleEvidenceObservationV1(
                        result_ref=record.result_ref,
                        result_digest=record.result_digest,
                        invocation_ordinal=record.invocation_ordinal,
                        result_type=observation.result_type,
                        content_kind="visual",
                        locator_label=(
                            f"Page {observation.page_number} bbox "
                            f"[{bbox.left},{bbox.top},{bbox.right},{bbox.bottom}]"
                        ),
                        model_visible_content=_canonical(
                            observation.model_dump(mode="json")
                        ).decode("utf-8"),
                        modalities=["figure"],
                    )
                )
                carrier = visible_image_by_handle.get(observation.visual_handle)
                if carrier is not None:
                    if (
                        carrier.image_ref != observation.image_ref
                        or carrier.image_digest != observation.image_digest
                        or carrier.width != observation.width
                        or carrier.height != observation.height
                    ):
                        raise RetrievalStoreConflict(
                            "model-visible visual carrier lineage changed"
                        )
                    visual_carriers[observation.visual_handle] = carrier

        items: list[DeclaredEvidenceItemV1] = []
        subset_position_by_handle: dict[str, int] = {}
        unresolved_reason_by_handle: dict[str, str] = {}
        for handle in unique_handles:
            resolved_handle = resolved_by_handle[handle]
            if resolved_handle is None:
                unresolved_reason_by_handle[handle] = "unknown_or_out_of_execution"
                continue
            if resolved_handle.handle_kind not in {"evidence", "visual"}:
                unresolved_reason_by_handle[handle] = "wrong_handle_kind"
                continue
            if (
                resolved_handle.source_result_ref is None
                or resolved_handle.source_result_digest is None
                or resolved_handle.source_invocation_ordinal is None
            ):
                unresolved_reason_by_handle[handle] = (
                    "model_visible_observation_unavailable"
                )
                continue
            observations = observations_by_handle[handle]
            if (
                not observations
                or (
                    resolved_handle.handle_kind == "visual"
                    and handle not in visual_carriers
                )
            ):
                unresolved_reason_by_handle[handle] = (
                    "model_visible_observation_unavailable"
                )
                continue
            subset_position = len(items) + 1
            subset_position_by_handle[handle] = subset_position
            items.append(
                DeclaredEvidenceItemV1(
                    subset_position=subset_position,
                    first_declared_position=first_positions[handle],
                    evidence_handle=handle,
                    handle_kind=resolved_handle.handle_kind,
                    evidence_ref=resolved_handle.resource_ref,
                    evidence_digest=self._evidence_digest(resolved_handle),
                    source_result_ref=resolved_handle.source_result_ref,
                    source_result_digest=resolved_handle.source_result_digest,
                    source_invocation_ordinal=(
                        resolved_handle.source_invocation_ordinal
                    ),
                    observations=observations,
                )
            )

        mappings: list[DeclaredEvidenceMappingV1] = []
        for position, handle in enumerate(handles, start=1):
            first_position = first_positions[handle]
            subset_position = subset_position_by_handle.get(handle)
            mappings.append(
                DeclaredEvidenceMappingV1(
                    position=position,
                    handle=handle,
                    resolution_status=(
                        "resolved" if subset_position is not None else "unresolved"
                    ),
                    duplicate_of_position=(
                        None if position == first_position else first_position
                    ),
                    subset_position=subset_position,
                    reason_code=(
                        "resolved"
                        if subset_position is not None
                        else unresolved_reason_by_handle[handle]
                    ),
                )
            )
        serializable = {
            "schema_version": "declared-evidence-subset-v1",
            "execution_id": execution_id,
            "catalog_ref": catalog_ref,
            "mappings": [item.model_dump(mode="json") for item in mappings],
            "items": [item.model_dump(mode="json") for item in items],
        }
        return DeclaredEvidenceSubsetV1(
            **serializable,
            digest=_digest(serializable),
            visual_images=[
                visual_carriers[item.evidence_handle]
                for item in items
                if item.handle_kind == "visual"
            ],
        )

    def _evidence_pack_payload(self, record: EvidencePackRecord) -> dict[str, object]:
        catalog = self._store.get_catalog(
            execution_id=record.execution_id, catalog_ref=record.catalog_ref
        )
        resolved = self._store.resolve_handles(
            execution_id=record.execution_id,
            catalog_ref=record.catalog_ref,
            handles=tuple(item.evidence_handle for item in record.items),
        )
        documents = {item.document_handle: item for item in catalog.documents}
        public_items: list[EvidencePackLineageItemV1] = []
        for item, handle in zip(record.items, resolved, strict=True):
            document = documents.get(handle.document_handle)
            if document is None or document.resource_ref is None:
                raise RetrievalStoreConflict(
                    "evidence pack catalog lacks canonical authorization resource lineage"
                )
            public_items.append(
                EvidencePackLineageItemV1(
                    evidence_handle=item.evidence_handle,
                    evidence_ref=item.evidence_ref,
                    evidence_digest=item.evidence_digest,
                    resource_ref=document.resource_ref,
                    lifecycle_epoch=document.lifecycle_epoch,
                    document_version_ref=document.document_version_ref,
                    processing_revision_ref=item.processing_revision_ref,
                    processing_generation_ref=document.processing_generation_ref,
                    index_generation_ref=document.index_generation_ref,
                    page_artifact_ref=item.page_artifact_ref,
                    result_ref=item.result_ref,
                    invocation_ordinal=item.invocation_ordinal,
                )
            )
        return {
            "evidence_pack_ref": record.evidence_pack_ref,
            "execution_id": record.execution_id,
            "catalog_ref": record.catalog_ref,
            "items": public_items,
            "digest": record.digest,
            "created_at": record.created_at,
        }

    def release_catalog(
        self, *, execution_id: str, catalog_ref: str, idempotency_key: str
    ) -> None:
        self._store.release_catalog(
            ReleaseCatalogInput(
                release_id=_opaque("release", execution_id, catalog_ref),
                execution_id=execution_id,
                catalog_ref=catalog_ref,
                idempotency_key=idempotency_key,
            )
        )

    def release_execution_catalog(
        self, *, execution_id: str, idempotency_key: str
    ) -> None:
        catalog = self._store.get_catalog_for_execution(execution_id)
        if catalog is None:
            return
        self.release_catalog(
            execution_id=execution_id,
            catalog_ref=catalog.catalog_ref,
            idempotency_key=idempotency_key,
        )

    def _validate_action_handles(self, catalog: CatalogRecord, action: KnowledgeToolActionV1):
        handles: tuple[str, ...] = ()
        if isinstance(action, SearchKnowledgeV1):
            if not action.document_handles:
                raise RetrievalStoreConflict(
                    "search requires at least one selected document handle"
                )
            handles = tuple(action.document_handles)
        elif isinstance(action, InspectKnowledgeV1):
            handles = tuple(action.handles)
        elif isinstance(action, InspectVisualV1):
            handles = (action.handle,)
        elif isinstance(action, ExpandKnowledgeV1):
            handles = tuple(action.anchor_handles)
        elif isinstance(action, NavigateDocumentV1):
            handles = (
                (action.navigation_handle,)
                if action.mode == "around"
                else (action.document_handle,)
            )
        resolved = self._store.resolve_handles(
            execution_id=catalog.execution_id, catalog_ref=catalog.catalog_ref, handles=handles
        )
        if isinstance(action, SearchKnowledgeV1) and any(item.handle_kind != "document" for item in resolved):
            raise RetrievalStoreConflict("search accepts only catalog document handles")
        if isinstance(action, (InspectKnowledgeV1, ExpandKnowledgeV1)) and any(
            item.handle_kind != "evidence" for item in resolved
        ):
            raise RetrievalStoreConflict("inspect and expand require obtained evidence handles")
        if isinstance(action, InspectVisualV1) and any(
            item.handle_kind not in {"page", "visual"} for item in resolved
        ):
            raise RetrievalStoreConflict("inspect_visual requires a page or visual handle")
        if isinstance(action, NavigateDocumentV1):
            expected_kind = "navigation" if action.mode == "around" else "document"
            if len(resolved) != 1 or resolved[0].handle_kind != expected_kind:
                raise RetrievalStoreConflict(
                    f"navigate_document {action.mode} requires one {expected_kind} handle"
                )
        return resolved

    def _execute(self, catalog: CatalogRecord, action: KnowledgeToolActionV1, resolved):
        if isinstance(action, (ListKnowledgeDocumentsV1, FindKnowledgeDocumentsV1)):
            return self._catalog_page(catalog, action), (), None
        documents = self._backend_documents(catalog)
        by_handle = {document.document_handle: document for document in documents}
        if isinstance(action, SearchKnowledgeV1):
            selected = tuple(by_handle[handle] for handle in action.document_handles)
            evidence = self._backend.search(
                documents=selected,
                query_text=action.query_text,
                required_modalities=tuple(action.required_modalities),
                facet_hints=action.facet_hints.model_dump(mode="json"),
                limit=action.limit,
            )
            observation, handles = self._evidence_result(
                catalog, evidence, expansion_direction=None, limit=action.limit
            )
            return observation, handles, None
        if isinstance(action, NavigateDocumentV1):
            return (*self._navigate_document(catalog, action, resolved, by_handle), None)
        evidence_refs = tuple(item.resource_ref for item in resolved)
        if isinstance(action, InspectKnowledgeV1):
            evidence = self._backend.inspect(documents=documents, evidence_refs=evidence_refs)
            self._validate_backend_evidence(documents, evidence, expected_refs=set(evidence_refs))
            lineage_by_ref = {item.resource_ref: item for item in resolved}
            if any(
                item.evidence_identity != lineage_by_ref[item.evidence_ref].evidence_identity
                or item.document_handle != lineage_by_ref[item.evidence_ref].document_handle
                for item in evidence
            ):
                raise RetrievalStoreConflict("backend inspection changed obtained evidence lineage")
            return KnowledgeInspectionResultV1(
                result_type="knowledge_inspection_result",
                items=[
                    KnowledgeInspectionItemV1(
                        evidence_handle=lineage_by_ref[item.evidence_ref].handle,
                        document_handle=item.document_handle,
                        document_display_name=str(
                            by_handle[item.document_handle].descriptor["display_name"]
                        ),
                        locator_label=item.locator_label,
                        content=item.content,
                        modalities=list(item.modalities),
                    )
                    for item in evidence
                ],
            ), (), None
        if isinstance(action, InspectVisualV1):
            return self._inspect_visual(catalog, action, resolved, by_handle)
        assert isinstance(action, ExpandKnowledgeV1)
        evidence = self._backend.expand(
            documents=documents,
            anchor_evidence_refs=evidence_refs,
            direction=action.direction,
            limit=action.limit,
        )
        observation, handles = self._evidence_result(
            catalog, evidence, expansion_direction=action.direction, limit=action.limit
        )
        return observation, handles, None

    def _catalog_page(self, catalog: CatalogRecord, action):
        documents = list(catalog.documents)
        if isinstance(action, FindKnowledgeDocumentsV1):
            normalized_keyword = _normalize_identity(action.keyword)
            documents = [
                item
                for item in documents
                if self._matches(item, normalized_keyword)
            ]
            page_size = 10
            cursor_scope = f"find:{normalized_keyword}"
        else:
            page_size = action.page_size
            cursor_scope = "list"
        offset = self._cursor_offset(
            catalog.catalog_ref,
            cursor_scope,
            action.cursor,
        )
        page = documents[offset : offset + page_size]
        next_cursor = (
            self._cursor(catalog.catalog_ref, cursor_scope, offset + page_size)
            if offset + page_size < len(documents) else None
        )
        return KnowledgeCatalogPageV1(
            result_type="knowledge_catalog_page",
            documents=[self._public_descriptor(item) for item in page],
            next_cursor=next_cursor,
        )

    @staticmethod
    def _matches(
        document: CatalogDocumentInput,
        normalized_keyword: str,
    ) -> bool:
        value = document.descriptor
        identity_values = [
            str(value.get("display_name", "")),
            str(value.get("version_label") or ""),
            *(str(tag) for tag in value.get("tags", [])),
        ]
        return any(
            normalized_keyword in _normalize_identity(identity_value)
            for identity_value in identity_values
        )

    @staticmethod
    def _public_descriptor(document: CatalogDocumentInput) -> KnowledgeDocumentDescriptorV1:
        value = document.descriptor
        return KnowledgeDocumentDescriptorV1(
            document_handle=document.document_handle,
            display_name=str(value["display_name"]),
            media_type=str(value["media_type"]),
            modalities=list(value["modalities"]),
            tags=list(value.get("tags", [])),
            version_label=value.get("version_label"),
        )

    @staticmethod
    def _backend_documents(catalog: CatalogRecord) -> tuple[BackendCatalogDocument, ...]:
        return tuple(
            BackendCatalogDocument(
                document_handle=document.document_handle,
                lifecycle_epoch=document.lifecycle_epoch,
                document_version_ref=document.document_version_ref,
                processing_generation_ref=document.processing_generation_ref,
                processing_revision_ref=document.processing_revision_ref or "",
                index_generation_ref=document.index_generation_ref,
                manifest_digest=document.manifest_digest,
                descriptor={
                    key: value
                    for key, value in document.descriptor.items()
                    if key != AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY
                },
            )
            for document in catalog.documents
        )

    def _evidence_result(self, catalog, evidence, *, expansion_direction, limit):
        documents = self._backend_documents(catalog)
        documents_by_handle = {item.document_handle: item for item in documents}
        self._validate_backend_evidence(documents, evidence)
        descriptors = []
        handles = []
        for item in evidence[:limit]:
            handle = _opaque("evidence", catalog.execution_id, catalog.catalog_ref, item.evidence_identity)
            page_handle = (
                _opaque(
                    "page",
                    catalog.execution_id,
                    catalog.catalog_ref,
                    item.document_handle,
                    str(item.page_number),
                )
                if item.page_number is not None
                else None
            )
            descriptors.append(EvidenceDescriptorV1(
                evidence_handle=handle,
                document_handle=item.document_handle,
                document_display_name=str(
                    documents_by_handle[item.document_handle].descriptor["display_name"]
                ),
                locator_label=item.locator_label,
                snippet=item.snippet,
                modalities=list(item.modalities),
                page_handle=page_handle,
                page_number=item.page_number,
            ))
            handles.append(ResultHandleInput(
                handle=handle,
                handle_kind="evidence",
                resource_ref=item.evidence_ref,
                evidence_identity=item.evidence_identity,
                document_handle=item.document_handle,
            ))
            if page_handle is not None and item.page_number is not None:
                handles.append(ResultHandleInput(
                    handle=page_handle,
                    handle_kind="page",
                    resource_ref=_page_resource(
                        item.document_handle, item.page_number
                    ),
                    document_handle=item.document_handle,
                ))
        if expansion_direction is None:
            observation = KnowledgeSearchResultV1(
                result_type="knowledge_search_result", evidence=descriptors, next_cursor=None
            )
        else:
            observation = KnowledgeExpansionResultV1(
                result_type="knowledge_expansion_result",
                direction=expansion_direction,
                evidence=descriptors,
            )
        return observation, tuple({item.handle: item for item in handles}.values())

    def _navigate_document(
        self,
        catalog: CatalogRecord,
        action: NavigateDocumentV1,
        resolved,
        documents_by_handle: Mapping[str, BackendCatalogDocument],
    ):
        if action.mode == "around":
            source = resolved[0]
            if source.document_handle is None:
                raise RetrievalStoreConflict("navigation handle lacks document lineage")
            document = documents_by_handle.get(source.document_handle)
            node_ref = source.resource_ref.removeprefix("navigation|")
            if document is None or node_ref == source.resource_ref:
                raise RetrievalStoreConflict("navigation handle is outside catalog")
        else:
            assert action.document_handle is not None
            document = documents_by_handle[action.document_handle]
            node_ref = None
        navigation_map = self._backend.navigation_map(document=document)
        if navigation_map is None:
            return (
                KnowledgeToolErrorV1(
                    result_type="knowledge_tool_error",
                    error_code="navigation_unavailable",
                    message_code="document_navigation_is_unavailable",
                    retryable=False,
                ),
                (),
            )
        nodes = list(navigation_map.nodes)
        next_cursor = None
        if action.mode == "overview":
            cursor_scope = f"navigation:{document.document_handle}:{navigation_map.digest}"
            offset = self._cursor_offset(
                catalog.catalog_ref, cursor_scope, action.cursor
            )
            selected = nodes[offset : offset + action.limit]
            if offset + action.limit < len(nodes):
                next_cursor = self._cursor(
                    catalog.catalog_ref, cursor_scope, offset + action.limit
                )
        elif action.mode == "search":
            terms = tuple(
                term
                for term in _normalize_identity(action.query_text or "").split()
                if term
            )
            ranked = []
            for node in nodes:
                haystack = _normalize_identity(
                    " ".join([*node.structure_path, node.label, node.search_text])
                )
                score = sum(haystack.count(term) for term in terms)
                if score:
                    ranked.append((-score, node.ordinal, node))
            ranked.sort(key=lambda item: (item[0], item[1]))
            selected = [item[2] for item in ranked[: action.limit]]
        else:
            current = next((item for item in nodes if item.node_ref == node_ref), None)
            if current is None:
                raise RetrievalStoreConflict("navigation node is not in current map")
            if action.relation == "previous":
                selected = [
                    item for item in nodes if item.ordinal < current.ordinal
                ][-action.limit :]
            elif action.relation == "next":
                selected = [
                    item for item in nodes if item.ordinal > current.ordinal
                ][: action.limit]
            elif action.relation == "parent":
                selected = [
                    item
                    for item in nodes
                    if item.node_ref == current.parent_node_ref
                ][: action.limit]
            elif action.relation == "children":
                selected = [
                    item
                    for item in nodes
                    if item.parent_node_ref == current.node_ref
                ][: action.limit]
            else:
                selected = [
                    item
                    for item in nodes
                    if item.page_number == current.page_number
                    and item.node_ref != current.node_ref
                ][: action.limit]
        targets = []
        handles = []
        for node in selected:
            navigation_handle = _opaque(
                "navigation",
                catalog.execution_id,
                catalog.catalog_ref,
                document.document_handle,
                navigation_map.digest,
                node.node_ref,
            )
            page_handle = (
                _opaque(
                    "page",
                    catalog.execution_id,
                    catalog.catalog_ref,
                    document.document_handle,
                    str(node.page_number),
                )
                if node.has_page_visual
                else None
            )
            targets.append(
                NavigationTargetV1(
                    navigation_handle=navigation_handle,
                    document_handle=document.document_handle,
                    document_display_name=str(
                        document.descriptor["display_name"]
                    ),
                    kind=node.kind,
                    label=node.label,
                    structure_path=node.structure_path,
                    page_number=node.page_number,
                    content_traits=node.content_traits,
                    page_handle=page_handle,
                )
            )
            handles.append(
                ResultHandleInput(
                    handle=navigation_handle,
                    handle_kind="navigation",
                    resource_ref=f"navigation|{node.node_ref}",
                    document_handle=document.document_handle,
                )
            )
            if page_handle is not None:
                handles.append(
                    ResultHandleInput(
                        handle=page_handle,
                        handle_kind="page",
                        resource_ref=_page_resource(
                            document.document_handle, node.page_number
                        ),
                        document_handle=document.document_handle,
                    )
                )
        return (
            DocumentNavigationResultV1(
                result_type="document_navigation_result",
                mode=action.mode,
                map_digest=navigation_map.digest,
                targets=targets,
                next_cursor=next_cursor,
            ),
            tuple({item.handle: item for item in handles}.values()),
        )

    def _inspect_visual(self, catalog, action, resolved, documents_by_handle):
        if len(resolved) != 1:
            raise RetrievalStoreConflict("inspect_visual requires exactly one handle")
        source = resolved[0]
        document_handle, page_number, parent_bbox = _parse_visual_resource(
            source.handle_kind, source.resource_ref
        )
        document = documents_by_handle.get(document_handle)
        if document is None:
            raise RetrievalStoreConflict("visual handle document is outside catalog")
        requested_bbox = (
            (0, 0, 10_000, 10_000)
            if action.bbox is None
            else (
                action.bbox.left,
                action.bbox.top,
                action.bbox.right,
                action.bbox.bottom,
            )
        )
        root_bbox = (
            parent_bbox
            if action.scope == "full"
            else _compose_bbox(parent_bbox, requested_bbox)
        )
        rendered = self._backend.render_visual(
            document=document,
            page_number=page_number,
            normalized_bbox=root_bbox,
        )
        if hashlib.sha256(rendered.content).hexdigest() != rendered.digest:
            raise RetrievalStoreConflict("visual renderer digest changed")
        page_handle = _opaque(
            "page",
            catalog.execution_id,
            catalog.catalog_ref,
            document_handle,
            str(page_number),
        )
        visual_handle = _opaque(
            "visual",
            catalog.execution_id,
            catalog.catalog_ref,
            document_handle,
            str(page_number),
            *[str(value) for value in root_bbox],
            rendered.digest,
        )
        image_ref = f"image:{rendered.digest}"
        observation = VisualInspectionResultV1(
            result_type="visual_inspection_result",
            visual_handle=visual_handle,
            source_handle=action.handle,
            page_handle=page_handle,
            document_handle=document_handle,
            page_number=page_number,
            scope=action.scope,
            bbox={
                "left": root_bbox[0],
                "top": root_bbox[1],
                "right": root_bbox[2],
                "bottom": root_bbox[3],
            },
            image_ref=image_ref,
            image_digest=rendered.digest,
            width=rendered.width,
            height=rendered.height,
        )
        carrier = VisualImagePayloadV1(
            visual_handle=visual_handle,
            image_ref=image_ref,
            image_digest=rendered.digest,
            width=rendered.width,
            height=rendered.height,
            content=rendered.content,
        )
        return observation, (
            ResultHandleInput(
                handle=visual_handle,
                handle_kind="visual",
                resource_ref=_visual_resource(
                    document_handle, page_number, root_bbox, rendered.digest
                ),
                document_handle=document_handle,
            ),
        ), carrier

    def _replay_visual_image(self, catalog, observation):
        if not isinstance(observation, VisualInspectionResultV1):
            return None
        documents = {
            item.document_handle: item for item in self._backend_documents(catalog)
        }
        document = documents.get(observation.document_handle)
        if document is None:
            raise RetrievalStoreConflict("visual replay document is outside catalog")
        bbox = (
            observation.bbox.left,
            observation.bbox.top,
            observation.bbox.right,
            observation.bbox.bottom,
        )
        rendered = self._backend.render_visual(
            document=document,
            page_number=observation.page_number,
            normalized_bbox=bbox,
        )
        if (
            rendered.digest != observation.image_digest
            or rendered.width != observation.width
            or rendered.height != observation.height
        ):
            raise RetrievalStoreConflict("visual replay render changed")
        return VisualImagePayloadV1(
            visual_handle=observation.visual_handle,
            image_ref=observation.image_ref,
            image_digest=rendered.digest,
            width=rendered.width,
            height=rendered.height,
            content=rendered.content,
        )

    @staticmethod
    def _validate_backend_evidence(documents, evidence, expected_refs=None) -> None:
        allowed = {document.document_handle for document in documents}
        seen: set[str] = set()
        for item in evidence:
            if item.document_handle not in allowed or item.evidence_identity in seen:
                raise RetrievalStoreConflict("backend returned catalog-external or duplicate evidence")
            if expected_refs is not None and item.evidence_ref not in expected_refs:
                raise RetrievalStoreConflict("backend inspection returned unrequested evidence")
            seen.add(item.evidence_identity)

    @staticmethod
    def _cursor(catalog_ref: str, scope: str, offset: int) -> str:
        return f"kc_{offset}_{_digest([catalog_ref, scope, offset])[:16]}"

    @classmethod
    def _cursor_offset(
        cls,
        catalog_ref: str,
        scope: str,
        cursor: str | None,
    ) -> int:
        if cursor is None:
            return 0
        try:
            prefix, raw_offset, proof = cursor.split("_", 2)
            offset = int(raw_offset)
        except (ValueError, TypeError):
            raise RetrievalStoreConflict("catalog cursor is invalid") from None
        if (
            prefix != "kc"
            or offset < 0
            or proof != _digest([catalog_ref, scope, offset])[:16]
        ):
            raise RetrievalStoreConflict("catalog cursor is invalid")
        return offset

    @staticmethod
    def _evidence_digest(item: ResultHandleInput) -> str:
        return _digest(
            {
                "evidence_ref": item.resource_ref,
                "evidence_identity": item.evidence_identity,
                "document_handle": item.document_handle,
            }
        )

    def _envelope(
        self,
        catalog,
        observation,
        record,
        *,
        resolved=(),
        visual_image=None,
        tokenizer,
    ):
        candidates: list[str] = []
        lineage: list[RetrievalEvidenceLineageV1] = []
        if observation.result_type == "knowledge_catalog_page":
            candidates = [item.document_handle for item in observation.documents]
        elif observation.result_type == "relevant_document_discovery_result":
            candidates = [item.document_handle for item in observation.candidates]
        elif observation.result_type in {"knowledge_search_result", "knowledge_expansion_result"}:
            candidates = list(dict.fromkeys(item.document_handle for item in observation.evidence))
            evidence_handles = tuple(item.evidence_handle for item in observation.evidence)
            persisted = self._store.resolve_handles(
                execution_id=catalog.execution_id,
                catalog_ref=catalog.catalog_ref,
                handles=evidence_handles,
            )
            lineage = [
                RetrievalEvidenceLineageV1(
                    evidence_handle=item.handle,
                    evidence_ref=item.resource_ref,
                    evidence_digest=self._evidence_digest(item),
                    evidence_identity=item.evidence_identity,
                    document_handle=item.document_handle,
                    result_ref=item.source_result_ref or record.result_ref,
                    result_digest=item.source_result_digest or record.result_digest,
                    invocation_ordinal=item.source_invocation_ordinal or record.invocation_ordinal,
                )
                for item in persisted
                if item.evidence_identity is not None and item.document_handle is not None
            ]
        elif observation.result_type == "knowledge_inspection_result":
            candidates = list(
                dict.fromkeys(
                    item.document_handle for item in resolved if item.document_handle is not None
                )
            )
        elif observation.result_type == "visual_inspection_result":
            candidates = [observation.document_handle]
            persisted = self._store.resolve_handles(
                execution_id=catalog.execution_id,
                catalog_ref=catalog.catalog_ref,
                handles=(observation.visual_handle,),
            )
            lineage = [
                RetrievalEvidenceLineageV1(
                    evidence_handle=item.handle,
                    evidence_ref=item.resource_ref,
                    evidence_digest=self._evidence_digest(item),
                    evidence_identity=item.resource_ref,
                    document_handle=item.document_handle or "",
                    result_ref=item.source_result_ref or record.result_ref,
                    result_digest=item.source_result_digest or record.result_digest,
                    invocation_ordinal=(
                        item.source_invocation_ordinal or record.invocation_ordinal
                    ),
                )
                for item in persisted
            ]
        elif observation.result_type == "document_navigation_result":
            candidates = list(
                dict.fromkeys(
                    item.document_handle for item in observation.targets
                )
            )
        tool_tokens = len(
            tokenizer.encode(
                _canonical(observation.model_dump(mode="json")).decode("utf-8")
            )
        )
        return RetrievalInvocationEnvelopeV1(
            observation=observation,
            result_ref=record.result_ref,
            result_digest=record.result_digest,
            document_candidate_handles=candidates,
            evidence_lineage=lineage,
            catalog_pages=1 if observation.result_type == "knowledge_catalog_page" else 0,
            search_rounds=1
            if observation.result_type == "knowledge_search_result"
            or (
                observation.result_type == "document_navigation_result"
                and observation.mode == "search"
            )
            else 0,
            tool_tokens=tool_tokens,
            replayed=record.replayed,
            visual_image=visual_image,
        )


__all__ = [
    "BackendCatalogDocument", "BackendDiscoveryHit", "BackendEvidence", "BackendVisualImage",
    "KnowledgeRetrievalBackend", "KnowledgeToolService",
]
