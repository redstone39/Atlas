"""Notes-owned attachment binding over the shared immutable artifact store."""

from __future__ import annotations

import hashlib
import json

from atlas_production.infrastructure.bounded_artifact_writer import BoundedArtifactWriter
from atlas_production.infrastructure.postgres_owner.notes import PostgresNotesOwner
from atlas_production.modules.artifact_storage.errors import (
    ArtifactIntegrityError,
    ArtifactStorageError,
    ArtifactStorageUnavailable,
)
from atlas_production.modules.notes.images import inspect_note_image
from atlas_production.modules.notes.public import (
    MAX_NOTE_BINARY_BYTES,
    NoteAttachmentV1,
    NotesError,
)
from atlas_production.modules.notes.service import NoteAttachmentContent


def _request_fingerprint(content: bytes, claimed_mime_type: str | None) -> str:
    value = {
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "claimed_mime_type": claimed_mime_type,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _attachment_ref(note_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"atlas-note-attachment-v1\0{note_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"natt-{digest[:40]}"


class PostgresNotesAttachmentProvider:
    def __init__(
        self, owner: PostgresNotesOwner, artifact_writer: BoundedArtifactWriter
    ) -> None:
        self.owner = owner
        self.artifact_writer = artifact_writer

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
    ) -> NoteAttachmentV1:
        del filename  # Original bytes are retained; client filenames are not persisted.
        mime_type, width, height = inspect_note_image(content, claimed_mime_type)
        request_fingerprint = _request_fingerprint(content, claimed_mime_type)
        note, replay, _artifact_id = self.owner.prepare_attachment_upload(
            actor_id=actor_id,
            note_id=note_id,
            expected_collaboration_epoch=expected_collaboration_epoch,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay

        attachment_ref = _attachment_ref(note_id, idempotency_key)
        try:
            result = self.artifact_writer.write(
                content=content,
                artifact_class="note_image",
                logical_identity=f"notes:{attachment_ref}",
                content_type=mime_type,
                owner_scope_type=note.scope.scope_type,
                owner_scope_id=note.scope.scope_id,
                parent_resource_id=note_id,
                parent_lifecycle_epoch=expected_collaboration_epoch,
                document_version_id=None,
                source_artifact_id=None,
                processing_generation=None,
                pipeline_id="notes",
                pipeline_version="1",
                generation=None,
                authorization_bindings=((note.scope.scope_type, note.scope.scope_id),),
                allow_missing_parent=True,
            )
        except ArtifactStorageError as exc:
            raise NotesError(
                "storage_unavailable", "Notes attachment storage is unavailable", 503
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise NotesError(
                "storage_unavailable", "Notes attachment storage is unavailable", 503
            ) from exc

        return self.owner.bind_attachment(
            actor_id=actor_id,
            note_id=note_id,
            expected_collaboration_epoch=expected_collaboration_epoch,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            attachment_ref=attachment_ref,
            artifact_id=result.artifact.artifact_id,
            mime_type=mime_type,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
        )

    def open(
        self, *, actor_id: str, note_id: str, attachment_ref: str
    ) -> NoteAttachmentContent:
        attachment, artifact_id, read_lease_id = self.owner.authorize_attachment_open(
            actor_id=actor_id,
            note_id=note_id,
            attachment_ref=attachment_ref,
        )
        try:
            content = self.artifact_writer.read_active_artifact(
                artifact_id,
                read_lease_id=read_lease_id,
                expected_artifact_class="note_image",
                expected_parent_resource_id=note_id,
                expected_content_type=attachment.mime_type,
                expected_byte_size=attachment.byte_size,
                expected_sha256=attachment.sha256,
                max_bytes=MAX_NOTE_BINARY_BYTES,
            )
        except ArtifactStorageUnavailable as exc:
            raise NotesError(
                "storage_unavailable", "Notes attachment storage is unavailable", 503
            ) from exc
        except ArtifactIntegrityError as exc:
            raise NotesError(
                "integrity_failure", "Attachment content failed integrity checks", 503
            ) from exc
        except ArtifactStorageError as exc:
            raise NotesError(
                "integrity_failure", "Attachment content failed integrity checks", 503
            ) from exc
        except ValueError as exc:
            raise NotesError(
                "integrity_failure", "Attachment content failed integrity checks", 503
            ) from exc
        except RuntimeError as exc:
            raise NotesError(
                "storage_unavailable", "Notes attachment storage is unavailable", 503
            ) from exc
        finally:
            self.artifact_writer.complete_read_lease(read_lease_id)
        return NoteAttachmentContent(content=content, mime_type=attachment.mime_type)


__all__ = ["PostgresNotesAttachmentProvider"]
