from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow, add_event_rows
from atlas_production.infrastructure.persistence.identity_access import AtlasTeamRow, AtlasUserRow
from atlas_production.infrastructure.persistence.notes import (
    AtlasNoteAttachmentRow, AtlasNoteCategoryRow, AtlasNoteRevisionRow, AtlasNoteRow,
    AtlasNoteSavepointRow, AtlasNotesSettingsRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    PostgresNotesMembershipAuthority,
)
from atlas_production.modules.notes.public import (
    AcceptNoteRevisionRequestV1, BodyRestoreResultV1,
    CommitNoteBodyRestoreRequestV1, CreateNoteSavepointRequestV1,
    LifecycleStatus, MAX_NOTE_BINARY_BYTES, NoteBodyRestoreRequestV1,
    NoteCategoryCreateRequestV1, NoteCategoryRestoreRequestV1,
    NoteCategoryTrashRequestV1, NoteCategoryUpdateRequestV1, NoteCategoryV1,
    NoteAttachmentV1, NoteChangeSetV1, NoteCreateRequestV1, NoteDetailV1,
    NoteMetadataUpdateRequestV1, NoteRestoreRequestV1,
    NoteRevisionHistoryV1, NoteRevisionV1, NoteSavepointPreviewV1,
    NoteSavepointSummaryV1, NoteSavepointV1, NoteScopeRefV1, NoteSummaryV1,
    NoteTrashRequestV1, NotesError, NotesSettingsUpdateRequestV1,
    NotesSettingsV1, ScopeType,
)
from atlas_production.modules.notes.document_schema import (
    NOTE_DOCUMENT_SCHEMA_V2,
    NoteDocumentValidationError,
    validate_note_document,
)

