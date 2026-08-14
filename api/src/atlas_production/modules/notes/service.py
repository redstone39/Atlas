from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from secrets import token_bytes
import time
from typing import Literal, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cryptography.exceptions import InvalidTag
from .public import *


class CollaborationNotifier(Protocol):
    def invalidate_room(self, note_id: str, epoch: int) -> None: ...
    def reschedule_settings(self, revision: int) -> None: ...
    def restore_body(self, command: BodyRestoreCommandV1) -> BodyRestoreResultV1: ...
    def readiness_available(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class NoteAttachmentContent:
    content: bytes
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]

    def __post_init__(self) -> None:
        if (
            self.mime_type not in {"image/png", "image/jpeg", "image/webp"}
            or not self.content
            or len(self.content) > MAX_NOTE_BINARY_BYTES
        ):
            raise NotesError(
                "integrity_failure", "Attachment content failed integrity checks", 503
            )


class NotesAttachmentProvider(Protocol):
    def upload(
        self,
        *,
        actor_id: str,
        note_id: str,
        expected_collaboration_epoch: int,
        idempotency_key: str,
        filename: str | None,
        claimed_mime_type: str | None,
        content: bytes,
    ) -> NoteAttachmentV1: ...

    def open(
        self, *, actor_id: str, note_id: str, attachment_ref: str
    ) -> NoteAttachmentContent: ...


@dataclass(frozen=True, slots=True)
class UnavailableNotesAttachmentProvider:
    def upload(self, **_kwargs: object) -> NoteAttachmentV1:
        raise NotesError(
            "storage_unavailable", "Notes attachment storage is unavailable", 503
        )

    def open(self, **_kwargs: object) -> NoteAttachmentContent:
        raise NotesError(
            "storage_unavailable", "Notes attachment storage is unavailable", 503
        )


