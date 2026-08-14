export type JsonObject = Record<string, unknown>;

export interface AuthorizationResult {
  note_id: string;
  actor_id: string;
  room_name: string;
  connection_token: string;
  collaboration_epoch: number;
  read_only: boolean;
  accepted_update_head: number;
  savepoint_head: number;
}

export interface NotesSettings {
  checkpoint_interval_seconds: number;
  settings_revision: number;
  updated_actor_id: string;
  updated_at: string;
}

export interface TextChange {
  path: number[];
  change: "insert" | "delete" | "replace";
  before: string;
  after: string;
  from_offset: number;
  to_offset: number;
}

export interface NodeChange {
  change: "insert" | "delete" | "replace";
  path: number[];
  before_type: string | null;
  after_type: string | null;
}

export interface MarkChange {
  change: "add" | "remove" | "replace";
  path: number[];
  mark_type: string;
  before: JsonObject | null;
  after: JsonObject | null;
}

export interface AttributeChange {
  path: number[];
  node_type: string;
  attribute: string;
  before: unknown;
  after: unknown;
}

export interface MoveChange {
  block_id: string;
  from_path: [number];
  to_path: [number];
}

export interface ChangeSet {
  text: TextChange[];
  nodes: NodeChange[];
  marks: MarkChange[];
  attributes: AttributeChange[];
  moves: MoveChange[];
}

export interface RevisionHistory {
  revision_id: string;
  note_id: string;
  sequence: number;
  server_timestamp: string;
  actor_id: string;
  event_kind: "create" | "content_update" | "body_restore";
  before_digest: string;
  after_digest: string;
  change_set: ChangeSet;
  restore_source_savepoint_id: string | null;
}

export interface SavepointSummary {
  savepoint_id: string;
  note_id: string;
  sequence: number;
  covered_revision: number;
  body_digest: string;
  aggregate_change_set: ChangeSet;
  contributor_actor_ids: string[];
  created_at: string;
}

export interface SavepointPreview extends SavepointSummary {
  canonical_body: JsonObject;
  document_schema: string;
}

export interface LoadManifest {
  note: JsonObject & {
    note_id: string;
    accepted_update_head: number;
    savepoint_head: number;
    collaboration_epoch: number;
  };
  savepoint: JsonObject & { covered_revision: number; sequence: number };
  state_part: string;
  tail: Array<JsonObject & { sequence: number; update_part: string }>;
}

export interface RestoreContextManifest {
  note: LoadManifest["note"];
  current_savepoint: JsonObject & { covered_revision: number; sequence: number };
  current_state_part: string;
  tail: Array<JsonObject & { sequence: number; update_part: string }>;
  restore_source: JsonObject & { savepoint_id: string; covered_revision: number; canonical_body: JsonObject };
  restore_source_state_part: string;
}

export interface LoadedDocument {
  manifest: LoadManifest;
  state: Uint8Array;
  tail: Uint8Array[];
}

export interface RestoreContext {
  manifest: RestoreContextManifest;
  currentState: Uint8Array;
  tail: Uint8Array[];
  sourceState: Uint8Array;
}

export interface BodyRestoreCommand {
  command_id: string;
  note_id: string;
  room_name: string;
  savepoint_id: string;
  expected_revision_head: number;
  expected_collaboration_epoch: number;
  idempotency_key: string;
  request_fingerprint: string;
  authorization_token: string;
}

export interface BodyRestoreResult {
  revision: RevisionHistory;
  savepoint: SavepointPreview;
}
