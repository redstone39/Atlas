from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_production.infrastructure.postgres_authorization_v1_adapter import (
    PostgresAuthorizationV1Adapter,
)
from atlas_production.infrastructure.postgres_owner.authorization import (
    AuthorizationStoreConflict,
    GrantRecord,
)
from atlas_production.modules.authorization.public import (
    CreateTurnAccessGrantV1,
    CurrentGrantAuthorizationSnapshotV1,
    CurrentResourceAuthorizationSnapshotV1,
    GrantDocumentResourceSnapshotV1,
    GrantDocumentResourceV1,
    LineageResourceV1,
    ReleaseTurnAccessGrantV1,
)


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


class FakeAuthorizationStore:
    def __init__(self) -> None:
        self.in_call = False
        self.create_commands = []
        self.release_commands = []
        self.grant: GrantRecord | None = None
        self.idempotency_key: str | None = None
        self.concurrent_winner = False

    def create_grant(self, command):
        self.in_call = True
        try:
            self.create_commands.append(command)
            self.grant = GrantRecord(
                grant_ref=command.grant_ref,
                execution_id=command.execution_id,
                actor_id=command.actor_id,
                conversation_id=command.conversation_id,
                schema_version=command.schema_version,
                digest=command.authority_digest,
                authorization_revision=command.authorization_revision,
                issued_at=NOW,
                deadline_at=command.deadline_at,
            )
            self.idempotency_key = command.idempotency_key
            if self.concurrent_winner:
                self.concurrent_winner = False
                raise AuthorizationStoreConflict("concurrent creator won")
            return self.grant
        finally:
            self.in_call = False

    def get_grant(self, grant_ref: str, *, deadline_at=None):
        self.in_call = True
        try:
            if self.grant is None or self.grant.grant_ref != grant_ref:
                return None
            return self.grant
        finally:
            self.in_call = False

    def get_grant_by_idempotency(self, *, actor_id: str, idempotency_key: str):
        self.in_call = True
        try:
            if (
                self.grant is None
                or self.grant.actor_id != actor_id
                or self.idempotency_key != idempotency_key
            ):
                return None
            return self.grant
        finally:
            self.in_call = False

    def release_grant(self, command):
        self.in_call = True
        try:
            self.release_commands.append(command)
        finally:
            self.in_call = False


class FakeCurrentAuthorizationReader:
    def __init__(self, store: FakeAuthorizationStore) -> None:
        self.store = store
        self.authorized = True
        self.authorization_revision = 11
        self.snapshot_ref = "authority-snapshot-11"
        self.resources: tuple[CurrentResourceAuthorizationSnapshotV1, ...] = ()
        self.resource_calls: list[tuple[str, ...]] = []
        self.grant_calls = 0

    def current_grant_authorization(self, *, actor_id: str, conversation_id: str, deadline_at=None):
        assert not self.store.in_call
        self.grant_calls += 1
        return CurrentGrantAuthorizationSnapshotV1(
            actor_id=actor_id,
            conversation_id=conversation_id,
            authorization_revision=self.authorization_revision,
            snapshot_ref=self.snapshot_ref,
            authorized=self.authorized,
        )

    def current_resource_authorizations(self, *, actor_id: str, resource_refs: tuple[str, ...], deadline_at=None):
        assert not self.store.in_call
        self.resource_calls.append(resource_refs)
        return self.resources


def _adapter():
    store = FakeAuthorizationStore()
    reader = FakeCurrentAuthorizationReader(store)
    return PostgresAuthorizationV1Adapter(store, reader), store, reader


def _document(
    resource_ref: str,
    *,
    lifecycle_epoch: int = 3,
    version_ref: str | None = "version-3",
    processing_generation_ref: str | None = "processing-5",
    index_generation_ref: str | None = "index-7",
) -> LineageResourceV1:
    return LineageResourceV1(
        resource_ref=resource_ref,
        resource_kind="document",
        lifecycle_epoch=lifecycle_epoch,
        version_ref=version_ref,
        processing_generation_ref=processing_generation_ref,
        index_generation_ref=index_generation_ref,
    )