@dataclass(frozen=True, slots=True)
class NoopCollaborationNotifier:
    def invalidate_room(self, note_id: str, epoch: int) -> None:
        return None

    def reschedule_settings(self, revision: int) -> None:
        return None

    def restore_body(self, command: BodyRestoreCommandV1) -> BodyRestoreResultV1:
        raise OSError("Notes collaboration carrier is unavailable")

    def readiness_available(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class NotesApplicationService:
    owner: object
    notifier: CollaborationNotifier
    attachment_provider: NotesAttachmentProvider = field(
        default_factory=UnavailableNotesAttachmentProvider
    )

    def list_scopes(self, actor_id: str):
        return self.owner.list_scopes(actor_id=actor_id)

    def list_notes(self, actor_id: str, **filters):
        return self.owner.list_notes(actor_id=actor_id, **filters)

    def get_note(self, actor_id: str, note_id: str):
        return self.owner.get_note(actor_id=actor_id, note_id=note_id)

    def create_note(self, actor_id: str, command: NoteCreateRequestV1):
        return self.owner.create_note(actor_id=actor_id, command=command)

    def update_note(self, actor_id: str, note_id: str, command: NoteMetadataUpdateRequestV1):
        return self.owner.update_note_metadata(actor_id=actor_id, note_id=note_id, command=command)

    def trash_note(self, actor_id: str, note_id: str, command: NoteTrashRequestV1):
        result = self.owner.trash_note(actor_id=actor_id, note_id=note_id, command=command)
        try:
            self.notifier.invalidate_room(note_id, result.collaboration_epoch)
        except Exception:
            pass
        return result

    def restore_note(self, actor_id: str, note_id: str, command: NoteRestoreRequestV1):
        result = self.owner.restore_note(actor_id=actor_id, note_id=note_id, command=command)
        try:
            self.notifier.invalidate_room(note_id, result.collaboration_epoch)
        except Exception:
            pass
        return result

    def list_categories(self, actor_id: str, **filters):
        return self.owner.list_categories(actor_id=actor_id, **filters)

    def get_category(self, actor_id: str, category_id: str):
        return self.owner.get_category(actor_id=actor_id, category_id=category_id)

    def create_category(self, actor_id: str, command: NoteCategoryCreateRequestV1):
        return self.owner.create_category(actor_id=actor_id, command=command)

    def update_category(self, actor_id: str, category_id: str, command: NoteCategoryUpdateRequestV1):
        return self.owner.update_category(actor_id=actor_id, category_id=category_id, command=command)

    def trash_category(self, actor_id: str, category_id: str, command: NoteCategoryTrashRequestV1):
        return self.owner.trash_category(actor_id=actor_id, category_id=category_id, command=command)

    def restore_category(self, actor_id: str, category_id: str, command: NoteCategoryRestoreRequestV1):
        return self.owner.restore_category(actor_id=actor_id, category_id=category_id, command=command)

    def list_revisions(self, actor_id: str, note_id: str, **filters):
        return self.owner.list_revisions(actor_id=actor_id, note_id=note_id, **filters)

    def list_savepoints(self, actor_id: str, note_id: str):
        return self.owner.list_savepoints(actor_id=actor_id, note_id=note_id)

    def get_savepoint(self, actor_id: str, note_id: str, savepoint_id: str):
        return self.owner.get_savepoint(actor_id=actor_id, note_id=note_id, savepoint_id=savepoint_id)

    def upload_attachment(
        self,
        actor_id: str,
        note_id: str,
        *,
        expected_collaboration_epoch: int,
        idempotency_key: str,
        filename: str | None,
        claimed_mime_type: str | None,
        content: bytes,
    ) -> NoteAttachmentV1:
        return self.attachment_provider.upload(
            actor_id=actor_id,
            note_id=note_id,
            expected_collaboration_epoch=expected_collaboration_epoch,
            idempotency_key=idempotency_key,
            filename=filename,
            claimed_mime_type=claimed_mime_type,
            content=content,
        )

    def open_attachment(
        self, actor_id: str, note_id: str, attachment_ref: str
    ) -> NoteAttachmentContent:
        return self.attachment_provider.open(
            actor_id=actor_id, note_id=note_id, attachment_ref=attachment_ref
        )

    @staticmethod
    def _ticket_key() -> bytes:
        secret = os.environ.get("ATLAS_NOTES_COLLABORATION_TICKET_SECRET")
        transport_secret = os.environ.get(
            "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET"
        )
        if not secret or (transport_secret and secret == transport_secret):
            raise NotesError(
                "audit_failure",
                "Collaboration ticket authority is unavailable or not isolated",
                503,
            )
        return hashlib.sha256(secret.encode()).digest()

    @classmethod
    def _seal(cls, claims: dict[str, object]) -> str:
        nonce = token_bytes(12)
        plaintext = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = AESGCM(cls._ticket_key()).encrypt(nonce, plaintext, b"atlas-notes-v1")
        return base64.urlsafe_b64encode(nonce + ciphertext).rstrip(b"=").decode()

    @classmethod
    def _open(cls, token: str, purpose: str) -> dict[str, object]:
        try:
            packed = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            plaintext = AESGCM(cls._ticket_key()).decrypt(
                packed[:12], packed[12:], b"atlas-notes-v1"
            )
            claims = json.loads(plaintext)
            if claims.get("v") != 1 or claims.get("purpose") != purpose:
                raise ValueError
            expires_at = claims.get("expires_at")
            if expires_at is not None and int(expires_at) < int(time.time()):
                raise ValueError
        except (InvalidTag, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise NotesError("access_denied", "Collaboration authorization is invalid or expired", 403) from exc
        return claims

    @classmethod
    def _room_name(cls, note_id: str, epoch: int) -> str:
        digest = hmac.new(
            cls._ticket_key(), f"room\0{note_id}\0{epoch}".encode(), hashlib.sha256
        ).hexdigest()
        return f"notes-{digest}"

    def collaboration_ticket(self, actor_id: str, note_id: str) -> CollaborationTicketV1:
        note = self.owner.get_note(actor_id=actor_id, note_id=note_id)
        ws_url = os.environ.get("ATLAS_NOTES_COLLABORATION_PUBLIC_URL")
        if not ws_url:
            raise NotesError("audit_failure", "Notes collaboration is not configured", 503)
        room = self._room_name(note_id, note.collaboration_epoch)
        claims = {
            "v": 1,
            "purpose": "connect",
            "actor_id": actor_id,
            "note_id": note_id,
            "epoch": note.collaboration_epoch,
            "room": room,
            "expires_at": int(time.time()) + 60,
            "read_only": note.lifecycle_status == "trashed",
        }
        return CollaborationTicketV1(
            ticket=self._seal(claims),
            room_name=room,
            websocket_url=ws_url,
            collaboration_epoch=note.collaboration_epoch,
            read_only=note.lifecycle_status == "trashed",
        )

    def authorize_connection(self, ticket: str, room_name: str) -> dict[str, object]:
        claims = self._open(ticket, "connect")
        if not hmac.compare_digest(str(claims["room"]), room_name):
            raise NotesError("access_denied", "Collaboration room does not match ticket", 403)
        note = self.owner.get_note(actor_id=str(claims["actor_id"]), note_id=str(claims["note_id"]))
        if note.collaboration_epoch != int(claims["epoch"]):
            raise NotesError("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
        session_claims = {key: value for key, value in claims.items() if key != "expires_at"}
        session_claims["purpose"] = "connection"
        session_claims["read_only"] = note.lifecycle_status == "trashed"
        return {**session_claims, "connection_token": self._seal(session_claims)}

    def verify_connection(
        self, connection_token: str, room_name: str, expected_collaboration_epoch: int
    ) -> dict[str, object]:
        claims = self._open(connection_token, "connection")
        if not hmac.compare_digest(str(claims["room"]), room_name):
            raise NotesError("access_denied", "Collaboration room does not match authorization", 403)
        if int(claims["epoch"]) != expected_collaboration_epoch:
            raise NotesError("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
        note = self.owner.get_note(actor_id=str(claims["actor_id"]), note_id=str(claims["note_id"]))
        if note.collaboration_epoch != expected_collaboration_epoch:
            raise NotesError("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
        claims["read_only"] = note.lifecycle_status == "trashed"
        return claims

    def verify_restore_authorization(
        self, authorization_token: str, command: CommitNoteBodyRestoreRequestV1
    ) -> dict[str, object]:
        claims = self._open(authorization_token, "restore")
        expected = {
            "command_id": command.command_id,
            "note_id": command.note_id,
            "room": command.room_name,
            "savepoint_id": command.restore_source_savepoint_id,
            "expected_revision_head": command.expected_revision_head,
            "epoch": command.expected_collaboration_epoch,
            "idempotency_key": command.idempotency_key,
            "request_fingerprint": command.request_fingerprint,
        }
        try:
            actual = {key: claims[key] for key in expected}
            matches = hmac.compare_digest(
                json.dumps(actual, sort_keys=True, separators=(",", ":")),
                json.dumps(expected, sort_keys=True, separators=(",", ":")),
            )
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise NotesError(
                "access_denied", "Restore authorization does not match command", 403
            )
        self.owner.get_note(
            actor_id=str(claims["actor_id"]), note_id=command.note_id
        )
        return claims

    def verify_restore_source_authorization(
        self, authorization_token: str, command: BodyRestoreCommandV1
    ) -> tuple[
        dict[str, object],
        tuple[
            NoteDetailV1,
            NoteSavepointV1,
            tuple[NoteRevisionV1, ...],
            NoteSavepointV1,
        ],
    ]:
        claims = self._open(authorization_token, "restore")
        expected = {
            "command_id": command.command_id,
            "note_id": command.note_id,
            "room": command.room_name,
            "savepoint_id": command.savepoint_id,
            "expected_revision_head": command.expected_revision_head,
            "epoch": command.expected_collaboration_epoch,
            "idempotency_key": command.idempotency_key,
            "request_fingerprint": command.request_fingerprint,
        }
        try:
            actual = {key: claims[key] for key in expected}
            matches = hmac.compare_digest(
                json.dumps(actual, sort_keys=True, separators=(",", ":")),
                json.dumps(expected, sort_keys=True, separators=(",", ":")),
            )
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise NotesError(
                "access_denied",
                "Restore source authorization does not match command",
                403,
            )
        loaded = self.owner.load_restore_context(
            actor_id=str(claims["actor_id"]),
            note_id=command.note_id,
            command=NoteBodyRestoreRequestV1(
                savepoint_id=command.savepoint_id,
                expected_revision_head=command.expected_revision_head,
                expected_collaboration_epoch=command.expected_collaboration_epoch,
                idempotency_key=command.idempotency_key,
            ),
        )
        return claims, loaded

    @staticmethod
    def _restore_request_fingerprint(command: NoteBodyRestoreRequestV1) -> str:
        payload = command.model_dump(mode="json", exclude={"idempotency_key"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def restore_body(
        self, actor_id: str, note_id: str, command: NoteBodyRestoreRequestV1
    ) -> BodyRestoreResultV1:
        replay = self.owner.replay_body_restore(
            actor_id=actor_id, note_id=note_id, command=command
        )
        if replay is not None:
            return replay
        note, _ = self.owner.validate_body_restore(
            actor_id=actor_id, note_id=note_id, command=command
        )
        room_name = self._room_name(note_id, note.collaboration_epoch)
        request_fingerprint = self._restore_request_fingerprint(command)
        command_id = "restore-" + hmac.new(
            self._ticket_key(),
            (
                f"{actor_id}\0{note_id}\0{command.idempotency_key}"
                f"\0{request_fingerprint}"
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        claims = {
            "v": 1,
            "purpose": "restore",
            "actor_id": actor_id,
            "command_id": command_id,
            "note_id": note_id,
            "room": room_name,
            "savepoint_id": command.savepoint_id,
            "expected_revision_head": command.expected_revision_head,
            "epoch": command.expected_collaboration_epoch,
            "idempotency_key": command.idempotency_key,
            "request_fingerprint": request_fingerprint,
            "expires_at": int(time.time()) + 60,
        }
        handoff = BodyRestoreCommandV1(
            command_id=command_id,
            note_id=note_id,
            room_name=room_name,
            savepoint_id=command.savepoint_id,
            expected_revision_head=command.expected_revision_head,
            expected_collaboration_epoch=command.expected_collaboration_epoch,
            idempotency_key=command.idempotency_key,
            request_fingerprint=request_fingerprint,
            authorization_token=self._seal(claims),
        )
        try:
            return self.notifier.restore_body(handoff)
        except (OSError, TimeoutError) as exc:
            raise NotesError(
                "audit_failure", "Notes collaboration carrier is unavailable", 503
            ) from exc

    def get_settings(self, actor_id: str):
        return self.owner.get_settings(actor_id=actor_id, require_admin=True)

    def internal_settings(self):
        return self.owner.get_settings()

    def update_settings(self, actor_id: str, command: NotesSettingsUpdateRequestV1):
        result = self.owner.update_settings(actor_id=actor_id, command=command)
        try:
            self.notifier.reschedule_settings(result.settings_revision)
        except Exception:
            pass
        return result


__all__ = [
    "NoteAttachmentContent",
    "NotesApplicationService",
    "NotesAttachmentProvider",
    "NoopCollaborationNotifier",
    "UnavailableNotesAttachmentProvider",
]
