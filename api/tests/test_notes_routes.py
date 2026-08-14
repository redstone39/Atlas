from types import SimpleNamespace
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas_production.routes import notes as notes_routes
from atlas_production.routes import internal_notes_collaboration
from atlas_production.openapi_app import create_openapi_app


def test_notes_002_fixed_public_and_private_routes_are_registered() -> None:
    paths = create_openapi_app().openapi()["paths"]
    expected = {
        "/api/v1/notes/scopes", "/api/v1/notes", "/api/v1/notes/{note_id}",
        "/api/v1/notes/{note_id}/trash", "/api/v1/notes/{note_id}/restore",
        "/api/v1/notes/{note_id}/attachments",
        "/api/v1/notes/{note_id}/attachments/{attachment_ref}/content",
        "/api/v1/note-categories", "/api/v1/note-categories/{category_id}",
        "/api/v1/note-categories/{category_id}/trash",
        "/api/v1/note-categories/{category_id}/restore",
        "/api/v1/notes/{note_id}/collaboration-ticket",
        "/api/v1/notes/{note_id}/revisions", "/api/v1/notes/{note_id}/savepoints",
        "/api/v1/notes/{note_id}/savepoints/{savepoint_id}",
        "/api/v1/notes/{note_id}/savepoints/{savepoint_id}/restore-body",
        "/api/v1/admin/notes/settings", "/internal/v1/notes-collaboration/authorize",
        "/internal/v1/notes-collaboration/revalidate", "/internal/v1/notes-collaboration/load",
        "/internal/v1/notes-collaboration/append-revision",
        "/internal/v1/notes-collaboration/append-savepoint",
        "/internal/v1/notes-collaboration/settings",
        "/internal/v1/notes-collaboration/restore-source",
        "/internal/v1/notes-collaboration/commit-body-restore",
    }
    assert expected <= paths.keys()
    assert "delete" not in paths["/api/v1/notes/{note_id}"]
    assert "delete" not in paths["/api/v1/note-categories/{category_id}"]


def test_notes_002_binary_private_inputs_are_multipart_not_json_base64() -> None:
    paths = create_openapi_app().openapi()["paths"]
    for endpoint in ("append-revision", "append-savepoint"):
        content = paths[f"/internal/v1/notes-collaboration/{endpoint}"]["post"]["requestBody"]["content"]
        assert set(content) == {"multipart/form-data"}


def test_notes_002_private_mutations_derive_actor_and_note_from_ticket() -> None:
    paths = create_openapi_app().openapi()["paths"]
    for endpoint in ("append-revision", "append-savepoint"):
        schema = paths[f"/internal/v1/notes-collaboration/{endpoint}"]["post"][
            "requestBody"
        ]["content"]["multipart/form-data"]["schema"]
        properties = schema.get("properties")
        if properties is None:
            component = schema["$ref"].rsplit("/", 1)[-1]
            properties = create_openapi_app().openapi()["components"]["schemas"][
                component
            ]["properties"]
        assert "connection_token" in properties
        assert "actor_id" not in properties
        assert "note_id" not in properties
        assert "ticket" not in properties
    schemas = create_openapi_app().openapi()["components"]["schemas"]
    assert "raw_yjs_update" not in schemas["NoteRevisionHistoryV1"]["properties"]
    assert "encoded_yjs_state" not in schemas["NoteSavepointPreviewV1"]["properties"]
    assert "encoded_yjs_state" not in schemas["NoteSavepointSummaryV1"]["properties"]


