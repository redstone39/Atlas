from __future__ import annotations

import hashlib
import json
import pytest

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.notes import (
    AtlasNoteAttachmentRow,
    AtlasNoteCategoryRow,
    AtlasNoteRevisionRow,
    AtlasNoteRow,
    AtlasNoteSavepointRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_owner.notes import PostgresNotesOwner
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.notes.public import (
    AcceptNoteRevisionRequestV1,
    CreateNoteSavepointRequestV1,
    CommitNoteBodyRestoreRequestV1,
    NoteChangeSetV1,
    NoteCategoryCreateRequestV1,
    NoteCreateRequestV1,
    NoteMetadataUpdateRequestV1,
    NoteBodyRestoreRequestV1,
    NoteTrashRequestV1,
    NotesError,
    NoteSummaryV1,
)
from atlas_production.modules.notes.document_schema import NOTE_DOCUMENT_SCHEMA_V2


def _body(block_id: str, text: str = "Notes") -> dict[str, object]:
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


def _image_body(block_id: str, attachment_ref: str) -> dict[str, object]:
    return {
        "type": "doc",
        "content": [
            {
                "type": "noteImage",
                "attrs": {
                    "block_id": block_id,
                    "attachment_ref": attachment_ref,
                    "alt": "",
                    "caption": "",
                    "width": 5,
                    "height": 4,
                },
            }
        ],
    }


def _seed_project_member(runtime: PostgresRuntime) -> None:
    runtime.bootstrap_schema()
    with runtime.session_factory() as session:
        session.merge(AtlasUserRow(
            actor_id="user-notes-owner",
            display_name="Notes Owner",
            email="notes-owner@example.test",
            system_role="member",
            password_digest=None,
            active=True,
            actor_type="user",
            created_at="2026-08-12T00:00:00+00:00",
        ))
        session.merge(AtlasProjectRow(
            project_id="project-notes-owner",
            name="Notes Project",
            policy_profile_id="default",
        ))
        session.merge(AtlasPermissionGrantRow(
            grant_id="grant-notes-owner",
            project_id="project-notes-owner",
            subject_type="user",
            subject_id="user-notes-owner",
            role="viewer",
            effect="allow",
            status="active",
            created_at="2026-08-12T00:00:00+00:00",
            revoked_at=None,
        ))
        session.commit()


def _seed_team_member(runtime: PostgresRuntime) -> None:
    runtime.bootstrap_schema()
    with runtime.session_factory() as session:
        session.merge(AtlasUserRow(
            actor_id="user-notes-team-list",
            display_name="Notes Team List Member",
            email="notes-team-list@example.test",
            system_role="member",
            password_digest=None,
            active=True,
            actor_type="user",
            created_at="2026-08-13T00:00:00+00:00",
        ))
        session.merge(AtlasTeamRow(
            team_id="team-notes-list",
            name="Notes List Team",
            parent_team_id=None,
            status="active",
            created_at="2026-08-13T00:00:00+00:00",
            inherit_parent_documents=True,
        ))
        session.merge(AtlasTeamMembershipRow(
            membership_id="membership-notes-team-list",
            team_id="team-notes-list",
            member_actor_type="user",
            member_actor_id="user-notes-team-list",
            role="member",
            status="active",
            created_at="2026-08-13T00:00:00+00:00",
            removed_at=None,
        ))
        session.commit()


def _seed_inherited_team_member(runtime: PostgresRuntime) -> None:
    runtime.bootstrap_schema()
    with runtime.session_factory() as session:
        session.merge(AtlasUserRow(
            actor_id="user-notes-inherited-team",
            display_name="Inherited Team Notes Member",
            email="notes-inherited-team@example.test",
            system_role="member",
            password_digest=None,
            active=True,
            actor_type="user",
            created_at="2026-08-14T00:00:00+00:00",
        ))
        session.merge(AtlasTeamRow(
            team_id="team-notes-parent",
            name="Notes Parent Team",
            parent_team_id=None,
            status="active",
            created_at="2026-08-14T00:00:00+00:00",
            inherit_parent_documents=True,
        ))
        session.merge(AtlasTeamRow(
            team_id="team-notes-child",
            name="Notes Child Team",
            parent_team_id="team-notes-parent",
            status="active",
            created_at="2026-08-14T00:00:00+00:00",
            inherit_parent_documents=True,
        ))
        session.merge(AtlasTeamMembershipRow(
            membership_id="membership-notes-inherited-team",
            team_id="team-notes-child",
            member_actor_type="user",
            member_actor_id="user-notes-inherited-team",
            role="member",
            status="active",
            created_at="2026-08-14T00:00:00+00:00",
            removed_at=None,
        ))
        session.commit()


def test_notes_owner_lists_nonempty_project_and_team_scopes_as_closed_summaries(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    _seed_team_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    category = owner.create_category(
        actor_id="user-notes-owner",
        command=NoteCategoryCreateRequestV1(
            category_id="category-notes-list",
            scope_type="project",
            scope_id="project-notes-owner",
            name="List category",
            idempotency_key="create-category-notes-list",
        ),
    )
    project_notes = [
        owner.create_note(
            actor_id="user-notes-owner",
            command=NoteCreateRequestV1(
                note_id=f"note-project-list-{suffix}",
                scope_type="project",
                scope_id="project-notes-owner",
                category_id=category.category_id if suffix == "categorized" else None,
                title=f"Project list {suffix}",
                idempotency_key=f"create-project-list-{suffix}",
            ),
        )
        for suffix in ("categorized", "older", "trashed")
    ]
    team_note = owner.create_note(
        actor_id="user-notes-team-list",
        command=NoteCreateRequestV1(
            note_id="note-team-list",
            scope_type="team",
            scope_id="team-notes-list",
            title="Team list note",
            idempotency_key="create-team-list-note",
        ),
    )
    owner.update_note_metadata(
        actor_id="user-notes-owner",
        note_id=project_notes[0].note_id,
        command=NoteMetadataUpdateRequestV1(
            title="Project list categorized updated",
            category_id=category.category_id,
            expected_metadata_revision=1,
            idempotency_key="update-project-list-categorized",
        ),
    )
    trashed = owner.trash_note(
        actor_id="user-notes-owner",
        note_id=project_notes[2].note_id,
        command=NoteTrashRequestV1(
            expected_metadata_revision=1,
            idempotency_key="trash-project-list-note",
        ),
    )

    active_project = owner.list_notes(
        actor_id="user-notes-owner",
        scope_type="project",
        scope_id="project-notes-owner",
        lifecycle_status="active",
    )
    categorized_project = owner.list_notes(
        actor_id="user-notes-owner",
        scope_type="project",
        scope_id="project-notes-owner",
        lifecycle_status="active",
        category_id=category.category_id,
    )
    trashed_project = owner.list_notes(
        actor_id="user-notes-owner",
        scope_type="project",
        scope_id="project-notes-owner",
        lifecycle_status="trashed",
    )
    active_team = owner.list_notes(
        actor_id="user-notes-team-list",
        scope_type="team",
        scope_id="team-notes-list",
        lifecycle_status="active",
    )

    expected_keys = set(NoteSummaryV1.model_fields)
    assert [note.note_id for note in active_project] == [
        project_notes[0].note_id,
        project_notes[1].note_id,
    ]
    assert [note.note_id for note in categorized_project] == [project_notes[0].note_id]
    assert [note.note_id for note in trashed_project] == [trashed.note_id]
    assert [note.note_id for note in active_team] == [team_note.note_id]
    for note in (*active_project, *categorized_project, *trashed_project, *active_team):
        assert type(note) is NoteSummaryV1
        assert set(note.model_dump()) == expected_keys
        assert not {"created_actor_id", "created_at", "trashed_actor_id", "trashed_at"} & set(
            note.model_dump()
        )


def test_team_notes_inheritance_flag_updates_scope_list_and_exact_reads(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_inherited_team_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    parent_note = owner.create_note(
        actor_id="user-notes-inherited-team",
        command=NoteCreateRequestV1(
            note_id="note-inherited-parent",
            scope_type="team",
            scope_id="team-notes-parent",
            title="Inherited parent note",
            idempotency_key="create-inherited-parent-note",
        ),
    )

    flag_on_scopes = {
        (scope.scope_type, scope.scope_id)
        for scope in owner.list_scopes(actor_id="user-notes-inherited-team")
    }
    assert flag_on_scopes == {
        ("team", "team-notes-child"),
        ("team", "team-notes-parent"),
    }
    assert [
        note.note_id
        for note in owner.list_notes(
            actor_id="user-notes-inherited-team",
            scope_type="team",
            scope_id="team-notes-parent",
            lifecycle_status="active",
        )
    ] == [parent_note.note_id]

    with postgres_runtime.session_factory() as session:
        child = session.get(AtlasTeamRow, "team-notes-child")
        assert child is not None
        child.inherit_parent_documents = False
        session.commit()

    flag_off_scopes = {
        (scope.scope_type, scope.scope_id)
        for scope in owner.list_scopes(actor_id="user-notes-inherited-team")
    }
    assert flag_off_scopes == {("team", "team-notes-child")}
    with pytest.raises(NotesError) as denied:
        owner.list_notes(
            actor_id="user-notes-inherited-team",
            scope_type="team",
            scope_id="team-notes-parent",
            lifecycle_status="active",
        )
    assert denied.value.code == "access_denied"
    assert owner.list_notes(
        actor_id="user-notes-inherited-team",
        scope_type="team",
        scope_id="team-notes-child",
        lifecycle_status="active",
    ) == ()

def test_notes_owner_persists_replay_and_fences_trashed_updates(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    create = NoteCreateRequestV1(
        note_id="note-owner-behavior",
        scope_type="project",
        scope_id="project-notes-owner",
        title="Owner behavior",
        idempotency_key="create-owner-behavior",
    )

    created = owner.create_note(actor_id="user-notes-owner", command=create)
    replay = owner.create_note(actor_id="user-notes-owner", command=create)
    assert replay == created
    assert created.accepted_update_head == 1
    assert created.savepoint_head == 1

    accepted = owner.accept_revision(
        actor_id="user-notes-owner",
        command=AcceptNoteRevisionRequestV1(
            note_id=created.note_id,
            expected_revision_head=1,
            expected_collaboration_epoch=1,
            event_kind="content_update",
            raw_yjs_update=b"update-1",
            canonical_body=_body("owner-behavior"),
            document_schema=NOTE_DOCUMENT_SCHEMA_V2,
            change_set=NoteChangeSetV1(),
            idempotency_key="revision-owner-behavior",
        ),
    )
    assert accepted.sequence == 2
    trashed = owner.trash_note(
        actor_id="user-notes-owner",
        note_id=created.note_id,
        command=NoteTrashRequestV1(
            expected_metadata_revision=1,
            idempotency_key="trash-owner-behavior",
        ),
    )
    assert trashed.lifecycle_status == "trashed"
    assert trashed.collaboration_epoch == 2

    with pytest.raises(NotesError) as rejected:
        owner.accept_revision(
            actor_id="user-notes-owner",
            command=AcceptNoteRevisionRequestV1(
                note_id=created.note_id,
                expected_revision_head=2,
                expected_collaboration_epoch=1,
                event_kind="content_update",
                raw_yjs_update=b"stale-update",
                canonical_body=_body("stale-owner-behavior"),
                document_schema=NOTE_DOCUMENT_SCHEMA_V2,
                change_set=NoteChangeSetV1(),
                idempotency_key="stale-owner-behavior",
            ),
        )
    assert rejected.value.code == "note_trashed"

    with postgres_runtime.session_factory() as session:
        assert session.get(AtlasNoteRow, created.note_id).accepted_update_head == 2
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=created.note_id
        ).count() == 2


def test_notes_owner_denies_removed_project_member_before_protected_read(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    created = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-owner-revoked",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Protected title",
            idempotency_key="create-owner-revoked",
        ),
    )
    with postgres_runtime.session_factory() as session:
        session.get(AtlasPermissionGrantRow, "grant-notes-owner").status = "revoked"
        session.commit()

    with pytest.raises(NotesError) as denied:
        owner.get_note(actor_id="user-notes-owner", note_id=created.note_id)
    assert denied.value.code == "access_denied"


def test_notes_owner_rolls_back_when_required_audit_fails(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.notes.add_event_rows",
        fail_audit,
    )
    with pytest.raises(NotesError) as rejected:
        owner.create_note(
            actor_id="user-notes-owner",
            command=NoteCreateRequestV1(
                note_id="note-owner-audit-rollback",
                scope_type="project",
                scope_id="project-notes-owner",
                title="Must roll back",
                idempotency_key="create-owner-audit-rollback",
            ),
        )
    assert rejected.value.code == "audit_failure"

    with postgres_runtime.session_factory() as session:
        assert session.get(AtlasNoteRow, "note-owner-audit-rollback") is None
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id="note-owner-audit-rollback"
        ).count() == 0
        assert session.query(AtlasNoteSavepointRow).filter_by(
            note_id="note-owner-audit-rollback"
        ).count() == 0


def test_notes_owner_binds_idempotency_to_target_and_fences_trashed_metadata(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    notes = [
        owner.create_note(
            actor_id="user-notes-owner",
            command=NoteCreateRequestV1(
                note_id=f"note-target-{suffix}",
                scope_type="project",
                scope_id="project-notes-owner",
                title="Original",
                idempotency_key=f"create-target-{suffix}",
            ),
        )
        for suffix in ("a", "b")
    ]
    for note in notes:
        updated = owner.update_note_metadata(
            actor_id="user-notes-owner",
            note_id=note.note_id,
            command=NoteMetadataUpdateRequestV1(
                title="Updated",
                expected_metadata_revision=1,
                idempotency_key="same-key-different-target",
            ),
        )
        assert updated.title == "Updated"
    trashed = owner.trash_note(
        actor_id="user-notes-owner",
        note_id=notes[0].note_id,
        command=NoteTrashRequestV1(
            expected_metadata_revision=2,
            idempotency_key="trash-target-a",
        ),
    )
    with pytest.raises(NotesError) as rejected:
        owner.update_note_metadata(
            actor_id="user-notes-owner",
            note_id=trashed.note_id,
            command=NoteMetadataUpdateRequestV1(
                title="Forbidden",
                expected_metadata_revision=3,
                idempotency_key="metadata-while-trashed",
            ),
        )
    assert rejected.value.code == "note_trashed"


def test_notes_owner_commits_body_restore_revision_and_savepoint_atomically(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-atomic-restore",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Metadata remains",
            idempotency_key="create-atomic-restore",
        ),
    )
    source = owner.list_savepoints(
        actor_id="user-notes-owner", note_id=note.note_id
    )[0]
    public_command = NoteBodyRestoreRequestV1(
        savepoint_id=source.savepoint_id,
        expected_revision_head=1,
        expected_collaboration_epoch=1,
        idempotency_key="atomic-restore",
    )
    request_fingerprint = hashlib.sha256(
        json.dumps(
            public_command.model_dump(mode="json", exclude={"idempotency_key"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    command = CommitNoteBodyRestoreRequestV1(
        note_id=note.note_id,
        command_id="restore-command",
        restore_source_savepoint_id=source.savepoint_id,
        room_name="room",
        expected_revision_head=1,
        expected_collaboration_epoch=1,
        request_fingerprint=request_fingerprint,
        raw_yjs_update=b"\xffrestore-update",
        encoded_yjs_state=b"\xfestate",
        canonical_body=_body("atomic-restore"),
        document_schema=NOTE_DOCUMENT_SCHEMA_V2,
        change_set=NoteChangeSetV1(),
        idempotency_key="atomic-restore",
    )
    with postgres_runtime.session_factory() as session:
        audit_count = session.query(AtlasAuditEventRow).count()
    with pytest.raises(NotesError) as mismatch:
        owner.commit_body_restore(
            actor_id="user-notes-owner", command=command
        )
    assert mismatch.value.code == "restore_source_mismatch"
    with postgres_runtime.session_factory() as session:
        unchanged = session.get(AtlasNoteRow, note.note_id)
        assert unchanged is not None
        assert unchanged.accepted_update_head == 1
        assert unchanged.savepoint_head == 1
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=note.note_id
        ).count() == 1
        assert session.query(AtlasNoteSavepointRow).filter_by(
            note_id=note.note_id
        ).count() == 1
        assert session.query(AtlasAuditEventRow).count() == audit_count
    command = command.model_copy(
        update={"canonical_body": {"type": "doc", "content": []}}
    )
    result = owner.commit_body_restore(
        actor_id="user-notes-owner", command=command
    )
    replay = owner.commit_body_restore(
        actor_id="user-notes-owner", command=command
    )
    assert replay == result
    assert result.revision.sequence == 2
    assert result.savepoint.sequence == 2
    assert result.savepoint.covered_revision == 2
    public_replay = owner.replay_body_restore(
        actor_id="user-notes-owner",
        note_id=note.note_id,
        command=public_command,
    )
    assert public_replay == result
    assert owner.get_note(
        actor_id="user-notes-owner", note_id=note.note_id
    ).title == "Metadata remains"
    with postgres_runtime.session_factory() as session:
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=note.note_id
        ).count() == 2
        assert session.query(AtlasNoteSavepointRow).filter_by(
            note_id=note.note_id
        ).count() == 2



def test_notes_owner_rolls_back_atomic_restore_when_second_audit_fails(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-restore-audit-rollback",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Restore audit rollback",
            idempotency_key="create-restore-audit-rollback",
        ),
    )
    source = owner.list_savepoints(
        actor_id="user-notes-owner", note_id=note.note_id
    )[0]
    original_audit = owner._audit
    calls = 0

    def fail_second_audit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second audit unavailable")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(owner, "_audit", fail_second_audit)
    with pytest.raises(NotesError) as rejected:
        owner.commit_body_restore(
            actor_id="user-notes-owner",
            command=CommitNoteBodyRestoreRequestV1(
                note_id=note.note_id,
                command_id="restore-audit-rollback",
                room_name="room",
                restore_source_savepoint_id=source.savepoint_id,
                expected_revision_head=1,
                expected_collaboration_epoch=1,
                request_fingerprint="2" * 64,
                raw_yjs_update=b"restore",
                encoded_yjs_state=b"state",
                canonical_body={"type": "doc", "content": []},
                document_schema=NOTE_DOCUMENT_SCHEMA_V2,
                change_set=NoteChangeSetV1(),
                idempotency_key="restore-audit-rollback",
            ),
        )
    assert rejected.value.code == "audit_failure"
    with postgres_runtime.session_factory() as session:
        current = session.get(AtlasNoteRow, note.note_id)
        assert current.accepted_update_head == 1
        assert current.savepoint_head == 1
        assert session.query(AtlasNoteRevisionRow).filter_by(
            note_id=note.note_id
        ).count() == 1
        assert session.query(AtlasNoteSavepointRow).filter_by(
            note_id=note.note_id
        ).count() == 1


def test_notes_owner_derives_savepoint_contributors_from_revision_journal(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    with postgres_runtime.session_factory() as session:
        session.merge(
            AtlasUserRow(
                actor_id="user-notes-contributor",
                display_name="Notes Contributor",
                email="notes-contributor@example.test",
                system_role="member",
                password_digest=None,
                active=True,
                actor_type="user",
                created_at="2026-08-12T00:00:00+00:00",
            )
        )
        session.merge(
            AtlasPermissionGrantRow(
                grant_id="grant-notes-contributor",
                project_id="project-notes-owner",
                subject_type="user",
                subject_id="user-notes-contributor",
                role="viewer",
                effect="allow",
                status="active",
                created_at="2026-08-12T00:00:00+00:00",
                revoked_at=None,
            )
        )
        session.commit()
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    note = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-contributor-attribution",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Contributor attribution",
            idempotency_key="create-contributor-attribution",
        ),
    )
    for head, actor in ((1, "user-notes-owner"), (2, "user-notes-contributor")):
        owner.accept_revision(
            actor_id=actor,
            command=AcceptNoteRevisionRequestV1(
                note_id=note.note_id,
                expected_revision_head=head,
                expected_collaboration_epoch=1,
                event_kind="content_update",
                raw_yjs_update=actor.encode(),
                canonical_body=_body(f"actor-{actor}", actor),
                document_schema=NOTE_DOCUMENT_SCHEMA_V2,
                change_set=NoteChangeSetV1(),
                idempotency_key=f"revision-{actor}",
            ),
        )
    savepoint = owner.create_savepoint(
        actor_id="user-notes-owner",
        command=CreateNoteSavepointRequestV1(
            note_id=note.note_id,
            expected_revision_head=3,
            expected_savepoint_head=1,
            expected_collaboration_epoch=1,
            encoded_yjs_state=b"state",
            canonical_body=_body("savepoint-contributors"),
            document_schema=NOTE_DOCUMENT_SCHEMA_V2,
            aggregate_change_set=NoteChangeSetV1(),
            contributor_actor_ids=("spoofed-carrier-actor",),
            idempotency_key="savepoint-contributors",
        ),
    )
    assert savepoint.contributor_actor_ids == (
        "user-notes-owner",
        "user-notes-contributor",
    )


def test_notes_owner_binds_finalized_attachment_and_rejects_cross_note_body(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_project_member(postgres_runtime)
    owner = PostgresNotesOwner(postgres_runtime.session_factory)
    first = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-attachment-owner",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Attachment owner",
            idempotency_key="create-attachment-owner",
        ),
    )
    second = owner.create_note(
        actor_id="user-notes-owner",
        command=NoteCreateRequestV1(
            note_id="note-attachment-other",
            scope_type="project",
            scope_id="project-notes-owner",
            title="Attachment other",
            idempotency_key="create-attachment-other",
        ),
    )
    fingerprint = hashlib.sha256(b"request").hexdigest()
    assert owner.prepare_attachment_upload(
        actor_id="user-notes-owner",
        note_id=first.note_id,
        expected_collaboration_epoch=1,
        idempotency_key="attachment-upload-1",
        request_fingerprint=fingerprint,
    )[1] is None
    attachment = owner.bind_attachment(
        actor_id="user-notes-owner",
        note_id=first.note_id,
        expected_collaboration_epoch=1,
        idempotency_key="attachment-upload-1",
        request_fingerprint=fingerprint,
        attachment_ref="natt-owner-1",
        artifact_id="artifact-note-image-owner-1",
        mime_type="image/png",
        byte_size=70,
        sha256=hashlib.sha256(b"image").hexdigest(),
        width=5,
        height=4,
    )
    replay = owner.prepare_attachment_upload(
        actor_id="user-notes-owner",
        note_id=first.note_id,
        expected_collaboration_epoch=1,
        idempotency_key="attachment-upload-1",
        request_fingerprint=fingerprint,
    )[1]
    assert replay == attachment

    accepted = owner.accept_revision(
        actor_id="user-notes-owner",
        command=AcceptNoteRevisionRequestV1(
            note_id=first.note_id,
            expected_revision_head=1,
            expected_collaboration_epoch=1,
            event_kind="content_update",
            raw_yjs_update=b"image-update",
            canonical_body=_image_body("image-block-1", attachment.attachment_ref),
            document_schema=NOTE_DOCUMENT_SCHEMA_V2,
            change_set=NoteChangeSetV1(),
            idempotency_key="accept-image-update",
        ),
    )
    assert accepted.after_digest

    with pytest.raises(NotesError) as caught:
        owner.accept_revision(
            actor_id="user-notes-owner",
            command=AcceptNoteRevisionRequestV1(
                note_id=second.note_id,
                expected_revision_head=1,
                expected_collaboration_epoch=1,
                event_kind="content_update",
                raw_yjs_update=b"cross-note-image-update",
                canonical_body=_image_body("image-block-2", attachment.attachment_ref),
                document_schema=NOTE_DOCUMENT_SCHEMA_V2,
                change_set=NoteChangeSetV1(),
                idempotency_key="reject-cross-note-image-update",
            ),
        )
    assert caught.value.code == "invalid_attachment"

    with postgres_runtime.session_factory() as session:
        row = session.get(AtlasNoteAttachmentRow, attachment.attachment_ref)
        assert row is not None
        assert row.note_id == first.note_id
