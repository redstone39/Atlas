from __future__ import annotations

from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=4096)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ScopeType = Literal["project", "team"]
LifecycleStatus = Literal["active", "trashed"]
MAX_NOTE_BINARY_BYTES = 16 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoteScopeRefV1(_StrictModel):
    scope_type: ScopeType
    scope_id: Identity
    label: str = Field(min_length=1, max_length=500)


class NoteTextChangeV1(_StrictModel):
    change: Literal["insert", "delete", "replace"]
    path: tuple[int, ...]
    before: str
    after: str
    from_offset: int = Field(ge=0)
    to_offset: int = Field(ge=0)


class NoteNodeChangeV1(_StrictModel):
    change: Literal["insert", "delete", "replace"]
    path: tuple[int, ...]
    before_type: str | None = Field(default=None, max_length=100)
    after_type: str | None = Field(default=None, max_length=100)


class NoteMarkChangeV1(_StrictModel):
    change: Literal["add", "remove", "replace"]
    path: tuple[int, ...]
    mark_type: str = Field(min_length=1, max_length=100)
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None


class NoteAttributeChangeV1(_StrictModel):
    path: tuple[int, ...]
    node_type: str = Field(min_length=1, max_length=100)
    attribute: str = Field(min_length=1, max_length=100)
    before: object | None = None
    after: object | None = None


class NoteMoveChangeV1(_StrictModel):
    block_id: Identity
    from_path: tuple[Annotated[int, Field(ge=0)], ...] = Field(
        min_length=1, max_length=1
    )
    to_path: tuple[Annotated[int, Field(ge=0)], ...] = Field(
        min_length=1, max_length=1
    )


class NoteChangeSetV1(_StrictModel):
    text: tuple[NoteTextChangeV1, ...] = ()
    nodes: tuple[NoteNodeChangeV1, ...] = ()
    marks: tuple[NoteMarkChangeV1, ...] = ()
    attributes: tuple[NoteAttributeChangeV1, ...] = ()
    moves: tuple[NoteMoveChangeV1, ...] = ()


class NoteAttachmentV1(_StrictModel):
    attachment_ref: Identity
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int = Field(ge=1, le=MAX_NOTE_BINARY_BYTES)
    sha256: Digest
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    state: Literal["ready"] = "ready"


class NoteCategoryV1(_StrictModel):
    category_id: Identity
    scope: NoteScopeRefV1
    name: str = Field(min_length=1, max_length=200)
    lifecycle_status: LifecycleStatus
    metadata_revision: int = Field(ge=1)
    created_actor_id: Identity
    created_at: AwareDatetime
    updated_actor_id: Identity
    updated_at: AwareDatetime
    trashed_actor_id: Identity | None = None
    trashed_at: AwareDatetime | None = None


class NoteSummaryV1(_StrictModel):
    note_id: Identity
    scope: NoteScopeRefV1
    category_id: Identity | None = None
    title: str = Field(min_length=1, max_length=500)
    lifecycle_status: LifecycleStatus
    metadata_revision: int = Field(ge=1)
    accepted_update_head: int = Field(ge=1)
    savepoint_head: int = Field(ge=1)
    collaboration_epoch: int = Field(ge=1)
    updated_actor_id: Identity
    updated_at: AwareDatetime


class NoteDetailV1(NoteSummaryV1):
    created_actor_id: Identity
    created_at: AwareDatetime
    trashed_actor_id: Identity | None = None
    trashed_at: AwareDatetime | None = None


class NoteRevisionV1(_StrictModel):
    revision_id: Identity
    note_id: Identity
    sequence: int = Field(ge=1)
    server_timestamp: AwareDatetime
    actor_id: Identity
    event_kind: Literal["create", "content_update", "body_restore"]
    raw_yjs_update: bytes
    before_digest: Digest
    after_digest: Digest
    change_set: NoteChangeSetV1
    restore_source_savepoint_id: Identity | None = None


class NoteRevisionHistoryV1(_StrictModel):
    revision_id: Identity
    note_id: Identity
    sequence: int = Field(ge=1)
    server_timestamp: AwareDatetime
    actor_id: Identity
    event_kind: Literal["create", "content_update", "body_restore"]
    before_digest: Digest
    after_digest: Digest
    change_set: NoteChangeSetV1
    restore_source_savepoint_id: Identity | None = None


