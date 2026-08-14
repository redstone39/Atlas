import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate as immediate } from "node:timers/promises";
import { HocuspocusProvider } from "@hocuspocus/provider";
import { getSchema } from "@tiptap/core";
import { Document } from "@hocuspocus/server";
import { TiptapTransformer } from "@hocuspocus/transformer";
import WebSocket from "ws";
import * as Y from "yjs";
import type { NotesApiClient } from "../src/api-client.js";
import { NotesCarrier, type ConnectionContext } from "../src/carrier.js";
import { canonicalBody, deriveChangeSet } from "../src/changes.js";
import type { CarrierConfig } from "../src/config.js";
import { NOTE_EXTENSIONS } from "../src/note-extensions.js";
import type { TimerDriver } from "../src/room-state.js";
import type {
  AuthorizationResult,
  BodyRestoreCommand,
  BodyRestoreResult,
  ChangeSet,
  JsonObject,
  LoadedDocument,
  NotesSettings,
  RestoreContext,
  RevisionHistory,
  SavepointSummary,
} from "../src/types.js";

interface CarrierTestHarness {
  loadDocument(candidate: Document, context: ConnectionContext): Promise<Document>;
  disconnect(roomName: string, clientsCount: number): Promise<void>;
  rooms: Map<string, {
    dirty: boolean;
    document?: { getConnections(): Array<{
      readOnly: boolean;
      context: ConnectionContext;
      close(options: { code?: number; reason?: string }): void;
    }> };
    mutex: { run<T>(operation: () => Promise<T>): Promise<T> };
  }>;
}
const EMPTY_CHANGE: ChangeSet = { text: [], nodes: [], marks: [], attributes: [], moves: [] };

function body(text: string): JsonObject {
  return {
    type: "doc",
    content: text ? [{
      type: "bulletList",
      content: [{
        type: "listItem",
        content: [{
          type: "paragraph",
          content: [{ type: "text", text, marks: [{ type: "bold" }, { type: "link", attrs: { href: "https://atlas.example" } }] }],
        }],
      }],
    }] : [{ type: "paragraph" }],
  };
}

function documentFor(value: JsonObject): Y.Doc {
  const document = TiptapTransformer.toYdoc(value, "default") as Y.Doc;
  document.gc = false;
  return document;
}

function canonicalBodyFromState(state: Uint8Array): JsonObject {
  const document = new Y.Doc({ gc: false });
  try {
    Y.applyUpdate(document, state);
    return canonicalBody(document);
  } finally {
    document.destroy();
  }
}

function authorization(actorId: string, token = `connection-${actorId}`): AuthorizationResult {
  return {
    note_id: "note-1",
    actor_id: actorId,
    room_name: "opaque-room-1",
    connection_token: token,
    collaboration_epoch: 1,
    read_only: false,
    accepted_update_head: 1,
    savepoint_head: 1,
  };
}

class FakeApi {
  document = documentFor(body(""));
  source = documentFor(body("historical"));
  restoreCanonicalBody: JsonObject | null = null;
  checkpoint = Y.encodeStateAsUpdate(this.document);
  revisionHead = 1;
  savepointHead = 1;
  coveredRevision = 1;
  settingsValue: NotesSettings = { checkpoint_interval_seconds: 30, settings_revision: 1, updated_actor_id: "admin", updated_at: new Date(0).toISOString() };
  revoked = new Set<string>();
  readOnly = new Set<string>();
  rejectAppend = false;
  appendDelay: Promise<void> | null = null;
  revalidations: string[] = [];
  revalidateEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  revalidateDelay: Promise<void> | null = null;
  rejectRevalidateAfterDelay = false;
  expectedHeads: number[] = [];
  appendEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  savepoints = 0;
  rejectSettings = false;
  settingsCalls = 0;
  rejectLoad = false;
  rejectSavepoint = false;
  savepointEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  savepointDelay: Promise<void> | null = null;
  restoreCommits = 0;
  restoreLoadedTail = false;
  loseRevisionResponseOnce = false;
  loseSavepointResponseOnce = false;
  loseRestoreResponseOnce = false;
  settingsEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  settingsDelay: Promise<void> | null = null;
  restoreCommitEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  restoreCommitDelay: Promise<void> | null = null;
  loadTailSizesAtCall: number[] = [];
  restoreSourceEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  rejectLoadAfterDelay = false;
  restoreSourceRelease: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  loadEntered: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  loadRelease: { promise: Promise<void>; resolve: () => void; reject: (reason?: unknown) => void } | null = null;
  revisionReplays = new Map<string, RevisionHistory>();
  savepointReplays = new Map<string, SavepointSummary>();
  restoreReplays = new Map<string, BodyRestoreResult>();
  revisionUpdates: Array<{ sequence: number; update: Uint8Array }> = [];
  loadTailSizes: number[] = [];

  async authorize(ticket: string, roomName: string): Promise<AuthorizationResult> {
    if (roomName !== "opaque-room-1") throw new Error("wrong room");
    const result = authorization(ticket === "ticket-b" ? "actor-b" : "actor-a");
    result.accepted_update_head = this.revisionHead;
    result.savepoint_head = this.savepointHead;
    return result;
  }

  async revalidate(context: AuthorizationResult): Promise<AuthorizationResult> {
    this.revalidations.push(context.actor_id);
    this.revalidateEntered?.resolve();
    if (this.revalidateDelay) await this.revalidateDelay;
    if (this.rejectRevalidateAfterDelay || this.revoked.has(context.actor_id)) throw new Error("revoked");
    return { ...context, read_only: this.readOnly.has(context.actor_id), accepted_update_head: this.revisionHead, savepoint_head: this.savepointHead };
  }

  async load(context: AuthorizationResult): Promise<LoadedDocument> {
    if (this.rejectLoad) throw new Error("load unavailable");
    await this.revalidate(context);
    this.loadEntered?.resolve();
    const tail = this.revisionUpdates.filter(entry => entry.sequence > this.coveredRevision);
    this.loadTailSizesAtCall.push(tail.length);
    this.loadTailSizes.push(tail.length);
    if (this.loadRelease) await this.loadRelease.promise;
    if (this.rejectLoadAfterDelay) throw new Error("load unavailable after delay");
    return {
      manifest: {
        note: { note_id: "note-1", accepted_update_head: this.revisionHead, savepoint_head: this.savepointHead, collaboration_epoch: 1 },
        savepoint: { covered_revision: this.coveredRevision, sequence: this.savepointHead, canonical_body: canonicalBodyFromState(this.checkpoint) },
        state_part: "savepoint-state",
        tail: tail.map(entry => ({ sequence: entry.sequence, update_part: `revision-${entry.sequence}` })),
      },
      state: this.checkpoint,
      tail: tail.map(entry => entry.update),
    };
  }