SessionFactory = Callable[[], Session]
MAX_BINARY_BYTES = MAX_NOTE_BINARY_BYTES
MAX_JSON_BYTES = 1024 * 1024
EMPTY_BODY = {"type": "doc", "content": []}
EMPTY_STATE = b"\x00\x00"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _fingerprint(value: object) -> str:
    def safe(item: object) -> object:
        if isinstance(item, bytes):
            return {"length": len(item), "sha256": hashlib.sha256(item).hexdigest()}
        if isinstance(item, dict):
            return {str(k): safe(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [safe(v) for v in item]
        return item
    return _digest(safe(value))


def _error(code: str, message: str, status: int) -> NotesError:
    return NotesError(code, message, status)  # type: ignore[arg-type]


def _validate_body(canonical_body: object, document_schema: str) -> frozenset[str]:
    try:
        return validate_note_document(canonical_body, document_schema)
    except NoteDocumentValidationError as exc:
        raise _error("invalid_document_schema", str(exc), 422) from exc


def _receipt(actor_id: str, operation: str, target_ref: str, key: str) -> str:
    raw = f"notes-v1\0{actor_id}\0{operation}\0{target_ref}\0{key}".encode()
    return f"audit-notes-{hashlib.sha256(raw).hexdigest()}"


class PostgresNotesOwner:
    """Sole transaction owner for Notes state, authority rechecks and audit."""

    def __init__(self, session_factory: SessionFactory, artifact_reader: object | None = None) -> None:
        self.session_factory = session_factory
        self.artifact_reader = artifact_reader

    def _tx(self, callback: Callable[[Session], Any]) -> Any:
        session = self.session_factory()
        with session:
            try:
                with session.begin():
                    return callback(session)
            except NotesError:
                raise
            except Exception as exc:
                raise _error("audit_failure", "Notes transaction failed closed", 503) from exc

    @staticmethod
    def _authorize(session: Session, *, actor_id: str, scope_type: ScopeType,
                   scope_id: str, lock: bool = False, admin: bool = False) -> bool:
        query = select(AtlasUserRow).where(AtlasUserRow.actor_id == actor_id)
        actor = session.scalar(query.with_for_update() if lock else query)
        if actor is None or actor.actor_type != "user":
            raise _error("actor_not_human", "Notes require an active human actor", 403)
        if not actor.active:
            raise _error("access_denied", "Current Notes membership is required", 403)
        if admin:
            if actor.system_role != "admin":
                raise _error("access_denied", "System Admin access is required", 403)
            return True
        if scope_type == "project":
            decision = ActionAwareAclAuthority.resolve_in_session(
                session, actor_type="user", actor_id=actor_id, project_id=scope_id,
                action="notes_membership", lock_rows=lock,
            )
            if decision.reason == "project_missing":
                raise _error("scope_not_found", "Notes scope was not found", 404)
            if not decision.allowed:
                raise _error("access_denied", "Current Notes membership is required", 403)
            return decision.reason == "system_admin"
        team_query = select(AtlasTeamRow).where(AtlasTeamRow.team_id == scope_id)
        team = session.scalar(team_query.with_for_update() if lock else team_query)
        if team is None or team.status != "active":
            raise _error("scope_not_found", "Notes scope was not found", 404)
        if actor.system_role == "admin":
            return True
        team_ids, invalid = (
            PostgresNotesMembershipAuthority._actor_team_notes_memberships_with_validity(
                session,
                actor_type="user",
                actor_id=actor_id,
                lock_rows=lock,
            )
        )
        if invalid or scope_id not in team_ids:
            raise _error("access_denied", "Current Notes membership is required", 403)
        return False

    @staticmethod
    def _locks(session: Session, *keys: str) -> None:
        acquire_owner_locks(session, identity_keys=[f"notes:{key}" for key in keys])

    def _note_scope(self, session: Session, actor_id: str, note_id: str, *, write: bool = False) -> tuple[ScopeType, str]:
        query = select(AtlasNoteRow.scope_type, AtlasNoteRow.scope_id).where(AtlasNoteRow.note_id == note_id)
        found = session.execute(query.with_for_update() if write else query).one_or_none()
        if found is None:
            raise _error("note_not_found", "Note was not found", 404)
        scope_type, scope_id = found
        if write:
            self._locks(session, f"actor:{actor_id}", f"scope:{scope_type}:{scope_id}", f"note:{note_id}")
        self._authorize(session, actor_id=actor_id, scope_type=scope_type, scope_id=scope_id, lock=write)
        return scope_type, scope_id

    @staticmethod
    def _attachment(row: AtlasNoteAttachmentRow) -> NoteAttachmentV1:
        return NoteAttachmentV1(
            attachment_ref=row.attachment_id,
            mime_type=row.mime_type,
            byte_size=row.byte_size,
            sha256=row.sha256,
            width=row.width,
            height=row.height,
        )

    @staticmethod
    def _validate_attachment_refs(
        session: Session, note_id: str, attachment_refs: frozenset[str]
    ) -> None:
        if not attachment_refs:
            return
        rows = tuple(
            session.scalars(
                select(AtlasNoteAttachmentRow).where(
                    AtlasNoteAttachmentRow.note_id == note_id,
                    AtlasNoteAttachmentRow.attachment_id.in_(attachment_refs),
                )
            )
        )
        if {row.attachment_id for row in rows} != set(attachment_refs):
            raise _error(
                "invalid_attachment",
                "Every image must reference a finalized attachment for this note",
                422,
            )

    def prepare_attachment_upload(
        self,
        *,
        actor_id: str,
        note_id: str,
        expected_collaboration_epoch: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[NoteDetailV1, NoteAttachmentV1 | None, str | None]:
        def run(session: Session):
            self._note_scope(session, actor_id, note_id, write=True)
            note = session.get(AtlasNoteRow, note_id)
            assert note is not None
            if note.lifecycle_status != "active":
                raise _error("note_trashed", "Trashed notes are read-only", 409)
            if note.collaboration_epoch != expected_collaboration_epoch:
                raise _error("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
            existing = session.scalar(
                select(AtlasNoteAttachmentRow).where(
                    AtlasNoteAttachmentRow.note_id == note_id,
                    AtlasNoteAttachmentRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise _error(
                        "idempotency_payload_conflict",
                        "Idempotency key payload conflicts",
                        409,
                    )
                return self._note(session, note), self._attachment(existing), existing.artifact_id
            return self._note(session, note), None, None

        return self._tx(run)

    def bind_attachment(
        self,
        *,
        actor_id: str,
        note_id: str,
        expected_collaboration_epoch: int,
        idempotency_key: str,
        request_fingerprint: str,
        attachment_ref: str,
        artifact_id: str,
        mime_type: str,
        byte_size: int,
        sha256: str,
        width: int,
        height: int,
    ) -> NoteAttachmentV1:
        def run(session: Session):
            self._note_scope(session, actor_id, note_id, write=True)
            note = session.get(AtlasNoteRow, note_id)
            assert note is not None
            if note.lifecycle_status != "active":
                raise _error("note_trashed", "Trashed notes are read-only", 409)
            if note.collaboration_epoch != expected_collaboration_epoch:
                raise _error("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
            existing = session.scalar(
                select(AtlasNoteAttachmentRow).where(
                    AtlasNoteAttachmentRow.note_id == note_id,
                    AtlasNoteAttachmentRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise _error("idempotency_payload_conflict", "Idempotency key payload conflicts", 409)
                return self._attachment(existing)
            row = AtlasNoteAttachmentRow(
                attachment_id=attachment_ref,
                note_id=note_id,
                artifact_id=artifact_id,
                mime_type=mime_type,
                byte_size=byte_size,
                sha256=sha256,
                width=width,
                height=height,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                created_actor_id=actor_id,
                created_at=_now(),
            )
            session.add(row)
            session.flush()
            self._audit(
                session,
                event_type="note_attachment_created",
                actor_id=actor_id,
                target_ref=attachment_ref,
                scope_type=note.scope_type,
                scope_id=note.scope_id,
                metadata={
                    "operation": "attachment_create",
                    "request_fingerprint": request_fingerprint,
                    "note_id": note_id,
                    "event_kind": "attachment",
                },
                event_id=_receipt(actor_id, "attachment_create", note_id, idempotency_key),
            )
            return self._attachment(row)

        return self._tx(run)

    def authorize_attachment_open(
        self, *, actor_id: str, note_id: str, attachment_ref: str
    ) -> tuple[NoteAttachmentV1, str]:
        def run(session: Session):
            self._note_scope(session, actor_id, note_id, write=True)
            note = session.get(AtlasNoteRow, note_id)
            row = session.get(AtlasNoteAttachmentRow, attachment_ref)
            if note is None or row is None or row.note_id != note_id:
                raise _error("note_not_found", "Attachment was not found", 404)
            return self._attachment(row), row.artifact_id

        return self._tx(run)

    def _category_scope(self, session: Session, actor_id: str, category_id: str, *, write: bool = False) -> tuple[ScopeType, str]:
        query = select(AtlasNoteCategoryRow.scope_type, AtlasNoteCategoryRow.scope_id).where(AtlasNoteCategoryRow.category_id == category_id)
        found = session.execute(query.with_for_update() if write else query).one_or_none()
        if found is None:
            raise _error("category_not_found", "Note category was not found", 404)
        scope_type, scope_id = found
        if write:
            self._locks(session, f"actor:{actor_id}", f"scope:{scope_type}:{scope_id}", f"category:{category_id}")
        self._authorize(session, actor_id=actor_id, scope_type=scope_type, scope_id=scope_id, lock=write)
        return scope_type, scope_id

    @staticmethod
    def _scope(session: Session, scope_type: ScopeType, scope_id: str) -> NoteScopeRefV1:
        row = session.get(AtlasProjectRow if scope_type == "project" else AtlasTeamRow, scope_id)
        if row is None:
            raise _error("scope_not_found", "Notes scope was not found", 404)
        return NoteScopeRefV1(scope_type=scope_type, scope_id=scope_id, label=row.name)

    @classmethod
    def _note(cls, session: Session, row: AtlasNoteRow) -> NoteDetailV1:
        return NoteDetailV1(
            note_id=row.note_id, scope=cls._scope(session, row.scope_type, row.scope_id),
            category_id=row.category_id, title=row.title, lifecycle_status=row.lifecycle_status,
            metadata_revision=row.metadata_revision, accepted_update_head=row.accepted_update_head,
            savepoint_head=row.savepoint_head, collaboration_epoch=row.collaboration_epoch,
            created_actor_id=row.created_actor_id, created_at=row.created_at,
            updated_actor_id=row.updated_actor_id, updated_at=row.updated_at,
            trashed_actor_id=row.trashed_actor_id, trashed_at=row.trashed_at,
        )

    @classmethod
    def _note_summary(cls, session: Session, row: AtlasNoteRow) -> NoteSummaryV1:
        detail = cls._note(session, row)
        return NoteSummaryV1.model_validate(
            detail.model_dump(include=set(NoteSummaryV1.model_fields))
        )

    @classmethod
    def _category(cls, session: Session, row: AtlasNoteCategoryRow) -> NoteCategoryV1:
        return NoteCategoryV1(
            category_id=row.category_id, scope=cls._scope(session, row.scope_type, row.scope_id),
            name=row.name, lifecycle_status=row.lifecycle_status,
            metadata_revision=row.metadata_revision, created_actor_id=row.created_actor_id,
            created_at=row.created_at, updated_actor_id=row.updated_actor_id,
            updated_at=row.updated_at, trashed_actor_id=row.trashed_actor_id,
            trashed_at=row.trashed_at,
        )

    @staticmethod
    def _revision(row: AtlasNoteRevisionRow) -> NoteRevisionV1:
        return NoteRevisionV1(revision_id=row.revision_id, note_id=row.note_id,
            sequence=row.sequence, server_timestamp=row.server_timestamp,
            actor_id=row.actor_id, event_kind=row.event_kind,
            raw_yjs_update=row.raw_yjs_update, before_digest=row.before_digest,
            after_digest=row.after_digest, change_set=NoteChangeSetV1.model_validate(row.change_set),
            restore_source_savepoint_id=row.restore_source_savepoint_id)

    @staticmethod
    def _savepoint(row: AtlasNoteSavepointRow) -> NoteSavepointV1:
        return NoteSavepointV1(savepoint_id=row.savepoint_id, note_id=row.note_id,
            sequence=row.sequence, covered_revision=row.covered_revision,
            encoded_yjs_state=row.encoded_yjs_state, canonical_body=row.canonical_body,
            document_schema=row.document_schema, body_digest=row.body_digest,
            aggregate_change_set=NoteChangeSetV1.model_validate(row.aggregate_change_set),
            contributor_actor_ids=tuple(row.contributor_actor_ids), created_at=row.created_at)



    @classmethod
    def _revision_history(cls, row: AtlasNoteRevisionRow) -> NoteRevisionHistoryV1:
        return NoteRevisionHistoryV1.model_validate(
            cls._revision(row).model_dump(exclude={"raw_yjs_update"})
        )

    @classmethod
    def _savepoint_summary(cls, row: AtlasNoteSavepointRow) -> NoteSavepointSummaryV1:
        return NoteSavepointSummaryV1.model_validate(
            cls._savepoint(row).model_dump(
                exclude={"encoded_yjs_state", "canonical_body", "document_schema"}
            )
        )

    @classmethod
    def _savepoint_preview(cls, row: AtlasNoteSavepointRow) -> NoteSavepointPreviewV1:
        return NoteSavepointPreviewV1.model_validate(
            cls._savepoint(row).model_dump(exclude={"encoded_yjs_state"})
        )
    @staticmethod
    def _settings(row: AtlasNotesSettingsRow) -> NotesSettingsV1:
        return NotesSettingsV1(checkpoint_interval_seconds=row.checkpoint_interval_seconds,
            settings_revision=row.settings_revision, updated_actor_id=row.updated_actor_id,
            updated_at=row.updated_at)

    @staticmethod
    def _replay(session: Session, actor_id: str, operation: str, target_ref: str,
                key: str, fingerprint: str) -> AtlasAuditEventRow | None:
        row = session.get(AtlasAuditEventRow, _receipt(actor_id, operation, target_ref, key))
        if row is not None and row.event_metadata.get("request_fingerprint") != fingerprint:
            raise _error("idempotency_payload_conflict", "Idempotency key payload conflicts", 409)
        return row

    @staticmethod
    def _audit(session: Session, *, event_type: str, actor_id: str, target_ref: str,
               scope_type: ScopeType | None, scope_id: str | None,
               metadata: dict[str, object], event_id: str | None = None) -> None:
        event = build_audit_event(event_type=event_type, actor_id=actor_id,
            target_ref=target_ref, project_id=scope_id if scope_type == "project" else None,
            scope_type=scope_type, scope_id=scope_id,
            message_code="common.rejected", metadata=metadata)
        if event_id:
            event = replace(event, event_id=event_id)
        try:
            add_event_rows(session, [event]); session.flush()
        except Exception as exc:
            raise _error("audit_failure", "Required Notes audit failed", 503) from exc

    @staticmethod
    def _valid_category(session: Session, category_id: str | None, scope_type: ScopeType, scope_id: str) -> None:
        if category_id is None: return
        row = session.get(AtlasNoteCategoryRow, category_id)
        if row is None:
            raise _error("category_not_found", "Note category was not found", 404)
        if (row.scope_type, row.scope_id) != (scope_type, scope_id):
            raise _error("cross_scope_category", "Category belongs to another scope", 409)
        if row.lifecycle_status != "active":
            raise _error("category_not_found", "Active note category was not found", 404)

    def list_scopes(self, *, actor_id: str) -> tuple[NoteScopeRefV1, ...]:
        def run(session: Session):
            pairs = [("project", r.project_id, r.name) for r in session.scalars(select(AtlasProjectRow))]
            pairs += [("team", r.team_id, r.name) for r in session.scalars(select(AtlasTeamRow).where(AtlasTeamRow.status == "active"))]
            result = []
            for kind, scope_id, label in pairs:
                try: self._authorize(session, actor_id=actor_id, scope_type=kind, scope_id=scope_id)
                except NotesError as exc:
                    if exc.code == "access_denied": continue
                    raise
                result.append(NoteScopeRefV1(scope_type=kind, scope_id=scope_id, label=label))
            return tuple(sorted(result, key=lambda item: (item.scope_type, item.label, item.scope_id)))
        return self._tx(run)

    def list_notes(self, *, actor_id: str, scope_type: ScopeType, scope_id: str,
                   lifecycle_status: LifecycleStatus, category_id: str | None = None) -> tuple[NoteSummaryV1, ...]:
        def run(session: Session):
            self._authorize(session, actor_id=actor_id, scope_type=scope_type, scope_id=scope_id)
            if category_id: self._valid_category(session, category_id, scope_type, scope_id)
            query = select(AtlasNoteRow).where(AtlasNoteRow.scope_type == scope_type,
                AtlasNoteRow.scope_id == scope_id, AtlasNoteRow.lifecycle_status == lifecycle_status)
            if category_id: query = query.where(AtlasNoteRow.category_id == category_id)
            return tuple(self._note_summary(session, row)
                for row in session.scalars(query.order_by(AtlasNoteRow.updated_at.desc())))
        return self._tx(run)

    def get_note(self, *, actor_id: str, note_id: str) -> NoteDetailV1:
        def run(session: Session):
            self._note_scope(session, actor_id, note_id)
            return self._note(session, session.get(AtlasNoteRow, note_id))
        return self._tx(run)

    def create_note(self, *, actor_id: str, command: NoteCreateRequestV1) -> NoteDetailV1:
        fingerprint = _fingerprint(command.model_dump(mode="json", exclude={"idempotency_key"}))
        def run(session: Session):
            self._locks(session, f"actor:{actor_id}", f"scope:{command.scope_type}:{command.scope_id}", f"note:{command.note_id}")
            self._authorize(session, actor_id=actor_id, scope_type=command.scope_type, scope_id=command.scope_id, lock=True)
            if self._replay(session, actor_id, "note_create", command.note_id, command.idempotency_key, fingerprint):
                row = session.get(AtlasNoteRow, command.note_id)
                if row is None: raise _error("audit_failure", "Accepted replay target unavailable", 503)
                return self._note(session, row)
            if session.get(AtlasNoteRow, command.note_id):
                raise _error("idempotency_payload_conflict", "Note identity exists", 409)
            self._valid_category(session, command.category_id, command.scope_type, command.scope_id)
            at, digest = _now(), _digest(EMPTY_BODY)
            note = AtlasNoteRow(note_id=command.note_id, scope_type=command.scope_type,
                scope_id=command.scope_id, category_id=command.category_id, title=command.title,
                lifecycle_status="active", metadata_revision=1, accepted_update_head=1,
                savepoint_head=1, collaboration_epoch=1, created_actor_id=actor_id,
                created_at=at, updated_actor_id=actor_id, updated_at=at,
                trashed_actor_id=None, trashed_at=None)
            revision_id, savepoint_id = f"nrev-{uuid4().hex}", f"nsp-{uuid4().hex}"
            session.add(note); session.flush()
            session.add(AtlasNoteSavepointRow(savepoint_id=savepoint_id, note_id=command.note_id,
                sequence=1, covered_revision=1, encoded_yjs_state=EMPTY_STATE,
                canonical_body=EMPTY_BODY, document_schema=NOTE_DOCUMENT_SCHEMA_V2,
                body_digest=digest, aggregate_change_set=NoteChangeSetV1().model_dump(mode="json"),
                contributor_actor_ids=[actor_id], created_at=at)); session.flush()
            session.add(AtlasNoteRevisionRow(revision_id=revision_id, note_id=command.note_id,
                sequence=1, server_timestamp=at, actor_id=actor_id, event_kind="create",
                raw_yjs_update=EMPTY_STATE, before_digest=digest, after_digest=digest,
                change_set=NoteChangeSetV1().model_dump(mode="json"), restore_source_savepoint_id=None)); session.flush()
            self._audit(session, event_type="note_created", actor_id=actor_id,
                target_ref=command.note_id, scope_type=command.scope_type, scope_id=command.scope_id,
                metadata={"operation":"note_create","request_fingerprint":fingerprint,
                    "revision":1,"digest":digest,"event_kind":"create"},
                event_id=_receipt(actor_id,"note_create",command.note_id,command.idempotency_key))
            return self._note(session, note)
        return self._tx(run)

    def _mutate_note(self, *, actor_id: str, note_id: str, expected: int, key: str,
                     operation: str, payload: object, event: str,
                     mutate: Callable[[Session, AtlasNoteRow, datetime], None],
                     require_active: bool = False) -> NoteDetailV1:
        fingerprint = _fingerprint(payload)
        def run(session: Session):
            self._note_scope(session, actor_id, note_id, write=True)
            row = session.scalar(select(AtlasNoteRow).where(AtlasNoteRow.note_id == note_id).with_for_update())
            assert row
            if require_active and row.lifecycle_status != "active":
                raise _error("note_trashed", "Trashed notes are read-only", 409)
            if self._replay(session, actor_id, operation, note_id, key, fingerprint): return self._note(session, row)
            if row.metadata_revision != expected:
                raise _error("stale_metadata_revision", "Note metadata revision is stale", 409)
            at = _now(); mutate(session, row, at); row.metadata_revision += 1
            row.updated_actor_id, row.updated_at = actor_id, at; session.flush()
            self._audit(session, event_type=event, actor_id=actor_id, target_ref=note_id,
                scope_type=row.scope_type, scope_id=row.scope_id,
                metadata={"operation":operation,"request_fingerprint":fingerprint,
                    "revision":row.metadata_revision,"collaboration_epoch":row.collaboration_epoch,
                    "event_kind":event}, event_id=_receipt(actor_id,operation,note_id,key))
            return self._note(session, row)
        return self._tx(run)

    def update_note_metadata(self, *, actor_id: str, note_id: str, command: NoteMetadataUpdateRequestV1) -> NoteDetailV1:
        if command.category_id is not None and command.clear_category:
            raise _error("cross_scope_category", "Category cannot be set and cleared", 422)
        def mutate(session: Session, row: AtlasNoteRow, _at: datetime):
            if command.title is not None: row.title = command.title
            if command.clear_category: row.category_id = None
            elif command.category_id is not None:
                self._valid_category(session, command.category_id, row.scope_type, row.scope_id)
                row.category_id = command.category_id
        return self._mutate_note(actor_id=actor_id,note_id=note_id,expected=command.expected_metadata_revision,
            key=command.idempotency_key,operation="note_metadata_update",
            payload=command.model_dump(mode="json",exclude={"idempotency_key"}),
            event="note_metadata_updated",mutate=mutate,require_active=True)

    def trash_note(self, *, actor_id: str, note_id: str, command: NoteTrashRequestV1) -> NoteDetailV1:
        def mutate(_session: Session, row: AtlasNoteRow, at: datetime):
            if row.lifecycle_status != "active": raise _error("note_trashed", "Note already trashed", 409)
            row.lifecycle_status="trashed"; row.trashed_actor_id=actor_id; row.trashed_at=at; row.collaboration_epoch += 1
        return self._mutate_note(actor_id=actor_id,note_id=note_id,expected=command.expected_metadata_revision,
            key=command.idempotency_key,operation="note_trash",payload=command.model_dump(mode="json",exclude={"idempotency_key"}),event="note_trashed",mutate=mutate)

    def restore_note(self, *, actor_id: str, note_id: str, command: NoteRestoreRequestV1) -> NoteDetailV1:
        def mutate(_session: Session, row: AtlasNoteRow, _at: datetime):
            if row.lifecycle_status != "trashed": raise _error("stale_metadata_revision", "Note not trashed", 409)
            row.lifecycle_status="active"; row.trashed_actor_id=None; row.trashed_at=None; row.collaboration_epoch += 1
        return self._mutate_note(actor_id=actor_id,note_id=note_id,expected=command.expected_metadata_revision,
            key=command.idempotency_key,operation="note_restore",payload=command.model_dump(mode="json",exclude={"idempotency_key"}),event="note_restored",mutate=mutate)

    def list_categories(self, *, actor_id: str, scope_type: ScopeType, scope_id: str,
                        lifecycle_status: LifecycleStatus) -> tuple[NoteCategoryV1, ...]:
        def run(session: Session):
            self._authorize(session, actor_id=actor_id, scope_type=scope_type, scope_id=scope_id)
            rows = session.scalars(select(AtlasNoteCategoryRow).where(
                AtlasNoteCategoryRow.scope_type == scope_type, AtlasNoteCategoryRow.scope_id == scope_id,
                AtlasNoteCategoryRow.lifecycle_status == lifecycle_status).order_by(AtlasNoteCategoryRow.name))
            return tuple(self._category(session, row) for row in rows)
        return self._tx(run)

    def get_category(self, *, actor_id: str, category_id: str) -> NoteCategoryV1:
        def run(session: Session):
            self._category_scope(session, actor_id, category_id)
            return self._category(session, session.get(AtlasNoteCategoryRow, category_id))
        return self._tx(run)

    def create_category(self, *, actor_id: str, command: NoteCategoryCreateRequestV1) -> NoteCategoryV1:
        fingerprint = _fingerprint(command.model_dump(mode="json", exclude={"idempotency_key"}))
        def run(session: Session):
            self._locks(session, f"actor:{actor_id}", f"scope:{command.scope_type}:{command.scope_id}", f"category:{command.category_id}")
            self._authorize(session, actor_id=actor_id, scope_type=command.scope_type, scope_id=command.scope_id, lock=True)
            if self._replay(session, actor_id, "category_create", command.category_id, command.idempotency_key, fingerprint):
                row = session.get(AtlasNoteCategoryRow, command.category_id)
                if row is None: raise _error("audit_failure", "Accepted replay target unavailable", 503)
                return self._category(session, row)
            duplicate = session.scalar(select(AtlasNoteCategoryRow).where(
                (AtlasNoteCategoryRow.category_id == command.category_id) |
                ((AtlasNoteCategoryRow.scope_type == command.scope_type) &
                 (AtlasNoteCategoryRow.scope_id == command.scope_id) &
                 (AtlasNoteCategoryRow.name == command.name))))
            if duplicate: raise _error("idempotency_payload_conflict", "Category identity or name exists", 409)
            at = _now()
            row = AtlasNoteCategoryRow(category_id=command.category_id, scope_type=command.scope_type,
                scope_id=command.scope_id, name=command.name, lifecycle_status="active",
                metadata_revision=1, created_actor_id=actor_id, created_at=at,
                updated_actor_id=actor_id, updated_at=at, trashed_actor_id=None, trashed_at=None)
            session.add(row); session.flush()
            self._audit(session,event_type="note_category_created",actor_id=actor_id,
                target_ref=command.category_id,scope_type=command.scope_type,scope_id=command.scope_id,
                metadata={"operation":"category_create","request_fingerprint":fingerprint,
                    "category_id":command.category_id,"revision":1,"event_kind":"create"},
                event_id=_receipt(actor_id,"category_create",command.category_id,command.idempotency_key))
            return self._category(session,row)
        return self._tx(run)

    def _mutate_category(self, *, actor_id: str, category_id: str, expected: int, key: str,
                         operation: str, payload: object, event: str,
                         mutate: Callable[[Session, AtlasNoteCategoryRow, datetime], None]) -> NoteCategoryV1:
        fingerprint = _fingerprint(payload)
        def run(session: Session):
            self._category_scope(session,actor_id,category_id,write=True)
            row=session.scalar(select(AtlasNoteCategoryRow).where(AtlasNoteCategoryRow.category_id==category_id).with_for_update())
            assert row
            if self._replay(session,actor_id,operation,category_id,key,fingerprint): return self._category(session,row)
            if row.metadata_revision != expected:
                raise _error("stale_metadata_revision","Category metadata revision is stale",409)
            at=_now(); mutate(session,row,at); row.metadata_revision+=1
            row.updated_actor_id=actor_id; row.updated_at=at; session.flush()
            self._audit(session,event_type=event,actor_id=actor_id,target_ref=category_id,
                scope_type=row.scope_type,scope_id=row.scope_id,
                metadata={"operation":operation,"request_fingerprint":fingerprint,
                    "category_id":category_id,"revision":row.metadata_revision,"event_kind":event},
                event_id=_receipt(actor_id,operation,category_id,key))
            return self._category(session,row)
        return self._tx(run)

    def update_category(self, *, actor_id: str, category_id: str, command: NoteCategoryUpdateRequestV1) -> NoteCategoryV1:
        def mutate(session: Session,row: AtlasNoteCategoryRow,_at: datetime):
            duplicate=session.scalar(select(AtlasNoteCategoryRow.category_id).where(
                AtlasNoteCategoryRow.scope_type==row.scope_type,AtlasNoteCategoryRow.scope_id==row.scope_id,
                AtlasNoteCategoryRow.name==command.name,AtlasNoteCategoryRow.category_id!=category_id))
            if duplicate: raise _error("idempotency_payload_conflict","Category name exists",409)
            row.name=command.name
        return self._mutate_category(actor_id=actor_id,category_id=category_id,
            expected=command.expected_metadata_revision,key=command.idempotency_key,
            operation="category_update",payload=command.model_dump(mode="json",exclude={"idempotency_key"}),
            event="note_category_updated",mutate=mutate)

    def trash_category(self, *, actor_id: str, category_id: str, command: NoteCategoryTrashRequestV1) -> NoteCategoryV1:
        def mutate(session: Session,row: AtlasNoteCategoryRow,at: datetime):
            if row.lifecycle_status!="active": raise _error("stale_metadata_revision","Category already trashed",409)
            if session.scalar(select(func.count()).select_from(AtlasNoteRow).where(AtlasNoteRow.category_id==category_id)):
                raise _error("category_not_empty","Only an unreferenced category can be trashed",409)
            row.lifecycle_status="trashed"; row.trashed_actor_id=actor_id; row.trashed_at=at
        return self._mutate_category(actor_id=actor_id,category_id=category_id,
            expected=command.expected_metadata_revision,key=command.idempotency_key,
            operation="category_trash",payload=command.model_dump(mode="json",exclude={"idempotency_key"}),
            event="note_category_trashed",mutate=mutate)

    def restore_category(self, *, actor_id: str, category_id: str, command: NoteCategoryRestoreRequestV1) -> NoteCategoryV1:
        def mutate(_session: Session,row: AtlasNoteCategoryRow,_at: datetime):
            if row.lifecycle_status!="trashed": raise _error("stale_metadata_revision","Category not trashed",409)
            row.lifecycle_status="active"; row.trashed_actor_id=None; row.trashed_at=None
        return self._mutate_category(actor_id=actor_id,category_id=category_id,
            expected=command.expected_metadata_revision,key=command.idempotency_key,
            operation="category_restore",payload=command.model_dump(mode="json",exclude={"idempotency_key"}),
            event="note_category_restored",mutate=mutate)

    def list_revisions(self, *, actor_id: str, note_id: str, after_sequence: int | None = None,
                       limit: int = 100) -> tuple[NoteRevisionHistoryV1, ...]:
        def run(session: Session):
            self._note_scope(session,actor_id,note_id)
            query=select(AtlasNoteRevisionRow).where(AtlasNoteRevisionRow.note_id==note_id)
            if after_sequence is not None: query=query.where(AtlasNoteRevisionRow.sequence>after_sequence)
            return tuple(self._revision_history(row) for row in session.scalars(query.order_by(AtlasNoteRevisionRow.sequence).limit(limit)))
        return self._tx(run)

    def list_savepoints(self, *, actor_id: str, note_id: str) -> tuple[NoteSavepointSummaryV1, ...]:
        def run(session: Session):
            self._note_scope(session,actor_id,note_id)
            return tuple(self._savepoint_summary(row) for row in session.scalars(select(AtlasNoteSavepointRow).
                where(AtlasNoteSavepointRow.note_id==note_id).order_by(AtlasNoteSavepointRow.sequence)))
        return self._tx(run)

    def get_savepoint(self, *, actor_id: str, note_id: str, savepoint_id: str) -> NoteSavepointPreviewV1:
        def run(session: Session):
            self._note_scope(session,actor_id,note_id)
            row=session.scalar(select(AtlasNoteSavepointRow).where(
                AtlasNoteSavepointRow.note_id==note_id,AtlasNoteSavepointRow.savepoint_id==savepoint_id))
            if row is None: raise _error("note_not_found","Savepoint was not found",404)
            return self._savepoint_preview(row)
        return self._tx(run)

    def replay_body_restore(
        self, *, actor_id: str, note_id: str, command: NoteBodyRestoreRequestV1
    ) -> BodyRestoreResultV1 | None:
        request_fingerprint = _fingerprint(
            command.model_dump(mode="json", exclude={"idempotency_key"})
        )

        def run(session: Session) -> BodyRestoreResultV1 | None:
            self._note_scope(session, actor_id, note_id, write=True)
            receipt = session.get(
                AtlasAuditEventRow,
                _receipt(
                    actor_id, "body_restore_commit", note_id,
                    command.idempotency_key,
                ),
            )
            if receipt is None:
                return None
            if receipt.event_metadata.get("request_fingerprint") != request_fingerprint:
                raise _error(
                    "idempotency_payload_conflict",
                    "Idempotency key payload conflicts",
                    409,
                )
            revision = session.scalar(
                select(AtlasNoteRevisionRow).where(
                    AtlasNoteRevisionRow.note_id == note_id,
                    AtlasNoteRevisionRow.sequence
                    == int(receipt.event_metadata["revision"]),
                )
            )
            savepoint = session.scalar(
                select(AtlasNoteSavepointRow).where(
                    AtlasNoteSavepointRow.note_id == note_id,
                    AtlasNoteSavepointRow.sequence
                    == int(receipt.event_metadata["savepoint_sequence"]),
                )
            )
            if revision is None or savepoint is None:
                raise _error(
                    "audit_failure", "Accepted restore replay unavailable", 503
                )
            return BodyRestoreResultV1(
                revision=self._revision_history(revision),
                savepoint=self._savepoint_preview(savepoint),
            )

        return self._tx(run)

    def validate_body_restore(self, *, actor_id: str, note_id: str,
                              command: NoteBodyRestoreRequestV1) -> tuple[NoteDetailV1, NoteSavepointV1]:
        def run(session: Session):
            self._note_scope(session,actor_id,note_id,write=True)
            note=session.scalar(select(AtlasNoteRow).where(AtlasNoteRow.note_id==note_id).with_for_update()); assert note
            if note.lifecycle_status!="active": raise _error("note_trashed","Restore note from trash first",409)
            if note.accepted_update_head!=command.expected_revision_head: raise _error("stale_revision_head","Revision head is stale",409)
            if note.collaboration_epoch!=command.expected_collaboration_epoch: raise _error("stale_collaboration_epoch","Collaboration epoch is stale",409)
            sp=session.scalar(select(AtlasNoteSavepointRow).where(
                AtlasNoteSavepointRow.note_id==note_id,AtlasNoteSavepointRow.savepoint_id==command.savepoint_id))
            if sp is None: raise _error("note_not_found","Savepoint was not found",404)
            return self._note(session,note),self._savepoint(sp)
        return self._tx(run)

    def load_restore_context(
        self, *, actor_id: str, note_id: str,
        command: NoteBodyRestoreRequestV1,
    ) -> tuple[
        NoteDetailV1,
        NoteSavepointV1,
        tuple[NoteRevisionV1, ...],
        NoteSavepointV1,
    ]:
        def run(session: Session):
            self._note_scope(session, actor_id, note_id, write=True)
            note = session.scalar(
                select(AtlasNoteRow)
                .where(AtlasNoteRow.note_id == note_id)
                .with_for_update()
            )
            assert note
            if note.lifecycle_status != "active":
                raise _error("note_trashed", "Restore note from trash first", 409)
            if note.accepted_update_head != command.expected_revision_head:
                raise _error("stale_revision_head", "Revision head is stale", 409)
            if note.collaboration_epoch != command.expected_collaboration_epoch:
                raise _error(
                    "stale_collaboration_epoch",
                    "Collaboration epoch is stale",
                    409,
                )
            source = session.scalar(
                select(AtlasNoteSavepointRow).where(
                    AtlasNoteSavepointRow.note_id == note_id,
                    AtlasNoteSavepointRow.savepoint_id == command.savepoint_id,
                )
            )
            if source is None:
                raise _error("note_not_found", "Savepoint was not found", 404)
            latest = session.scalar(
                select(AtlasNoteSavepointRow)
                .where(AtlasNoteSavepointRow.note_id == note_id)
                .order_by(AtlasNoteSavepointRow.sequence.desc())
                .limit(1)
            )
            if latest is None:
                raise _error("audit_failure", "Savepoint head unavailable", 503)
            tail = tuple(
                self._revision(row)
                for row in session.scalars(
                    select(AtlasNoteRevisionRow)
                    .where(
                        AtlasNoteRevisionRow.note_id == note_id,
                        AtlasNoteRevisionRow.sequence > latest.covered_revision,
                    )
                    .order_by(AtlasNoteRevisionRow.sequence)
                )
            )
            return (
                self._note(session, note),
                self._savepoint(latest),
                tail,
                self._savepoint(source),
            )

        return self._tx(run)


    def commit_body_restore(
        self, *, actor_id: str, command: CommitNoteBodyRestoreRequestV1
    ) -> BodyRestoreResultV1:
        attachment_refs = _validate_body(command.canonical_body, command.document_schema)
        if (
            not command.raw_yjs_update
            or not command.encoded_yjs_state
            or len(command.raw_yjs_update) > MAX_BINARY_BYTES
            or len(command.encoded_yjs_state) > MAX_BINARY_BYTES
            or len(_json({
                "body": command.canonical_body,
                "changes": command.change_set.model_dump(mode="json"),
            })) > MAX_JSON_BYTES
        ):
            raise _error("payload_oversize", "Restore payload is empty or too large", 413)
        fingerprint = command.request_fingerprint

        def run(session: Session) -> BodyRestoreResultV1:
            self._note_scope(session, actor_id, command.note_id, write=True)
            note = session.scalar(
                select(AtlasNoteRow)
                .where(AtlasNoteRow.note_id == command.note_id)
                .with_for_update()
            )
            assert note
            replay = self._replay(
                session, actor_id, "body_restore_commit", command.note_id,
                command.idempotency_key, fingerprint,
            )
            if replay:
                revision = session.scalar(
                    select(AtlasNoteRevisionRow).where(
                        AtlasNoteRevisionRow.note_id == command.note_id,
                        AtlasNoteRevisionRow.sequence
                        == int(replay.event_metadata["revision"]),
                    )
                )
                savepoint = session.scalar(
                    select(AtlasNoteSavepointRow).where(
                        AtlasNoteSavepointRow.note_id == command.note_id,
                        AtlasNoteSavepointRow.sequence
                        == int(replay.event_metadata["savepoint_sequence"]),
                    )
                )
                if revision is None or savepoint is None:
                    raise _error("audit_failure", "Accepted restore replay unavailable", 503)
                return BodyRestoreResultV1(
                    revision=self._revision_history(revision),
                    savepoint=self._savepoint_preview(savepoint),
                )
            if note.lifecycle_status != "active":
                raise _error("note_trashed", "Restore note from trash first", 409)
            if note.accepted_update_head != command.expected_revision_head:
                raise _error("stale_revision_head", "Revision head is stale", 409)
            if note.collaboration_epoch != command.expected_collaboration_epoch:
                raise _error("stale_collaboration_epoch", "Collaboration epoch is stale", 409)
            self._validate_attachment_refs(session, command.note_id, attachment_refs)
            digest = _digest(command.canonical_body)
            source = session.scalar(
                select(AtlasNoteSavepointRow).where(
                    AtlasNoteSavepointRow.note_id == command.note_id,
                    AtlasNoteSavepointRow.savepoint_id
                    == command.restore_source_savepoint_id,
                )
            )
            if source is None:
                raise _error("note_not_found", "Restore source was not found", 404)
            if (
                source.body_digest != digest
                or source.document_schema != command.document_schema
            ):
                raise _error(
                    "restore_source_mismatch",
                    "Restore body does not match the selected savepoint",
                    409,
                )
            previous = session.scalar(
                select(AtlasNoteRevisionRow).where(
                    AtlasNoteRevisionRow.note_id == command.note_id,
                    AtlasNoteRevisionRow.sequence == note.accepted_update_head,
                )
            )
            if previous is None:
                raise _error("audit_failure", "Current revision digest unavailable", 503)
            at = _now()
            revision_sequence = note.accepted_update_head + 1
            savepoint_sequence = note.savepoint_head + 1
            revision = AtlasNoteRevisionRow(
                revision_id=f"nrev-{uuid4().hex}",
                note_id=command.note_id,
                sequence=revision_sequence,
                server_timestamp=at,
                actor_id=actor_id,
                event_kind="body_restore",
                raw_yjs_update=command.raw_yjs_update,
                before_digest=previous.after_digest,
                after_digest=digest,
                change_set=command.change_set.model_dump(mode="json"),
                restore_source_savepoint_id=command.restore_source_savepoint_id,
            )
            savepoint = AtlasNoteSavepointRow(
                savepoint_id=f"nsp-{uuid4().hex}",
                note_id=command.note_id,
                sequence=savepoint_sequence,
                covered_revision=revision_sequence,
                encoded_yjs_state=command.encoded_yjs_state,
                canonical_body=command.canonical_body,
                document_schema=command.document_schema,
                body_digest=digest,
                aggregate_change_set=command.change_set.model_dump(mode="json"),
                contributor_actor_ids=[actor_id],
                created_at=at,
            )
            session.add_all([revision, savepoint])
            note.accepted_update_head = revision_sequence
            note.savepoint_head = savepoint_sequence
            note.updated_actor_id = actor_id
            note.updated_at = at
            session.flush()
            receipt_metadata = {
                "operation": "body_restore_commit",
                "request_fingerprint": fingerprint,
                "note_id": command.note_id,
                "revision": revision_sequence,
                "savepoint_id": savepoint.savepoint_id,
                "savepoint_sequence": savepoint_sequence,
                "digest": digest,
                "event_kind": "body_restore",
            }
            self._audit(
                session,
                event_type="note_body_restored",
                actor_id=actor_id,
                target_ref=revision.revision_id,
                scope_type=note.scope_type,
                scope_id=note.scope_id,
                metadata=receipt_metadata,
                event_id=_receipt(
                    actor_id, "body_restore_commit", command.note_id,
                    command.idempotency_key,
                ),
            )
            self._audit(
                session,
                event_type="note_savepoint_created",
                actor_id=actor_id,
                target_ref=savepoint.savepoint_id,
                scope_type=note.scope_type,
                scope_id=note.scope_id,
                metadata={
                    **receipt_metadata,
                    "operation": "body_restore_savepoint",
                    "event_kind": "savepoint",
                },
            )
            return BodyRestoreResultV1(
                revision=self._revision_history(revision),
                savepoint=self._savepoint_preview(savepoint),
            )

        return self._tx(run)

    def accept_revision(self, *, actor_id: str, command: AcceptNoteRevisionRequestV1) -> NoteRevisionV1:
        attachment_refs = _validate_body(command.canonical_body, command.document_schema)
        if not command.raw_yjs_update or len(command.raw_yjs_update)>MAX_BINARY_BYTES or len(_json(
            {"body":command.canonical_body,"changes":command.change_set.model_dump(mode="json")}))>MAX_JSON_BYTES:
            raise _error("payload_oversize","Revision payload is empty or too large",413)
        fingerprint=_fingerprint(command.model_dump(mode="python",exclude={"idempotency_key"}))
        def run(session: Session):
            self._note_scope(session,actor_id,command.note_id,write=True)
            note=session.scalar(select(AtlasNoteRow).where(AtlasNoteRow.note_id==command.note_id).with_for_update()); assert note
            replay=self._replay(session,actor_id,"revision_accept",command.note_id,command.idempotency_key,fingerprint)
            if replay:
                row=session.scalar(select(AtlasNoteRevisionRow).where(AtlasNoteRevisionRow.note_id==command.note_id,
                    AtlasNoteRevisionRow.sequence==int(replay.event_metadata["revision"])))
                if row is None: raise _error("audit_failure","Accepted replay target unavailable",503)
                return self._revision(row)
            if note.lifecycle_status!="active": raise _error("note_trashed","Trashed notes are read-only",409)
            if note.accepted_update_head!=command.expected_revision_head: raise _error("stale_revision_head","Revision head is stale",409)
            if note.collaboration_epoch!=command.expected_collaboration_epoch: raise _error("stale_collaboration_epoch","Collaboration epoch is stale",409)
            self._validate_attachment_refs(session, command.note_id, attachment_refs)
            previous=session.scalar(select(AtlasNoteRevisionRow).where(AtlasNoteRevisionRow.note_id==command.note_id,
                AtlasNoteRevisionRow.sequence==note.accepted_update_head))
            if previous is None: raise _error("audit_failure","Current revision digest unavailable",503)
            sequence=note.accepted_update_head+1; after=_digest(command.canonical_body); at=_now()
            row=AtlasNoteRevisionRow(revision_id=f"nrev-{uuid4().hex}",note_id=command.note_id,
                sequence=sequence,server_timestamp=at,actor_id=actor_id,event_kind=command.event_kind,
                raw_yjs_update=command.raw_yjs_update,before_digest=previous.after_digest,after_digest=after,
                change_set=command.change_set.model_dump(mode="json"),
                restore_source_savepoint_id=None)
            session.add(row); note.accepted_update_head=sequence; note.updated_actor_id=actor_id; note.updated_at=at; session.flush()
            event="note_revision_accepted"
            self._audit(session,event_type=event,actor_id=actor_id,target_ref=row.revision_id,
                scope_type=note.scope_type,scope_id=note.scope_id,
                metadata={"operation":"revision_accept","request_fingerprint":fingerprint,
                    "note_id":command.note_id,"revision":sequence,"digest":after,"event_kind":command.event_kind},
                event_id=_receipt(actor_id,"revision_accept",command.note_id,command.idempotency_key))
            return self._revision(row)
        return self._tx(run)

    def create_savepoint(self, *, actor_id: str, command: CreateNoteSavepointRequestV1) -> NoteSavepointV1:
        attachment_refs = _validate_body(command.canonical_body, command.document_schema)
        if not command.encoded_yjs_state or len(command.encoded_yjs_state)>MAX_BINARY_BYTES or len(_json(
            {"body":command.canonical_body,"changes":command.aggregate_change_set.model_dump(mode="json")}))>MAX_JSON_BYTES:
            raise _error("payload_oversize","Savepoint payload is empty or too large",413)
        fingerprint=_fingerprint(command.model_dump(mode="python",exclude={"idempotency_key","contributor_actor_ids"}))
        def run(session: Session):
            self._note_scope(session,actor_id,command.note_id,write=True)
            note=session.scalar(select(AtlasNoteRow).where(AtlasNoteRow.note_id==command.note_id).with_for_update()); assert note
            replay=self._replay(session,actor_id,"savepoint_create",command.note_id,command.idempotency_key,fingerprint)
            if replay:
                row=session.scalar(select(AtlasNoteSavepointRow).where(AtlasNoteSavepointRow.note_id==command.note_id,
                    AtlasNoteSavepointRow.sequence==int(replay.event_metadata["savepoint_sequence"])))
                if row is None: raise _error("audit_failure","Accepted replay target unavailable",503)
                return self._savepoint(row)
            if note.lifecycle_status!="active": raise _error("note_trashed","Trashed notes cannot checkpoint",409)
            if note.accepted_update_head!=command.expected_revision_head: raise _error("stale_revision_head","Revision head is stale",409)
            if note.savepoint_head!=command.expected_savepoint_head: raise _error("stale_savepoint_head","Savepoint head is stale",409)
            if note.collaboration_epoch!=command.expected_collaboration_epoch: raise _error("stale_collaboration_epoch","Collaboration epoch is stale",409)
            self._validate_attachment_refs(session, command.note_id, attachment_refs)
            if session.scalar(select(AtlasNoteSavepointRow.savepoint_id).where(
                AtlasNoteSavepointRow.note_id==command.note_id,
                AtlasNoteSavepointRow.covered_revision==command.expected_revision_head)):
                raise _error("stale_savepoint_head","Revision is already checkpointed",409)
            prior=session.scalar(select(func.max(AtlasNoteSavepointRow.covered_revision)).where(
                AtlasNoteSavepointRow.note_id==command.note_id)) or 0
            contributors=list(dict.fromkeys(session.scalars(select(AtlasNoteRevisionRow.actor_id).where(
                AtlasNoteRevisionRow.note_id==command.note_id,AtlasNoteRevisionRow.sequence>prior,
                AtlasNoteRevisionRow.sequence<=command.expected_revision_head).order_by(AtlasNoteRevisionRow.sequence))))
            sequence=note.savepoint_head+1; digest=_digest(command.canonical_body)
            row=AtlasNoteSavepointRow(savepoint_id=f"nsp-{uuid4().hex}",note_id=command.note_id,
                sequence=sequence,covered_revision=command.expected_revision_head,
                encoded_yjs_state=command.encoded_yjs_state,canonical_body=command.canonical_body,
                document_schema=command.document_schema,body_digest=digest,
                aggregate_change_set=command.aggregate_change_set.model_dump(mode="json"),
                contributor_actor_ids=contributors,created_at=_now())
            session.add(row); note.savepoint_head=sequence; session.flush()
            self._audit(session,event_type="note_savepoint_created",actor_id=actor_id,target_ref=row.savepoint_id,
                scope_type=note.scope_type,scope_id=note.scope_id,
                metadata={"operation":"savepoint_create","request_fingerprint":fingerprint,
                    "note_id":command.note_id,"savepoint_id":row.savepoint_id,"savepoint_sequence":sequence,
                    "revision":command.expected_revision_head,"digest":digest,"event_kind":"savepoint"},
                event_id=_receipt(actor_id,"savepoint_create",command.note_id,command.idempotency_key))
            return self._savepoint(row)
        return self._tx(run)

    def load_collaboration(self, *, actor_id: str, note_id: str,
                           expected_collaboration_epoch: int) -> tuple[NoteDetailV1, NoteSavepointV1, tuple[NoteRevisionV1, ...]]:
        def run(session: Session):
            self._note_scope(session,actor_id,note_id)
            note=session.get(AtlasNoteRow,note_id); assert note
            if note.collaboration_epoch!=expected_collaboration_epoch:
                raise _error("stale_collaboration_epoch","Collaboration epoch is stale",409)
            sp=session.scalar(select(AtlasNoteSavepointRow).where(AtlasNoteSavepointRow.note_id==note_id).
                order_by(AtlasNoteSavepointRow.sequence.desc()).limit(1))
            if sp is None: raise _error("audit_failure","Savepoint head unavailable",503)
            tail=tuple(self._revision(row) for row in session.scalars(select(AtlasNoteRevisionRow).where(
                AtlasNoteRevisionRow.note_id==note_id,AtlasNoteRevisionRow.sequence>sp.covered_revision).
                order_by(AtlasNoteRevisionRow.sequence)))
            return self._note(session,note),self._savepoint(sp),tail
        return self._tx(run)

    def get_settings(self, *, actor_id: str | None = None, require_admin: bool = False) -> NotesSettingsV1:
        def run(session: Session):
            if actor_id is not None:
                self._authorize(session,actor_id=actor_id,scope_type="team",scope_id="__settings__",admin=require_admin)
            row=session.get(AtlasNotesSettingsRow,"global")
            if row is None: raise _error("invalid_settings","Notes settings unavailable",503)
            return self._settings(row)
        return self._tx(run)

    def update_settings(self, *, actor_id: str, command: NotesSettingsUpdateRequestV1) -> NotesSettingsV1:
        fingerprint=_fingerprint(command.model_dump(mode="json",exclude={"idempotency_key"}))
        def run(session: Session):
            self._locks(session,f"actor:{actor_id}","settings:global")
            self._authorize(session,actor_id=actor_id,scope_type="team",scope_id="__settings__",lock=True,admin=True)
            replay=self._replay(session,actor_id,"settings_update","global",command.idempotency_key,fingerprint)
            row=session.scalar(select(AtlasNotesSettingsRow).where(
                AtlasNotesSettingsRow.settings_key=="global").with_for_update())
            if row is None: raise _error("invalid_settings","Notes settings unavailable",503)
            if replay: return self._settings(row)
            if row.settings_revision!=command.expected_settings_revision:
                raise _error("stale_settings_revision","Settings revision is stale",409)
            row.checkpoint_interval_seconds=command.checkpoint_interval_seconds
            row.settings_revision+=1; row.updated_actor_id=actor_id; row.updated_at=_now(); session.flush()
            self._audit(session,event_type="notes_settings_updated",actor_id=actor_id,
                target_ref="notes-settings:global",scope_type=None,scope_id=None,
                metadata={"operation":"settings_update","request_fingerprint":fingerprint,
                    "settings_revision":row.settings_revision,"revision":row.settings_revision,
                    "event_kind":"settings"},event_id=_receipt(actor_id,"settings_update","global",command.idempotency_key))
            return self._settings(row)
        return self._tx(run)


__all__ = ["MAX_BINARY_BYTES", "PostgresNotesOwner"]