def _current(
    resource_ref: str,
    *,
    authorized: bool = True,
    active: bool = True,
    lifecycle_epoch: int | None = 3,
    version_ref: str | None = "version-3",
    processing_generation_ref: str | None = "processing-5",
    index_generation_ref: str | None = "index-7",
) -> CurrentResourceAuthorizationSnapshotV1:
    return CurrentResourceAuthorizationSnapshotV1(
        actor_id="actor-1",
        resource_ref=resource_ref,
        authorization_revision=12,
        snapshot_ref=f"snapshot-{resource_ref}",
        authorized=authorized,
        active=active,
        lifecycle_epoch=lifecycle_epoch,
        version_ref=version_ref,
        processing_generation_ref=processing_generation_ref,
        index_generation_ref=index_generation_ref,
    )


def test_create_and_release_refs_are_deterministic_and_reader_precedes_store() -> None:
    adapter, store, _reader = _adapter()
    command = CreateTurnAccessGrantV1(
        execution_id="execution-1",
        actor_id="actor-1",
        conversation_id="conversation-1",
        deadline_at=NOW + timedelta(hours=1),
        idempotency_key="create-key-1",
    )

    first = adapter.create_grant(command)
    second = adapter.create_grant(command)

    assert first.grant_ref == second.grant_ref
    assert first.authorization_revision == 11
    assert len(store.create_commands) == 1
    assert store.create_commands[0].grant_ref.startswith("grant-")

    release = ReleaseTurnAccessGrantV1(
        execution_id="execution-1",
        grant_ref=first.grant_ref,
        idempotency_key="release-key-1",
    )
    adapter.release_grant(release)
    adapter.release_grant(release)
    assert store.release_commands[0].release_id == store.release_commands[1].release_id
    assert store.release_commands[0].release_id.startswith("release-")


def test_grant_exact_replay_ignores_later_authority_revision_or_revoke() -> None:
    adapter, store, reader = _adapter()
    command = CreateTurnAccessGrantV1(
        execution_id="execution-1",
        actor_id="actor-1",
        conversation_id="conversation-1",
        deadline_at=NOW + timedelta(hours=1),
        idempotency_key="create-key-1",
    )
    first = adapter.create_grant(command)
    assert reader.grant_calls == 1

    reader.authorization_revision = 12
    reader.snapshot_ref = "authority-snapshot-12"
    reader.authorized = False
    second = adapter.create_grant(command)

    assert second == first
    assert reader.grant_calls == 1
    assert len(store.create_commands) == 1


def test_grant_replay_rejects_changed_public_command() -> None:
    adapter, _store, _reader = _adapter()
    original = CreateTurnAccessGrantV1(
        execution_id="execution-1", actor_id="actor-1",
        conversation_id="conversation-1", deadline_at=NOW + timedelta(hours=1),
        idempotency_key="create-key-1",
    )
    adapter.create_grant(original)
    with pytest.raises(AuthorizationStoreConflict, match="public command changed"):
        adapter.create_grant(
            original.model_copy(update={"execution_id": "execution-2"})
        )


def test_concurrent_first_creator_loser_rereads_winning_replay() -> None:
    adapter, store, _reader = _adapter()
    store.concurrent_winner = True
    command = CreateTurnAccessGrantV1(
        execution_id="execution-1", actor_id="actor-1",
        conversation_id="conversation-1", deadline_at=NOW + timedelta(hours=1),
        idempotency_key="create-key-1",
    )
    result = adapter.create_grant(command)
    assert result.grant_ref == store.grant.grant_ref


def test_create_grant_fails_closed_when_current_acl_is_revoked() -> None:
    adapter, store, reader = _adapter()
    reader.authorized = False

    with pytest.raises(PermissionError, match="not currently authorized"):
        adapter.create_grant(
            CreateTurnAccessGrantV1(
                execution_id="execution-1",
                actor_id="actor-1",
                conversation_id="conversation-1",
                deadline_at=NOW + timedelta(hours=1),
                idempotency_key="create-key-1",
            )
        )

    assert store.create_commands == []