def test_notes_002_private_binary_commits_return_binary_free_receipts(
    monkeypatch,
) -> None:
    from atlas_production.modules.notes.public import (
        NoteChangeSetV1,
        NoteRevisionV1,
        NoteSavepointV1,
    )

    now = datetime.now(timezone.utc)

    class Owner:
        def accept_revision(self, **_kwargs):
            return NoteRevisionV1(
                revision_id="revision",
                note_id="note",
                sequence=2,
                server_timestamp=now,
                actor_id="actor",
                event_kind="content_update",
                raw_yjs_update=b"\xffupdate",
                before_digest="0" * 64,
                after_digest="1" * 64,
                change_set=NoteChangeSetV1(),
            )

        def create_savepoint(self, **_kwargs):
            return NoteSavepointV1(
                savepoint_id="savepoint",
                note_id="note",
                sequence=2,
                covered_revision=2,
                encoded_yjs_state=b"\xfestate",
                canonical_body={"type": "doc"},
                document_schema="schema",
                body_digest="1" * 64,
                aggregate_change_set=NoteChangeSetV1(),
                contributor_actor_ids=("actor",),
                created_at=now,
            )

    class Service:
        owner = Owner()

        def verify_connection(self, *_args):
            return {"actor_id": "actor", "note_id": "note"}

        def verify_restore_source_authorization(self, *_args):
            class Record:
                def __init__(self, **values):
                    self.__dict__.update(values)

                def model_dump(self, *, exclude=None, **_kwargs):
                    excluded = exclude or set()
                    return {
                        key: value
                        for key, value in self.__dict__.items()
                        if key not in excluded
                    }

            note = Record(note_id="note", collaboration_epoch=1)
            latest = Record(
                savepoint_id="latest",
                encoded_yjs_state=b"\xfecurrent",
                sequence=2,
            )
            source = Record(
                savepoint_id="selected",
                encoded_yjs_state=b"\xffsource",
                sequence=1,
            )
            return {"actor_id": "actor"}, (note, latest, (), source)

    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET", "secret")
    monkeypatch.setattr(
        internal_notes_collaboration,
        "api_composition",
        lambda _request: SimpleNamespace(notes=Service()),
    )
    app = FastAPI()
    app.include_router(internal_notes_collaboration.router)
    client = TestClient(app)
    common = {
        "connection_token": "connection",
        "room_name": "room",
        "expected_revision_head": "1",
        "expected_collaboration_epoch": "1",
        "canonical_body_json": '{"type":"doc"}',
        "document_schema": "schema",
        "idempotency_key": "key",
    }
    revision = client.post(
        "/internal/v1/notes-collaboration/append-revision",
        headers={"X-Atlas-Notes-Internal-Secret": "secret"},
        data={**common, "change_set_json": "{}"},
        files={"update": ("update.bin", b"\xffupdate", "application/octet-stream")},
    )
    assert revision.status_code == 200
    assert "raw_yjs_update" not in revision.json()

    savepoint = client.post(
        "/internal/v1/notes-collaboration/append-savepoint",
        headers={"X-Atlas-Notes-Internal-Secret": "secret"},
        data={
            **common,
            "expected_savepoint_head": "1",
            "aggregate_change_set_json": "{}",
        },
        files={"state": ("state.bin", b"\xfestate", "application/octet-stream")},
    )
    assert savepoint.status_code == 200
    assert "encoded_yjs_state" not in savepoint.json()

    restore_source = client.post(
        "/internal/v1/notes-collaboration/restore-source",
        headers={"X-Atlas-Notes-Internal-Secret": "secret"},
        json={
            "command_id": "restore-command",
            "note_id": "note",
            "room_name": "room",
            "savepoint_id": "selected",
            "expected_revision_head": 1,
            "expected_collaboration_epoch": 1,
            "idempotency_key": "restore-key",
            "request_fingerprint": "1" * 64,
            "authorization_token": "authorization",
        },
    )
    assert restore_source.status_code == 200
    assert b"\xfecurrent" in restore_source.content
    assert b"\xffsource" in restore_source.content