class NoteSavepointV1(_StrictModel):
    savepoint_id: Identity
    note_id: Identity
    sequence: int = Field(ge=1)
    covered_revision: int = Field(ge=1)
    encoded_yjs_state: bytes
    canonical_body: dict[str, object]
    document_schema: str = Field(min_length=1, max_length=100)
    body_digest: Digest
    aggregate_change_set: NoteChangeSetV1
    contributor_actor_ids: tuple[Identity, ...]
    created_at: AwareDatetime


class NoteSavepointSummaryV1(_StrictModel):
    savepoint_id: Identity
    note_id: Identity
    sequence: int = Field(ge=1)
    covered_revision: int = Field(ge=1)
    body_digest: Digest
    aggregate_change_set: NoteChangeSetV1
    contributor_actor_ids: tuple[Identity, ...]
    created_at: AwareDatetime


class NoteSavepointPreviewV1(NoteSavepointSummaryV1):
    canonical_body: dict[str, object]
    document_schema: str = Field(min_length=1, max_length=100)


class NotesSettingsV1(_StrictModel):
    checkpoint_interval_seconds: int = Field(gt=0)
    settings_revision: int = Field(ge=1)
    updated_actor_id: Identity
    updated_at: AwareDatetime


class CollaborationTicketV1(_StrictModel):
    ticket: OpaqueRef
    room_name: OpaqueRef
    websocket_url: str = Field(min_length=1, max_length=2000)
    collaboration_epoch: int = Field(ge=1)
    read_only: bool


class BodyRestoreCommandV1(_StrictModel):
    command_id: OpaqueRef
    note_id: Identity
    room_name: OpaqueRef
    savepoint_id: Identity
    expected_revision_head: int = Field(ge=1)
    expected_collaboration_epoch: int = Field(ge=1)
    idempotency_key: Identity
    request_fingerprint: Digest
    authorization_token: OpaqueRef


class BodyRestoreResultV1(_StrictModel):
    revision: NoteRevisionHistoryV1
    savepoint: NoteSavepointPreviewV1


class NoteCreateRequestV1(_StrictModel):
    note_id: Identity
    scope_type: ScopeType
    scope_id: Identity
    category_id: Identity | None = None
    title: str = Field(min_length=1, max_length=500)
    idempotency_key: Identity


