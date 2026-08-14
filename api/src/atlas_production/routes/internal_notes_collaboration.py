from __future__ import annotations

import hmac
import json
import os
from secrets import token_hex

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from atlas_production.modules.notes.public import *
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition


router = APIRouter(prefix="/internal/v1/notes-collaboration")


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str
    room_name: str


class RevalidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_token: str
    room_name: str
    expected_collaboration_epoch: int


class AuthorizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    note_id: str
    actor_id: str
    room_name: str
    connection_token: str
    collaboration_epoch: int
    read_only: bool
    accepted_update_head: int
    savepoint_head: int


class LoadRequest(RevalidateRequest):
    pass


def _service(request: Request):
    return api_composition(request).notes


def _owner(request: Request):
    return _service(request).owner


def _failure(exc: NotesError):
    return error(exc.code, "common.rejected", exc.status_code)


def _secret(value: str | None = Header(None, alias="X-Atlas-Notes-Internal-Secret")):
    expected = os.environ.get("ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET")
    if not expected or value is None or not hmac.compare_digest(value.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Internal collaboration authentication failed")


def _call(callback):
    try:
        return callback()
    except NotesError as exc:
        return _failure(exc)


async def _bytes(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_NOTE_BINARY_BYTES + 1)
    if not data or len(data) > MAX_NOTE_BINARY_BYTES:
        raise NotesError("payload_oversize", "Binary payload is empty or too large", 413)
    return data


def _authorization(service, claims: dict[str, object]) -> AuthorizationResult:
    note = service.owner.get_note(
        actor_id=str(claims["actor_id"]), note_id=str(claims["note_id"])
    )
    return AuthorizationResult(
        note_id=note.note_id,
        actor_id=str(claims["actor_id"]),
        room_name=str(claims["room"]),
        connection_token=str(claims["connection_token"]),
        collaboration_epoch=note.collaboration_epoch,
        read_only=note.lifecycle_status == "trashed",
        accepted_update_head=note.accepted_update_head,
        savepoint_head=note.savepoint_head,
    )


def _multipart_load(note, savepoint, tail) -> Response:
    boundary = f"atlas-notes-{token_hex(16)}"
    manifest = {
        "note": note.model_dump(mode="json"),
        "savepoint": savepoint.model_dump(
            mode="json", exclude={"encoded_yjs_state"}
        ),
        "state_part": "savepoint-state",
        "tail": [
            {
                **revision.model_dump(mode="json", exclude={"raw_yjs_update"}),
                "update_part": f"revision-{revision.sequence}",
            }
            for revision in tail
        ],
    }
    parts: list[bytes] = []

    def add(headers: tuple[str, ...], body: bytes) -> None:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append("\r\n".join(headers).encode() + b"\r\n\r\n")
        parts.append(body + b"\r\n")

    add(
        ("Content-Type: application/json", 'Content-ID: "manifest"'),
        json.dumps(manifest, separators=(",", ":")).encode(),
    )
    add(
        ("Content-Type: application/octet-stream", 'Content-ID: "savepoint-state"'),
        savepoint.encoded_yjs_state,
    )
    for revision in tail:
        add(
            (
                "Content-Type: application/octet-stream",
                f'Content-ID: "revision-{revision.sequence}"',
            ),
            revision.raw_yjs_update,
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return Response(
        content=b"".join(parts),
        media_type=f'multipart/mixed; boundary="{boundary}"',
    )


def _multipart_restore_context(note, latest, tail, source) -> Response:
    boundary = f"atlas-notes-restore-{token_hex(16)}"
    manifest = {
        "note": note.model_dump(mode="json"),
        "current_savepoint": latest.model_dump(
            mode="json", exclude={"encoded_yjs_state"}
        ),
        "current_state_part": "current-savepoint-state",
        "tail": [
            {
                **revision.model_dump(mode="json", exclude={"raw_yjs_update"}),
                "update_part": f"revision-{revision.sequence}",
            }
            for revision in tail
        ],
        "restore_source": source.model_dump(
            mode="json", exclude={"encoded_yjs_state"}
        ),
        "restore_source_state_part": "restore-source-state",
    }
    parts: list[bytes] = []

    def add(content_id: str, content_type: str, body: bytes) -> None:
        parts.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f"Content-Type: {content_type}\r\n"
                    f'Content-ID: "{content_id}"\r\n\r\n'
                ).encode(),
                body + b"\r\n",
            )
        )

    add(
        "manifest",
        "application/json",
        json.dumps(manifest, separators=(",", ":")).encode(),
    )
    add("current-savepoint-state", "application/octet-stream", latest.encoded_yjs_state)
    for revision in tail:
        add(
            f"revision-{revision.sequence}",
            "application/octet-stream",
            revision.raw_yjs_update,
        )
    add("restore-source-state", "application/octet-stream", source.encoded_yjs_state)
    parts.append(f"--{boundary}--\r\n".encode())
    return Response(
        content=b"".join(parts),
        media_type=f'multipart/mixed; boundary="{boundary}"',
    )