def test_notes_002_public_scope_route_uses_composed_notes_service(
    monkeypatch,
) -> None:
    scope = SimpleNamespace(
        model_dump=lambda: {
            "scope_type": "project",
            "scope_id": "project-route",
            "label": "Route Project",
        }
    )

    class Service:
        def list_scopes(self, actor_id: str):
            assert actor_id == "user-route"
            from atlas_production.modules.notes.public import NoteScopeRefV1

            return (NoteScopeRefV1.model_validate(scope.model_dump()),)

    monkeypatch.setattr(
        notes_routes,
        "current_user",
        lambda _request: SimpleNamespace(actor_id="user-route"),
    )
    monkeypatch.setattr(
        notes_routes,
        "api_composition",
        lambda _request: SimpleNamespace(notes=Service()),
    )
    app = FastAPI()
    app.include_router(notes_routes.router)

    response = TestClient(app).get("/api/v1/notes/scopes")

    assert response.status_code == 200
    assert response.json() == {"items": [scope.model_dump()]}


def test_notes_block_attachment_routes_use_multipart_and_private_no_store(
    monkeypatch,
) -> None:
    from atlas_production.modules.notes.public import NoteAttachmentV1
    from atlas_production.modules.notes.service import NoteAttachmentContent

    calls: list[tuple[str, object]] = []

    class Service:
        def upload_attachment(self, actor_id: str, note_id: str, **values):
            calls.append(("upload", (actor_id, note_id, values)))
            return NoteAttachmentV1(
                attachment_ref="natt-opaque",
                mime_type="image/png",
                byte_size=len(values["content"]),
                sha256="a" * 64,
                width=2,
                height=3,
            )

        def open_attachment(self, actor_id: str, note_id: str, attachment_ref: str):
            calls.append(("open", (actor_id, note_id, attachment_ref)))
            return NoteAttachmentContent(content=b"\x89PNG", mime_type="image/png")

    monkeypatch.setattr(
        notes_routes,
        "current_user",
        lambda _request: SimpleNamespace(actor_id="user-route"),
    )
    monkeypatch.setattr(
        notes_routes,
        "api_composition",
        lambda _request: SimpleNamespace(notes=Service()),
    )
    app = FastAPI()
    app.include_router(notes_routes.router)
    client = TestClient(app)

    uploaded = client.post(
        "/api/v1/notes/note-1/attachments",
        headers={"Idempotency-Key": "paste-1"},
        data={"expected_collaboration_epoch": "3", "idempotency_key": "paste-1"},
        files={"file": ("clipboard.png", b"\x89PNG", "image/png")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["attachment_ref"] == "natt-opaque"
    assert calls[0][1][2]["expected_collaboration_epoch"] == 3
    assert calls[0][1][2]["content"] == b"\x89PNG"

    opened = client.get(
        "/api/v1/notes/note-1/attachments/natt-opaque/content"
    )
    assert opened.status_code == 200
    assert opened.content == b"\x89PNG"
    assert opened.headers["cache-control"] == "private, no-store"
    assert opened.headers["x-content-type-options"] == "nosniff"

    paths = create_openapi_app().openapi()["paths"]
    upload_content = paths["/api/v1/notes/{note_id}/attachments"]["post"][
        "requestBody"
    ]["content"]
    assert set(upload_content) == {"multipart/form-data"}
    open_content = paths[
        "/api/v1/notes/{note_id}/attachments/{attachment_ref}/content"
    ]["get"]["responses"]["200"]["content"]
    assert set(open_content) == {"image/png", "image/jpeg", "image/webp"}


def test_notes_block_attachment_upload_rejects_header_form_mismatch(
    monkeypatch,
) -> None:
    class Service:
        def upload_attachment(self, *_args, **_kwargs):
            raise AssertionError("provider must not be called")

    monkeypatch.setattr(
        notes_routes,
        "current_user",
        lambda _request: SimpleNamespace(actor_id="user-route"),
    )
    monkeypatch.setattr(
        notes_routes,
        "api_composition",
        lambda _request: SimpleNamespace(notes=Service()),
    )
    app = FastAPI()
    app.include_router(notes_routes.router)
    response = TestClient(app).post(
        "/api/v1/notes/note-1/attachments",
        headers={"Idempotency-Key": "header-key"},
        data={"expected_collaboration_epoch": "1", "idempotency_key": "form-key"},
        files={"file": ("clipboard.png", b"\x89PNG", "image/png")},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "idempotency_payload_conflict"
