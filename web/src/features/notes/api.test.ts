import { beforeEach, describe, expect, it, vi } from "vitest";

import { notesApi } from "./api";
import type { NoteDetail, NoteRevision, NoteScope } from "./types";

const scope: NoteScope = { scope_type: "team", scope_id: "team-1", label: "Team One" };
const note: NoteDetail = {
  note_id: "note-1",
  scope,
  category_id: null,
  title: "Shared note",
  lifecycle_status: "active",
  metadata_revision: 3,
  accepted_update_head: 7,
  savepoint_head: 2,
  collaboration_epoch: 4,
  updated_actor_id: "actor-1",
  updated_at: "2026-08-13T00:00:00Z",
  created_actor_id: "actor-1",
  created_at: "2026-08-13T00:00:00Z",
  trashed_actor_id: null,
  trashed_at: null,
};

describe("Notes REST consumer", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "request-key") });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(note), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
  });

  it("creates notes when randomUUID is unavailable", async () => {
    vi.stubGlobal("crypto", {
      getRandomValues: vi.fn((bytes: Uint8Array) => {
        bytes.fill(10);
        return bytes;
      }),
    });

    await notesApi.createNote({
      scopeType: "team",
      scopeId: "team-1",
      title: "Fallback identity note",
      categoryId: null,
    });

    const [, request] = vi.mocked(fetch).mock.calls[0];
    expect(request).toEqual(expect.objectContaining({ method: "POST" }));
    const headers = new Headers(request?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key"))
      .toBe("post-request-0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a");
    expect(JSON.parse(String(request?.body))).toEqual({
      note_id: "note-0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a",
      scope_type: "team",
      scope_id: "team-1",
      title: "Fallback identity note",
      category_id: null,
      idempotency_key: "post-request-0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a",
    });
  });

  it("filters the exact scope and lifecycle without using a knowledge endpoint", async () => {
    await notesApi.listNotes("team", "team-1", "trashed", "category-1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/notes?scope_type=team&scope_id=team-1&lifecycle_status=trashed&category_id=category-1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("sends current head and epoch when restoring a previewed body", async () => {
    await notesApi.restoreBody(note, "savepoint-1");
    const [, request] = vi.mocked(fetch).mock.calls[0];
    expect(request).toEqual(expect.objectContaining({ method: "POST" }));
    const headers = new Headers(request?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe("post-request-key");
    expect(headers.get("If-Match")).toBe("7");
    expect(JSON.parse(String(request?.body))).toEqual({
      savepoint_id: "savepoint-1",
      expected_revision_head: 7,
      expected_collaboration_epoch: 4,
      idempotency_key: "post-request-key",
    });
  });

  it("uploads original image bytes with exact epoch and one idempotency key", async () => {
    const file = new File([new Uint8Array([137, 80, 78, 71])], "screen.png", { type: "image/png" });
    await notesApi.uploadAttachment(note, file, "paste-image-1");

    const [, request] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(request?.headers);
    const form = request?.body as FormData;
    expect(request?.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toBe("paste-image-1");
    expect(headers.has("Content-Type")).toBe(false);
    expect(form.get("file")).toBe(file);
    expect(form.get("expected_collaboration_epoch")).toBe("4");
    expect(form.get("idempotency_key")).toBe("paste-image-1");
  });

  it("loads every accepted update across the 100-item server page boundary", async () => {
    const revision = (sequence: number): NoteRevision => ({
      revision_id: `revision-${sequence}`,
      note_id: note.note_id,
      sequence,
      server_timestamp: "2026-08-13T00:00:00Z",
      actor_id: "actor-1",
      event_kind: "content_update",
      before_digest: "before",
      after_digest: "after",
      change_set: { text: [], nodes: [], marks: [], attributes: [], moves: [] },
      restore_source_savepoint_id: null,
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: Array.from({ length: 100 }, (_, index) => revision(index + 1)),
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [revision(101)],
      }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const result = await notesApi.listRevisions(note.note_id);

    expect(result.items).toHaveLength(101);
    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/notes/note-1/revisions?limit=100",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/notes/note-1/revisions?limit=100&after_sequence=100",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
