import { randomUUID, timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { Server, type Connection, type Document } from "@hocuspocus/server";
import { TiptapTransformer } from "@hocuspocus/transformer";
import * as Y from "yjs";
import { NotesApiClient } from "./api-client.js";
import { canonicalBody, deriveChangeSet, mergeChangeSets } from "./changes.js";
import type { CarrierConfig } from "./config.js";
import { PromiseMutex, RoomState, systemTimer, type TimerDriver } from "./room-state.js";
import type { AuthorizationResult, BodyRestoreCommand, BodyRestoreResult, ChangeSet, JsonObject, LoadedDocument, NotesSettings } from "./types.js";
import { NOTE_EXTENSIONS } from "./note-extensions.js";
import {
  NOTES_COLLABORATION_HEALTH_PATH,
  NOTES_COLLABORATION_READINESS_PATH,
  NOTES_COLLABORATION_WEBSOCKET_PATH,
} from "./public.js";

const SYNC_STEP_2 = 1;
const SYNC_UPDATE = 2;
const MAX_COMMAND_BYTES = 64 * 1024;

export type ConnectionContext = AuthorizationResult;

function closeConnection(connection: Connection<ConnectionContext>, reason: string): void {
  connection.close({ code: 4403, reason } as Parameters<Connection<ConnectionContext>["close"]>[0]);
}

function copyXmlNode(node: Y.XmlElement | Y.XmlText): Y.XmlElement | Y.XmlText {
  if (node instanceof Y.XmlText) {
    const copy = new Y.XmlText();
    copy.applyDelta(node.toDelta());
    return copy;
  }
  if (node instanceof Y.XmlElement) {
    const copy = new Y.XmlElement(node.nodeName);
    const attributes = node.getAttributes();
    for (const [name, value] of Object.entries(attributes)) {
      if (value !== undefined) copy.setAttribute(name, value);
    }
    const children = node.toArray();
    copy.insert(0, children.map(child => copyXmlNode(child as Y.XmlElement | Y.XmlText)));
    return copy;
  }
  throw new Error("Unsupported Yjs XML node in canonical body");
}
function documentFromCanonicalBody(value: unknown): Y.Doc {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Restore source does not contain a canonical ProseMirror body");
  }
  const document = TiptapTransformer.toYdoc(value as JsonObject, "default", NOTE_EXTENSIONS) as Y.Doc;
  document.gc = false;
  return document;
}


function replaceBody(targetDocument: Y.Doc, sourceDocument: Y.Doc): Uint8Array {
  const target = targetDocument.getXmlFragment("default");
  const source = sourceDocument.getXmlFragment("default");
  const vector = Y.encodeStateVector(targetDocument);
  targetDocument.transact(() => {
    if (target.length > 0) target.delete(0, target.length);
    const children = source.toArray();
    if (children.length > 0) target.insert(0, children.map(child => copyXmlNode(child as Y.XmlElement | Y.XmlText)));
  }, "atlas-body-restore");
  return Y.encodeStateAsUpdate(targetDocument, vector);
}

function rebuildDocument(loaded: LoadedDocument): Y.Doc {
  const document = new Y.Doc({ gc: false });
  Y.applyUpdate(document, loaded.state, "atlas-load");
  for (const update of loaded.tail) Y.applyUpdate(document, update, "atlas-load");
  return document;
}

async function readJson(request: IncomingMessage): Promise<JsonObject> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > MAX_COMMAND_BYTES) throw new Error("Request body is too large");
    chunks.push(bytes);
  }
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Request body must be a JSON object");
  return parsed as JsonObject;
}

function sendJson(response: ServerResponse, status: number, value: unknown): void {
  const body = JSON.stringify(value);
  response.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
  response.end(body);
}

export class NotesCarrier {
  readonly server: Server<ConnectionContext>;
  private readonly rooms = new Map<string, RoomState>();
  private settingsValue: NotesSettings | null = null;
  private readonly roomMutexes = new Map<string, PromiseMutex>();
  private readonly messageReleases = new WeakMap<Connection<ConnectionContext>, () => void>();
  private readonly invalidatedEpochByNote = new Map<string, number>();

