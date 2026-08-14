import type { JSONContent } from "@tiptap/core";

export type NoteScopeType = "project" | "team";
export type NoteLifecycleStatus = "active" | "trashed";

export interface NoteScope {
  scope_type: NoteScopeType;
  scope_id: string;
  label: string;
}

export interface NoteMoveChange {
  block_id: string;
  from_path: [number];
  to_path: [number];
}

export interface NoteChangeSet {
  text: Array<{
    change: "insert" | "delete" | "replace";
    path: number[];
    before: string;
    after: string;
    from_offset: number;
    to_offset: number;
  }>;
  nodes: Array<{
    change: "insert" | "delete" | "replace";
    path: number[];
    before_type: string | null;
    after_type: string | null;
  }>;
  marks: Array<{
    change: "add" | "remove" | "replace";
    path: number[];
    mark_type: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
  }>;
  attributes: Array<{
    path: number[];
    node_type: string;
    attribute: string;
    before: unknown;
    after: unknown;
  }>;
  moves: NoteMoveChange[];
}

export interface NoteAttachment {
  attachment_ref: string;
  mime_type: "image/png" | "image/jpeg" | "image/webp";
  byte_size: number;
  sha256: string;
  width: number;
  height: number;
  state: "ready";
}

export interface NoteCategory {
  category_id: string;
  scope: NoteScope;
  name: string;
  lifecycle_status: NoteLifecycleStatus;
  metadata_revision: number;
  created_actor_id: string;
  created_at: string;
  updated_actor_id: string;
  updated_at: string;
  trashed_actor_id: string | null;
  trashed_at: string | null;
}

export interface NoteSummary {
  note_id: string;
  scope: NoteScope;
  category_id: string | null;
  title: string;
  lifecycle_status: NoteLifecycleStatus;
  metadata_revision: number;
  accepted_update_head: number;
  savepoint_head: number;
  collaboration_epoch: number;
  updated_actor_id: string;
  updated_at: string;
}

export interface NoteDetail extends NoteSummary {
  created_actor_id: string;
  created_at: string;
  trashed_actor_id: string | null;
  trashed_at: string | null;
}

export interface NoteRevision {
  revision_id: string;
  note_id: string;
  sequence: number;
  server_timestamp: string;
  actor_id: string;
  event_kind: "create" | "content_update" | "body_restore";
  before_digest: string;
  after_digest: string;
  change_set: NoteChangeSet;
  restore_source_savepoint_id: string | null;
}

export interface NoteSavepointSummary {
  savepoint_id: string;
  note_id: string;
  sequence: number;
  covered_revision: number;
  body_digest: string;
  aggregate_change_set: NoteChangeSet;
  contributor_actor_ids: string[];
  created_at: string;
}

export interface NoteSavepointPreview extends NoteSavepointSummary {
  canonical_body: JSONContent;
  document_schema: string;
}

export interface CollaborationTicket {
  ticket: string;
  room_name: string;
  websocket_url: string;
  collaboration_epoch: number;
  read_only: boolean;
}

export interface NotesSettings {
  checkpoint_interval_seconds: number;
  settings_revision: number;
  updated_actor_id: string;
  updated_at: string;
}

export interface BodyRestoreResult {
  revision: NoteRevision;
  savepoint: NoteSavepointPreview;
}

export type NotesConnectionState =
  | "connecting"
  | "syncing"
  | "synced"
  | "reconnecting"
  | "authentication_failed"
  | "access_revoked"
  | "save_failed";

export type NotesSurface =
  | { view: "list" | "trash" }
  | { view: "editor" | "history"; noteId: string }
  | { view: "preview"; noteId: string; savepointId: string };

export interface NotesScopeFeatureProps {
  scopeType: NoteScopeType;
  scopeId: string;
  surface: NotesSurface;
  workspace?: boolean;
  onNavigate: (route: import("../../shared/routes").AppRoute) => void;
}
