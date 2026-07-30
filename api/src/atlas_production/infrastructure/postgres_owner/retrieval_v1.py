"""Retrieval-owned persistence for strict-turn catalogs and tool results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.retrieval import (
    AtlasTurnCatalogDocumentRow,
    AtlasTurnEvidenceIdentityRow,
    AtlasTurnKnowledgeCatalogRow,
    AtlasTurnRetrievalEvidencePackRow,
    AtlasTurnRetrievalHandleRow,
    AtlasTurnRetrievalInvocationRow,
    AtlasTurnRetrievalReleaseRow,
    AtlasTurnRetrievalResultRow,
)


SessionFactory = Callable[[], Session]
CATALOG_SCHEMA_VERSION = "knowledge-catalog-snapshot-v1"
AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY = "_authorization_resource_ref"
PROCESSING_REVISION_REF_DESCRIPTOR_KEY = "_processing_revision_ref"
ActionName = Literal[
    "list_knowledge_documents",
    "find_knowledge_documents",
    "discover_relevant_documents",
    "search_knowledge",
    "inspect_knowledge",
    "inspect_visual",
    "expand_knowledge",
    "navigate_document",
]


class RetrievalStoreConflict(RuntimeError):
    """A catalog, invocation, result, or handle identity conflicts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CatalogDocumentInput:
    document_handle: str
    lifecycle_epoch: int
    document_version_ref: str
    generation_ref: str
    processing_generation_ref: str
    processing_revision_ref: str | None
    index_generation_ref: str
    manifest_digest: str
    descriptor: Mapping[str, object]
    resource_ref: str | None = None


@dataclass(frozen=True, slots=True)
class CreateCatalogInput:
    catalog_ref: str
    execution_id: str
    grant_ref: str
    generation_retention_ref: str
    authorization_revision: int
    retrieval_generation_ref: str
    documents: tuple[CatalogDocumentInput, ...]
    idempotency_key: str
    schema_version: str = CATALOG_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    catalog_ref: str
    execution_id: str
    grant_ref: str
    generation_retention_ref: str
    authorization_revision: int
    schema_version: str
    retrieval_generation_ref: str
    documents: tuple[CatalogDocumentInput, ...]
    digest: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ResultHandleInput:
    handle: str
    handle_kind: Literal["document", "evidence", "page", "visual", "navigation"]
    resource_ref: str
    evidence_identity: str | None = None
    document_handle: str | None = None
    source_result_ref: str | None = None
    source_result_digest: str | None = None
    source_invocation_ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class PersistInvocationResultInput:
    invocation_id: str
    result_ref: str
    execution_id: str
    catalog_ref: str
    invocation_ordinal: int
    action: ActionName
    schema_version: str
    canonical_arguments: Mapping[str, object]
    result_type: str
    observation: Mapping[str, object]
    error_code: str | None
    handles: tuple[ResultHandleInput, ...] = ()


@dataclass(frozen=True, slots=True)
class InvocationResultRecord:
    invocation_id: str
    result_ref: str
    execution_id: str
    catalog_ref: str
    invocation_ordinal: int
    action: str
    schema_version: str
    arguments_digest: str
    canonical_arguments: Mapping[str, object]
    result_type: str
    result_digest: str
    observation: Mapping[str, object]
    error_code: str | None
    created_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class EvidencePackLineageInput:
    evidence_handle: str
    evidence_ref: str
    evidence_digest: str
    resource_ref: str
    document_version_ref: str
    processing_revision_ref: str
    index_generation_ref: str
    page_artifact_ref: str | None
    result_ref: str
    invocation_ordinal: int


@dataclass(frozen=True, slots=True)
class MaterializeEvidencePackInput:
    evidence_pack_ref: str
    execution_id: str
    catalog_ref: str
    items: tuple[EvidencePackLineageInput, ...]


@dataclass(frozen=True, slots=True)
class EvidencePackRecord:
    evidence_pack_ref: str
    execution_id: str
    catalog_ref: str
    items: tuple[EvidencePackLineageInput, ...]
    digest: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseCatalogInput:
    release_id: str
    execution_id: str
    catalog_ref: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CatalogReleaseRecord:
    release_id: str
    execution_id: str
    catalog_ref: str
    idempotency_key: str
    released_at: datetime