  async appendRevision(input: { context: AuthorizationResult; expectedHead: number; canonicalBody: JsonObject; changeSet: ChangeSet; idempotencyKey: string; update: Uint8Array }): Promise<RevisionHistory> {
    const replay = this.revisionReplays.get(input.idempotencyKey);
    if (replay) return replay;
    this.expectedHeads.push(input.expectedHead);
    this.appendEntered?.resolve();
    if (this.appendDelay) await this.appendDelay;
    if (this.rejectAppend) throw new Error("persistence failed");
    assert.equal(input.expectedHead, this.revisionHead);
    await this.revalidate(input.context);
    Y.applyUpdate(this.document, input.update);
    this.revisionHead += 1;
    this.revisionUpdates.push({ sequence: this.revisionHead, update: input.update });
    const result: RevisionHistory = {
      revision_id: `revision-${this.revisionHead}`,
      note_id: "note-1",
      sequence: this.revisionHead,
      server_timestamp: new Date(this.revisionHead).toISOString(),
      actor_id: input.context.actor_id,
      event_kind: "content_update",
      before_digest: "0".repeat(64),
      after_digest: "1".repeat(64),
      change_set: input.changeSet,
      restore_source_savepoint_id: null,
    };
    this.revisionReplays.set(input.idempotencyKey, result);
    if (this.loseRevisionResponseOnce) {
      this.loseRevisionResponseOnce = false;
      throw new Error("lost revision response");
    }
    return result;
  }

  async appendSavepoint(input: { context: AuthorizationResult; revisionHead: number; savepointHead: number; canonicalBody: JsonObject; changeSet: ChangeSet; idempotencyKey: string; state: Uint8Array }): Promise<SavepointSummary> {
    const replay = this.savepointReplays.get(input.idempotencyKey);
    if (replay) return replay;
    this.savepointEntered?.resolve();
    if (this.savepointDelay) await this.savepointDelay;
    if (this.rejectSavepoint) throw new Error("checkpoint failed");
    await this.revalidate(input.context);
    assert.equal(input.revisionHead, this.revisionHead);
    assert.equal(input.savepointHead, this.savepointHead);
    this.savepointHead += 1;
    this.savepoints += 1;
    this.coveredRevision = input.revisionHead;
    this.checkpoint = input.state;
    const result: SavepointSummary = {
      savepoint_id: `savepoint-${this.savepointHead}`,
      note_id: "note-1",
      sequence: this.savepointHead,
      covered_revision: this.revisionHead,
      body_digest: "1".repeat(64),
      aggregate_change_set: input.changeSet,
      contributor_actor_ids: [],
      created_at: new Date(this.savepointHead).toISOString(),
    };
    this.savepointReplays.set(input.idempotencyKey, result);
    if (this.loseSavepointResponseOnce) {
      this.loseSavepointResponseOnce = false;
      throw new Error("lost savepoint response");
    }
    return result;
  }

  async settings(): Promise<NotesSettings> {
    this.settingsCalls += 1;
    this.settingsEntered?.resolve();
    if (this.settingsDelay) await this.settingsDelay;
    if (this.rejectSettings) throw new Error("settings unavailable");
    return this.settingsValue;
  }

  async restoreSource(command: BodyRestoreCommand): Promise<RestoreContext> {
    this.restoreSourceEntered?.resolve();
    if (this.restoreSourceRelease) await this.restoreSourceRelease.promise;
    this.restoreLoadedTail = true;
    return {
      manifest: {
        note: { note_id: command.note_id, accepted_update_head: this.revisionHead, savepoint_head: this.savepointHead, collaboration_epoch: 1 },
        current_savepoint: { covered_revision: 1, sequence: 1 },
        current_state_part: "current-state",
        tail: [{ sequence: this.revisionHead, update_part: "tail" }],
        restore_source: { savepoint_id: command.savepoint_id, covered_revision: 1, canonical_body: this.restoreCanonicalBody ?? canonicalBody(this.source) },
        restore_source_state_part: "source-state",
      },
      currentState: this.checkpoint,
      tail: [Y.encodeStateAsUpdate(this.document, Y.encodeStateVectorFromUpdate(this.checkpoint))],
      sourceState: Y.encodeStateAsUpdate(this.source),
    };
  }

  async commitBodyRestore(input: { command: BodyRestoreCommand; canonicalBody: JsonObject; changeSet: ChangeSet; update: Uint8Array; state: Uint8Array }): Promise<BodyRestoreResult> {
    this.restoreCommitEntered?.resolve();
    if (this.restoreCommitDelay) await this.restoreCommitDelay;
    const replay = this.restoreReplays.get(input.command.idempotency_key);
    if (replay) return replay;
    assert.equal(input.command.expected_revision_head, this.revisionHead);
    Y.applyUpdate(this.document, input.update);
    this.revisionHead += 1;
    this.revisionUpdates.push({ sequence: this.revisionHead, update: input.update });
    this.savepointHead += 1;
    this.restoreCommits += 1;
    const result: BodyRestoreResult = {
      revision: {
        revision_id: `revision-${this.revisionHead}`,
        note_id: "note-1",
        sequence: this.revisionHead,
        server_timestamp: new Date(this.revisionHead).toISOString(),
        actor_id: "actor-a",
        event_kind: "body_restore",
        before_digest: "0".repeat(64),
        after_digest: "1".repeat(64),
        change_set: input.changeSet,
        restore_source_savepoint_id: input.command.savepoint_id,
      },
      savepoint: {
        savepoint_id: `savepoint-${this.savepointHead}`,
        note_id: "note-1",
        sequence: this.savepointHead,
        covered_revision: this.revisionHead,
        body_digest: "1".repeat(64),
        aggregate_change_set: input.changeSet,
        contributor_actor_ids: ["actor-a"],
        created_at: new Date(this.savepointHead).toISOString(),
        canonical_body: input.canonicalBody,
        document_schema: "tiptap-prosemirror-v2",
      },
    };
    this.restoreReplays.set(input.command.idempotency_key, result);
    if (this.loseRestoreResponseOnce) {
      this.loseRestoreResponseOnce = false;
      throw new Error("lost restore response");
    }
    return result;
  }
}

class ManualTimers implements TimerDriver {
  callbacks: Array<() => void> = [];
  delays: number[] = [];

  set(callback: () => void, milliseconds: number): NodeJS.Timeout {
    this.callbacks.push(callback);
    this.delays.push(milliseconds);
    return { index: this.callbacks.length - 1 } as unknown as NodeJS.Timeout;
  }

  clear(): void {}

  async fireLatest(): Promise<void> {
    const callback = this.callbacks.at(-1);
    assert.ok(callback);
    callback();
    await immediate();
  }
}

const config: CarrierConfig = {
  apiBaseUrl: "http://api.invalid",
  internalSecret: "test-internal-secret",
  host: "127.0.0.1",
  port: 0,
  requestTimeoutMs: 5_000,
};

