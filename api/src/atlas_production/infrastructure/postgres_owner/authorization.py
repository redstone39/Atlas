"""Authorization-owned immutable grant persistence for strict turns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.authorization import (
    AtlasAuthorizationRevisionRow,
    AtlasTurnAccessGrantReleaseRow,
    AtlasTurnAccessGrantRow,
    AtlasTurnGrantDocumentResourceRow,
    AtlasTurnGrantResourceSnapshotRow,
)


SessionFactory = Callable[[], Session]
GRANT_SCHEMA_VERSION = "turn-access-grant-v1"


def _apply_statement_deadline(session: Session, deadline_at: datetime | None) -> None:
    if deadline_at is None:
        return
    remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("retrieval tool deadline elapsed")
    timeout_ms = max(1, int(remaining * 1000))
    session.execute(select(func.set_config("statement_timeout", f"{timeout_ms}ms", True)))


class AuthorizationStoreConflict(RuntimeError):
    """An immutable grant/release identity was reused with different content."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CreateGrantInput:
    grant_ref: str
    execution_id: str
    actor_id: str
    conversation_id: str
    authorization_revision: int
    authority_digest: str
    deadline_at: datetime
    idempotency_key: str
    schema_version: str = GRANT_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GrantRecord:
    grant_ref: str
    execution_id: str
    actor_id: str
    conversation_id: str
    schema_version: str
    digest: str
    authorization_revision: int
    issued_at: datetime
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseGrantInput:
    release_id: str
    execution_id: str
    grant_ref: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GrantReleaseRecord:
    release_id: str
    execution_id: str
    grant_ref: str
    idempotency_key: str
    released_at: datetime


@dataclass(frozen=True, slots=True)
class GrantDocumentResourceInput:
    resource_ref: str
    lifecycle_epoch: int
    document_version_ref: str
    processing_generation_ref: str
    index_generation_ref: str
    manifest_digest: str
    descriptor: dict[str, object]


