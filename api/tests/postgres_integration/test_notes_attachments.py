from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import delete

from atlas_production.infrastructure.artifact_storage_config_adapter import (
    RootOnlyStorageTargetConfig,
)
from atlas_production.infrastructure.artifact_storage_filesystem_adapter import (
    LocalArtifactFilesystemAdapter,
)
from atlas_production.infrastructure.bounded_artifact_writer import BoundedArtifactWriter
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactStorageControlRow,
    AtlasArtifactStorageTargetRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_notes_attachments import (
    PostgresNotesAttachmentProvider,
)
from atlas_production.infrastructure.postgres_owner.notes import PostgresNotesOwner
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.notes.public import (
    NoteCreateRequestV1,
    NoteTrashRequestV1,
    NotesError,
)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (9, 7), color=(1, 2, 3)).save(output, format="PNG")
    return output.getvalue()


def _seed(runtime: PostgresRuntime, tmp_path: Path) -> PostgresNotesAttachmentProvider:
    runtime.bootstrap_schema()
    storage_root = tmp_path / "artifact-root"
    storage_root.mkdir()
    filesystem = LocalArtifactFilesystemAdapter(
        storage_root, allowlisted_parents=(tmp_path,), create_layout=True
    )
    config_directory = tmp_path / "artifact-config"
    config_key = RootOnlyStorageTargetConfig(config_directory).put_target(
        target_id="target-notes-test",
        revision=1,
        kind="local",
        raw_path=str(storage_root),
    )
    with runtime.session_factory() as session:
        session.execute(delete(AtlasArtifactStorageControlRow))
        session.add(
            AtlasArtifactStorageTargetRow(
                target_id="target-notes-test",
                target_revision=1,
                target_kind="local",
                masked_label="Notes test",
                config_key=config_key,
                root_identity_digest=filesystem.root_identity_digest,
                capabilities={
                    "create_file": True,
                    "modify_file": True,
                    "remove_file": True,
                },
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                updated_at="2026-08-13T00:00:00+00:00",
                created_by="notes-test",
                verification_mode="full_hash",
                evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
                failure_code=None,
                registration_idempotency_key=None,
                registration_request_fingerprint=None,
            )
        )
        session.flush()
        session.add(
            AtlasArtifactStorageControlRow(
                control_id="global",
                mode="active",
                active_target_id="target-notes-test",
                active_target_revision=1,
                root_identity_digest=filesystem.root_identity_digest,
                storage_epoch=2,
                updated_at="2026-08-13T00:00:00+00:00",
            )
        )
        session.add(
            AtlasUserRow(
                actor_id="user-notes-attachment",
                display_name="Notes Attachment",
                email="notes-attachment@example.test",
                system_role="member",
                password_digest=None,
                active=True,
                actor_type="user",
                created_at="2026-08-13T00:00:00+00:00",
            )
        )
        session.add(
            AtlasProjectRow(
                project_id="project-notes-attachment",
                name="Notes attachment project",
                policy_profile_id="default",
            )
        )
        session.add(
            AtlasPermissionGrantRow(
                grant_id="grant-notes-attachment",
                project_id="project-notes-attachment",
                subject_type="user",
                subject_id="user-notes-attachment",
                role="viewer",
                effect="allow",
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                revoked_at=None,
            )
        )
        session.commit()
    writer = BoundedArtifactWriter(
        runtime.engine,
        target_config_directory=str(config_directory),
        allowlisted_parents=(str(tmp_path),),
    )
    owner = PostgresNotesOwner(runtime.session_factory, writer)
    return PostgresNotesAttachmentProvider(
        owner,
        writer,
    )


def test_notes_attachment_upload_replay_open_trash_and_exact_note_binding(
    postgres_runtime: PostgresRuntime, tmp_path: Path
) -> None:
    provider = _seed(postgres_runtime, tmp_path)
    owner = provider.owner
    first = owner.create_note(
        actor_id="user-notes-attachment",
        command=NoteCreateRequestV1(
            note_id="note-attachment-storage",
            scope_type="project",
            scope_id="project-notes-attachment",
            title="Attachment storage",
            idempotency_key="create-attachment-storage",
        ),
    )
    owner.create_note(
        actor_id="user-notes-attachment",
        command=NoteCreateRequestV1(
            note_id="note-attachment-storage-other",
            scope_type="project",
            scope_id="project-notes-attachment",
            title="Other note",
            idempotency_key="create-attachment-storage-other",
        ),
    )
    content = _png()
    attachment = provider.upload(
        actor_id="user-notes-attachment",
        note_id=first.note_id,
        expected_collaboration_epoch=first.collaboration_epoch,
        idempotency_key="paste-image-1",
        filename="screen.png",
        claimed_mime_type="image/png",
        content=content,
    )
    replay = provider.upload(
        actor_id="user-notes-attachment",
        note_id=first.note_id,
        expected_collaboration_epoch=first.collaboration_epoch,
        idempotency_key="paste-image-1",
        filename="renamed.png",
        claimed_mime_type="image/png",
        content=content,
    )
    assert replay == attachment
    assert provider.open(
        actor_id="user-notes-attachment",
        note_id=first.note_id,
        attachment_ref=attachment.attachment_ref,
    ).content == content

    with pytest.raises(NotesError) as cross_note:
        provider.open(
            actor_id="user-notes-attachment",
            note_id="note-attachment-storage-other",
            attachment_ref=attachment.attachment_ref,
        )
    assert cross_note.value.code == "note_not_found"

    trashed = owner.trash_note(
        actor_id="user-notes-attachment",
        note_id=first.note_id,
        command=NoteTrashRequestV1(
            expected_metadata_revision=first.metadata_revision,
            idempotency_key="trash-attachment-storage",
        ),
    )
    assert trashed.lifecycle_status == "trashed"
    assert provider.open(
        actor_id="user-notes-attachment",
        note_id=first.note_id,
        attachment_ref=attachment.attachment_ref,
    ).content == content

    with postgres_runtime.session_factory() as session:
        grant = session.get(AtlasPermissionGrantRow, "grant-notes-attachment")
        assert grant is not None
        grant.status = "revoked"
        grant.revoked_at = "2026-08-13T01:00:00+00:00"
        session.commit()
    with pytest.raises(NotesError) as revoked:
        provider.open(
            actor_id="user-notes-attachment",
            note_id=first.note_id,
            attachment_ref=attachment.attachment_ref,
        )
    assert revoked.value.code == "access_denied"
