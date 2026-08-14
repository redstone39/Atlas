from types import SimpleNamespace

import pytest

from atlas_production.modules.notes.public import NoteBodyRestoreRequestV1, NotesError
from atlas_production.modules.notes.service import (
    NoteAttachmentContent,
    NotesApplicationService,
    NoopCollaborationNotifier,
)


class Owner:
    def trash_note(self, **_kwargs): return SimpleNamespace(collaboration_epoch=4)
    def restore_note(self, **_kwargs): return SimpleNamespace(collaboration_epoch=5)
    def update_settings(self, **_kwargs): return SimpleNamespace(settings_revision=3)
    def validate_body_restore(self, **_kwargs):
        return SimpleNamespace(collaboration_epoch=1), SimpleNamespace()
    def replay_body_restore(self, **_kwargs): return None

class FailingNotifier:
    def invalidate_room(self, *_args): raise OSError("carrier down")
    def reschedule_settings(self, *_args): raise OSError("carrier down")
    def restore_body(self, *_args): raise OSError("carrier down")
    def readiness_available(self): return False


def test_notes_002_committed_lifecycle_and_settings_survive_best_effort_notification() -> None:
    service=NotesApplicationService(Owner(),FailingNotifier())
    assert service.trash_note("actor","note",object()).collaboration_epoch==4
    assert service.restore_note("actor","note",object()).collaboration_epoch==5
    assert service.update_settings("actor",object()).settings_revision==3


def test_notes_002_restore_handoff_failure_is_typed_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "ticket-secret")
    service=NotesApplicationService(Owner(),FailingNotifier())
    command=NoteBodyRestoreRequestV1(savepoint_id="sp",expected_revision_head=1,
        expected_collaboration_epoch=1,idempotency_key="key")
    with pytest.raises(NotesError) as rejected:
        service.restore_body("actor","note",command)
    assert rejected.value.code == "audit_failure"
    assert rejected.value.status_code == 503


def test_notes_002_missing_carrier_never_reports_restore_handoff_success(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "ticket-secret")
    service = NotesApplicationService(Owner(), NoopCollaborationNotifier())
    command = NoteBodyRestoreRequestV1(
        savepoint_id="sp",
        expected_revision_head=1,
        expected_collaboration_epoch=1,
        idempotency_key="key",
    )
    with pytest.raises(NotesError) as rejected:
        service.restore_body("actor", "note", command)
    assert rejected.value.code == "audit_failure"
    assert rejected.value.status_code == 503


def test_notes_002_ticket_exchange_binds_stable_room_and_outlives_client_ticket(
    monkeypatch,
) -> None:
    class TicketOwner:
        def get_note(self, *, actor_id, note_id):
            return SimpleNamespace(
                note_id=note_id,
                collaboration_epoch=7,
                lifecycle_status="active",
                accepted_update_head=3,
                savepoint_head=2,
            )

    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "api-only")
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_PUBLIC_URL", "ws://notes.test")
    service = NotesApplicationService(TicketOwner(), NoopCollaborationNotifier())
    first = service.collaboration_ticket("actor-a", "note-shared")
    second = service.collaboration_ticket("actor-b", "note-shared")
    assert first.room_name == second.room_name
    assert "actor-a" not in first.ticket
    with pytest.raises(NotesError, match="room does not match"):
        service.authorize_connection(first.ticket, "wrong-room")

    claims = service.authorize_connection(first.ticket, first.room_name)
    monkeypatch.setattr("atlas_production.modules.notes.service.time.time", lambda: 10**12)
    verified = service.verify_connection(
        str(claims["connection_token"]), first.room_name, 7
    )
    assert verified["actor_id"] == "actor-a"


def test_notes_002_carrier_transport_secret_cannot_mint_actor_authority(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "carrier-transport-secret"
    )
    forged = NotesApplicationService._seal(
        {
            "v": 1,
            "purpose": "connection",
            "actor_id": "victim",
            "note_id": "note",
            "epoch": 1,
            "room": "room",
        }
    )
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "api-only-secret")
    monkeypatch.setenv(
        "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET", "carrier-transport-secret"
    )
    with pytest.raises(NotesError, match="invalid or expired"):
        NotesApplicationService._open(forged, "connection")



def test_notes_002_ticket_authority_rejects_carrier_transport_key(monkeypatch):
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET", "same")
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "same")
    with pytest.raises(NotesError) as rejected:
        NotesApplicationService._ticket_key()
    assert rejected.value.code == "audit_failure"
    assert rejected.value.status_code == 503