  constructor(
    private readonly config: CarrierConfig,
    private readonly api = new NotesApiClient(config),
    private readonly timerDriver: TimerDriver = systemTimer,
  ) {
    this.server = new Server<ConnectionContext>({
      address: config.host,
      port: config.port,
      quiet: true,
      yDocOptions: { gc: false, gcFilter: () => false },
      unloadImmediately: true,
      websocketOptions: { maxPayload: 16 * 1024 * 1024 },
      onAuthenticate: async payload => {
        const pathname = new URL(payload.request.url).pathname;
        if (pathname !== NOTES_COLLABORATION_WEBSOCKET_PATH && !pathname.startsWith(`${NOTES_COLLABORATION_WEBSOCKET_PATH}/`)) {
          throw new Error("Unknown collaboration WebSocket path");
        }
        const context = await api.authorize(payload.token, payload.documentName) as ConnectionContext;
        if (context.room_name !== payload.documentName) throw new Error("Authorized room does not match requested room");
        this.assertEpochActive(context.note_id, context.collaboration_epoch);
        const room = this.rooms.get(payload.documentName);
        if (room && (room.noteId !== context.note_id || room.context.collaboration_epoch !== context.collaboration_epoch)) {
          throw new Error("Authorized room no longer matches active room state");
        }
        payload.connectionConfig.readOnly = context.read_only;
        return context;
      },
      onLoadDocument: payload => this.loadDocument(payload.document, payload.context),
      beforeSync: payload => this.beforeSyncUpdate(payload.connection, payload.document, payload.type, payload.payload),
      afterHandleMessage: async payload => {
        const release = this.messageReleases.get(payload.connection);
        this.messageReleases.delete(payload.connection);
        release?.();
        this.cleanupRoomMutex(payload.documentName);
      },
      onDisconnect: payload => this.disconnect(payload.documentName, payload.clientsCount),
      onRequest: payload => this.handleHttp(payload.request, payload.response),
      onDestroy: async () => {
        for (const room of this.rooms.values()) room.cancelTimer();
        this.roomMutexes.clear();
        this.rooms.clear();
      },
    });
  }

  private roomMutex(roomName: string): PromiseMutex {
    let mutex = this.roomMutexes.get(roomName);
    if (!mutex) {
      mutex = new PromiseMutex();
      this.roomMutexes.set(roomName, mutex);
    }
    return mutex;
  }
  private assertEpochActive(noteId: string, epoch: number): void {
    if ((this.invalidatedEpochByNote.get(noteId) ?? 0) > epoch) {
      throw new Error("Notes collaboration epoch was invalidated");
    }
  }

  private assertOperationCurrent(room: RoomState | undefined, noteId: string, epoch: number): void {
    this.assertEpochActive(noteId, epoch);
    if (room && !this.isCurrentRoom(room)) throw new Error("Notes room generation was retired");
  }

  private cleanupRoomMutex(roomName: string): void {
    const mutex = this.roomMutexes.get(roomName);
    if (mutex?.isIdle && !this.rooms.has(roomName)) this.roomMutexes.delete(roomName);
  }

  private async withRoomMutex<T>(roomName: string, operation: () => Promise<T>): Promise<T> {
    const mutex = this.roomMutex(roomName);
    try {
      return await mutex.run(operation);
    } finally {
      this.cleanupRoomMutex(roomName);
    }
  }

