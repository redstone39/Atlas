"""Public Authorization V1 adapter over the owner-local store."""

from __future__ import annotations

import hashlib
import json

from atlas_production.infrastructure.postgres_owner.authorization import (
    CreateGrantInput,
    GrantDocumentResourceInput,
    GrantRecord,
    GrantResourceSnapshotRecord,
    MaterializeGrantResourcesInput,
    PostgresAuthorizationStore,
    ReleaseGrantInput,
    AuthorizationStoreConflict,
)
from atlas_production.modules.authorization.public import (
    CreateTurnAccessGrantV1,
    CurrentResourceAuthorizationReader,
    CurrentResourceAuthorizationSnapshotV1,
    GrantDocumentResourceSnapshotV1,
    GrantDocumentResourceV1,
    LineageResourceV1,
    MaterializeGrantDocumentResourcesV1,
    ReleaseTurnAccessGrantV1,
    TurnAccessGrantRefV1,
    VisibilityDecisionV1,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _opaque(kind: str, value: object) -> str:
    return f"{kind}-{_digest(value)}"


def _grant_ref(record: GrantRecord) -> TurnAccessGrantRefV1:
    return TurnAccessGrantRefV1(
        grant_ref=record.grant_ref,
        schema_version=record.schema_version,
        digest=record.digest,
        actor_id=record.actor_id,
        authorization_revision=record.authorization_revision,
        issued_at=record.issued_at,
        deadline_at=record.deadline_at,
    )


def _snapshot(record: GrantResourceSnapshotRecord) -> GrantDocumentResourceSnapshotV1:
    return GrantDocumentResourceSnapshotV1(
        grant_ref=record.grant_ref,
        authorization_revision=record.authorization_revision,
        resources=[
            GrantDocumentResourceV1(
                resource_ref=item.resource_ref,
                lifecycle_epoch=item.lifecycle_epoch,
                document_version_ref=item.document_version_ref,
                processing_generation_ref=item.processing_generation_ref,
                index_generation_ref=item.index_generation_ref,
                manifest_digest=item.manifest_digest,
                **dict(item.descriptor),
            )
            for item in record.resources
        ],
        digest=record.digest,
        created_at=record.created_at,
    )


class _GrantDocumentResourceAdapter:
    def __init__(self, store: PostgresAuthorizationStore) -> None:
        self._store = store

    def materialize_grant_document_resources(
        self, command: MaterializeGrantDocumentResourcesV1
    ) -> GrantDocumentResourceSnapshotV1:
        record = self._store.materialize_grant_resources(
            MaterializeGrantResourcesInput(
                execution_id=command.execution_id,
                grant_ref=command.grant_ref,
                authorization_revision=command.authorization_revision,
                resources=tuple(
                    GrantDocumentResourceInput(
                        resource_ref=item.resource_ref,
                        lifecycle_epoch=item.lifecycle_epoch,
                        document_version_ref=item.document_version_ref,
                        processing_generation_ref=item.processing_generation_ref,
                        index_generation_ref=item.index_generation_ref,
                        manifest_digest=item.manifest_digest,
                        descriptor={
                            "display_name": item.display_name,
                            "media_type": item.media_type,
                            "modalities": list(item.modalities),
                            "tags": list(item.tags),
                            "language": item.language,
                            "created_at_label": item.created_at_label,
                            "searchable_content": item.searchable_content,
                            "version_label": item.version_label,
                        },
                    )
                    for item in command.resources
                ),
                idempotency_key=command.idempotency_key,
            )
        )
        return _snapshot(record)

    def grant_document_resources(
        self, *, execution_id: str, grant_ref: str
    ) -> GrantDocumentResourceSnapshotV1:
        return _snapshot(
            self._store.grant_resources(execution_id=execution_id, grant_ref=grant_ref)
        )

    def current_grant_document_resources(
        self, *, execution_id: str, grant_ref: str
    ) -> GrantDocumentResourceSnapshotV1:
        raise PermissionError("current grant authorization is unavailable")


class PostgresAuthorizationV1Adapter(_GrantDocumentResourceAdapter):
    """Complete public owner adapter with external currentness reads."""

    def __init__(
        self,
        store: PostgresAuthorizationStore,
        current_authorization: CurrentResourceAuthorizationReader,
    ) -> None:
        super().__init__(store)
        self._current_authorization = current_authorization

    def create_grant(self, command: CreateTurnAccessGrantV1) -> TurnAccessGrantRefV1:
        replay = self._store.get_grant_by_idempotency(
            actor_id=command.actor_id, idempotency_key=command.idempotency_key
        )
        if replay is not None:
            self._validate_grant_replay(command, replay)
            return _grant_ref(replay)
        # The external owner read is complete before the Authorization store opens
        # its owner-local transaction.
        current = self._current_authorization.current_grant_authorization(
            actor_id=command.actor_id,
            conversation_id=command.conversation_id,
        )
        if (
            current.actor_id != command.actor_id
            or current.conversation_id != command.conversation_id
            or not current.authorized
        ):
            raise PermissionError("turn grant is not currently authorized")
        authority_digest = _digest(
            {
                "actor_id": current.actor_id,
                "conversation_id": current.conversation_id,
                "authorization_revision": current.authorization_revision,
                "snapshot_ref": current.snapshot_ref,
                "authorized": current.authorized,
            }
        )
        grant_ref = _opaque(
            "grant",
            {
                "execution_id": command.execution_id,
                "actor_id": command.actor_id,
                "conversation_id": command.conversation_id,
                "authorization_revision": current.authorization_revision,
                "authority_digest": authority_digest,
                "deadline_at": command.deadline_at.isoformat(),
                "idempotency_key": command.idempotency_key,
            },
        )
        try:
            created = self._store.create_grant(
                CreateGrantInput(
                    grant_ref=grant_ref,
                    execution_id=command.execution_id,
                    actor_id=command.actor_id,
                    conversation_id=command.conversation_id,
                    authorization_revision=current.authorization_revision,
                    authority_digest=authority_digest,
                    deadline_at=command.deadline_at,
                    idempotency_key=command.idempotency_key,
                )
            )
        except AuthorizationStoreConflict:
            # A concurrent first creator may have won after our owner-local
            # replay read. Re-read the immutable replay; never consult current
            # authority again for an already accepted idempotency identity.
            replay = self._store.get_grant_by_idempotency(
                actor_id=command.actor_id, idempotency_key=command.idempotency_key
            )
            if replay is None:
                raise
            self._validate_grant_replay(command, replay)
            return _grant_ref(replay)
        return _grant_ref(created)

    def current_grant_document_resources(
        self, *, execution_id: str, grant_ref: str
    ) -> GrantDocumentResourceSnapshotV1:
        snapshot = self.grant_document_resources(
            execution_id=execution_id, grant_ref=grant_ref
        )
        grant = self._store.get_grant(grant_ref)
        if grant is None or grant.execution_id != execution_id:
            raise PermissionError("turn grant is unavailable")
        current_grant = self._current_authorization.current_grant_authorization(
            actor_id=grant.actor_id,
            conversation_id=grant.conversation_id,
        )
        if (
            current_grant.actor_id != grant.actor_id
            or current_grant.conversation_id != grant.conversation_id
            or not current_grant.authorized
        ):
            raise PermissionError("turn grant is no longer authorized")
        decisions = self.current_visibility(
            actor_id=grant.actor_id,
            resources=[
                LineageResourceV1(
                    resource_ref=item.resource_ref,
                    resource_kind="document",
                    lifecycle_epoch=item.lifecycle_epoch,
                    version_ref=item.document_version_ref,
                    processing_generation_ref=item.processing_generation_ref,
                    index_generation_ref=item.index_generation_ref,
                )
                for item in snapshot.resources
            ],
        )
        if len(decisions) != len(snapshot.resources) or any(
            item.decision != "visible" for item in decisions
        ):
            raise PermissionError("grant resources are no longer authorized")
        return snapshot

    @staticmethod
    def _validate_grant_replay(
        command: CreateTurnAccessGrantV1, replay: GrantRecord
    ) -> None:
        if (
            replay.execution_id != command.execution_id
            or replay.actor_id != command.actor_id
            or replay.conversation_id != command.conversation_id
            or replay.deadline_at != command.deadline_at
        ):
            raise AuthorizationStoreConflict("grant replay public command changed")

    def release_grant(self, command: ReleaseTurnAccessGrantV1) -> None:
        # This read closes before the release transaction starts.  Its immutable
        # grant digest/revision bind the release identity to the authority snapshot
        # that produced the grant.
        grant = self._store.get_grant(command.grant_ref)
        if grant is None or grant.execution_id != command.execution_id:
            raise PermissionError("turn grant does not belong to execution")
        release_id = _opaque(
            "release",
            {
                "execution_id": command.execution_id,
                "grant_ref": command.grant_ref,
                "grant_digest": grant.digest,
                "authorization_revision": grant.authorization_revision,
                "idempotency_key": command.idempotency_key,
            },
        )
        self._store.release_grant(
            ReleaseGrantInput(
                release_id=release_id,
                execution_id=command.execution_id,
                grant_ref=command.grant_ref,
                idempotency_key=command.idempotency_key,
            )
        )

    def release_execution_grant(
        self, *, execution_id: str, idempotency_key: str
    ) -> None:
        grant = self._store.get_grant_for_execution(execution_id)
        if grant is None:
            return
        self.release_grant(
            ReleaseTurnAccessGrantV1(
                execution_id=execution_id,
                grant_ref=grant.grant_ref,
                idempotency_key=idempotency_key,
            )
        )

    def current_visibility(
        self, *, actor_id: str, resources: list[LineageResourceV1]
    ) -> list[VisibilityDecisionV1]:
        # Several evidence items may legitimately resolve to the same document.
        # Ask the current-authority owner once per document while preserving the
        # caller's ordered per-lineage decisions below.  Duplicate records
        # returned for one unique request remain ambiguous and fail closed.
        document_refs = tuple(
            dict.fromkeys(
                resource.resource_ref
                for resource in resources
                if resource.resource_kind == "document"
            )
        )
        current_records = self._current_authorization.current_resource_authorizations(
            actor_id=actor_id,
            resource_refs=document_refs,
        )
        by_ref: dict[str, CurrentResourceAuthorizationSnapshotV1] = {}
        duplicate_refs: set[str] = set()
        for current in current_records:
            if current.resource_ref in by_ref:
                duplicate_refs.add(current.resource_ref)
            else:
                by_ref[current.resource_ref] = current

        return [
            self._visibility_decision(
                actor_id=actor_id,
                resource=resource,
                current=None
                if resource.resource_ref in duplicate_refs
                else by_ref.get(resource.resource_ref),
            )
            for resource in resources
        ]

    @staticmethod
    def _visibility_decision(
        *,
        actor_id: str,
        resource: LineageResourceV1,
        current: CurrentResourceAuthorizationSnapshotV1 | None,
    ) -> VisibilityDecisionV1:
        if resource.resource_kind != "document":
            return VisibilityDecisionV1(
                resource_ref=resource.resource_ref,
                decision="hidden",
                reason="dependency_hidden",
            )
        if current is None or current.actor_id != actor_id:
            reason = "resource_inactive"
        elif not current.authorized:
            reason = "access_revoked"
        elif not current.active or current.lifecycle_epoch != resource.lifecycle_epoch:
            reason = "resource_inactive"
        else:
            # Canonical-processing revisions are immutable retained resources.
            # Visibility rechecks the current Document binding ACL/lifecycle;
            # it must not follow identity.current_revision or hide an older
            # citation merely because a newer revision was published.
            return VisibilityDecisionV1(
                resource_ref=resource.resource_ref,
                decision="visible",
                reason="authorized",
            )
        return VisibilityDecisionV1(
            resource_ref=resource.resource_ref,
            decision="hidden",
            reason=reason,
        )


class PostgresGrantDocumentResourceAdapter(_GrantDocumentResourceAdapter):
    """Existing narrow grant-resource adapter retained for current consumers."""


__all__ = ["PostgresAuthorizationV1Adapter", "PostgresGrantDocumentResourceAdapter"]