def test_notes_002_restore_authorization_binds_previewed_command(monkeypatch) -> None:
    from atlas_production.modules.notes.public import (
        CommitNoteBodyRestoreRequestV1,
        NoteChangeSetV1,
    )

    class RestoreOwner(Owner):
        def get_note(self, *, actor_id, note_id):
            return SimpleNamespace(
                note_id=note_id,
                collaboration_epoch=3,
                lifecycle_status="active",
            )
        def load_restore_context(self, **_kwargs):
            return ("note", "latest", (), "source")

        def validate_body_restore(self, **_kwargs):
            return self.get_note(actor_id="actor", note_id="note"), SimpleNamespace()

    class CapturingNotifier:
        commands = []

        def restore_body(self, command):
            self.commands.append(command)
            return SimpleNamespace()

        def readiness_available(self): return True
        def invalidate_room(self, *_args): return None
        def reschedule_settings(self, *_args): return None

    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "api-only")
    notifier = CapturingNotifier()
    service = NotesApplicationService(RestoreOwner(), notifier)
    request = NoteBodyRestoreRequestV1(
        savepoint_id="previewed-savepoint",
        expected_revision_head=4,
        expected_collaboration_epoch=3,
        idempotency_key="restore-key",
    )
    service.restore_body("actor", "note", request)
    service.restore_body("actor", "note", request)
    handoff = notifier.commands[0]
    assert notifier.commands[1].command_id == handoff.command_id
    _, loaded = service.verify_restore_source_authorization(
        handoff.authorization_token, handoff
    )
    assert loaded[-1] == "source"
    with pytest.raises(NotesError, match="does not match"):
        service.verify_restore_source_authorization(
            handoff.authorization_token,
            handoff.model_copy(update={"savepoint_id": "different-savepoint"}),
        )
    commit = CommitNoteBodyRestoreRequestV1(
        note_id=handoff.note_id,
        command_id=handoff.command_id,
        room_name=handoff.room_name,
        restore_source_savepoint_id=handoff.savepoint_id,
        expected_revision_head=handoff.expected_revision_head,
        expected_collaboration_epoch=handoff.expected_collaboration_epoch,
        request_fingerprint=handoff.request_fingerprint,
        raw_yjs_update=b"update",
        encoded_yjs_state=b"state",
        canonical_body={"type": "doc"},
        document_schema="schema",
        change_set=NoteChangeSetV1(),
        idempotency_key=handoff.idempotency_key,
    )
    claims = service.verify_restore_authorization(
        handoff.authorization_token, commit
    )
    assert claims["actor_id"] == "actor"
    with pytest.raises(NotesError, match="does not match"):
        service.verify_restore_authorization(
            handoff.authorization_token,
            commit.model_copy(
                update={"restore_source_savepoint_id": "different-savepoint"}
            ),
        )


def test_notes_002_restore_replays_durable_result_before_stale_validation() -> None:
    durable = SimpleNamespace(revision="revision", savepoint="savepoint")

    class ReplayOwner(Owner):
        def replay_body_restore(self, **_kwargs):
            return durable

        def validate_body_restore(self, **_kwargs):
            raise AssertionError("durable replay must precede stale validation")

    result = NotesApplicationService(
        ReplayOwner(), NoopCollaborationNotifier()
    ).restore_body(
        "actor",
        "note",
        NoteBodyRestoreRequestV1(
            savepoint_id="savepoint",
            expected_revision_head=1,
            expected_collaboration_epoch=1,
            idempotency_key="key",
        ),
    )
    assert result is durable


def test_notes_block_attachment_content_rejects_empty_or_oversize_bytes() -> None:
    with pytest.raises(NotesError) as empty:
        NoteAttachmentContent(content=b"", mime_type="image/png")
    assert empty.value.code == "integrity_failure"

    with pytest.raises(NotesError) as oversize:
        NoteAttachmentContent(
            content=b"x" * (16 * 1024 * 1024 + 1), mime_type="image/webp"
        )
    assert oversize.value.code == "integrity_failure"

    with pytest.raises(NotesError) as wrong_mime:
        NoteAttachmentContent(content=b"GIF89a", mime_type="image/gif")  # type: ignore[arg-type]
    assert wrong_mime.value.code == "integrity_failure"