  private async loadDocument(document: Document, context: ConnectionContext): Promise<Document> {
    return this.withRoomMutex(context.room_name, async () => {
      const existing = this.rooms.get(context.room_name);
      if (existing && existing.document !== document) {
        if (!existing.document?.isDestroyed) throw new Error("Room already has a different live document");
        existing.cancelTimer();
        this.rooms.delete(context.room_name);
      }
      const loaded = await this.api.load(context);
      if (loaded.manifest.note.note_id !== context.note_id || loaded.manifest.note.collaboration_epoch !== context.collaboration_epoch) {
        throw new Error("Loaded note does not match authorized connection");
      }
      this.assertEpochActive(context.note_id, context.collaboration_epoch);
      Y.applyUpdate(document, loaded.state, "atlas-load");
      for (const update of loaded.tail) Y.applyUpdate(document, update, "atlas-load");
      const currentBody = canonicalBody(document);
      const checkpointBodyValue = loaded.manifest.savepoint.canonical_body;
      if (!checkpointBodyValue || typeof checkpointBodyValue !== "object" || Array.isArray(checkpointBodyValue)) {
        throw new Error("Loaded savepoint has no canonical body");
      }
      const room = new RoomState(
        context.room_name,
        context.note_id,
        this.roomMutex(context.room_name),
        { ...context, accepted_update_head: loaded.manifest.note.accepted_update_head, savepoint_head: loaded.manifest.note.savepoint_head },
        currentBody,
        checkpointBodyValue as JsonObject,
        this.timerDriver,
      );
      room.document = document;
      room.revisionHead = loaded.manifest.note.accepted_update_head;
      room.savepointHead = loaded.manifest.note.savepoint_head;
      room.dirty = loaded.manifest.savepoint.covered_revision < loaded.manifest.note.accepted_update_head;
      const settings = await this.api.settings();
      this.assertEpochActive(context.note_id, context.collaboration_epoch);
      this.settingsValue = settings;
      this.rooms.set(context.room_name, room);
      this.scheduleCheckpoint(room, settings.checkpoint_interval_seconds);
      return document;
    });
  }

  private async revalidateConnections(
    room: RoomState,
    document: Document,
    sender: Connection<ConnectionContext>,
  ): Promise<ConnectionContext> {
    const connections = document.getConnections() as Array<Connection<ConnectionContext>>;
    let senderContext: ConnectionContext | null = null;
    let unavailable = false;
    await Promise.all(connections.map(async connection => {
      try {
        const checked = await this.api.revalidate(connection.context) as ConnectionContext;
        this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
        if (checked.room_name !== document.name || checked.note_id !== connection.context.note_id || checked.collaboration_epoch !== connection.context.collaboration_epoch) {
          throw new Error("Revalidation changed the stable room binding");
        }
        if (checked.read_only) connection.readOnly = true;
        connection.context = checked;
        if (connection === sender) senderContext = checked;
      } catch {
        this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
        closeConnection(connection, "Notes access was revoked");
        if (connection === sender) unavailable = true;
      }
    }));
    if (unavailable || !senderContext) throw new Error("Sender authorization could not be revalidated");
    return senderContext;
  }

  private fenceRoom(room: RoomState, reason: string): void {
    room.cancelTimer();
    for (const peer of room.document?.getConnections() ?? []) {
      closeConnection(peer as Connection<ConnectionContext>, reason);
    }
    if (this.rooms.get(room.roomName) === room) this.rooms.delete(room.roomName);
  }