@router.post(
    "/authorize",
    response_model=AuthorizationResult,
    dependencies=[Depends(_secret)],
)
def authorize(payload: AuthorizeRequest, request: Request):
    def call():
        service = _service(request)
        claims = service.authorize_connection(payload.ticket, payload.room_name)
        return _authorization(service, claims)

    return _call(call)


@router.post(
    "/revalidate",
    response_model=AuthorizationResult,
    dependencies=[Depends(_secret)],
)
def revalidate(payload: RevalidateRequest, request: Request):
    def call():
        service = _service(request)
        claims = service.verify_connection(
            payload.connection_token,
            payload.room_name,
            payload.expected_collaboration_epoch,
        )
        claims["connection_token"] = payload.connection_token
        return _authorization(service, claims)

    return _call(call)


@router.post(
    "/load",
    response_class=Response,
    responses={200: {"content": {"multipart/mixed": {"schema": {"type": "string", "format": "binary"}}}}},
    dependencies=[Depends(_secret)],
)
def load(payload: LoadRequest, request: Request):
    def call():
        service = _service(request)
        claims = service.verify_connection(
            payload.connection_token,
            payload.room_name,
            payload.expected_collaboration_epoch,
        )
        loaded = _owner(request).load_collaboration(
            actor_id=str(claims["actor_id"]),
            note_id=str(claims["note_id"]),
            expected_collaboration_epoch=payload.expected_collaboration_epoch,
        )
        return _multipart_load(*loaded)

    return _call(call)


