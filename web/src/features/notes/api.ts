import { requestJson } from "../../shared/api-client";
import type {
  BodyRestoreResult,
  CollaborationTicket,
  NoteCategory,
  NoteAttachment,
  NoteDetail,
  NoteLifecycleStatus,
  NoteRevision,
  NoteSavepointPreview,
  NoteSavepointSummary,
  NoteScope,
  NoteScopeType,
  NoteSummary,
  NotesSettings,
} from "./types";

function clientId(prefix: string) {
  if (
    typeof globalThis.crypto !== "undefined"
    && typeof globalThis.crypto.randomUUID === "function"
  ) {
    return globalThis.crypto.randomUUID();
  }
  if (
    typeof globalThis.crypto !== "undefined"
    && typeof globalThis.crypto.getRandomValues === "function"
  ) {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return `${prefix}-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const idempotencyKey = (kind: string) =>
  `${kind}-${clientId("request")}`;

function mutation<T>(path: string, method: "POST" | "PATCH", body: object, revision?: number) {
  const key = idempotencyKey(method.toLowerCase());
  return requestJson<T>(path, {
    method,
    headers: {
      "Idempotency-Key": key,
      ...(revision === undefined ? {} : { "If-Match": String(revision) }),
    },
    body: JSON.stringify({ ...body, idempotency_key: key }),
  });
}

export const notesApi = {
  listScopes: () =>
    requestJson<{ items: NoteScope[] }>("/api/v1/notes/scopes"),
  listNotes: (
    scopeType: NoteScopeType,
    scopeId: string,
    lifecycleStatus: NoteLifecycleStatus,
    categoryId?: string,
  ) => {
    const params = new URLSearchParams({
      scope_type: scopeType,
      scope_id: scopeId,
      lifecycle_status: lifecycleStatus,
    });
    if (categoryId) params.set("category_id", categoryId);
    return requestJson<{ items: NoteSummary[] }>(`/api/v1/notes?${params}`);
  },
  getNote: (noteId: string) =>
    requestJson<NoteDetail>(`/api/v1/notes/${encodeURIComponent(noteId)}`),
  createNote: (input: {
    scopeType: NoteScopeType;
    scopeId: string;
    title: string;
    categoryId: string | null;
  }) => {
    const noteId = clientId("note");
    return mutation<NoteDetail>("/api/v1/notes", "POST", {
      note_id: noteId,
      scope_type: input.scopeType,
      scope_id: input.scopeId,
      title: input.title,
      category_id: input.categoryId,
    });
  },
  updateNote: (
    noteId: string,
    revision: number,
    input: { title: string; categoryId: string | null },
  ) =>
    mutation<NoteDetail>(`/api/v1/notes/${encodeURIComponent(noteId)}`, "PATCH", {
      title: input.title,
      category_id: input.categoryId,
      clear_category: input.categoryId === null,
      expected_metadata_revision: revision,
    }, revision),
  trashNote: (note: NoteDetail | NoteSummary) =>
    mutation<NoteDetail>(`/api/v1/notes/${encodeURIComponent(note.note_id)}/trash`, "POST", {
      expected_metadata_revision: note.metadata_revision,
    }, note.metadata_revision),
  restoreNote: (note: NoteDetail | NoteSummary) =>
    mutation<NoteDetail>(`/api/v1/notes/${encodeURIComponent(note.note_id)}/restore`, "POST", {
      expected_metadata_revision: note.metadata_revision,
    }, note.metadata_revision),
  listCategories: (
    scopeType: NoteScopeType,
    scopeId: string,
    lifecycleStatus: NoteLifecycleStatus,
  ) => {
    const params = new URLSearchParams({
      scope_type: scopeType,
      scope_id: scopeId,
      lifecycle_status: lifecycleStatus,
    });
    return requestJson<{ items: NoteCategory[] }>(`/api/v1/note-categories?${params}`);
  },
  createCategory: (scopeType: NoteScopeType, scopeId: string, name: string) =>
    mutation<NoteCategory>("/api/v1/note-categories", "POST", {
      category_id: clientId("category"),
      scope_type: scopeType,
      scope_id: scopeId,
      name,
    }),
  updateCategory: (category: NoteCategory, name: string) =>
    mutation<NoteCategory>(
      `/api/v1/note-categories/${encodeURIComponent(category.category_id)}`,
      "PATCH",
      { name, expected_metadata_revision: category.metadata_revision },
      category.metadata_revision,
    ),
  trashCategory: (category: NoteCategory) =>
    mutation<NoteCategory>(
      `/api/v1/note-categories/${encodeURIComponent(category.category_id)}/trash`,
      "POST",
      { expected_metadata_revision: category.metadata_revision },
      category.metadata_revision,
    ),
  restoreCategory: (category: NoteCategory) =>
    mutation<NoteCategory>(
      `/api/v1/note-categories/${encodeURIComponent(category.category_id)}/restore`,
      "POST",
      { expected_metadata_revision: category.metadata_revision },
      category.metadata_revision,
    ),
  collaborationTicket: (noteId: string) =>
    requestJson<CollaborationTicket>(
      `/api/v1/notes/${encodeURIComponent(noteId)}/collaboration-ticket`,
      { method: "POST" },
    ),
  uploadAttachment: (note: NoteDetail, file: File, key = idempotencyKey("note-image")) => {
    const form = new FormData();
    form.set("file", file);
    form.set("expected_collaboration_epoch", String(note.collaboration_epoch));
    form.set("idempotency_key", key);
    return requestJson<NoteAttachment>(
      `/api/v1/notes/${encodeURIComponent(note.note_id)}/attachments`,
      {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: form,
      },
    );
  },
  listRevisions: async (noteId: string) => {
    const items: NoteRevision[] = [];
    let afterSequence: number | null = null;
    const limit = 100;
    while (true) {
      const params = new URLSearchParams({ limit: String(limit) });
      if (afterSequence !== null) {
        params.set("after_sequence", String(afterSequence));
      }
      const page = await requestJson<{ items: NoteRevision[] }>(
        `/api/v1/notes/${encodeURIComponent(noteId)}/revisions?${params}`,
      );
      if (page.items.length === 0) return { items };
      const nextSequence = page.items.at(-1)?.sequence;
      if (nextSequence === undefined || (afterSequence !== null && nextSequence <= afterSequence)) {
        throw new Error("Notes revision pagination did not advance");
      }
      items.push(...page.items);
      afterSequence = nextSequence;
      if (page.items.length < limit) return { items };
    }
  },
  listSavepoints: (noteId: string) =>
    requestJson<{ items: NoteSavepointSummary[] }>(
      `/api/v1/notes/${encodeURIComponent(noteId)}/savepoints`,
    ),
  getSavepoint: (noteId: string, savepointId: string) =>
    requestJson<NoteSavepointPreview>(
      `/api/v1/notes/${encodeURIComponent(noteId)}/savepoints/${encodeURIComponent(savepointId)}`,
    ),
  restoreBody: (note: NoteDetail, savepointId: string) =>
    mutation<BodyRestoreResult>(
      `/api/v1/notes/${encodeURIComponent(note.note_id)}/savepoints/${encodeURIComponent(savepointId)}/restore-body`,
      "POST",
      {
        savepoint_id: savepointId,
        expected_revision_head: note.accepted_update_head,
        expected_collaboration_epoch: note.collaboration_epoch,
      },
      note.accepted_update_head,
    ),
  getSettings: () =>
    requestJson<NotesSettings>("/api/v1/admin/notes/settings"),
  updateSettings: (settings: NotesSettings, checkpointIntervalSeconds: number) =>
    mutation<NotesSettings>("/api/v1/admin/notes/settings", "PATCH", {
      checkpoint_interval_seconds: checkpointIntervalSeconds,
      expected_settings_revision: settings.settings_revision,
    }, settings.settings_revision),
};
