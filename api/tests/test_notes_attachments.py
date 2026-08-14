from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from atlas_production.infrastructure.postgres_notes_attachments import (
    PostgresNotesAttachmentProvider,
)
from atlas_production.modules.artifact_storage.errors import ArtifactStorageUnavailable
from atlas_production.modules.notes.public import NoteAttachmentV1
from atlas_production.modules.notes.public import NotesError


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (5, 4), color=(1, 2, 3)).save(output, format="PNG")
    return output.getvalue()


class _Owner:
    def __init__(self) -> None:
        self.bound: dict[str, object] | None = None

    def prepare_attachment_upload(self, **_kwargs: object):
        note = SimpleNamespace(
            scope=SimpleNamespace(scope_type="project", scope_id="project-1")
        )
        return note, None, None

    def bind_attachment(self, **kwargs: object) -> NoteAttachmentV1:
        self.bound = kwargs
        return NoteAttachmentV1(
            attachment_ref=str(kwargs["attachment_ref"]),
            mime_type=str(kwargs["mime_type"]),
            byte_size=int(kwargs["byte_size"]),
            sha256=str(kwargs["sha256"]),
            width=int(kwargs["width"]),
            height=int(kwargs["height"]),
        )

    def authorize_attachment_open(self, **_kwargs: object):
        assert self.bound is not None
        attachment = NoteAttachmentV1(
            attachment_ref=str(self.bound["attachment_ref"]),
            mime_type=str(self.bound["mime_type"]),
            byte_size=int(self.bound["byte_size"]),
            sha256=str(self.bound["sha256"]),
            width=int(self.bound["width"]),
            height=int(self.bound["height"]),
        )
        return attachment, "artifact-1", "lease-1"


class _Writer:
    def __init__(self) -> None:
        self.content: bytes | None = None
        self.write_args: dict[str, object] | None = None
        self.read_args: dict[str, object] | None = None
        self.completed_lease: str | None = None

    def write(self, **kwargs: object):
        self.write_args = kwargs
        self.content = bytes(kwargs["content"])
        return SimpleNamespace(artifact=SimpleNamespace(artifact_id="artifact-1"))

    def read_active_artifact(self, artifact_id: str, **kwargs: object) -> bytes:
        assert artifact_id == "artifact-1"
        self.read_args = kwargs
        assert self.content is not None
        return self.content

    def complete_read_lease(self, read_lease_id: str) -> None:
        self.completed_lease = read_lease_id


def test_upload_finalizes_artifact_before_binding_and_open_rechecks_integrity() -> None:
    owner = _Owner()
    writer = _Writer()
    provider = PostgresNotesAttachmentProvider(owner, writer)  # type: ignore[arg-type]
    content = _png()

    attachment = provider.upload(
        actor_id="user-1",
        note_id="note-1",
        expected_collaboration_epoch=7,
        idempotency_key="upload-1",
        filename="private-name.png",
        claimed_mime_type="image/png",
        content=content,
    )

    assert owner.bound is not None
    assert writer.write_args is not None
    assert writer.write_args["artifact_class"] == "note_image"
    assert writer.write_args["allow_missing_parent"] is True
    assert "private-name.png" not in repr(writer.write_args)
    assert "content" not in owner.bound
    assert attachment.attachment_ref.startswith("natt-")

    opened = provider.open(
        actor_id="user-1", note_id="note-1", attachment_ref=attachment.attachment_ref
    )

    assert opened.content == content
    assert writer.read_args == {
        "read_lease_id": "lease-1",
        "expected_artifact_class": "note_image",
        "expected_parent_resource_id": "note-1",
        "expected_content_type": "image/png",
        "expected_byte_size": len(content),
        "expected_sha256": attachment.sha256,
        "max_bytes": 16 * 1024 * 1024,
    }
    assert writer.completed_lease == "lease-1"


def test_open_maps_storage_unavailable_separately_from_integrity_failure() -> None:
    owner = _Owner()
    writer = _Writer()
    provider = PostgresNotesAttachmentProvider(owner, writer)  # type: ignore[arg-type]
    provider.upload(
        actor_id="user-1",
        note_id="note-1",
        expected_collaboration_epoch=7,
        idempotency_key="upload-1",
        filename=None,
        claimed_mime_type="image/png",
        content=_png(),
    )

    def unavailable(_artifact_id: str, **_kwargs: object) -> bytes:
        raise ArtifactStorageUnavailable("artifact.storage_is_temporarily_unavailable")

    writer.read_active_artifact = unavailable  # type: ignore[method-assign]
    with pytest.raises(NotesError) as caught:
        provider.open(actor_id="user-1", note_id="note-1", attachment_ref="natt-any")
    assert caught.value.code == "storage_unavailable"
    assert writer.completed_lease == "lease-1"