def test_current_visibility_recomputes_revoke_and_restore_without_state() -> None:
    adapter, _store, reader = _adapter()
    resource = _document("document-1")
    reader.resources = (_current("document-1", authorized=False),)

    revoked = adapter.current_visibility(actor_id="actor-1", resources=[resource])
    reader.resources = (_current("document-1", authorized=True),)
    restored = adapter.current_visibility(actor_id="actor-1", resources=[resource])

    assert [(item.decision, item.reason) for item in revoked] == [
        ("hidden", "access_revoked")
    ]
    assert [(item.decision, item.reason) for item in restored] == [
        ("visible", "authorized")
    ]


def test_current_grant_resources_rechecks_grant_and_exact_document_acl() -> None:
    adapter, _store, reader = _adapter()
    grant = adapter.create_grant(
        CreateTurnAccessGrantV1(
            execution_id="execution-1",
            actor_id="actor-1",
            conversation_id="conversation-1",
            deadline_at=NOW + timedelta(hours=1),
            idempotency_key="create-key-current-resource",
        )
    )
    snapshot = GrantDocumentResourceSnapshotV1(
        grant_ref=grant.grant_ref,
        authorization_revision=grant.authorization_revision,
        resources=[
            GrantDocumentResourceV1(
                resource_ref="document-1",
                lifecycle_epoch=3,
                document_version_ref="version-3",
                processing_generation_ref="processing-5",
                index_generation_ref="index-7",
                manifest_digest="d" * 64,
                display_name="Current.pdf",
                media_type="application/pdf",
                modalities=["figure"],
                tags=[],
                language=None,
                created_at_label=None,
                searchable_content="",
                version_label=None,
            )
        ],
        digest="e" * 64,
        created_at=NOW,
    )
    adapter.grant_document_resources = lambda **_kwargs: snapshot
    reader.resources = (_current("document-1"),)

    assert adapter.current_grant_document_resources(
        execution_id="execution-1", grant_ref=grant.grant_ref
    ) == snapshot

    reader.resources = (_current("document-1", authorized=False),)
    with pytest.raises(PermissionError, match="no longer authorized"):
        adapter.current_grant_document_resources(
            execution_id="execution-1", grant_ref=grant.grant_ref
        )


def test_current_visibility_coalesces_repeated_document_lineage_inputs() -> None:
    adapter, _store, reader = _adapter()
    resource = _document("document-1")
    reader.resources = (_current("document-1"),)

    decisions = adapter.current_visibility(
        actor_id="actor-1", resources=[resource, resource]
    )

    assert [(item.decision, item.reason) for item in decisions] == [
        ("visible", "authorized"),
        ("visible", "authorized"),
    ]
    assert reader.resource_calls == [("document-1",)]


def test_current_visibility_is_ordered_and_rechecks_binding_acl_not_current_revision() -> None:
    adapter, _store, reader = _adapter()
    resources = [
        _document("missing"),
        _document("lifecycle"),
        _document("version"),
        _document("processing"),
        _document("generation"),
        _document("exact"),
        LineageResourceV1(
            resource_ref="turn-1", resource_kind="turn", lifecycle_epoch=1
        ),
    ]
    reader.resources = (
        _current("exact"),
        _current("generation", index_generation_ref="index-8"),
        _current("processing", processing_generation_ref="processing-6"),
        _current("version", version_ref="version-4"),
        _current("lifecycle", lifecycle_epoch=4),
    )

    decisions = adapter.current_visibility(actor_id="actor-1", resources=resources)

    assert [item.resource_ref for item in decisions] == [
        "missing", "lifecycle", "version", "processing", "generation", "exact", "turn-1"
    ]
    assert [item.reason for item in decisions] == [
        "resource_inactive",
        "resource_inactive",
        "authorized",
        "authorized",
        "authorized",
        "authorized",
        "dependency_hidden",
    ]
    assert reader.resource_calls == [
        ("missing", "lifecycle", "version", "processing", "generation", "exact")
    ]


def test_current_visibility_allows_retained_lineage_after_current_revision_changes() -> None:
    adapter, _store, reader = _adapter()
    reader.resources = (_current("missing-version"), _current("missing-generation"))

    decisions = adapter.current_visibility(
        actor_id="actor-1",
        resources=[
            _document("missing-version", version_ref=None),
            _document("missing-generation", index_generation_ref=None),
        ],
    )

    assert [item.reason for item in decisions] == ["authorized", "authorized"]
