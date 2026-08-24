from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from atlas_production.infrastructure.persistence.schema import OrmBase
from atlas_production.modules.notes.public import (
    NoteAttachmentV1,
    NoteCategoryRestoreRequestV1,
    NoteCategoryTrashRequestV1,
    NoteCategoryUpdateRequestV1,
    NoteCreateRequestV1,
    NoteMoveChangeV1,
    NoteTextChangeV1,
    NoteMetadataUpdateRequestV1,
    NotesSettingsUpdateRequestV1,
)


NOTES_TABLES = {
    "atlas_notes",
    "atlas_note_categories",
    "atlas_note_revisions",
    "atlas_note_savepoints",
    "atlas_note_attachments",
    "atlas_notes_settings",
}


def _constraint_names(table_name: str, constraint_type: type[object]) -> set[str]:
    return {
        constraint.name
        for constraint in OrmBase.metadata.tables[table_name].constraints
        if isinstance(constraint, constraint_type) and constraint.name is not None
    }


def test_notes_mutation_contracts_are_closed_and_require_conflict_keys() -> None:
    command = NoteCreateRequestV1(
        scope_type="project",
        scope_id="project-1",
        title="Shared note",
        idempotency_key="idem-1",
    )
    assert command.scope_type == "project"

    with pytest.raises(ValidationError):
        NoteCreateRequestV1.model_validate({**command.model_dump(), "role": "admin"})
    with pytest.raises(ValidationError):
        NoteMetadataUpdateRequestV1(
            title="Updated",
            expected_metadata_revision=0,
            idempotency_key="idem-2",
        )
    for category_request in (
        NoteCategoryUpdateRequestV1,
        NoteCategoryTrashRequestV1,
        NoteCategoryRestoreRequestV1,
    ):
        payload: dict[str, object] = {
            "expected_metadata_revision": 0,
            "idempotency_key": "idem-category",
        }
        if category_request is NoteCategoryUpdateRequestV1:
            payload["name"] = "Updated category"
        with pytest.raises(ValidationError):
            category_request.model_validate(payload)
    with pytest.raises(ValidationError):
        NotesSettingsUpdateRequestV1(
            checkpoint_interval_seconds=0,
            expected_settings_revision=1,
            idempotency_key="idem-3",
        )

    text_change = NoteTextChangeV1(
        change="replace",
        path=(2, 0),
        before="before",
        after="after",
        from_offset=0,
        to_offset=6,
    )
    assert text_change.path == (2, 0)
    with pytest.raises(ValidationError):
        NoteTextChangeV1.model_validate({
            key: value
            for key, value in text_change.model_dump(mode="json").items()
            if key != "path"
        })

    move = NoteMoveChangeV1(block_id="block-1", from_path=(0,), to_path=(2,))
    assert move.block_id == "block-1"
    with pytest.raises(ValidationError):
        NoteMoveChangeV1(block_id="block-1", from_path=(-1,), to_path=(2,))
    with pytest.raises(ValidationError):
        NoteMoveChangeV1(block_id="block-1", from_path=(0, 1), to_path=(2,))
    attachment = NoteAttachmentV1(
        attachment_ref="natt-1",
        mime_type="image/png",
        byte_size=8,
        sha256="a" * 64,
        width=1,
        height=1,
    )
    assert attachment.state == "ready"


def test_notes_owner_tables_register_exact_scope_and_monotonic_invariants() -> None:
    assert NOTES_TABLES <= set(OrmBase.metadata.tables)
    assert "fk_atlas_note_exact_scope_category" in _constraint_names(
        "atlas_notes", ForeignKeyConstraint
    )
    assert "ck_atlas_note_heads" in _constraint_names(
        "atlas_notes", CheckConstraint
    )
    assert "ck_atlas_note_category_metadata_revision" in _constraint_names(
        "atlas_note_categories", CheckConstraint
    )
    assert "metadata_revision" in OrmBase.metadata.tables[
        "atlas_note_categories"
    ].columns
    assert "uq_atlas_note_revision_sequence" in _constraint_names(
        "atlas_note_revisions", UniqueConstraint
    )
    assert "uq_atlas_note_savepoint_sequence" in _constraint_names(
        "atlas_note_savepoints", UniqueConstraint
    )
    assert "ck_atlas_notes_settings_positive_interval" in _constraint_names(
        "atlas_notes_settings", CheckConstraint
    )
    assert "ck_atlas_note_attachment_mime" in _constraint_names(
        "atlas_note_attachments", CheckConstraint
    )
    assert "uq_atlas_note_attachment_idempotency" in _constraint_names(
        "atlas_note_attachments", UniqueConstraint
    )

    for table_name in NOTES_TABLES:
        for foreign_key in OrmBase.metadata.tables[table_name].foreign_keys:
            assert foreign_key.ondelete is None


def test_development_baseline_guards_journal_and_savepoints_from_mutation() -> None:
    migration = (
        Path(__file__).parents[1]
        / "src/atlas_production/migrations/versions/20260711_0001_development_baseline.py"
    ).read_text(encoding="utf-8")

    assert "BEFORE UPDATE OR DELETE ON atlas_note_revisions" in migration
    assert "BEFORE UPDATE OR DELETE ON atlas_note_savepoints" in migration
    assert "BEFORE UPDATE OR DELETE ON atlas_note_attachments" in migration
    assert "BEFORE DELETE ON atlas_notes" in migration
    assert "BEFORE DELETE ON atlas_note_categories" in migration
    assert "atlas_reject_notes_append_only_mutation" in migration
    assert "VALUES ('global', 30, 1" in migration
    assert "'note_image'" in migration