  private async persistRevision(
    room: RoomState,
    context: ConnectionContext,
    canonicalBodyValue: JsonObject,
    changeSet: ChangeSet,
    update: Uint8Array,
  ): Promise<number> {
    const idempotencyKey = randomUUID();
    let lastError: unknown;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        const revision = await this.api.appendRevision({
          context,
          expectedHead: room.revisionHead,
          canonicalBody: canonicalBodyValue,
          changeSet,
          idempotencyKey,
          update,
        });
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        return revision.sequence;
      } catch (error) {
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        lastError = error;
        let loaded: LoadedDocument;
        try {
          loaded = await this.api.load(context);
        } catch {
          this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
          this.fenceRoom(room, "Notes persistence could not be reconciled");
          throw error;
        }
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        const authoritative = rebuildDocument(loaded);
        try {
          const before = Y.encodeStateAsUpdate(authoritative);
          Y.applyUpdate(authoritative, update, "atlas-reconcile");
          const accepted = Buffer.from(before).equals(Buffer.from(Y.encodeStateAsUpdate(authoritative)));
          if (accepted && loaded.manifest.note.accepted_update_head > room.revisionHead) {
            room.revisionHead = loaded.manifest.note.accepted_update_head;
            room.savepointHead = loaded.manifest.note.savepoint_head;
            room.currentBody = canonicalBodyValue;
            room.checkpointBody = loaded.manifest.savepoint.canonical_body as JsonObject;
            room.dirty = loaded.manifest.savepoint.covered_revision < room.revisionHead;
            return room.revisionHead;
          }
        } finally {
          authoritative.destroy();
        }
      }
    }
    throw lastError;
  }

  private async beforeSyncUpdate(
    connection: Connection<ConnectionContext>,
    document: Document,
    type: number,
    update: Uint8Array,
  ): Promise<void> {
    if (type !== SYNC_STEP_2 && type !== SYNC_UPDATE) return;
    const room = this.rooms.get(document.name);
    if (!room || room.document !== document) throw new Error("Room is not loaded");
    const release = await room.mutex.acquire();
    this.messageReleases.set(connection, release);
    try {
      this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
      const checked = await this.revalidateConnections(room, document, connection);
      this.assertOperationCurrent(room, checked.note_id, checked.collaboration_epoch);
      const candidate = new Y.Doc({ gc: false });
      try {
        const beforeState = Y.encodeStateAsUpdate(document);
        Y.applyUpdate(candidate, beforeState, "atlas-candidate");
        const before = room.currentBody;
        Y.applyUpdate(candidate, update, "atlas-candidate");
        const afterState = Y.encodeStateAsUpdate(candidate);
        if (Buffer.from(beforeState).equals(Buffer.from(afterState))) return;
        if (checked.read_only || connection.readOnly) throw new Error("Trashed notes are read-only");
        const after = canonicalBody(candidate);
        const changeSet = deriveChangeSet(before, after);
        const revisionSequence = await this.persistRevision(room, checked, after, changeSet, update);
        this.assertOperationCurrent(room, checked.note_id, checked.collaboration_epoch);
        room.context = checked;
        room.revisionHead = revisionSequence;
        room.currentBody = after;
        room.dirty = true;
      } finally {
        candidate.destroy();
      }
    } catch (error) {
      this.messageReleases.delete(connection);
      release();
      this.cleanupRoomMutex(document.name);
      throw error;
    }
  }

  private isCurrentRoom(room: RoomState): boolean {
    return this.rooms.get(room.roomName) === room
      && (this.invalidatedEpochByNote.get(room.noteId) ?? 0) <= room.context.collaboration_epoch;
  }

  private scheduleCheckpoint(room: RoomState, intervalSeconds: number): void {
    if (!this.isCurrentRoom(room)) return;
    room.schedule(intervalSeconds, async () => {
      if (this.isCurrentRoom(room)) await this.checkpoint(room);
    });
  }

  private async checkpointContext(room: RoomState): Promise<ConnectionContext> {
    for (const peer of room.document?.getConnections() ?? []) {
      this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
      try {
        const connection = peer as Connection<ConnectionContext>;
        const checked = await this.api.revalidate(connection.context) as ConnectionContext;
        if (!checked.read_only) {
          this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
          connection.context = checked;
          return checked;
        }
      } catch {
        this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
        closeConnection(peer as Connection<ConnectionContext>, "Notes access was revoked");
      }
    }
    this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
    const checked = await this.api.revalidate(room.context) as ConnectionContext;
    this.assertOperationCurrent(room, room.noteId, room.context.collaboration_epoch);
    if (checked.read_only) throw new Error("Read-only room cannot be checkpointed");
    return checked;
  }

  private async persistCheckpoint(room: RoomState, context: ConnectionContext): Promise<number> {
    if (!room.document) throw new Error("Room has no live document");
    const idempotencyKey = randomUUID();
    let lastError: unknown;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
      try {
        const savepoint = await this.api.appendSavepoint({
          context,
          revisionHead: room.revisionHead,
          savepointHead: room.savepointHead,
          canonicalBody: room.currentBody,
          changeSet: mergeChangeSets(room.checkpointBody, room.currentBody),
          idempotencyKey,
          state: Y.encodeStateAsUpdate(room.document),
        });
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        return savepoint.sequence;
      } catch (error) {
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        lastError = error;
        let loaded: LoadedDocument;
        try {
          loaded = await this.api.load(context);
        } catch {
          this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
          this.fenceRoom(room, "Notes checkpoint could not be reconciled");
          throw error;
        }
        this.assertOperationCurrent(room, context.note_id, context.collaboration_epoch);
        if (
          loaded.manifest.note.accepted_update_head === room.revisionHead
          && loaded.manifest.savepoint.covered_revision === room.revisionHead
          && loaded.manifest.note.savepoint_head > room.savepointHead
        ) {
          room.checkpointBody = loaded.manifest.savepoint.canonical_body as JsonObject;
          return loaded.manifest.note.savepoint_head;
        }
      }
    }
    throw lastError;
  }

  private async checkpointLocked(room: RoomState): Promise<void> {
    if (!this.isCurrentRoom(room)) return;
    const settings = await this.api.settings();
    if (!this.isCurrentRoom(room)) return;
    this.settingsValue = settings;
    if (!room.document || !room.dirty) {
      this.scheduleCheckpoint(room, settings.checkpoint_interval_seconds);
      return;
    }
    const checked = await this.checkpointContext(room);
    if (!this.isCurrentRoom(room)) return;
    this.assertEpochActive(checked.note_id, checked.collaboration_epoch);
    const savepointSequence = await this.persistCheckpoint(room, checked);
    if (!this.isCurrentRoom(room)) return;
    room.context = checked;
    room.savepointHead = savepointSequence;
    room.checkpointBody = room.currentBody;
    room.dirty = false;
    this.scheduleCheckpoint(room, settings.checkpoint_interval_seconds);
  }

  private async checkpoint(room: RoomState): Promise<void> {
    if (!this.isCurrentRoom(room)) return;
    try {
      await room.mutex.run(async () => {
        if (this.isCurrentRoom(room)) await this.checkpointLocked(room);
      });
    } catch {
      const interval = this.settingsValue?.checkpoint_interval_seconds;
      if (interval && this.isCurrentRoom(room)) this.scheduleCheckpoint(room, interval);
    }
  }

  private async disconnect(roomName: string, clientsCount: number): Promise<void> {
    if (clientsCount > 0) return;
    await this.withRoomMutex(roomName, async () => {
      const room = this.rooms.get(roomName);
      if (!room || (room.document?.getConnections().length ?? 0) > 0) return;
      try {
        if (room.dirty) await this.checkpointLocked(room);
      } catch {
        if (!this.isCurrentRoom(room)) return;
        room.cancelTimer();
        this.rooms.delete(roomName);
        return;
      }
      if (!this.isCurrentRoom(room)) return;
      if ((room.document?.getConnections().length ?? 0) > 0) return;
      room.cancelTimer();
      this.rooms.delete(roomName);
    });
  }

  async invalidateRoom(noteId: string, epoch: number): Promise<void> {
    this.invalidatedEpochByNote.set(noteId, Math.max(epoch, this.invalidatedEpochByNote.get(noteId) ?? 0));
    const roomNames = [...this.rooms.values()]
      .filter(room => room.noteId === noteId && room.context.collaboration_epoch < epoch)
      .map(room => room.roomName);
    await Promise.all(roomNames.map(roomName => this.withRoomMutex(roomName, async () => {
      const room = this.rooms.get(roomName);
      if (!room || room.noteId !== noteId || room.context.collaboration_epoch >= epoch) return;
      this.fenceRoom(room, "Notes room was invalidated");
    })));
  }

  async rescheduleSettings(expectedRevision: number): Promise<void> {
    const settings = await this.api.settings();
    if (settings.settings_revision < expectedRevision) throw new Error("Notes settings have not reached the requested revision");
    this.settingsValue = settings;
    for (const room of this.rooms.values()) this.scheduleCheckpoint(room, settings.checkpoint_interval_seconds);
  }

  async restoreBody(command: BodyRestoreCommand): Promise<BodyRestoreResult> {
    return this.withRoomMutex(command.room_name, async () => {
      const room = this.rooms.get(command.room_name);
      const restore = await this.api.restoreSource(command);
      this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
      if (restore.manifest.note.note_id !== command.note_id || restore.manifest.note.collaboration_epoch !== command.expected_collaboration_epoch) {
        throw new Error("Restore context does not match command");
      }
      const current = new Y.Doc({ gc: false });
      const source = documentFromCanonicalBody(restore.manifest.restore_source.canonical_body);
      try {
        Y.applyUpdate(current, restore.currentState, "atlas-restore-load");
        for (const update of restore.tail) Y.applyUpdate(current, update, "atlas-restore-load");
        const before = canonicalBody(current);
        const update = replaceBody(current, source);
        const after = canonicalBody(current);
        const changeSet = deriveChangeSet(before, after);
        if (room?.document) {
          const connections = room.document.getConnections() as Array<Connection<ConnectionContext>>;
          await Promise.all(connections.map(async connection => {
            try {
              const checked = await this.api.revalidate(connection.context) as ConnectionContext;
              this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
              connection.context = checked;
              if (checked.read_only) closeConnection(connection, "Notes access was revoked");
            } catch {
              this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
              closeConnection(connection, "Notes access was revoked");
            }
          }));
        }
        let result: BodyRestoreResult | null = null;
        let lastError: unknown;
        for (let attempt = 0; attempt < 2 && !result; attempt += 1) {
          try {
            this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
            result = await this.api.commitBodyRestore({
              command,
              canonicalBody: after,
              changeSet,
              update,
              state: Y.encodeStateAsUpdate(current),
            });
            this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
          } catch (error) {
            this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
            lastError = error;
          }
        }
        if (!result) {
          if (room) this.fenceRoom(room, "Notes restore could not be reconciled");
          throw lastError;
        }
        this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
        if (room?.document) {
          Y.applyUpdate(room.document, update, "atlas-body-restore");
          room.revisionHead = result.revision.sequence;
          room.savepointHead = result.savepoint.sequence;
          room.currentBody = after;
          room.checkpointBody = after;
          room.dirty = false;
          const settings = await this.api.settings();
          this.assertOperationCurrent(room, command.note_id, command.expected_collaboration_epoch);
          this.settingsValue = settings;
          this.scheduleCheckpoint(room, settings.checkpoint_interval_seconds);
        }
        return result;
      } finally {
        current.destroy();
        source.destroy();
      }
    });
  }

  private secretMatches(value: string | undefined): boolean {
    if (value === undefined) return false;
    const actual = Buffer.from(value);
    const expected = Buffer.from(this.config.internalSecret);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  }

  private async handleHttp(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const pathname = new URL(request.url || "/", "http://carrier.internal").pathname;
    try {
      if (request.method === "GET" && pathname === NOTES_COLLABORATION_HEALTH_PATH) {
        sendJson(response, 200, { status: "ok" });
        throw null;
      }
      if (!this.secretMatches(request.headers["x-atlas-notes-internal-secret"] as string | undefined)) {
        sendJson(response, 403, { status: "forbidden" });
        throw null;
      }
      if (request.method === "GET" && pathname === NOTES_COLLABORATION_READINESS_PATH) {
        await this.api.settings();
        sendJson(response, 200, { status: "ready" });
        throw null;
      }
      if (request.method === "POST" && pathname === "/internal/v1/rooms/invalidate") {
        const body = await readJson(request);
        if (typeof body.note_id !== "string" || typeof body.collaboration_epoch !== "number" || !Number.isInteger(body.collaboration_epoch)) throw new Error("Invalid room invalidation command");
        await this.invalidateRoom(body.note_id, body.collaboration_epoch);
        sendJson(response, 200, { status: "accepted" });
        throw null;
      }
      if (request.method === "POST" && pathname === "/internal/v1/settings/reschedule") {
        const body = await readJson(request);
        if (typeof body.settings_revision !== "number" || !Number.isInteger(body.settings_revision)) throw new Error("Invalid settings reschedule command");
        await this.rescheduleSettings(body.settings_revision);
        sendJson(response, 200, { status: "accepted" });
        throw null;
      }
      if (request.method === "POST" && pathname === "/internal/v1/restores") {
        const body = await readJson(request);
        const result = await this.restoreBody(body as unknown as BodyRestoreCommand);
        sendJson(response, 200, result);
        throw null;
      }
      sendJson(response, 404, { status: "not_found" });
      throw null;
    } catch (error) {
      if (error === null) throw null;
      if (!response.headersSent) sendJson(response, 503, { status: "unavailable" });
      throw null;
    }
  }

  listen(): Promise<unknown> {
    return this.server.listen();
  }

  destroy(): Promise<void> {
    return this.server.destroy();
  }
}
