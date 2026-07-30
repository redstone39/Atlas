"""Processing-owned exact-generation retention claims for Strict Turn catalogs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingGenerationRetentionEntryRow,
    AtlasProcessingGenerationRetentionRow,
    AtlasProcessingGenerationRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
)
from atlas_production.modules.processing_pipeline.public import (
    CreateGenerationRetentionV1,
    GenerationRetentionRefV1,
    ReleaseGenerationRetentionV1,
)


SessionFactory = Callable[[], Session]


class GenerationRetentionConflict(RuntimeError):
    """The requested exact generation set is absent, stale, or conflicting."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _processing_generation(reference: str) -> int:
    prefix = "processing-generation-"
    if not reference.startswith(prefix):
        raise GenerationRetentionConflict("processing generation ref is invalid")
    try:
        value = int(reference.removeprefix(prefix))
    except ValueError as error:
        raise GenerationRetentionConflict("processing generation ref is invalid") from error
    if value < 1:
        raise GenerationRetentionConflict("processing generation ref is invalid")
    return value


class PostgresGenerationRetentionOwner:
    """Creates and releases claims using only processing-owned tables."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _model(row: AtlasProcessingGenerationRetentionRow) -> GenerationRetentionRefV1:
        return GenerationRetentionRefV1(
            retention_ref=row.retention_ref,
            execution_id=row.execution_id,
            resource_count=row.resource_count,
            digest=row.digest,
            created_at=row.created_at,
        )

    def create_generation_retention(
        self, command: CreateGenerationRetentionV1
    ) -> GenerationRetentionRefV1:
        with self._session_factory() as pin_session:
            exact_resources: list[dict[str, object]] = []
            for resource in command.resources:
                item = resource.model_dump(mode="json")
                index = pin_session.get(
                    AtlasIndexGenerationRow, resource.index_generation_ref
                )
                revision = (
                    pin_session.get(
                        AtlasProcessingRevisionRow,
                        index.processing_revision_id,
                    )
                    if index is not None
                    and index.processing_revision_id is not None
                    else None
                )
                if (
                    index is None
                    or revision is None
                    or revision.state != "ready"
                    or index.processing_revision_id
                    != revision.processing_revision_id
                    or index.manifest_digest != resource.manifest_digest
                    or revision.manifest_digest != resource.manifest_digest
                    or resource.processing_generation_ref
                    != f"processing-generation-{index.source_processing_generation}"
                    or (
                        resource.processing_revision_ref is not None
                        and resource.processing_revision_ref
                        != revision.processing_revision_id
                    )
                ):
                    raise GenerationRetentionConflict(
                        "generation retention revision pin is unavailable"
                    )
                item["processing_revision_ref"] = revision.processing_revision_id
                exact_resources.append(item)
        canonical = sorted(
            exact_resources,
            key=lambda item: str(item["index_generation_ref"]),
        )
        if len({str(item["index_generation_ref"]) for item in canonical}) != len(canonical):
            raise GenerationRetentionConflict("generation retention contains duplicates")
        digest = _digest(
            {
                "operation": "create_generation_retention",
                "execution_id": command.execution_id,
                "resources": canonical,
            }
        )
        retention_ref = f"generation-retention-{digest}"
        with self._session_factory() as session, session.begin():
            acquire_owner_locks(
                session,
                identity_keys=(
                    f"processing_generation_retention:{command.execution_id}",
                ),
            )
            replay = session.scalar(
                select(AtlasProcessingGenerationRetentionRow).where(
                    AtlasProcessingGenerationRetentionRow.execution_id
                    == command.execution_id
                )
            )
            if replay is not None:
                if replay.status == "released":
                    raise GenerationRetentionConflict(
                        "generation retention execution was already released"
                    )
                if (
                    replay.digest != digest
                    or replay.idempotency_key != command.idempotency_key
                    or replay.retention_ref != retention_ref
                ):
                    raise GenerationRetentionConflict("generation retention replay changed")
                return self._model(replay)

            generation_ids = [str(item["index_generation_ref"]) for item in canonical]
            generations = (
                session.scalars(
                    select(AtlasIndexGenerationRow)
                    .where(AtlasIndexGenerationRow.index_generation_id.in_(generation_ids))
                    .order_by(AtlasIndexGenerationRow.index_generation_id)
                    .with_for_update()
                ).all()
                if generation_ids
                else []
            )
            by_id = {row.index_generation_id: row for row in generations}
            if set(by_id) != set(generation_ids):
                raise GenerationRetentionConflict("index generation is unavailable")

            now = datetime.now(timezone.utc)
            row = AtlasProcessingGenerationRetentionRow(
                retention_ref=retention_ref,
                execution_id=command.execution_id,
                resource_count=len(canonical),
                digest=digest,
                idempotency_key=command.idempotency_key,
                status="active",
                release_idempotency_key=None,
                created_at=now,
                released_at=None,
            )
            session.add(row)
            session.flush()
            for item in canonical:
                index = by_id[str(item["index_generation_ref"])]
                revision = session.get(
                    AtlasProcessingRevisionRow,
                    str(item["processing_revision_ref"]),
                )
                binding_version = session.get(
                    AtlasDocumentVersionRow,
                    str(item["document_version_ref"]),
                )
                binding = (
                    session.get(AtlasDocumentRow, binding_version.document_id)
                    if binding_version is not None
                    else None
                )
                processing_generation = _processing_generation(
                    str(item["processing_generation_ref"])
                )
                processing = session.get(
                    AtlasProcessingGenerationRow,
                    (index.document_id, processing_generation),
                )
                if (
                    index.status != "active"
                    or revision is None
                    or revision.state != "ready"
                    or binding is None
                    or binding.lifecycle_status != "active"
                    or binding.processing_identity_id
                    != revision.processing_identity_id
                    or processing is None
                    or processing.status != "active"
                    or index.processing_revision_id
                    != item["processing_revision_ref"]
                    or index.source_processing_generation != processing_generation
                    or index.manifest_digest != item["manifest_digest"]
                    or revision.manifest_digest != item["manifest_digest"]
                ):
                    raise GenerationRetentionConflict("generation identity is not current and exact")
                session.add(
                    AtlasProcessingGenerationRetentionEntryRow(
                        retention_ref=retention_ref,
                        index_generation_id=index.index_generation_id,
                        document_id=index.document_id,
                        document_version_id=index.document_version_id,
                        processing_generation=processing_generation,
                        manifest_digest=str(item["manifest_digest"]),
                        created_at=now,
                    )
                )
            session.flush()
            return self._model(row)

    def release_generation_retention(
        self, command: ReleaseGenerationRetentionV1
    ) -> None:
        self._release(
            execution_id=command.execution_id,
            retention_ref=command.retention_ref,
            idempotency_key=command.idempotency_key,
        )

    def release_execution_generation_retention(
        self, *, execution_id: str, idempotency_key: str
    ) -> None:
        self._release(
            execution_id=execution_id,
            retention_ref=None,
            idempotency_key=idempotency_key,
        )

    def _release(
        self, *, execution_id: str, retention_ref: str | None, idempotency_key: str
    ) -> None:
        with self._session_factory() as session, session.begin():
            acquire_owner_locks(
                session,
                identity_keys=(f"processing_generation_retention:{execution_id}",),
            )
            row = session.scalar(
                select(AtlasProcessingGenerationRetentionRow)
                .where(
                    AtlasProcessingGenerationRetentionRow.execution_id == execution_id
                )
                .with_for_update()
            )
            if row is None:
                tombstone_digest = _digest(
                    {
                        "operation": "release_generation_retention_before_create",
                        "execution_id": execution_id,
                        "retention_ref": retention_ref,
                    }
                )
                session.add(
                    AtlasProcessingGenerationRetentionRow(
                        retention_ref=(
                            retention_ref
                            or f"generation-retention-tombstone-{tombstone_digest}"
                        ),
                        execution_id=execution_id,
                        resource_count=0,
                        digest=tombstone_digest,
                        idempotency_key=idempotency_key,
                        status="released",
                        release_idempotency_key=idempotency_key,
                        created_at=datetime.now(timezone.utc),
                        released_at=datetime.now(timezone.utc),
                    )
                )
                session.flush()
                return
            if retention_ref is not None and row.retention_ref != retention_ref:
                raise GenerationRetentionConflict("generation retention does not belong to execution")
            if row.status == "released":
                if row.release_idempotency_key != idempotency_key:
                    raise GenerationRetentionConflict("generation retention release replay changed")
                return
            row.status = "released"
            row.release_idempotency_key = idempotency_key
            row.released_at = datetime.now(timezone.utc)


__all__ = ["GenerationRetentionConflict", "PostgresGenerationRetentionOwner"]