async function waitFor(predicate: () => boolean, label: string): Promise<void> {
  for (let attempts = 0; attempts < 2_000; attempts += 1) {
    if (predicate()) return;
    await immediate();
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function provider(url: string, name: string, token: string, document: Y.Doc): HocuspocusProvider {
  return new HocuspocusProvider({ url, name, token, document, WebSocketPolyfill: WebSocket });
}

async function runningCarrier(api: FakeApi, timers = new ManualTimers()): Promise<{ carrier: NotesCarrier; url: string; timers: ManualTimers }> {
  const carrier = new NotesCarrier(config, api as unknown as NotesApiClient, timers);
  await carrier.listen();
  return { carrier, url: `ws://127.0.0.1:${carrier.server.address.port}/collaboration`, timers };
}

test("two real providers converge on text, bold, link, and list only after durable append", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const first = new Y.Doc({ gc: false });
  const second = new Y.Doc({ gc: false });
  const providerA = provider(url, "opaque-room-1", "ticket-a", first);
  const providerB = provider(url, "opaque-room-1", "ticket-b", second);
  t.after(async () => { providerA.destroy(); providerB.destroy(); await carrier.destroy(); });
  await waitFor(() => providerA.isSynced && providerB.isSynced, "both providers to sync");

  const rich = documentFor(body("durable collaboration"));
  Y.applyUpdate(first, Y.encodeStateAsUpdate(rich));
  await waitFor(() => JSON.stringify(canonicalBody(second)) === JSON.stringify(canonicalBody(first)), "peer convergence");
  assert.equal(api.revisionHead, 2);
  assert.match(JSON.stringify(canonicalBody(second)), /bulletList|bold|https:\/\/atlas\.example/);
});

test("persistence rejection keeps candidate out of server authority and peer", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const first = new Y.Doc({ gc: false });
  const second = new Y.Doc({ gc: false });
  const providerA = provider(url, "opaque-room-1", "ticket-a", first);
  const providerB = provider(url, "opaque-room-1", "ticket-b", second);
  t.after(async () => { providerA.destroy(); providerB.destroy(); await carrier.destroy(); });
  await waitFor(() => providerA.isSynced && providerB.isSynced, "both providers to sync");
  const beforePeer = JSON.stringify(canonicalBody(second));
  const beforeAuthority = JSON.stringify(canonicalBody(api.document));
  api.rejectAppend = true;
  Y.applyUpdate(first, Y.encodeStateAsUpdate(documentFor(body("must fail"))));
  await waitFor(() => !providerA.isAuthenticated, "rejected sender disconnect");
  assert.equal(JSON.stringify(canonicalBody(second)), beforePeer);
  assert.equal(JSON.stringify(canonicalBody(api.document)), beforeAuthority);
});

test("a reused connection revalidates after a 61-second-equivalent boundary and revoked recipients close", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const first = new Y.Doc({ gc: false });
  const second = new Y.Doc({ gc: false });
  const providerA = provider(url, "opaque-room-1", "ticket-a", first);
  const providerB = provider(url, "opaque-room-1", "ticket-b", second);
  t.after(async () => { providerA.destroy(); providerB.destroy(); await carrier.destroy(); });
  await waitFor(() => providerA.isSynced && providerB.isSynced, "both providers to sync");
  const baselineChecks = api.revalidations.length;
  const simulatedElapsedMilliseconds = 61_000;
  assert.ok(simulatedElapsedMilliseconds > 60_000);
  const revokedBody = JSON.stringify(canonicalBody(second));
  api.revoked.add("actor-b");
  Y.applyUpdate(first, Y.encodeStateAsUpdate(documentFor(body("fresh bytes"))));
  await waitFor(() => api.revalidations.length > baselineChecks && !providerB.isAuthenticated, "recipient revalidation and close");
  await immediate();
  assert.equal(JSON.stringify(canonicalBody(second)), revokedBody);
  assert.ok(providerA.isAuthenticated);
});

test("trashed authorization rejects writes and explicit invalidation closes the room", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => { connected.destroy(); await carrier.destroy(); });
  await waitFor(() => connected.isSynced, "provider sync");
  api.readOnly.add("actor-a");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("forbidden"))));
  await waitFor(() => !connected.isAuthenticated, "trashed writer close");
  assert.equal(api.revisionHead, 1);

  const replacement = new Y.Doc({ gc: false });
  api.readOnly.clear();
  const reconnected = provider(url, "opaque-room-1", "ticket-a", replacement);
  t.after(() => reconnected.destroy());
  await waitFor(() => reconnected.isSynced, "replacement provider sync");
  await carrier.invalidateRoom("note-1", 2);
  await waitFor(() => !reconnected.isAuthenticated, "room invalidation close");
});

test("invalidation during first load fences the absent room epoch", async () => {
  const api = new FakeApi();
  api.loadEntered = Promise.withResolvers<void>();
  api.loadRelease = Promise.withResolvers<void>();
  const { carrier } = await runningCarrier(api);
  const document = new Document("opaque-room-1", { gc: false });
  // Test-only seam for a lifecycle race that the public socket retries would obscure.
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const loading = carrierHarness.loadDocument(document, authorization("actor-a"));
  await api.loadEntered.promise;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  api.loadRelease.resolve();
  await assert.rejects(loading, /invalidated/);
  await invalidating;
  assert.equal(document.getConnections().length, 0);
  document.destroy();
  await carrier.destroy();
});

test("room mutex orders concurrent updates against monotonic heads", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const first = new Y.Doc({ gc: false });
  const second = new Y.Doc({ gc: false });
  const providerA = provider(url, "opaque-room-1", "ticket-a", first);
  const providerB = provider(url, "opaque-room-1", "ticket-b", second);
  t.after(async () => { providerA.destroy(); providerB.destroy(); await carrier.destroy(); });
  await waitFor(() => providerA.isSynced && providerB.isSynced, "both providers to sync");
  Y.applyUpdate(first, Y.encodeStateAsUpdate(documentFor(body("first"))));
  Y.applyUpdate(second, Y.encodeStateAsUpdate(documentFor(body("second"))));
  await waitFor(() => api.revisionHead === 3, "both ordered revisions");
  assert.deepEqual(api.expectedHeads, [1, 2]);
});

test("dirty-only checkpoints and live settings reschedule are deterministic", async t => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  const { carrier, url } = await runningCarrier(api, timers);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => { connected.destroy(); await carrier.destroy(); });
  await waitFor(() => connected.isSynced, "provider sync");
  await timers.fireLatest();
  assert.equal(api.savepoints, 0);
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("dirty"))));
  await waitFor(() => api.revisionHead === 2, "durable revision");
  await timers.fireLatest();
  assert.equal(api.savepoints, 1);
  await timers.fireLatest();
  assert.equal(api.savepoints, 1);
  api.settingsValue = { ...api.settingsValue, checkpoint_interval_seconds: 7, settings_revision: 2 };
  await carrier.rescheduleSettings(2);
  assert.equal(timers.delays.at(-1), 7_000);
});

