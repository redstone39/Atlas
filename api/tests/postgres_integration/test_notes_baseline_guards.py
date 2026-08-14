from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from atlas_production.infrastructure.postgres_runtime import PostgresRuntime


CATEGORY_ID = "category-notes-baseline-guards"
NOTE_ID = "note-notes-baseline-guards"
REVISION_ID = "revision-notes-baseline-guards"
SAVEPOINT_ID = "savepoint-notes-baseline-guards"
ATTACHMENT_ID = "natt-notes-baseline-guards"


def _execute_rejected(runtime: PostgresRuntime, statement: str) -> None:
    with pytest.raises(DBAPIError):
        with runtime.engine.begin() as connection:
            connection.execute(text(statement))


def test_notes_baseline_rejects_history_mutation_and_physical_delete(
    postgres_runtime: PostgresRuntime,
) -> None:
    postgres_runtime.bootstrap_schema()
    with postgres_runtime.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO atlas_note_categories (
                    category_id, scope_type, scope_id, name, lifecycle_status,
                    metadata_revision, created_actor_id, created_at,
                    updated_actor_id, updated_at, trashed_actor_id, trashed_at
                ) VALUES (
                    :category_id, 'project', 'project-notes-baseline-guards',
                    'Baseline guard category', 'active', 1, 'user-guard',
                    CURRENT_TIMESTAMP, 'user-guard', CURRENT_TIMESTAMP, NULL, NULL
                )
                ON CONFLICT (category_id) DO NOTHING
                """
            ),
            {"category_id": CATEGORY_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO atlas_notes (
                    note_id, scope_type, scope_id, category_id, title,
                    lifecycle_status, metadata_revision, accepted_update_head,
                    savepoint_head, collaboration_epoch, created_actor_id,
                    created_at, updated_actor_id, updated_at,
                    trashed_actor_id, trashed_at
                ) VALUES (
                    :note_id, 'project', 'project-notes-baseline-guards', NULL,
                    'Baseline guard note', 'active', 1, 1, 1, 1,
                    'user-guard', CURRENT_TIMESTAMP, 'user-guard',
                    CURRENT_TIMESTAMP, NULL, NULL
                )
                ON CONFLICT (note_id) DO NOTHING
                """
            ),
            {"note_id": NOTE_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO atlas_note_revisions (
                    revision_id, note_id, sequence, server_timestamp, actor_id,
                    event_kind, raw_yjs_update, before_digest, after_digest,
                    change_set, restore_source_savepoint_id
                ) VALUES (
                    :revision_id, :note_id, 1, CURRENT_TIMESTAMP, 'user-guard',
                    'create', decode('', 'hex'), :digest, :digest,
                    '{}'::jsonb, NULL
                )
                ON CONFLICT (revision_id) DO NOTHING
                """
            ),
            {
                "revision_id": REVISION_ID,
                "note_id": NOTE_ID,
                "digest": "a" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO atlas_note_attachments (
                    attachment_id, note_id, artifact_id, mime_type, byte_size,
                    sha256, width, height, idempotency_key,
                    request_fingerprint, created_actor_id, created_at
                ) VALUES (
                    :attachment_id, :note_id, 'artifact-note-image-guard',
                    'image/png', 8, :digest, 2, 3, 'paste-guard',
                    :fingerprint, 'user-guard', CURRENT_TIMESTAMP
                )
                ON CONFLICT (attachment_id) DO NOTHING
                """
            ),
            {
                "attachment_id": ATTACHMENT_ID,
                "note_id": NOTE_ID,
                "digest": "b" * 64,
                "fingerprint": "c" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO atlas_note_savepoints (
                    savepoint_id, note_id, sequence, covered_revision,
                    encoded_yjs_state, canonical_body, document_schema,
                    body_digest, aggregate_change_set, contributor_actor_ids,
                    created_at
                ) VALUES (
                    :savepoint_id, :note_id, 1, 1, decode('', 'hex'),
                    '{}'::jsonb, 'prosemirror-v1', :digest, '{}'::jsonb,
                    '["user-guard"]'::jsonb, CURRENT_TIMESTAMP
                )
                ON CONFLICT (savepoint_id) DO NOTHING
                """
            ),
            {
                "savepoint_id": SAVEPOINT_ID,
                "note_id": NOTE_ID,
                "digest": "a" * 64,
            },
        )

    for statement in (
        f"UPDATE atlas_note_revisions SET actor_id = 'attacker' WHERE revision_id = '{REVISION_ID}'",
        f"DELETE FROM atlas_note_revisions WHERE revision_id = '{REVISION_ID}'",
        f"UPDATE atlas_note_savepoints SET document_schema = 'mutated' WHERE savepoint_id = '{SAVEPOINT_ID}'",
        f"DELETE FROM atlas_note_savepoints WHERE savepoint_id = '{SAVEPOINT_ID}'",
        f"UPDATE atlas_note_attachments SET width = 4 WHERE attachment_id = '{ATTACHMENT_ID}'",
        f"DELETE FROM atlas_note_attachments WHERE attachment_id = '{ATTACHMENT_ID}'",
        f"DELETE FROM atlas_notes WHERE note_id = '{NOTE_ID}'",
        f"DELETE FROM atlas_note_categories WHERE category_id = '{CATEGORY_ID}'",
    ):
        _execute_rejected(postgres_runtime, statement)

    with postgres_runtime.engine.connect() as connection:
        assert connection.execute(
            text("SELECT actor_id FROM atlas_note_revisions WHERE revision_id = :id"),
            {"id": REVISION_ID},
        ).scalar_one() == "user-guard"
        assert connection.execute(
            text("SELECT document_schema FROM atlas_note_savepoints WHERE savepoint_id = :id"),
            {"id": SAVEPOINT_ID},
        ).scalar_one() == "prosemirror-v1"
        assert connection.execute(
            text("SELECT count(*) FROM atlas_notes WHERE note_id = :id"),
            {"id": NOTE_ID},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT width FROM atlas_note_attachments WHERE attachment_id = :id"),
            {"id": ATTACHMENT_ID},
        ).scalar_one() == 2
        assert connection.execute(
            text("SELECT count(*) FROM atlas_note_categories WHERE category_id = :id"),
            {"id": CATEGORY_ID},
        ).scalar_one() == 1
