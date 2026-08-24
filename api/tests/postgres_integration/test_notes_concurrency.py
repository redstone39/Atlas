from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import pytest

from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.notes import (
    AtlasNoteRevisionRow,
    AtlasNoteRow,
    AtlasNoteSavepointRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_owner.notes import PostgresNotesOwner
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.notes.public import (
    AcceptNoteRevisionRequestV1,
    CommitNoteBodyRestoreRequestV1,
    NoteChangeSetV1,
    NoteCreateRequestV1,
    NoteMetadataUpdateRequestV1,
    NoteTrashRequestV1,
    NotesError,
)
from atlas_production.modules.notes.document_schema import NOTE_DOCUMENT_SCHEMA_V2


def _body(block_id: str, text: str) -> dict[str, object]:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"block_id": block_id},
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _seed(runtime: PostgresRuntime) -> None:
    runtime.bootstrap_schema()
    with runtime.session_factory() as session:
        session.merge(AtlasUserRow(
            actor_id="user-notes-race",
            display_name="Notes Race",
            email="notes-race@example.test",
            system_role="member",
            password_digest=None,
            active=True,
            actor_type="user",
            created_at="2026-08-12T00:00:00+00:00",
        ))
        session.merge(AtlasProjectRow(
            project_id="project-notes-race",
            name="Notes Race Project",
            policy_profile_id="default",
            status="active",
        ))
        session.merge(AtlasPermissionGrantRow(
            grant_id="grant-notes-race",
            project_id="project-notes-race",
            subject_type="user",
            subject_id="user-notes-race",
            role="viewer",
            effect="allow",
            status="active",
            created_at="2026-08-12T00:00:00+00:00",
            revoked_at=None,
        ))
        session.commit()


def test_update_vs_trash_same_metadata_revision_has_one_winner(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    created = owner.create_note(
        actor_id="user-notes-race",
        command=NoteCreateRequestV1(scope_type="project",
        scope_id="project-notes-race",
        title="Before race",
        idempotency_key="create-update-trash-race",),
    )

    def update() -> object:
        return owner.update_note_metadata(
            actor_id="user-notes-race",
            note_id=created.note_id,
            command=NoteMetadataUpdateRequestV1(
                title="Update won",
                expected_metadata_revision=1,
                idempotency_key="update-race",
            ),
        )

    def trash() -> object:
        return owner.trash_note(
            actor_id="user-notes-race",
            note_id=created.note_id,
            command=NoteTrashRequestV1(
                expected_metadata_revision=1,
                idempotency_key="trash-race",
            ),
        )

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for future in (executor.submit(update), executor.submit(trash)):
            try:
                outcomes.append(future.result())
            except NotesError as error:
                outcomes.append(error)

    assert sum(not isinstance(item, NotesError) for item in outcomes) == 1
    failures = [item for item in outcomes if isinstance(item, NotesError)]
    assert [item.code for item in failures] == ["stale_metadata_revision"]
    current = owner.get_note(
        actor_id="user-notes-race",
        note_id=created.note_id,
    )
    assert current.metadata_revision == 2
    assert (current.title, current.lifecycle_status) in {
        ("Update won", "active"),
        ("Before race", "trashed"),
    }


def _content_command(note_id: str, key: str) -> AcceptNoteRevisionRequestV1:
    return AcceptNoteRevisionRequestV1(
        note_id=note_id,
        expected_revision_head=1,
        expected_collaboration_epoch=1,
        event_kind="content_update",
        raw_yjs_update=key.encode(),
        canonical_body=_body(key, key),
        document_schema=NOTE_DOCUMENT_SCHEMA_V2,
        change_set=NoteChangeSetV1(),
        idempotency_key=key,
    )


def test_two_content_updates_on_same_head_have_one_winner(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-race",
        command=NoteCreateRequestV1(scope_type="project",
        scope_id="project-notes-race",
        title="Content race",
        idempotency_key="create-content-race",),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                owner.accept_revision,
                actor_id="user-notes-race",
                command=_content_command(note.note_id, key),
            )
            for key in ("content-race-a", "content-race-b")
        ]
    outcomes: list[object] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except NotesError as error:
            outcomes.append(error)
    assert sum(not isinstance(item, NotesError) for item in outcomes) == 1
    assert [item.code for item in outcomes if isinstance(item, NotesError)] == [
        "stale_revision_head"
    ]
    with postgres_runtime.session_factory() as session:
        assert session.get(AtlasNoteRow, note.note_id).accepted_update_head == 2
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=note.note_id
        ).count() == 2