@dataclass(frozen=True, slots=True)
class MaterializeGrantResourcesInput:
    execution_id: str
    grant_ref: str
    authorization_revision: int
    resources: tuple[GrantDocumentResourceInput, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GrantResourceSnapshotRecord:
    execution_id: str
    grant_ref: str
    authorization_revision: int
    resources: tuple[GrantDocumentResourceInput, ...]
    digest: str
    created_at: datetime


def _grant(row: AtlasTurnAccessGrantRow) -> GrantRecord:
    return GrantRecord(
        grant_ref=row.grant_ref,
        execution_id=row.execution_id,
        actor_id=row.actor_id,
        conversation_id=row.conversation_id,
        schema_version=row.schema_version,
        digest=row.digest,
        authorization_revision=row.authorization_revision,
        issued_at=row.issued_at,
        deadline_at=row.deadline_at,
    )


def _release(row: AtlasTurnAccessGrantReleaseRow) -> GrantReleaseRecord:
    return GrantReleaseRecord(
        release_id=row.release_id,
        execution_id=row.execution_id,
        grant_ref=row.grant_ref,
        idempotency_key=row.idempotency_key,
        released_at=row.released_at,
    )


def _grant_digest(command: CreateGrantInput) -> str:
    return _digest(
        {
            "operation": "create_grant",
            "schema_version": command.schema_version,
            "execution_id": command.execution_id,
            "actor_id": command.actor_id,
            "conversation_id": command.conversation_id,
            "authorization_revision": command.authorization_revision,
            "authority_digest": command.authority_digest,
            "deadline_at": command.deadline_at.isoformat(),
        }
    )


class PostgresAuthorizationStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create_grant(self, command: CreateGrantInput) -> GrantRecord:
        if command.schema_version != GRANT_SCHEMA_VERSION:
            raise ValueError("unsupported grant schema version")
        if command.authorization_revision < 1:
            raise ValueError("authorization_revision must be positive")
        if command.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        digest = _grant_digest(command)
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnAccessGrantRow).where(
                    AtlasTurnAccessGrantRow.actor_id == command.actor_id,
                    AtlasTurnAccessGrantRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.digest != digest or replay.grant_ref != command.grant_ref:
                    raise AuthorizationStoreConflict("grant replay payload changed")
                return _grant(replay)
            if session.get(AtlasTurnAccessGrantRow, command.grant_ref) is not None:
                raise AuthorizationStoreConflict("grant identity already exists")
            execution = session.scalar(
                select(AtlasTurnAccessGrantRow).where(
                    AtlasTurnAccessGrantRow.execution_id == command.execution_id
                )
            )
            if execution is not None:
                raise AuthorizationStoreConflict("execution already has a grant")

            revision = session.get(AtlasAuthorizationRevisionRow, command.authorization_revision)
            if revision is None:
                session.add(
                    AtlasAuthorizationRevisionRow(
                        revision=command.authorization_revision,
                        authority_digest=command.authority_digest,
                        created_at=_now(),
                    )
                )
            elif revision.authority_digest != command.authority_digest:
                raise AuthorizationStoreConflict("authorization revision digest changed")

            issued_at = _now()
            if command.deadline_at < issued_at:
                raise ValueError("deadline_at precedes grant issue time")
            row = AtlasTurnAccessGrantRow(
                grant_ref=command.grant_ref,
                execution_id=command.execution_id,
                actor_id=command.actor_id,
                conversation_id=command.conversation_id,
                schema_version=command.schema_version,
                digest=digest,
                authorization_revision=command.authorization_revision,
                idempotency_key=command.idempotency_key,
                issued_at=issued_at,
                deadline_at=command.deadline_at,
            )
            session.add(row)
            session.flush()
            return _grant(row)

    def get_grant_for_execution(self, execution_id: str) -> GrantRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnAccessGrantRow).where(
                    AtlasTurnAccessGrantRow.execution_id == execution_id
                )
            )
            return _grant(row) if row is not None else None

    def release_grant(self, command: ReleaseGrantInput) -> GrantReleaseRecord:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnAccessGrantReleaseRow).where(
                    AtlasTurnAccessGrantReleaseRow.execution_id == command.execution_id,
                    AtlasTurnAccessGrantReleaseRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.grant_ref != command.grant_ref or replay.release_id != command.release_id:
                    raise AuthorizationStoreConflict("grant release replay payload changed")
                return _release(replay)
            binding = session.scalar(
                select(AtlasTurnAccessGrantReleaseRow).where(
                    AtlasTurnAccessGrantReleaseRow.execution_id == command.execution_id,
                    AtlasTurnAccessGrantReleaseRow.grant_ref == command.grant_ref,
                )
            )
            if binding is not None:
                raise AuthorizationStoreConflict("grant release binding already exists")
            grant = session.get(AtlasTurnAccessGrantRow, command.grant_ref)
            if grant is None or grant.execution_id != command.execution_id:
                raise AuthorizationStoreConflict("grant does not belong to execution")
            if session.get(AtlasTurnAccessGrantReleaseRow, command.release_id) is not None:
                raise AuthorizationStoreConflict("release identity already exists")
            row = AtlasTurnAccessGrantReleaseRow(
                release_id=command.release_id,
                grant_ref=command.grant_ref,
                execution_id=command.execution_id,
                idempotency_key=command.idempotency_key,
                released_at=_now(),
            )
            session.add(row)
            session.flush()
            return _release(row)

    def get_grant(
        self, grant_ref: str, *, deadline_at: datetime | None = None
    ) -> GrantRecord | None:
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            row = session.get(AtlasTurnAccessGrantRow, grant_ref)
            return None if row is None else _grant(row)

    def get_grant_by_idempotency(
        self, *, actor_id: str, idempotency_key: str
    ) -> GrantRecord | None:
        """Read an accepted replay before consulting mutable external authority."""

        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnAccessGrantRow).where(
                    AtlasTurnAccessGrantRow.actor_id == actor_id,
                    AtlasTurnAccessGrantRow.idempotency_key == idempotency_key,
                )
            )
            return None if row is None else _grant(row)

    def materialize_grant_resources(
        self, command: MaterializeGrantResourcesInput
    ) -> GrantResourceSnapshotRecord:
        refs = [resource.resource_ref for resource in command.resources]
        if len(refs) != len(set(refs)):
            raise ValueError("grant document resource refs must be unique")
        if command.authorization_revision < 1 or any(
            resource.lifecycle_epoch < 1 for resource in command.resources
        ):
            raise ValueError("grant resource revisions and lifecycle epochs must be positive")
        allowed_descriptor_fields = {
            "display_name", "media_type", "modalities", "tags", "language",
            "created_at_label", "searchable_content", "version_label",
        }
        for resource in command.resources:
            if set(resource.descriptor) != allowed_descriptor_fields:
                raise ValueError("grant document descriptor fields are not closed")
            if any(
                not value.strip()
                for value in (
                    resource.resource_ref,
                    resource.document_version_ref,
                    resource.processing_generation_ref,
                    resource.index_generation_ref,
                )
            ):
                raise ValueError("grant document exact pin refs must be non-empty")
            if (
                len(resource.manifest_digest) != 64
                or any(character not in "0123456789abcdef" for character in resource.manifest_digest)
            ):
                raise ValueError("grant document manifest digest is invalid")
            descriptor = resource.descriptor
            if (
                not isinstance(descriptor["display_name"], str)
                or not descriptor["display_name"].strip()
                or not isinstance(descriptor["media_type"], str)
                or not descriptor["media_type"].strip()
                or not isinstance(descriptor["modalities"], list)
                or not descriptor["modalities"]
                or any(
                    not isinstance(modality, str)
                    or modality not in {"text", "table", "figure"}
                    for modality in descriptor["modalities"]
                )
                or not isinstance(descriptor["tags"], list)
                or not isinstance(descriptor["searchable_content"], str)
            ):
                raise ValueError("grant document descriptor is invalid")
        payload = {
            "operation": "materialize_grant_document_resources",
            "execution_id": command.execution_id,
            "grant_ref": command.grant_ref,
            "authorization_revision": command.authorization_revision,
            "resources": [asdict(resource) for resource in command.resources],
        }
        digest = _digest(payload)
        with self._session_factory() as session, session.begin():
            replay = session.get(AtlasTurnGrantResourceSnapshotRow, command.grant_ref)
            if replay is not None:
                if replay.digest != digest or replay.idempotency_key != command.idempotency_key:
                    raise AuthorizationStoreConflict("grant resource snapshot replay payload changed")
                return self._load_grant_resources(session, replay)
            grant = session.get(AtlasTurnAccessGrantRow, command.grant_ref)
            if (
                grant is None
                or grant.execution_id != command.execution_id
                or grant.authorization_revision != command.authorization_revision
            ):
                raise AuthorizationStoreConflict("grant resource snapshot does not match exact grant")
            created_at = _now()
            row = AtlasTurnGrantResourceSnapshotRow(
                grant_ref=command.grant_ref,
                execution_id=command.execution_id,
                authorization_revision=command.authorization_revision,
                resource_count=len(command.resources),
                digest=digest,
                idempotency_key=command.idempotency_key,
                created_at=created_at,
            )
            session.add(row)
            session.flush()
            for ordinal, resource in enumerate(command.resources, start=1):
                session.add(
                    AtlasTurnGrantDocumentResourceRow(
                        grant_ref=command.grant_ref,
                        resource_ref=resource.resource_ref,
                        ordinal=ordinal,
                        lifecycle_epoch=resource.lifecycle_epoch,
                        document_version_ref=resource.document_version_ref,
                        processing_generation_ref=resource.processing_generation_ref,
                        index_generation_ref=resource.index_generation_ref,
                        manifest_digest=resource.manifest_digest,
                        descriptor=dict(resource.descriptor),
                    )
                )
            session.flush()
            return self._load_grant_resources(session, row)

    def grant_resources(
        self,
        *,
        execution_id: str,
        grant_ref: str,
        deadline_at: datetime | None = None,
    ) -> GrantResourceSnapshotRecord:
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            row = session.get(AtlasTurnGrantResourceSnapshotRow, grant_ref)
            if row is None or row.execution_id != execution_id:
                raise AuthorizationStoreConflict("grant resource snapshot is unavailable")
            return self._load_grant_resources(session, row)

    @staticmethod
    def _load_grant_resources(
        session: Session, row: AtlasTurnGrantResourceSnapshotRow
    ) -> GrantResourceSnapshotRecord:
        resources = session.scalars(
            select(AtlasTurnGrantDocumentResourceRow)
            .where(AtlasTurnGrantDocumentResourceRow.grant_ref == row.grant_ref)
            .order_by(AtlasTurnGrantDocumentResourceRow.ordinal)
        ).all()
        if len(resources) != row.resource_count:
            raise AuthorizationStoreConflict("grant resource snapshot is incomplete")
        return GrantResourceSnapshotRecord(
            execution_id=row.execution_id,
            grant_ref=row.grant_ref,
            authorization_revision=row.authorization_revision,
            resources=tuple(
                GrantDocumentResourceInput(
                    resource_ref=resource.resource_ref,
                    lifecycle_epoch=resource.lifecycle_epoch,
                    document_version_ref=resource.document_version_ref,
                    processing_generation_ref=resource.processing_generation_ref,
                    index_generation_ref=resource.index_generation_ref,
                    manifest_digest=resource.manifest_digest,
                    descriptor=resource.descriptor,
                )
                for resource in resources
            ),
            digest=row.digest,
            created_at=row.created_at,
        )


__all__ = [
    "AuthorizationStoreConflict",
    "CreateGrantInput",
    "GrantRecord",
    "GrantDocumentResourceInput",
    "GrantResourceSnapshotRecord",
    "GrantReleaseRecord",
    "PostgresAuthorizationStore",
    "MaterializeGrantResourcesInput",
    "ReleaseGrantInput",
]