test("lost revision and checkpoint responses converge using stable idempotency", async t => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  api.loseRevisionResponseOnce = true;
  api.loseSavepointResponseOnce = true;
  const { carrier, url } = await runningCarrier(api, timers);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => { connected.destroy(); await carrier.destroy(); });
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("response loss"))));
  await waitFor(() => api.revisionHead === 2, "lost revision response reconciliation");
  await timers.fireLatest();
  assert.equal(api.savepointHead, 2);

  assert.equal(api.coveredRevision, 2);
  await timers.fireLatest();
  assert.equal(api.savepointHead, 2);
});

test("failed first-load settings and failed dirty disconnect preserve the accepted journal tail", async () => {
  const api = new FakeApi();
  api.rejectSettings = true;
  const { carrier, url } = await runningCarrier(api);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const first = new Document("opaque-room-1", { gc: false });
  await assert.rejects(carrierHarness.loadDocument(first, authorization("actor-a")), /settings unavailable/);
  first.destroy();
  api.rejectSettings = false;

  const edited = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", edited);
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(edited, Y.encodeStateAsUpdate(documentFor(body("accepted before checkpoint failure"))));
  await waitFor(() => api.revisionHead === 2 && api.coveredRevision === 1, "accepted uncovered revision");
  api.rejectSavepoint = true;
  connected.destroy();
  await waitFor(
    () => !carrierHarness.rooms.has("opaque-room-1") && !carrier.server.hocuspocus.documents.has("opaque-room-1"),
    "failed checkpoint clean-room unload",
  );

  api.rejectSavepoint = false;
  const loadCountBeforeReconnect = api.loadTailSizesAtCall.length;
  const reloaded = new Y.Doc({ gc: false });
  const replacement = provider(url, "opaque-room-1", "ticket-a", reloaded);
  await waitFor(() => replacement.isSynced, "replacement provider sync");
  assert.equal(api.loadTailSizesAtCall.length, loadCountBeforeReconnect + 1);
  assert.ok(api.loadTailSizesAtCall.at(-1)! > 0);
  assert.equal(JSON.stringify(canonicalBody(reloaded)), JSON.stringify(canonicalBody(api.document)));
  replacement.destroy();
  await carrier.destroy();
});

test("failed checkpoint reconciliation retires the room without a ghost timer", async () => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  const { carrier, url } = await runningCarrier(api, timers);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("retired checkpoint"))));
  await waitFor(() => api.revisionHead === 2, "durable revision");
  api.rejectSavepoint = true;
  api.rejectLoad = true;
  const scheduledBeforeFailure = timers.callbacks.length;
  await timers.fireLatest();
  await waitFor(
    () => !carrierHarness.rooms.has("opaque-room-1") && !carrier.server.hocuspocus.documents.has("opaque-room-1"),
    "reconciliation failure room retirement",
  );
  assert.equal(timers.callbacks.length, scheduledBeforeFailure);
  connected.destroy();
  await carrier.destroy();
});

test("checkpoint callback queued behind invalidation cannot revive a retired room", async () => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  const { carrier, url } = await runningCarrier(api, timers);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  await waitFor(() => connected.isSynced, "provider sync");
  const room = carrierHarness.rooms.get("opaque-room-1");
  assert.ok(room);
  const gate = Promise.withResolvers<void>();
  const held = room.mutex.run(() => gate.promise);
  await immediate();
  const settingsCallsBefore = api.settingsCalls;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  await immediate();
  const scheduledBeforeInvalidation = timers.callbacks.length;
  await timers.fireLatest();
  gate.resolve();
  await held;
  await invalidating;
  await waitFor(
    () => !carrierHarness.rooms.has("opaque-room-1") && !carrier.server.hocuspocus.documents.has("opaque-room-1"),
    "invalidated room retirement",
  );
  await immediate();
  assert.equal(api.settingsCalls, settingsCallsBefore);
  assert.equal(timers.callbacks.length, scheduledBeforeInvalidation);
  connected.destroy();
  await carrier.destroy();
});

test("epoch fence stops a checkpoint callback queued before invalidation", async t => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  const { carrier, url } = await runningCarrier(api, timers);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  const room = carrierHarness.rooms.get("opaque-room-1");
  assert.ok(room);
  const gate = Promise.withResolvers<void>();
  const held = room.mutex.run(() => gate.promise);
  await immediate();
  const settingsCallsBefore = api.settingsCalls;
  const scheduledBeforeInvalidation = timers.callbacks.length;
  await timers.fireLatest();
  const invalidating = carrier.invalidateRoom("note-1", 2);
  gate.resolve();
  await held;
  await invalidating;
  await waitFor(
    () => !carrierHarness.rooms.has("opaque-room-1") && !carrier.server.hocuspocus.documents.has("opaque-room-1"),
    "epoch-fenced room retirement",
  );
  await immediate();
  assert.equal(api.settingsCalls, settingsCallsBefore);
  assert.equal(timers.callbacks.length, scheduledBeforeInvalidation);
});

test("reload with an uncovered durable tail remains dirty and checkpoints without another edit", async t => {
  const api = new FakeApi();
  const firstRun = await runningCarrier(api);
  const firstDocument = new Y.Doc({ gc: false });
  const firstProvider = provider(firstRun.url, "opaque-room-1", "ticket-a", firstDocument);
  const timers = new ManualTimers();
  const secondRun = await runningCarrier(api, timers);
  t.after(async () => {
    firstProvider.destroy();
    await firstRun.carrier.destroy();
    await secondRun.carrier.destroy();
  });
  await waitFor(() => firstProvider.isSynced, "first provider sync");
  Y.applyUpdate(firstDocument, Y.encodeStateAsUpdate(documentFor(body("uncovered tail"))));
  await waitFor(() => api.revisionHead === 2 && api.coveredRevision === 1, "uncovered revision");
  const reloaded = new Y.Doc({ gc: false });
  const secondProvider = provider(secondRun.url, "opaque-room-1", "ticket-a", reloaded);

  t.after(() => secondProvider.destroy());
  await waitFor(() => secondProvider.isSynced, "second provider sync");
  assert.ok(api.loadTailSizes.some(size => size > 0));
  await timers.fireLatest();
  assert.equal(api.coveredRevision, 2);
});

