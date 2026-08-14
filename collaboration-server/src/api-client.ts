import type { CarrierConfig } from "./config.js";
import type {
  AuthorizationResult,
  BodyRestoreCommand,
  BodyRestoreResult,
  ChangeSet,
  JsonObject,
  LoadedDocument,
  LoadManifest,
  NotesSettings,
  RestoreContext,
  RestoreContextManifest,
  RevisionHistory,
  SavepointSummary,
} from "./types.js";

const INTERNAL_PREFIX = "/internal/v1/notes-collaboration";

export class NotesApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function requireObject(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} is not an object`);
  return value as JsonObject;
}

function contentBoundary(contentType: string | null): string {
  const match = contentType?.match(/^multipart\/mixed\s*;\s*boundary=(?:"([^"]+)"|([^;\s]+))/i);
  const boundary = match?.[1] || match?.[2];
  if (!boundary) throw new Error("API multipart response has no boundary");
  return boundary;
}

function parseMultipart(body: Uint8Array, contentType: string | null): Map<string, Uint8Array> {
  const bytes = Buffer.from(body);
  const boundary = Buffer.from(`--${contentBoundary(contentType)}`);
  const separator = Buffer.from("\r\n\r\n");
  const result = new Map<string, Uint8Array>();
  let cursor = bytes.indexOf(boundary);
  while (cursor >= 0) {
    cursor += boundary.length;
    if (bytes.subarray(cursor, cursor + 2).equals(Buffer.from("--"))) break;
    if (!bytes.subarray(cursor, cursor + 2).equals(Buffer.from("\r\n"))) throw new Error("Malformed multipart boundary");
    cursor += 2;
    const headerEnd = bytes.indexOf(separator, cursor);
    if (headerEnd < 0) throw new Error("Malformed multipart headers");
    const headers = bytes.subarray(cursor, headerEnd).toString("utf8");
    const id = headers.match(/^Content-ID:\s*"?([^"\r\n]+)"?\s*$/im)?.[1];
    if (!id) throw new Error("Multipart part has no Content-ID");
    const dataStart = headerEnd + separator.length;
    const next = bytes.indexOf(Buffer.concat([Buffer.from("\r\n"), boundary]), dataStart);
    if (next < 0) throw new Error("Malformed multipart part terminator");
    result.set(id, new Uint8Array(bytes.subarray(dataStart, next)));
    cursor = next + 2;
  }
  return result;
}

function jsonPart<T>(parts: Map<string, Uint8Array>, id: string): T {
  const part = parts.get(id);
  if (!part) throw new Error(`Multipart response is missing ${id}`);
  return requireObject(JSON.parse(Buffer.from(part).toString("utf8")), id) as unknown as T;
}

function binaryPart(parts: Map<string, Uint8Array>, id: unknown): Uint8Array {
  if (typeof id !== "string" || !parts.has(id)) throw new Error("Multipart manifest references a missing binary part");
  return parts.get(id)!;
}

export class NotesApiClient {
  constructor(private readonly config: CarrierConfig, private readonly fetcher: typeof fetch = fetch) {}

  private async request(path: string, init: RequestInit = {}, timeoutMs = this.config.requestTimeoutMs): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const headers = new Headers(init.headers);
      headers.set("X-Atlas-Notes-Internal-Secret", this.config.internalSecret);
      const response = await this.fetcher(`${this.config.apiBaseUrl}${INTERNAL_PREFIX}${path}`, { ...init, headers, signal: controller.signal });
      if (!response.ok) throw new NotesApiError("Notes API rejected the collaboration operation", response.status);
      return response;
    } finally {
      clearTimeout(timer);
    }
  }

  private async json<T>(path: string, payload?: unknown): Promise<T> {
    const init: RequestInit = payload === undefined
      ? { method: "GET" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
    return (await this.request(path, init)).json() as Promise<T>;
  }

  authorize(ticket: string, roomName: string): Promise<AuthorizationResult> {
    return this.json("/authorize", { ticket, room_name: roomName });
  }

  revalidate(context: AuthorizationResult): Promise<AuthorizationResult> {
    return this.json("/revalidate", {
      connection_token: context.connection_token,
      room_name: context.room_name,
      expected_collaboration_epoch: context.collaboration_epoch,
    });
  }

  async load(context: AuthorizationResult): Promise<LoadedDocument> {
    const response = await this.request("/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        connection_token: context.connection_token,
        room_name: context.room_name,
        expected_collaboration_epoch: context.collaboration_epoch,
      }),
    });
    const parts = parseMultipart(new Uint8Array(await response.arrayBuffer()), response.headers.get("content-type"));
    const manifest = jsonPart<LoadManifest>(parts, "manifest");
    return {
      manifest,
      state: binaryPart(parts, manifest.state_part),
      tail: manifest.tail.map(item => binaryPart(parts, item.update_part)),
    };
  }

  appendRevision(input: {
    context: AuthorizationResult;
    expectedHead: number;
    canonicalBody: JsonObject;
    changeSet: ChangeSet;
    idempotencyKey: string;
    update: Uint8Array;
  }): Promise<RevisionHistory> {
    const form = new FormData();
    form.set("connection_token", input.context.connection_token);
    form.set("room_name", input.context.room_name);
    form.set("expected_revision_head", String(input.expectedHead));
    form.set("expected_collaboration_epoch", String(input.context.collaboration_epoch));
    form.set("canonical_body_json", JSON.stringify(input.canonicalBody));
    form.set("document_schema", "tiptap-prosemirror-v2");
    form.set("change_set_json", JSON.stringify(input.changeSet));
    form.set("idempotency_key", input.idempotencyKey);
    form.set("update", new Blob([input.update]), "update.bin");
    return this.request("/append-revision", { method: "POST", body: form }).then(response => response.json() as Promise<RevisionHistory>);
  }

  appendSavepoint(input: {
    context: AuthorizationResult;
    revisionHead: number;
    savepointHead: number;
    canonicalBody: JsonObject;
    changeSet: ChangeSet;
    idempotencyKey: string;
    state: Uint8Array;
  }): Promise<SavepointSummary> {
    const form = new FormData();
    form.set("connection_token", input.context.connection_token);
    form.set("room_name", input.context.room_name);
    form.set("expected_revision_head", String(input.revisionHead));
    form.set("expected_savepoint_head", String(input.savepointHead));
    form.set("expected_collaboration_epoch", String(input.context.collaboration_epoch));
    form.set("canonical_body_json", JSON.stringify(input.canonicalBody));
    form.set("document_schema", "tiptap-prosemirror-v2");
    form.set("aggregate_change_set_json", JSON.stringify(input.changeSet));
    form.set("idempotency_key", input.idempotencyKey);
    form.set("state", new Blob([input.state]), "state.bin");
    return this.request("/append-savepoint", { method: "POST", body: form }).then(response => response.json() as Promise<SavepointSummary>);
  }

  settings(): Promise<NotesSettings> {
    return this.json("/settings");
  }

  async restoreSource(command: BodyRestoreCommand): Promise<RestoreContext> {
    const response = await this.request("/restore-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    }, 30_000);
    const parts = parseMultipart(new Uint8Array(await response.arrayBuffer()), response.headers.get("content-type"));
    const manifest = jsonPart<RestoreContextManifest>(parts, "manifest");
    return {
      manifest,
      currentState: binaryPart(parts, manifest.current_state_part),
      tail: manifest.tail.map(item => binaryPart(parts, item.update_part)),
      sourceState: binaryPart(parts, manifest.restore_source_state_part),
    };
  }

  commitBodyRestore(input: {
    command: BodyRestoreCommand;
    canonicalBody: JsonObject;
    changeSet: ChangeSet;
    update: Uint8Array;
    state: Uint8Array;
  }): Promise<BodyRestoreResult> {
    const { command } = input;
    const form = new FormData();
    form.set("authorization_token", command.authorization_token);
    form.set("command_id", command.command_id);
    form.set("note_id", command.note_id);
    form.set("room_name", command.room_name);
    form.set("restore_source_savepoint_id", command.savepoint_id);
    form.set("expected_revision_head", String(command.expected_revision_head));
    form.set("expected_collaboration_epoch", String(command.expected_collaboration_epoch));
    form.set("request_fingerprint", command.request_fingerprint);
    form.set("canonical_body_json", JSON.stringify(input.canonicalBody));
    form.set("document_schema", "tiptap-prosemirror-v2");
    form.set("change_set_json", JSON.stringify(input.changeSet));
    form.set("idempotency_key", command.idempotency_key);
    form.set("update", new Blob([input.update]), "update.bin");
    form.set("state", new Blob([input.state]), "state.bin");
    return this.request("/commit-body-restore", { method: "POST", body: form }, 30_000).then(response => response.json() as Promise<BodyRestoreResult>);
  }
}