def _catalog_payload(command: CreateCatalogInput) -> dict[str, object]:
    return {
        "operation": "create_catalog",
        "schema_version": command.schema_version,
        "execution_id": command.execution_id,
        "grant_ref": command.grant_ref,
        "generation_retention_ref": command.generation_retention_ref,
        "authorization_revision": command.authorization_revision,
        "retrieval_generation_ref": command.retrieval_generation_ref,
        "documents": [
            {**asdict(document), "descriptor": dict(document.descriptor)}
            for document in command.documents
        ],
    }


class PostgresRetrievalV1Store:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        canonicalize_catalog: Callable[[CreateCatalogInput], CreateCatalogInput]
        | None = None,
        canonicalize_evidence_pack: Callable[
            [MaterializeEvidencePackInput, tuple[ResultHandleInput, ...]],
            MaterializeEvidencePackInput,
        ]
        | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._canonicalize_catalog = canonicalize_catalog
        self._canonicalize_evidence_pack = canonicalize_evidence_pack

    def create_catalog(self, command: CreateCatalogInput) -> CatalogRecord:
        if command.schema_version != CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported catalog schema version")
        if command.authorization_revision < 1:
            raise ValueError("authorization_revision must be positive")
        handles = [document.document_handle for document in command.documents]
        if len(handles) != len(set(handles)) or any(len(handle) < 8 for handle in handles):
            raise ValueError("catalog document handles must be unique opaque values")
        if any(document.lifecycle_epoch < 1 for document in command.documents):
            raise ValueError("document lifecycle epochs must be positive")
        if self._canonicalize_catalog is not None:
            command = self._canonicalize_catalog(command)
        digest = _digest(_catalog_payload(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnKnowledgeCatalogRow).where(
                    AtlasTurnKnowledgeCatalogRow.grant_ref == command.grant_ref,
                    AtlasTurnKnowledgeCatalogRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.digest != digest or replay.catalog_ref != command.catalog_ref:
                    raise RetrievalStoreConflict("catalog replay payload changed")
                return self._load_catalog(session, replay)
            execution = session.scalar(
                select(AtlasTurnKnowledgeCatalogRow).where(
                    AtlasTurnKnowledgeCatalogRow.execution_id == command.execution_id
                )
            )
            if execution is not None or session.get(AtlasTurnKnowledgeCatalogRow, command.catalog_ref) is not None:
                raise RetrievalStoreConflict("catalog or execution identity already exists")
            created_at = _now()
            row = AtlasTurnKnowledgeCatalogRow(
                catalog_ref=command.catalog_ref,
                execution_id=command.execution_id,
                grant_ref=command.grant_ref,
                generation_retention_ref=command.generation_retention_ref,
                authorization_revision=command.authorization_revision,
                schema_version=command.schema_version,
                retrieval_generation_ref=command.retrieval_generation_ref,
                document_count=len(command.documents),
                digest=digest,
                idempotency_key=command.idempotency_key,
                created_at=created_at,
            )
            session.add(row)
            # Catalog documents and execution-local handles reference the
            # immutable catalog. Their owner-local FK must see the parent first.
            session.flush()
            for ordinal, document in enumerate(command.documents, start=1):
                session.add(
                    AtlasTurnCatalogDocumentRow(
                        catalog_ref=command.catalog_ref,
                        document_handle=document.document_handle,
                        ordinal=ordinal,
                        lifecycle_epoch=document.lifecycle_epoch,
                        document_version_ref=document.document_version_ref,
                        generation_ref=document.generation_ref,
                        processing_generation_ref=document.processing_generation_ref,
                        index_generation_ref=document.index_generation_ref,
                        manifest_digest=document.manifest_digest,
                        descriptor={
                            **dict(document.descriptor),
                            PROCESSING_REVISION_REF_DESCRIPTOR_KEY: (
                                document.processing_revision_ref
                            ),
                            **(
                                {AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY: document.resource_ref}
                                if document.resource_ref is not None
                                else {}
                            ),
                        },
                    )
                )
                session.add(
                    AtlasTurnRetrievalHandleRow(
                        handle=document.document_handle,
                        execution_id=command.execution_id,
                        catalog_ref=command.catalog_ref,
                        handle_kind="document",
                        resource_ref=document.document_version_ref,
                        evidence_identity=None,
                        document_handle=document.document_handle,
                        source_invocation_id=None,
                        created_at=created_at,
                    )
                )
            session.flush()
            return self._load_catalog(session, row)

    def get_catalog(self, *, execution_id: str, catalog_ref: str) -> CatalogRecord:
        with self._session_factory() as session:
            row = session.get(AtlasTurnKnowledgeCatalogRow, catalog_ref)
            if row is None or row.execution_id != execution_id:
                raise RetrievalStoreConflict("catalog does not belong to execution")
            return self._load_catalog(session, row)

    def get_catalog_for_execution(self, execution_id: str) -> CatalogRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnKnowledgeCatalogRow).where(
                    AtlasTurnKnowledgeCatalogRow.execution_id == execution_id
                )
            )
            return self._load_catalog(session, row) if row is not None else None

    def replay_invocation(
        self,
        *,
        execution_id: str,
        catalog_ref: str,
        action: ActionName,
        schema_version: str,
        canonical_arguments: Mapping[str, object],
    ) -> InvocationResultRecord | None:
        arguments_digest = _digest(dict(canonical_arguments))
        with self._session_factory() as session:
            invocation = session.scalar(
                select(AtlasTurnRetrievalInvocationRow).where(
                    AtlasTurnRetrievalInvocationRow.execution_id == execution_id,
                    AtlasTurnRetrievalInvocationRow.catalog_ref == catalog_ref,
                    AtlasTurnRetrievalInvocationRow.action == action,
                    AtlasTurnRetrievalInvocationRow.schema_version == schema_version,
                    AtlasTurnRetrievalInvocationRow.arguments_digest == arguments_digest,
                )
            )
            if invocation is None:
                return None
            result = session.scalar(
                select(AtlasTurnRetrievalResultRow).where(
                    AtlasTurnRetrievalResultRow.invocation_id == invocation.invocation_id
                )
            )
            if result is None:
                raise RetrievalStoreConflict("replayed invocation has no immutable result")
            return self._invocation_result(invocation, result, replayed=True)

    def persist_invocation_result(
        self, command: PersistInvocationResultInput
    ) -> InvocationResultRecord:
        if command.invocation_ordinal < 1:
            raise ValueError("invocation_ordinal must be positive")
        arguments = dict(command.canonical_arguments)
        if arguments.get("action") != command.action:
            raise ValueError("canonical action discriminator does not match action")
        arguments_digest = _digest(arguments)
        result_digest = _digest(
            {
                "operation": "retrieval_result",
                "result_type": command.result_type,
                "observation": dict(command.observation),
                "error_code": command.error_code,
                "handles": [asdict(handle) for handle in command.handles],
            }
        )
        with self._session_factory() as session, session.begin():
            catalog = session.get(AtlasTurnKnowledgeCatalogRow, command.catalog_ref)
            if catalog is None or catalog.execution_id != command.execution_id:
                raise RetrievalStoreConflict("catalog does not belong to execution")
            replay = session.scalar(
                select(AtlasTurnRetrievalInvocationRow).where(
                    AtlasTurnRetrievalInvocationRow.execution_id == command.execution_id,
                    AtlasTurnRetrievalInvocationRow.catalog_ref == command.catalog_ref,
                    AtlasTurnRetrievalInvocationRow.action == command.action,
                    AtlasTurnRetrievalInvocationRow.schema_version == command.schema_version,
                    AtlasTurnRetrievalInvocationRow.arguments_digest == arguments_digest,
                )
            )
            if replay is not None:
                result = session.scalar(
                    select(AtlasTurnRetrievalResultRow).where(
                        AtlasTurnRetrievalResultRow.invocation_id == replay.invocation_id
                    )
                )
                if result is None:
                    raise RetrievalStoreConflict("replayed invocation has no immutable result")
                if result.result_digest != result_digest:
                    raise RetrievalStoreConflict("invocation replay result changed")
                return self._invocation_result(replay, result, replayed=True)
            ordinal = session.scalar(
                select(AtlasTurnRetrievalInvocationRow).where(
                    AtlasTurnRetrievalInvocationRow.execution_id == command.execution_id,
                    AtlasTurnRetrievalInvocationRow.invocation_ordinal == command.invocation_ordinal,
                )
            )
            if ordinal is not None:
                raise RetrievalStoreConflict("invocation ordinal already consumed")
            if session.get(AtlasTurnRetrievalInvocationRow, command.invocation_id) is not None:
                raise RetrievalStoreConflict("invocation identity already exists")
            if session.get(AtlasTurnRetrievalResultRow, command.result_ref) is not None:
                raise RetrievalStoreConflict("result identity already exists")

            created_at = _now()
            invocation = AtlasTurnRetrievalInvocationRow(
                invocation_id=command.invocation_id,
                execution_id=command.execution_id,
                catalog_ref=command.catalog_ref,
                invocation_ordinal=command.invocation_ordinal,
                action=command.action,
                schema_version=command.schema_version,
                arguments_digest=arguments_digest,
                canonical_arguments=arguments,
                status="completed" if command.error_code is None else "failed",
                created_at=created_at,
            )
            session.add(invocation)
            session.flush()
            self._persist_handles(session, command, created_at)
            result = AtlasTurnRetrievalResultRow(
                result_ref=command.result_ref,
                invocation_id=command.invocation_id,
                result_type=command.result_type,
                result_digest=result_digest,
                observation=dict(command.observation),
                error_code=command.error_code,
                created_at=created_at,
            )
            session.add(result)
            session.flush()
            return self._invocation_result(invocation, result, replayed=False)

    def resolve_handles(
        self, *, execution_id: str, catalog_ref: str, handles: tuple[str, ...]
    ) -> tuple[ResultHandleInput, ...]:
        if len(handles) != len(set(handles)):
            raise ValueError("duplicate handle requested")
        if not handles:
            return ()
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnRetrievalHandleRow).where(
                    AtlasTurnRetrievalHandleRow.handle.in_(handles),
                    AtlasTurnRetrievalHandleRow.execution_id == execution_id,
                    AtlasTurnRetrievalHandleRow.catalog_ref == catalog_ref,
                )
            ).all()
            by_handle = {row.handle: row for row in rows}
            if set(by_handle) != set(handles):
                raise RetrievalStoreConflict("unknown or out-of-scope retrieval handle")
            evidence_identities = {
                row.evidence_identity
                for row in rows
                if row.evidence_identity is not None
            }
            evidence_rows = session.scalars(
                select(AtlasTurnEvidenceIdentityRow).where(
                    AtlasTurnEvidenceIdentityRow.catalog_ref == catalog_ref,
                    AtlasTurnEvidenceIdentityRow.evidence_identity.in_(
                        evidence_identities or {""}
                    ),
                )
            ).all()
            document_by_evidence = {
                row.evidence_identity: row.document_handle for row in evidence_rows
            }
            if set(document_by_evidence) != evidence_identities:
                raise RetrievalStoreConflict("evidence handle lineage is incomplete")
            source_ids = {
                row.source_invocation_id
                for row in rows
                if row.source_invocation_id is not None
            }
            invocation_rows = session.scalars(
                select(AtlasTurnRetrievalInvocationRow).where(
                    AtlasTurnRetrievalInvocationRow.invocation_id.in_(source_ids or {""})
                )
            ).all()
            result_rows = session.scalars(
                select(AtlasTurnRetrievalResultRow).where(
                    AtlasTurnRetrievalResultRow.invocation_id.in_(source_ids or {""})
                )
            ).all()
            invocation_by_id = {item.invocation_id: item for item in invocation_rows}
            result_by_invocation = {item.invocation_id: item for item in result_rows}
            if set(invocation_by_id) != source_ids or set(result_by_invocation) != source_ids:
                raise RetrievalStoreConflict("evidence source result lineage is incomplete")
            return tuple(
                ResultHandleInput(
                    handle=handle,
                    handle_kind=by_handle[handle].handle_kind,  # type: ignore[arg-type]
                    resource_ref=by_handle[handle].resource_ref,
                    evidence_identity=by_handle[handle].evidence_identity,
                    document_handle=by_handle[handle].document_handle,
                    source_result_ref=(
                        result_by_invocation[by_handle[handle].source_invocation_id].result_ref
                        if by_handle[handle].source_invocation_id is not None else None
                    ),
                    source_result_digest=(
                        result_by_invocation[by_handle[handle].source_invocation_id].result_digest
                        if by_handle[handle].source_invocation_id is not None else None
                    ),
                    source_invocation_ordinal=(
                        invocation_by_id[by_handle[handle].source_invocation_id].invocation_ordinal
                        if by_handle[handle].source_invocation_id is not None else None
                    ),
                )
                for handle in handles
            )

    def resolve_claimed_handles(
        self, *, execution_id: str, catalog_ref: str, handles: tuple[str, ...]
    ) -> tuple[ResultHandleInput | None, ...]:
        """Tolerantly resolve only handles scoped to one exact execution catalog."""

        if not handles:
            return ()
        requested = set(handles)
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnRetrievalHandleRow).where(
                    AtlasTurnRetrievalHandleRow.handle.in_(requested),
                    AtlasTurnRetrievalHandleRow.execution_id == execution_id,
                    AtlasTurnRetrievalHandleRow.catalog_ref == catalog_ref,
                )
            ).all()
            by_handle = {row.handle: row for row in rows}
            source_ids = {
                row.source_invocation_id
                for row in rows
                if row.source_invocation_id is not None
            }
            invocation_rows = session.scalars(
                select(AtlasTurnRetrievalInvocationRow).where(
                    AtlasTurnRetrievalInvocationRow.invocation_id.in_(
                        source_ids or {""}
                    )
                )
            ).all()
            result_rows = session.scalars(
                select(AtlasTurnRetrievalResultRow).where(
                    AtlasTurnRetrievalResultRow.invocation_id.in_(
                        source_ids or {""}
                    )
                )
            ).all()
            invocation_by_id = {row.invocation_id: row for row in invocation_rows}
            result_by_invocation = {row.invocation_id: row for row in result_rows}
            resolved: list[ResultHandleInput | None] = []
            for requested_handle in handles:
                row = by_handle.get(requested_handle)
                source_id = row.source_invocation_id if row is not None else None
                invocation = invocation_by_id.get(source_id) if source_id else None
                result = result_by_invocation.get(source_id) if source_id else None
                if row is None or invocation is None or result is None:
                    resolved.append(None)
                    continue
                resolved.append(
                    ResultHandleInput(
                        handle=row.handle,
                        handle_kind=row.handle_kind,  # type: ignore[arg-type]
                        resource_ref=row.resource_ref,
                        evidence_identity=row.evidence_identity,
                        document_handle=row.document_handle,
                        source_result_ref=result.result_ref,
                        source_result_digest=result.result_digest,
                        source_invocation_ordinal=invocation.invocation_ordinal,
                    )
                )
            return tuple(resolved)

    def materialize_evidence_pack(
        self, command: MaterializeEvidencePackInput
    ) -> EvidencePackRecord:
        if len(command.items) > 40:
            raise ValueError("evidence pack lineage must be bounded")
        handles = [item.evidence_handle for item in command.items]
        refs = [item.evidence_ref for item in command.items]
        if len(handles) != len(set(handles)) or len(refs) != len(set(refs)):
            raise ValueError("evidence pack handles and refs must be unique")
        resolved = self.resolve_handles(
            execution_id=command.execution_id,
            catalog_ref=command.catalog_ref,
            handles=tuple(handles),
        )
        if self._canonicalize_evidence_pack is not None:
            command = self._canonicalize_evidence_pack(command, resolved)
        for proposed, actual in zip(command.items, resolved, strict=True):
            actual_digest = _digest(
                {
                    "evidence_ref": actual.resource_ref,
                    "evidence_identity": actual.evidence_identity,
                    "document_handle": actual.document_handle,
                }
            )
            if (
                actual.handle_kind not in {"evidence", "visual"}
                or proposed.evidence_ref != actual.resource_ref
                or proposed.evidence_digest != actual_digest
                or proposed.result_ref != actual.source_result_ref
                or proposed.invocation_ordinal != actual.source_invocation_ordinal
            ):
                raise RetrievalStoreConflict("evidence pack lineage does not match owner records")
        digest = _digest(
            {
                "operation": "materialize_evidence_pack",
                "schema_version": "retrieval-evidence-pack-v1",
                "execution_id": command.execution_id,
                "catalog_ref": command.catalog_ref,
                "items": [asdict(item) for item in command.items],
            }
        )
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnRetrievalEvidencePackRow).where(
                    AtlasTurnRetrievalEvidencePackRow.execution_id == command.execution_id
                )
            )
            if replay is not None:
                if (
                    replay.evidence_pack_ref != command.evidence_pack_ref
                    or replay.catalog_ref != command.catalog_ref
                    or replay.digest != digest
                ):
                    raise RetrievalStoreConflict("evidence pack replay payload changed")
                return self._evidence_pack(replay)
            catalog = session.get(AtlasTurnKnowledgeCatalogRow, command.catalog_ref)
            if catalog is None or catalog.execution_id != command.execution_id:
                raise RetrievalStoreConflict("catalog does not belong to evidence-pack execution")
            known = set(
                session.scalars(
                    select(AtlasTurnEvidenceIdentityRow.evidence_ref).where(
                        AtlasTurnEvidenceIdentityRow.catalog_ref == command.catalog_ref,
                        AtlasTurnEvidenceIdentityRow.evidence_ref.in_(refs),
                    )
                ).all()
            )
            known.update(
                session.scalars(
                    select(AtlasTurnRetrievalHandleRow.resource_ref).where(
                        AtlasTurnRetrievalHandleRow.execution_id
                        == command.execution_id,
                        AtlasTurnRetrievalHandleRow.catalog_ref
                        == command.catalog_ref,
                        AtlasTurnRetrievalHandleRow.handle_kind == "visual",
                        AtlasTurnRetrievalHandleRow.resource_ref.in_(refs),
                    )
                ).all()
            )
            if known != set(refs):
                raise RetrievalStoreConflict("evidence pack contains unknown evidence refs")
            row = AtlasTurnRetrievalEvidencePackRow(
                evidence_pack_ref=command.evidence_pack_ref,
                execution_id=command.execution_id,
                catalog_ref=command.catalog_ref,
                lineage_items=[asdict(item) for item in command.items],
                digest=digest,
                created_at=_now(),
            )
            session.add(row)
            session.flush()
            return self._evidence_pack(row)

    def read_evidence_pack(self, evidence_pack_ref: str) -> EvidencePackRecord | None:
        if not evidence_pack_ref:
            raise ValueError("evidence_pack_ref must be non-empty")
        with self._session_factory() as session:
            row = session.get(AtlasTurnRetrievalEvidencePackRow, evidence_pack_ref)
            return None if row is None else self._evidence_pack(row)

    def read_invocation_result(self, result_ref: str) -> InvocationResultRecord | None:
        with self._session_factory() as session:
            result = session.get(AtlasTurnRetrievalResultRow, result_ref)
            if result is None:
                return None
            invocation = session.get(
                AtlasTurnRetrievalInvocationRow, result.invocation_id
            )
            if invocation is None:
                raise RetrievalStoreConflict("retrieval result invocation is missing")
            return self._invocation_result(invocation, result, replayed=False)

    def read_invocation_results(
        self, *, execution_id: str, catalog_ref: str, action: ActionName
    ) -> tuple[InvocationResultRecord, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    AtlasTurnRetrievalInvocationRow,
                    AtlasTurnRetrievalResultRow,
                )
                .join(
                    AtlasTurnRetrievalResultRow,
                    AtlasTurnRetrievalResultRow.invocation_id
                    == AtlasTurnRetrievalInvocationRow.invocation_id,
                )
                .where(
                    AtlasTurnRetrievalInvocationRow.execution_id == execution_id,
                    AtlasTurnRetrievalInvocationRow.catalog_ref == catalog_ref,
                    AtlasTurnRetrievalInvocationRow.action == action,
                )
                .order_by(AtlasTurnRetrievalInvocationRow.invocation_ordinal)
            ).all()
            return tuple(
                self._invocation_result(invocation, result, replayed=False)
                for invocation, result in rows
            )

    def release_catalog(self, command: ReleaseCatalogInput) -> CatalogReleaseRecord:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnRetrievalReleaseRow).where(
                    AtlasTurnRetrievalReleaseRow.execution_id == command.execution_id,
                    AtlasTurnRetrievalReleaseRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.catalog_ref != command.catalog_ref or replay.release_id != command.release_id:
                    raise RetrievalStoreConflict("catalog release replay payload changed")
                return self._release(replay)
            catalog = session.get(AtlasTurnKnowledgeCatalogRow, command.catalog_ref)
            if catalog is None or catalog.execution_id != command.execution_id:
                raise RetrievalStoreConflict("catalog does not belong to release execution")
            binding = session.scalar(
                select(AtlasTurnRetrievalReleaseRow).where(
                    AtlasTurnRetrievalReleaseRow.execution_id == command.execution_id,
                    AtlasTurnRetrievalReleaseRow.catalog_ref == command.catalog_ref,
                )
            )
            if binding is not None or session.get(AtlasTurnRetrievalReleaseRow, command.release_id) is not None:
                raise RetrievalStoreConflict("catalog release identity already exists")
            row = AtlasTurnRetrievalReleaseRow(
                release_id=command.release_id,
                catalog_ref=command.catalog_ref,
                execution_id=command.execution_id,
                idempotency_key=command.idempotency_key,
                released_at=_now(),
            )
            session.add(row)
            session.flush()
            return self._release(row)

    def _persist_handles(
        self, session: Session, command: PersistInvocationResultInput, created_at: datetime
    ) -> None:
        if len({handle.handle for handle in command.handles}) != len(command.handles):
            raise ValueError("result contains duplicate handles")
        catalog_document_handles = set(
            session.scalars(
                select(AtlasTurnCatalogDocumentRow.document_handle).where(
                    AtlasTurnCatalogDocumentRow.catalog_ref == command.catalog_ref
                )
            ).all()
        )
        for handle in command.handles:
            if len(handle.handle) < 8:
                raise ValueError("retrieval handles must be opaque")
            existing = session.get(AtlasTurnRetrievalHandleRow, handle.handle)
            if existing is not None:
                if (
                    existing.execution_id != command.execution_id
                    or existing.catalog_ref != command.catalog_ref
                    or existing.handle_kind != handle.handle_kind
                    or existing.resource_ref != handle.resource_ref
                    or existing.evidence_identity != handle.evidence_identity
                    or existing.document_handle != handle.document_handle
                ):
                    raise RetrievalStoreConflict("handle is scoped to another execution or resource")
                continue
            if handle.handle_kind == "document":
                # Every document handle is seeded from the immutable catalog.
                # Tool results may reference it but can never expand the
                # execution's authorized document universe.
                raise RetrievalStoreConflict("tool result cannot create a document handle")
            if handle.handle_kind == "evidence":
                if not handle.evidence_identity or handle.document_handle not in catalog_document_handles:
                    raise RetrievalStoreConflict("evidence handle lacks catalog-scoped document lineage")
                identity = session.get(
                    AtlasTurnEvidenceIdentityRow,
                    (command.catalog_ref, handle.evidence_identity),
                )
                if identity is None:
                    session.add(
                        AtlasTurnEvidenceIdentityRow(
                            catalog_ref=command.catalog_ref,
                            evidence_identity=handle.evidence_identity,
                            document_handle=handle.document_handle,
                            evidence_ref=handle.resource_ref,
                            first_seen_at=created_at,
                        )
                    )
                elif (
                    identity.document_handle != handle.document_handle
                    or identity.evidence_ref != handle.resource_ref
                ):
                    raise RetrievalStoreConflict("evidence identity lineage changed")
            elif handle.handle_kind in {"page", "visual", "navigation"}:
                if handle.document_handle not in catalog_document_handles:
                    raise RetrievalStoreConflict(
                        "visual handle lacks catalog-scoped document lineage"
                    )
            session.add(
                AtlasTurnRetrievalHandleRow(
                    handle=handle.handle,
                    execution_id=command.execution_id,
                    catalog_ref=command.catalog_ref,
                    handle_kind=handle.handle_kind,
                    resource_ref=handle.resource_ref,
                    evidence_identity=handle.evidence_identity,
                    document_handle=handle.document_handle,
                    source_invocation_id=command.invocation_id,
                    created_at=created_at,
                )
            )

    def _load_catalog(self, session: Session, row: AtlasTurnKnowledgeCatalogRow) -> CatalogRecord:
        documents = session.scalars(
            select(AtlasTurnCatalogDocumentRow)
            .where(AtlasTurnCatalogDocumentRow.catalog_ref == row.catalog_ref)
            .order_by(AtlasTurnCatalogDocumentRow.ordinal)
        ).all()
        return CatalogRecord(
            catalog_ref=row.catalog_ref,
            execution_id=row.execution_id,
            grant_ref=row.grant_ref,
            generation_retention_ref=row.generation_retention_ref,
            authorization_revision=row.authorization_revision,
            schema_version=row.schema_version,
            retrieval_generation_ref=row.retrieval_generation_ref,
            documents=tuple(
                CatalogDocumentInput(
                    document_handle=document.document_handle,
                    lifecycle_epoch=document.lifecycle_epoch,
                    document_version_ref=document.document_version_ref,
                    generation_ref=document.generation_ref,
                    processing_generation_ref=document.processing_generation_ref,
                    processing_revision_ref=document.descriptor.get(
                        PROCESSING_REVISION_REF_DESCRIPTOR_KEY
                    ),
                    index_generation_ref=document.index_generation_ref,
                    manifest_digest=document.manifest_digest,
                    descriptor={
                        key: value
                        for key, value in document.descriptor.items()
                        if key
                        not in {
                            AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY,
                            PROCESSING_REVISION_REF_DESCRIPTOR_KEY,
                        }
                    },
                    resource_ref=document.descriptor.get(
                        AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY
                    ),
                )
                for document in documents
            ),
            digest=row.digest,
            created_at=row.created_at,
        )

    @staticmethod
    def _invocation_result(
        invocation: AtlasTurnRetrievalInvocationRow,
        result: AtlasTurnRetrievalResultRow,
        *,
        replayed: bool,
    ) -> InvocationResultRecord:
        return InvocationResultRecord(
            invocation_id=invocation.invocation_id,
            result_ref=result.result_ref,
            execution_id=invocation.execution_id,
            catalog_ref=invocation.catalog_ref,
            invocation_ordinal=invocation.invocation_ordinal,
            action=invocation.action,
            schema_version=invocation.schema_version,
            arguments_digest=invocation.arguments_digest,
            canonical_arguments=invocation.canonical_arguments,
            result_type=result.result_type,
            result_digest=result.result_digest,
            observation=result.observation,
            error_code=result.error_code,
            created_at=result.created_at,
            replayed=replayed,
        )

    @staticmethod
    def _evidence_pack(row: AtlasTurnRetrievalEvidencePackRow) -> EvidencePackRecord:
        return EvidencePackRecord(
            evidence_pack_ref=row.evidence_pack_ref,
            execution_id=row.execution_id,
            catalog_ref=row.catalog_ref,
            items=tuple(EvidencePackLineageInput(**item) for item in row.lineage_items),
            digest=row.digest,
            created_at=row.created_at,
        )

    @staticmethod
    def _release(row: AtlasTurnRetrievalReleaseRow) -> CatalogReleaseRecord:
        return CatalogReleaseRecord(
            release_id=row.release_id,
            execution_id=row.execution_id,
            catalog_ref=row.catalog_ref,
            idempotency_key=row.idempotency_key,
            released_at=row.released_at,
        )


__all__ = [
    "AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY",
    "CatalogDocumentInput",
    "CatalogRecord",
    "CatalogReleaseRecord",
    "CreateCatalogInput",
    "EvidencePackRecord",
    "EvidencePackLineageInput",
    "InvocationResultRecord",
    "MaterializeEvidencePackInput",
    "PersistInvocationResultInput",
    "PostgresRetrievalV1Store",
    "ReleaseCatalogInput",
    "ResultHandleInput",
    "RetrievalStoreConflict",
]