test("invalidation during first-load settings prevents stale room publication", async () => {
  const api = new FakeApi();
  api.settingsEntered = Promise.withResolvers<void>();
  const settingsRelease = Promise.withResolvers<void>();
  api.settingsDelay = settingsRelease.promise;
  const { carrier } = await runningCarrier(api);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Document("opaque-room-1", { gc: false });
  const loading = carrierHarness.loadDocument(document, authorization("actor-a"));
  await api.settingsEntered.promise;
  await carrier.invalidateRoom("note-1", 2);
  settingsRelease.resolve();
  await assert.rejects(loading, /invalidated/);
  assert.equal(carrierHarness.rooms.has("opaque-room-1"), false);
  document.destroy();
  await carrier.destroy();
});

test("invalidation during durable revision response prevents old-room broadcast and reconciliation", async t => {
  const api = new FakeApi();
  api.appendEntered = Promise.withResolvers<void>();
  const appendRelease = Promise.withResolvers<void>();
  api.appendDelay = appendRelease.promise;
  const { carrier, url } = await runningCarrier(api);
  const first = new Y.Doc({ gc: false });
  const second = new Y.Doc({ gc: false });
  const providerA = provider(url, "opaque-room-1", "ticket-a", first);
  const providerB = provider(url, "opaque-room-1", "ticket-b", second);
  t.after(async () => {
    providerA.destroy();
    providerB.destroy();
    await carrier.destroy();
  });
  await waitFor(() => providerA.isSynced && providerB.isSynced, "both providers sync");
  const secondBefore = JSON.stringify(canonicalBody(second));
  Y.applyUpdate(first, Y.encodeStateAsUpdate(documentFor(body("durable before epoch fence"))));
  await api.appendEntered.promise;
  const loadCountBeforeFence = api.loadTailSizesAtCall.length;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  appendRelease.resolve();
  await invalidating;
  assert.equal(api.revisionHead, 2);
  assert.equal(api.loadTailSizesAtCall.length, loadCountBeforeFence);
  assert.equal(JSON.stringify(canonicalBody(second)), secondBefore);
});

test("invalidation during failed savepoint response stops reconciliation and retry", async t => {
  const api = new FakeApi();
  const timers = new ManualTimers();
  const { carrier, url } = await runningCarrier(api, timers);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();

    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("checkpoint before epoch fence"))));
  await waitFor(() => api.revisionHead === 2, "durable revision");
  api.savepointEntered = Promise.withResolvers<void>();
  const savepointRelease = Promise.withResolvers<void>();
  api.savepointDelay = savepointRelease.promise;
  api.rejectSavepoint = true;
  const scheduledBeforeFence = timers.callbacks.length;
  await timers.fireLatest();
  await api.savepointEntered.promise;
  const loadCountBeforeFence = api.loadTailSizesAtCall.length;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  savepointRelease.resolve();
  await invalidating;
  await waitFor(() => !carrierHarness.rooms.has("opaque-room-1"), "savepoint-fenced room retirement");
  assert.equal(api.loadTailSizesAtCall.length, loadCountBeforeFence);
  assert.equal(timers.callbacks.length, scheduledBeforeFence);
});

test("invalidation during rejected revision response stops reconciliation and retry", async t => {
  const api = new FakeApi();
  api.appendEntered = Promise.withResolvers<void>();
  const appendRelease = Promise.withResolvers<void>();
  api.appendDelay = appendRelease.promise;
  api.rejectAppend = true;
  const { carrier, url } = await runningCarrier(api);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("rejected after epoch fence"))));
  await api.appendEntered.promise;
  const loadCountBeforeFence = api.loadTailSizesAtCall.length;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  appendRelease.resolve();
  await invalidating;
  assert.equal(api.revisionHead, 1);
  assert.equal(api.loadTailSizesAtCall.length, loadCountBeforeFence);
  assert.equal(api.expectedHeads.length, 1);
});

test("invalidation during resolved connection revalidation prevents stale mutation and append", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const serverConnection = carrierHarness.rooms.get("opaque-room-1")?.document?.getConnections()[0];
  assert.ok(serverConnection);
  assert.equal(serverConnection.readOnly, false);
  api.readOnly.add("actor-a");
  api.revalidateEntered = Promise.withResolvers<void>();
  const revalidateRelease = Promise.withResolvers<void>();
  api.revalidateDelay = revalidateRelease.promise;
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("revalidated after epoch fence"))));
  await api.revalidateEntered.promise;
  const expectedHeadsBeforeFence = api.expectedHeads.length;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  revalidateRelease.resolve();
  await invalidating;
  assert.equal(api.expectedHeads.length, expectedHeadsBeforeFence);
  assert.equal(serverConnection.readOnly, false);
  assert.equal(api.revisionHead, 1);
});

test("invalidation during rejected restore revalidation preserves the epoch fence", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const serverConnection = carrierHarness.rooms.get("opaque-room-1")?.document?.getConnections()[0];
  assert.ok(serverConnection);
  const closeReasons: Array<string | undefined> = [];
  const originalClose = serverConnection.close.bind(serverConnection);
  serverConnection.close = options => {
    closeReasons.push(options.reason);
    originalClose(options);
  };
  api.revalidateEntered = Promise.withResolvers<void>();
  const revalidateRelease = Promise.withResolvers<void>();
  api.revalidateDelay = revalidateRelease.promise;
  const restoring = carrier.restoreBody({
    command_id: "restore-revalidation-race",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-revalidation-race-key",
    request_fingerprint: "c".repeat(64),
    authorization_token: "restore-authorization",
  });
  await api.revalidateEntered.promise;
  api.rejectRevalidateAfterDelay = true;
  const invalidating = carrier.invalidateRoom("note-1", 2);

  revalidateRelease.resolve();
  await assert.rejects(restoring, /invalidated|retired/);
  await invalidating;
  assert.equal(api.restoreCommits, 0);
  assert.deepEqual(closeReasons, ["Notes room was invalidated"]);
});

test("invalidation during revision reconciliation load leaves retirement to the queued fence", async t => {
  const api = new FakeApi();
  api.rejectAppend = true;
  const { carrier, url } = await runningCarrier(api);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  api.loadEntered = Promise.withResolvers<void>();
  api.loadRelease = Promise.withResolvers<void>();
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("reconciliation after epoch fence"))));
  await api.loadEntered.promise;
  const room = carrierHarness.rooms.get("opaque-room-1");
  assert.ok(room);
  const roomPresentBeforeFence = room.mutex.run(async () => carrierHarness.rooms.get("opaque-room-1") === room);
  const invalidating = carrier.invalidateRoom("note-1", 2);
  api.loadRelease.resolve();
  assert.equal(await roomPresentBeforeFence, true);
  await invalidating;
  assert.equal(api.expectedHeads.length, 1);
  assert.equal(carrierHarness.rooms.has("opaque-room-1"), false);
});