class NoteMetadataUpdateRequestV1(_StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    category_id: Identity | None = None
    clear_category: bool = False
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteTrashRequestV1(_StrictModel):
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteRestoreRequestV1(_StrictModel):
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteCategoryCreateRequestV1(_StrictModel):
    category_id: Identity
    scope_type: ScopeType
    scope_id: Identity
    name: str = Field(min_length=1, max_length=200)
    idempotency_key: Identity


class NoteCategoryUpdateRequestV1(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteCategoryTrashRequestV1(_StrictModel):
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteCategoryRestoreRequestV1(_StrictModel):
    expected_metadata_revision: int = Field(ge=1)
    idempotency_key: Identity


class NoteHistoryRequestV1(_StrictModel):
    note_id: Identity
    after_sequence: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=500)


class NoteSavepointPreviewRequestV1(_StrictModel):
    note_id: Identity
    savepoint_id: Identity


class NoteBodyRestoreRequestV1(_StrictModel):
    savepoint_id: Identity
    expected_revision_head: int = Field(ge=1)
    expected_collaboration_epoch: int = Field(ge=1)
    idempotency_key: Identity


class NotesSettingsUpdateRequestV1(_StrictModel):
    checkpoint_interval_seconds: int = Field(gt=0)
    expected_settings_revision: int = Field(ge=1)
    idempotency_key: Identity


class AcceptNoteRevisionRequestV1(_StrictModel):
    note_id: Identity
    expected_revision_head: int = Field(ge=1)
    expected_collaboration_epoch: int = Field(ge=1)
    event_kind: Literal["content_update"]
    raw_yjs_update: bytes
    canonical_body: dict[str, object]
    document_schema: str = Field(min_length=1, max_length=100)
    change_set: NoteChangeSetV1
    idempotency_key: Identity


class CreateNoteSavepointRequestV1(_StrictModel):
    note_id: Identity
    expected_revision_head: int = Field(ge=1)
    expected_savepoint_head: int = Field(ge=1)
    expected_collaboration_epoch: int = Field(ge=1)
    encoded_yjs_state: bytes
    canonical_body: dict[str, object]
    document_schema: str = Field(min_length=1, max_length=100)
    aggregate_change_set: NoteChangeSetV1
    contributor_actor_ids: tuple[Identity, ...]
    idempotency_key: Identity


class CommitNoteBodyRestoreRequestV1(_StrictModel):
    note_id: Identity
    command_id: OpaqueRef
    room_name: OpaqueRef
    restore_source_savepoint_id: Identity
    expected_revision_head: int = Field(ge=1)
    expected_collaboration_epoch: int = Field(ge=1)
    request_fingerprint: Digest
    raw_yjs_update: bytes
    encoded_yjs_state: bytes
    canonical_body: dict[str, object]
    document_schema: str = Field(min_length=1, max_length=100)
    change_set: NoteChangeSetV1
    idempotency_key: Identity


NotesErrorCode: TypeAlias = Literal[
    "unauthenticated",
    "access_denied",
    "actor_not_human",
    "scope_not_found",
    "note_not_found",
    "category_not_found",
    "cross_scope_category",
    "category_not_empty",
    "note_trashed",
    "stale_metadata_revision",
    "stale_revision_head",
    "stale_savepoint_head",
    "stale_collaboration_epoch",
    "stale_settings_revision",
    "idempotency_payload_conflict",
    "payload_oversize",
    "invalid_settings",
    "invalid_document_schema",
    "invalid_attachment",
    "unsupported_media_type",
    "invalid_image",
    "attachment_not_ready",
    "storage_unavailable",
    "integrity_failure",
    "audit_failure",
]


class NotesError(RuntimeError):
    def __init__(self, code: NotesErrorCode, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class NotesOwner(Protocol):
    def create_note(self, *, actor_id: Identity, command: NoteCreateRequestV1) -> NoteDetailV1: ...

    def update_note_metadata(
        self, *, actor_id: Identity, note_id: Identity, command: NoteMetadataUpdateRequestV1
    ) -> NoteDetailV1: ...

    def accept_revision(
        self, *, actor_id: Identity, command: AcceptNoteRevisionRequestV1
    ) -> NoteRevisionV1: ...

    def create_savepoint(
        self, *, actor_id: Identity, command: CreateNoteSavepointRequestV1
    ) -> NoteSavepointV1: ...


__all__ = [
    "AcceptNoteRevisionRequestV1",
    "BodyRestoreCommandV1",
    "BodyRestoreResultV1",
    "CollaborationTicketV1",
    "CommitNoteBodyRestoreRequestV1",
    "CreateNoteSavepointRequestV1",
    "LifecycleStatus",
    "MAX_NOTE_BINARY_BYTES",
    "NoteAttributeChangeV1",
    "NoteAttachmentV1",
    "NoteBodyRestoreRequestV1",
    "NoteCategoryCreateRequestV1",
    "NoteCategoryRestoreRequestV1",
    "NoteCategoryTrashRequestV1",
    "NoteCategoryUpdateRequestV1",
    "NoteCategoryV1",
    "NoteChangeSetV1",
    "NoteCreateRequestV1",
    "NoteDetailV1",
    "NoteHistoryRequestV1",
    "NoteMarkChangeV1",
    "NoteMetadataUpdateRequestV1",
    "NoteMoveChangeV1",
    "NoteNodeChangeV1",
    "NoteRestoreRequestV1",
    "NoteRevisionV1",
    "NoteRevisionHistoryV1",
    "NoteSavepointPreviewRequestV1",
    "NoteSavepointV1",
    "NoteSavepointPreviewV1",
    "NoteSavepointSummaryV1",
    "NoteScopeRefV1",
    "NoteSummaryV1",
    "NoteTextChangeV1",
    "NoteTrashRequestV1",
    "NotesError",
    "NotesErrorCode",
    "NotesOwner",
    "NotesSettingsUpdateRequestV1",
    "NotesSettingsV1",
    "ScopeType",
]