@router.post(
    "/restore-source",
    response_class=Response,
    responses={
        200: {
            "content": {
                "multipart/mixed": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
    dependencies=[Depends(_secret)],
)
def restore_source(payload: BodyRestoreCommandV1, request: Request):
    def call():
        _, loaded = _service(request).verify_restore_source_authorization(
            payload.authorization_token, payload
        )
        return _multipart_restore_context(*loaded)

    return _call(call)


@router.post(
    "/append-revision",
    response_model=NoteRevisionHistoryV1,
    dependencies=[Depends(_secret)],
)
async def append_revision(
    request: Request,
    connection_token: str = Form(...),
    room_name: str = Form(...),
    expected_revision_head: int = Form(...),
    expected_collaboration_epoch: int = Form(...),
    canonical_body_json: str = Form(...),
    document_schema: str = Form(...),
    change_set_json: str = Form(...),
    idempotency_key: str = Form(...),
    update: UploadFile = File(...),
):
    try:
        claims = _service(request).verify_connection(
            connection_token, room_name, expected_collaboration_epoch
        )
        command = AcceptNoteRevisionRequestV1(
            note_id=str(claims["note_id"]),
            expected_revision_head=expected_revision_head,
            expected_collaboration_epoch=expected_collaboration_epoch,
            event_kind="content_update",
            raw_yjs_update=await _bytes(update),
            canonical_body=json.loads(canonical_body_json),
            document_schema=document_schema,
            change_set=NoteChangeSetV1.model_validate_json(change_set_json),
            idempotency_key=idempotency_key,
        )
        revision = _owner(request).accept_revision(
            actor_id=str(claims["actor_id"]), command=command
        )
        return NoteRevisionHistoryV1.model_validate(
            revision.model_dump(exclude={"raw_yjs_update"})
        )
    except (NotesError, ValueError, json.JSONDecodeError) as exc:
        return _failure(exc) if isinstance(exc, NotesError) else error(
            "invalid_payload", "common.rejected", 422
        )


@router.post(
    "/append-savepoint",
    response_model=NoteSavepointSummaryV1,
    dependencies=[Depends(_secret)],
)
async def append_savepoint(
    request: Request,
    connection_token: str = Form(...),
    room_name: str = Form(...),
    expected_revision_head: int = Form(...),
    expected_savepoint_head: int = Form(...),
    expected_collaboration_epoch: int = Form(...),
    canonical_body_json: str = Form(...),
    document_schema: str = Form(...),
    aggregate_change_set_json: str = Form(...),
    idempotency_key: str = Form(...),
    state: UploadFile = File(...),
):
    try:
        claims = _service(request).verify_connection(
            connection_token, room_name, expected_collaboration_epoch
        )
        command = CreateNoteSavepointRequestV1(
            note_id=str(claims["note_id"]),
            expected_revision_head=expected_revision_head,
            expected_savepoint_head=expected_savepoint_head,
            expected_collaboration_epoch=expected_collaboration_epoch,
            encoded_yjs_state=await _bytes(state),
            canonical_body=json.loads(canonical_body_json),
            document_schema=document_schema,
            aggregate_change_set=NoteChangeSetV1.model_validate_json(
                aggregate_change_set_json
            ),
            contributor_actor_ids=(),
            idempotency_key=idempotency_key,
        )
        savepoint = _owner(request).create_savepoint(
            actor_id=str(claims["actor_id"]), command=command
        )
        return NoteSavepointSummaryV1.model_validate(
            savepoint.model_dump(
                exclude={"encoded_yjs_state", "canonical_body", "document_schema"}
            )
        )
    except (NotesError, ValueError, json.JSONDecodeError) as exc:
        return _failure(exc) if isinstance(exc, NotesError) else error(
            "invalid_payload", "common.rejected", 422
        )


@router.post(
    "/commit-body-restore",
    response_model=BodyRestoreResultV1,
    dependencies=[Depends(_secret)],
)
async def commit_body_restore(
    request: Request,
    authorization_token: str = Form(...),
    command_id: str = Form(...),
    note_id: str = Form(...),
    room_name: str = Form(...),
    restore_source_savepoint_id: str = Form(...),
    expected_revision_head: int = Form(...),
    expected_collaboration_epoch: int = Form(...),
    request_fingerprint: str = Form(...),
    canonical_body_json: str = Form(...),
    document_schema: str = Form(...),
    change_set_json: str = Form(...),
    idempotency_key: str = Form(...),
    update: UploadFile = File(...),
    state: UploadFile = File(...),
):
    try:
        command = CommitNoteBodyRestoreRequestV1(
            note_id=note_id,
            command_id=command_id,
            room_name=room_name,
            restore_source_savepoint_id=restore_source_savepoint_id,
            expected_revision_head=expected_revision_head,
            expected_collaboration_epoch=expected_collaboration_epoch,
            request_fingerprint=request_fingerprint,
            raw_yjs_update=await _bytes(update),
            encoded_yjs_state=await _bytes(state),
            canonical_body=json.loads(canonical_body_json),
            document_schema=document_schema,
            change_set=NoteChangeSetV1.model_validate_json(change_set_json),
            idempotency_key=idempotency_key,
        )
        claims = _service(request).verify_restore_authorization(
            authorization_token, command
        )
        return _owner(request).commit_body_restore(
            actor_id=str(claims["actor_id"]), command=command
        )
    except (NotesError, ValueError, json.JSONDecodeError) as exc:
        return _failure(exc) if isinstance(exc, NotesError) else error(
            "invalid_payload", "common.rejected", 422
        )


@router.get(
    "/settings",
    response_model=NotesSettingsV1,
    dependencies=[Depends(_secret)],
)
def settings(request: Request):
    return _call(lambda: _service(request).internal_settings())


__all__ = ["router"]