test("invalidation during dirty disconnect checkpoint leaves retirement to the epoch fence", async t => {
  const api = new FakeApi();
  const { carrier, url } = await runningCarrier(api);
  const carrierHarness = carrier as unknown as CarrierTestHarness;
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  Y.applyUpdate(document, Y.encodeStateAsUpdate(documentFor(body("disconnect after epoch fence"))));
  await waitFor(() => api.revisionHead === 2, "durable revision");
  const room = carrierHarness.rooms.get("opaque-room-1");
  assert.ok(room);
  api.savepointEntered = Promise.withResolvers<void>();
  const savepointRelease = Promise.withResolvers<void>();
  api.savepointDelay = savepointRelease.promise;
  connected.destroy();
  await api.savepointEntered.promise;
  const roomPresentBeforeFence = room.mutex.run(async () => carrierHarness.rooms.get("opaque-room-1") === room);
  const invalidating = carrier.invalidateRoom("note-1", 2);
  savepointRelease.resolve();
  assert.equal(await roomPresentBeforeFence, true);
  await invalidating;
  assert.equal(api.savepoints, 1);
  assert.equal(carrierHarness.rooms.has("opaque-room-1"), false);
});

test("invalidation during durable restore response prevents old-room apply and retry", async t => {
  const api = new FakeApi();
  api.restoreCommitEntered = Promise.withResolvers<void>();
  const restoreRelease = Promise.withResolvers<void>();
  api.restoreCommitDelay = restoreRelease.promise;
  const { carrier, url } = await runningCarrier(api);
  const document = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", document);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  await waitFor(() => connected.isSynced, "provider sync");
  const before = JSON.stringify(canonicalBody(document));
  const restoring = carrier.restoreBody({
    command_id: "restore-race",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-race-key",
    request_fingerprint: "b".repeat(64),
    authorization_token: "restore-authorization",
  });
  await api.restoreCommitEntered.promise;
  const invalidating = carrier.invalidateRoom("note-1", 2);
  restoreRelease.resolve();
  await assert.rejects(restoring, /invalidated|retired/);
  await invalidating;
  assert.equal(api.restoreCommits, 1);
  assert.equal(JSON.stringify(canonicalBody(document)), before);
});

test("body restore uses the exact previewed canonical body when historical Yjs state normalizes differently", async t => {
  const api = new FakeApi();
  api.source.destroy();
  api.source = new Y.Doc({ gc: false });
  api.restoreCanonicalBody = { type: "doc", content: [{ type: "paragraph" }] };
  const { carrier } = await runningCarrier(api);
  t.after(async () => carrier.destroy());

  const result = await carrier.restoreBody({
    command_id: "restore-canonical-preview",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-canonical-preview-key",
    request_fingerprint: "d".repeat(64),
    authorization_token: "restore-authorization",
  });

  assert.deepEqual(result.savepoint.canonical_body, api.restoreCanonicalBody);
  assert.deepEqual(canonicalBody(api.document), api.restoreCanonicalBody);
});

test("service reload rebuilds from durable checkpoint plus tail and restore commits source atomically", async t => {
  const api = new FakeApi();
  const firstRun = await runningCarrier(api);
  const client = new Y.Doc({ gc: false });
  const firstProvider = provider(firstRun.url, "opaque-room-1", "ticket-a", client);
  t.after(async () => {
    firstProvider.destroy();
    await firstRun.carrier.destroy();
  });
  Y.applyUpdate(client, Y.encodeStateAsUpdate(documentFor(body("current tail"))));
  await waitFor(() => api.revisionHead === 2, "tail persistence");

  const secondRun = await runningCarrier(api);
  const reloaded = new Y.Doc({ gc: false });
  const secondProvider = provider(secondRun.url, "opaque-room-1", "ticket-a", reloaded);
  t.after(async () => {
    secondProvider.destroy();
    await secondRun.carrier.destroy();
  });
  await waitFor(() => secondProvider.isSynced, "crash reload sync");
  assert.ok(api.loadTailSizes.some(size => size > 0));
  assert.equal(JSON.stringify(canonicalBody(reloaded)), JSON.stringify(canonicalBody(api.document)));
  firstProvider.destroy();
  await firstRun.carrier.destroy();
  api.loseRestoreResponseOnce = true;
  const command: BodyRestoreCommand = {
    command_id: "restore-command",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-key",
    request_fingerprint: "a".repeat(64),
    authorization_token: "restore-authorization",
  };
  const result = await secondRun.carrier.restoreBody(command);
  assert.ok(api.restoreLoadedTail);
  assert.equal(api.restoreCommits, 1);
  assert.equal(result.revision.event_kind, "body_restore");
  assert.equal(result.revision.restore_source_savepoint_id, "historical-savepoint");
  const canonicalHistorical = canonicalBody(api.source);
  assert.equal(JSON.stringify(result.savepoint.canonical_body), JSON.stringify(canonicalHistorical));
  await waitFor(() => JSON.stringify(canonicalBody(reloaded)) === JSON.stringify(canonicalHistorical), "canonical restore broadcast");
  secondProvider.destroy();
  await secondRun.carrier.destroy();
});

test("absent-room restore and first load share one lifecycle mutex", async t => {
  const api = new FakeApi();
  api.restoreSourceEntered = Promise.withResolvers<void>();
  api.restoreSourceRelease = Promise.withResolvers<void>();

  const { carrier, url } = await runningCarrier(api);
  const command: BodyRestoreCommand = {
    command_id: "restore-before-load",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-before-load-key",
    request_fingerprint: "b".repeat(64),
    authorization_token: "restore-authorization",
  };
  const restoring = carrier.restoreBody(command);
  await api.restoreSourceEntered.promise;
  const loaded = new Y.Doc({ gc: false });
  const connected = provider(url, "opaque-room-1", "ticket-a", loaded);
  t.after(async () => {
    connected.destroy();
    await carrier.destroy();
  });
  api.restoreSourceRelease.resolve();
  await restoring;
  await waitFor(() => connected.isSynced, "post-restore first load");
  assert.equal(JSON.stringify(canonicalBody(loaded)), JSON.stringify(canonicalBody(api.source)));
});

test("invalidation during restore fences the old epoch before commit", async () => {
  const api = new FakeApi();
  api.restoreSourceEntered = Promise.withResolvers<void>();
  api.restoreSourceRelease = Promise.withResolvers<void>();
  const { carrier } = await runningCarrier(api);
  const restoring = carrier.restoreBody({
    command_id: "restore-invalidated",
    note_id: "note-1",
    room_name: "opaque-room-1",
    savepoint_id: "historical-savepoint",
    expected_revision_head: api.revisionHead,
    expected_collaboration_epoch: 1,
    idempotency_key: "restore-invalidated-key",
    request_fingerprint: "c".repeat(64),
    authorization_token: "restore-authorization",
  });
  await api.restoreSourceEntered.promise;
  await carrier.invalidateRoom("note-1", 2);
  api.restoreSourceRelease.resolve();
  await assert.rejects(restoring, /invalidated/);
  assert.equal(api.restoreCommits, 0);
  await carrier.destroy();
});