def test_content_update_and_trash_are_linearized_without_post_trash_append(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-race",
        command=NoteCreateRequestV1(scope_type="project",
        scope_id="project-notes-race",
        title="Content trash race",
        idempotency_key="create-content-trash-race",),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        update_future = executor.submit(
            owner.accept_revision,
            actor_id="user-notes-race",
            command=_content_command(note.note_id, "content-before-trash"),
        )
        trash_future = executor.submit(
            owner.trash_note,
            actor_id="user-notes-race",
            note_id=note.note_id,
            command=NoteTrashRequestV1(
                expected_metadata_revision=1,
                idempotency_key="trash-content-race",
            ),
        )
    outcomes = []
    for future in (update_future, trash_future):
        try:
            outcomes.append(future.result())
        except NotesError as error:
            outcomes.append(error)
    assert not isinstance(outcomes[1], NotesError)
    assert (
        not isinstance(outcomes[0], NotesError)
        or outcomes[0].code == "note_trashed"
    )
    current = owner.get_note(
        actor_id="user-notes-race", note_id=note.note_id
    )
    assert current.lifecycle_status == "trashed"
    assert current.collaboration_epoch == 2
    with pytest.raises(NotesError) as rejected:
        owner.accept_revision(
            actor_id="user-notes-race",
            command=AcceptNoteRevisionRequestV1(
                **{
                    **_content_command(note.note_id, "after-trash").model_dump(),
                    "expected_revision_head": current.accepted_update_head,
                    "expected_collaboration_epoch": 2,
                }
            ),
        )
    assert rejected.value.code == "note_trashed"


def test_body_restore_and_content_update_on_same_head_have_one_winner(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-race",
        command=NoteCreateRequestV1(scope_type="project",
        scope_id="project-notes-race",
        title="Restore race",
        idempotency_key="create-restore-content-race",),
    )
    source = owner.list_savepoints(
        actor_id="user-notes-race", note_id=note.note_id
    )[0]
    restore = CommitNoteBodyRestoreRequestV1(
        note_id=note.note_id,
        command_id="restore-race",
        room_name="room",
        restore_source_savepoint_id=source.savepoint_id,
        expected_revision_head=1,
        expected_collaboration_epoch=1,
        request_fingerprint="1" * 64,
        raw_yjs_update=b"restore",
        encoded_yjs_state=b"state",
        canonical_body={"type": "doc", "content": []},
        document_schema=NOTE_DOCUMENT_SCHEMA_V2,
        change_set=NoteChangeSetV1(),
        idempotency_key="restore-content-race",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(
            owner.commit_body_restore,
            actor_id="user-notes-race",
            command=restore,
        )
        update_future = executor.submit(
            owner.accept_revision,
            actor_id="user-notes-race",
            command=_content_command(note.note_id, "update-restore-race"),
        )
    outcomes = []
    for future in (restore_future, update_future):
        try:
            outcomes.append(future.result())
        except NotesError as error:
            outcomes.append(error)
    assert sum(not isinstance(item, NotesError) for item in outcomes) == 1
    assert [item.code for item in outcomes if isinstance(item, NotesError)] == [
        "stale_revision_head"
    ]
    with postgres_runtime.session_factory() as session:
        current = session.get(AtlasNoteRow, note.note_id)
        assert current.accepted_update_head == 2
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=note.note_id
        ).count() == 2
        expected_savepoints = 2 if not isinstance(outcomes[0], NotesError) else 1
        assert current.savepoint_head == expected_savepoints
        assert session.query(AtlasNoteSavepointRow).filter_by(
            note_id=note.note_id
        ).count() == expected_savepoints