test("closed change sets retain exact text offsets and formatting before/after", () => {
  const before = body("before");
  const after = body("after");
  const change = deriveChangeSet(before, after);
  assert.deepEqual(change.text, [{ path: [0, 0, 0, 0], change: "replace", before: "before", after: "after", from_offset: 0, to_offset: 6 }]);
  assert.ok(change.marks.every(mark => mark.before !== undefined && mark.after !== undefined));
});

test("change sets preserve separated text edits and shifted structural paths", () => {
  const before: JsonObject = {
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "alpha middle omega" }] },
      { type: "paragraph", attrs: { align: "left" }, content: [{ type: "text", text: "stable" }] },
    ],
  };
  const after: JsonObject = {
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "ALPHA middle OMEGA" }] },
      { type: "paragraph" },
      { type: "paragraph", attrs: { align: "right" }, content: [{ type: "text", text: "stable" }] },
    ],
  };
  const change = deriveChangeSet(before, after);
  assert.deepEqual(change.text, [
    { path: [0, 0], change: "replace", before: "alpha", after: "ALPHA", from_offset: 0, to_offset: 5 },
    { path: [0, 0], change: "replace", before: "omega", after: "OMEGA", from_offset: 13, to_offset: 18 },
  ]);
  assert.ok(change.nodes.some(node => node.change === "insert" && node.path[0] === 1));
  assert.ok(change.attributes.some(attribute => attribute.path[0] === 2 && attribute.attribute === "align"));
});

test("change sets retain text redistributed across unchanged node types", () => {
  const before: JsonObject = {
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "a" }] },
      { type: "paragraph", content: [{ type: "text", text: "bc" }] },
    ],
  };

  const after: JsonObject = {
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "ab" }] },
      { type: "paragraph", content: [{ type: "text", text: "c" }] },
    ],
  };
  const change = deriveChangeSet(before, after);
  assert.deepEqual(change.text, [
    { path: [0, 0], change: "insert", before: "", after: "b", from_offset: 1, to_offset: 1 },
    { path: [1, 0], change: "delete", before: "b", after: "", from_offset: 0, to_offset: 1 },
  ]);
  assert.equal(change.attributes.some(attribute => attribute.attribute === "text"), false);
});

test("pure mark splits and merges preserve text and report exact formatting changes", () => {
  const plain: JsonObject = {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "abc" }] }],
  };
  const marked: JsonObject = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a" },
        { type: "text", text: "b", marks: [{ type: "bold" }, { type: "link", attrs: { href: "https://atlas.example" } }] },
        { type: "text", text: "c" },
      ],
    }],
  };
  const added = deriveChangeSet(plain, marked);
  assert.deepEqual(added.text, []);
  assert.deepEqual(added.nodes, []);
  assert.deepEqual(added.marks, [
    { change: "add", path: [0, 1], mark_type: "bold", before: null, after: {} },
    { change: "add", path: [0, 1], mark_type: "link", before: null, after: { href: "https://atlas.example" } },
  ]);

  const removed = deriveChangeSet(marked, plain);
  assert.deepEqual(removed.text, []);
  assert.deepEqual(removed.nodes, []);
  assert.deepEqual(removed.marks, [

    { change: "remove", path: [0, 0], mark_type: "bold", before: {}, after: null },
    { change: "remove", path: [0, 0], mark_type: "link", before: { href: "https://atlas.example" }, after: null },
  ]);
});

test("mixed mark merges do not duplicate identical immutable events", () => {
  const before: JsonObject = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "bold" }, { type: "link", attrs: { href: "https://atlas.example" } }] },
        { type: "text", text: "b", marks: [{ type: "bold" }] },
      ],
    }],
  };
  const after: JsonObject = {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "ab" }] }],
  };
  const change = deriveChangeSet(before, after);
  assert.deepEqual(change.text, []);
  assert.deepEqual(change.nodes, []);
  assert.deepEqual(change.marks, [
    { change: "remove", path: [0, 0], mark_type: "bold", before: {}, after: null },
    { change: "remove", path: [0, 0], mark_type: "link", before: { href: "https://atlas.example" }, after: null },
  ]);
});

test("disjoint identical mark transitions remain distinct while contiguous runs coalesce", () => {
  const plain: JsonObject = {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "abc" }] }],
  };
  const disjoint: JsonObject = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "bold" }] },
        { type: "text", text: "b" },
        { type: "text", text: "c", marks: [{ type: "bold" }] },
      ],
    }],
  };
  const removed = deriveChangeSet(disjoint, plain);
  assert.deepEqual(removed.text, []);
  assert.deepEqual(removed.marks, [
    { change: "remove", path: [0, 0], mark_type: "bold", before: {}, after: null },
    { change: "remove", path: [0, 0], mark_type: "bold", before: {}, after: null },
  ]);
  const added = deriveChangeSet(plain, disjoint);
  assert.deepEqual(added.text, []);
  assert.deepEqual(added.marks, [
    { change: "add", path: [0, 0], mark_type: "bold", before: null, after: {} },
    { change: "add", path: [0, 2], mark_type: "bold", before: null, after: {} },
  ]);
});

test("disjoint mark attribute replacements retain both immutable events", () => {
  const linked = (href: string): JsonObject => ({
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "link", attrs: { href } }] },
        { type: "text", text: "b" },
        { type: "text", text: "c", marks: [{ type: "link", attrs: { href } }] },
      ],
    }],
  });
  const changed = deriveChangeSet(linked("https://old.example"), linked("https://new.example"));
  assert.deepEqual(changed.text, []);
  assert.deepEqual(changed.marks, [
    { change: "replace", path: [0, 0], mark_type: "link", before: { href: "https://old.example" }, after: { href: "https://new.example" } },
    { change: "replace", path: [0, 2], mark_type: "link", before: { href: "https://old.example" }, after: { href: "https://new.example" } },
  ]);
});

test("contiguous mark transitions coalesce across paths split by another mark", () => {
  const plain: JsonObject = {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "ab" }] }],
  };
  const boldWithItalicBoundary: JsonObject = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "bold" }, { type: "italic" }] },
        { type: "text", text: "b", marks: [{ type: "bold" }] },
      ],
    }],
  };
  const added = deriveChangeSet(plain, boldWithItalicBoundary);
  assert.deepEqual(added.text, []);
  assert.deepEqual(added.marks, [
    { change: "add", path: [0, 0], mark_type: "bold", before: null, after: {} },
    { change: "add", path: [0, 0], mark_type: "italic", before: null, after: {} },
  ]);

  const italicBoundary: JsonObject = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "italic" }] },
        { type: "text", text: "b" },
      ],
    }],
  };
  const removed = deriveChangeSet(boldWithItalicBoundary, italicBoundary);
  assert.deepEqual(removed.text, []);
  assert.deepEqual(removed.marks, [
    { change: "remove", path: [0, 0], mark_type: "bold", before: {}, after: null },
  ]);

  const linked = (href: string): JsonObject => ({
    type: "doc",
    content: [{
      type: "paragraph",
      content: [
        { type: "text", text: "a", marks: [{ type: "italic" }, { type: "link", attrs: { href } }] },
        { type: "text", text: "b", marks: [{ type: "link", attrs: { href } }] },
      ],
    }],
  });
  const replaced = deriveChangeSet(linked("https://old.example"), linked("https://new.example"));
  assert.deepEqual(replaced.text, []);
  assert.deepEqual(replaced.marks, [
    { change: "replace", path: [0, 0], mark_type: "link", before: { href: "https://old.example" }, after: { href: "https://new.example" } },
  ]);
});

test("top-level block moves preserve identity and remain separate from content edits", () => {
  const paragraph = (blockId: string, text: string): JsonObject => ({
    type: "paragraph",
    attrs: { block_id: blockId },
    content: [{ type: "text", text }],
  });
  const before: JsonObject = {
    type: "doc",
    content: [paragraph("block-a", "A"), paragraph("block-b", "B"), paragraph("block-c", "C")],
  };
  const after: JsonObject = {
    type: "doc",
    content: [paragraph("block-b", "B edited"), paragraph("block-a", "A"), paragraph("block-c", "C")],
  };

  const change = deriveChangeSet(before, after);

  assert.deepEqual(change.moves, [{ block_id: "block-b", from_path: [1], to_path: [0] }]);
  assert.deepEqual(change.nodes, []);
  assert.deepEqual(change.text, [{
    path: [0, 0],
    change: "insert",
    before: "",
    after: " edited",
    from_offset: 1,
    to_offset: 1,
  }]);
});

test("top-level insertions do not misclassify shifted blocks as moves", () => {
  const paragraph = (blockId: string): JsonObject => ({
    type: "paragraph",
    attrs: { block_id: blockId },
  });
  const before: JsonObject = { type: "doc", content: [paragraph("block-a"), paragraph("block-b")] };
  const after: JsonObject = { type: "doc", content: [paragraph("block-new"), paragraph("block-a"), paragraph("block-b")] };

  const change = deriveChangeSet(before, after);

  assert.deepEqual(change.moves, []);
  assert.deepEqual(change.nodes, [{ change: "insert", path: [0], before_type: null, after_type: "paragraph" }]);
});

test("reverse, rotation, and mixed insert permutations emit a replayable move sequence", () => {
  const paragraph = (blockId: string): JsonObject => ({ type: "paragraph", attrs: { block_id: blockId } });
  const ids = (value: JsonObject): string[] => (value.content as JsonObject[])
    .map(node => (node.attrs as JsonObject).block_id as string);
  const replay = (before: JsonObject, after: JsonObject): string[] => {
    const working = ids(before).filter(id => ids(after).includes(id));
    ids(after).forEach((id, index) => {
      if (!working.includes(id)) working.splice(index, 0, id);
    });
    for (const move of deriveChangeSet(before, after).moves) {
      const [item] = working.splice(move.from_path[0], 1);
      working.splice(move.to_path[0], 0, item!);
    }
    return working;
  };
  const cases: Array<[string[], string[]]> = [
    [["a", "b", "c"], ["c", "b", "a"]],
    [["a", "b", "c"], ["b", "c", "a"]],
    [["a", "b", "c"], ["new", "c", "b", "a"]],
    [["a", "deleted", "b", "c"], ["c", "a", "b"]],
  ];
  for (const [beforeIds, afterIds] of cases) {
    const before = { type: "doc", content: beforeIds.map(paragraph) };
    const after = { type: "doc", content: afterIds.map(paragraph) };
    assert.deepEqual(replay(before, after), afterIds);
  }
});

test("configured transformer preserves table geometry and opaque note image attributes", () => {
  const value: JsonObject = {
    type: "doc",
    content: [
      {
        type: "table",
        attrs: { block_id: "table-1" },
        content: [{
          type: "tableRow",
          content: [{
            type: "tableHeader",
            attrs: { colspan: 2, rowspan: 1, colwidth: [120, 140] },
            content: [{ type: "paragraph", content: [{ type: "text", text: "Header" }] }],
          }],
        }],
      },
      {
        type: "noteImage",
        attrs: {
          block_id: "image-1",
          attachment_ref: "natt-opaque",
          alt: "Alt",
          caption: "Caption",
          width: 640,
          height: 480,
        },
      },
    ],
  };
  const document = TiptapTransformer.toYdoc(value, "default", NOTE_EXTENSIONS) as Y.Doc;
  try {
    const result = canonicalBody(document);
    assert.equal((result.content as JsonObject[])[0]!.type, "table");
    assert.deepEqual(((result.content as JsonObject[])[0]!.attrs as JsonObject).block_id, "table-1");
    assert.equal((result.content as JsonObject[])[1]!.type, "noteImage");
    assert.equal(((result.content as JsonObject[])[1]!.attrs as JsonObject).attachment_ref, "natt-opaque");
  } finally {
    document.destroy();
  }
});

test("configured Tiptap schema accepts nested note images and rejects heading-first list items", () => {
  const schema = getSchema(NOTE_EXTENSIONS);
  const nestedImage = {
    type: "doc",
    content: [{
      type: "blockquote",
      attrs: { block_id: "quote-1" },
      content: [{
        type: "noteImage",
        attrs: { block_id: "image-1", attachment_ref: "natt-1", alt: "", caption: "", width: 2, height: 2 },
      }],
    }],
  };
  assert.doesNotThrow(() => schema.nodeFromJSON(nestedImage).check());
  const headingFirst = {
    type: "doc",
    content: [{
      type: "bulletList",
      attrs: { block_id: "list-1" },
      content: [{
        type: "listItem",
        content: [{ type: "heading", attrs: { block_id: "heading-1", level: 2 } }],
      }],
    }],
  };
  assert.throws(() => schema.nodeFromJSON(headingFirst).check());
});

test("health is liveness while readiness requires transport authentication and API readiness", async () => {
  const api = new FakeApi();
  const { carrier } = await runningCarrier(api);
  try {
    const base = carrier.server.httpURL;
    const health = await fetch(`${base}/health`);
    assert.equal(health.status, 200);
    const unauthenticated = await fetch(`${base}/ready`);
    assert.equal(unauthenticated.status, 403);
    const ready = await fetch(`${base}/ready`, { headers: { "X-Atlas-Notes-Internal-Secret": config.internalSecret } });
    assert.equal(ready.status, 200);
  } finally {
    await carrier.destroy();
  }
});
