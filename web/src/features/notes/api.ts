import { requestJson } from "../../shared/api-client";
import { clientRequestId } from "../../shared/ids";
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


function mutation<T>(
  path: string,
  method: "POST" | "PATCH",
  body: object,
  revision?: number,
  idempotencyKey = clientRequestId(`notes-${method.toLowerCase()}`),
) {
  return requestJson<T>(path, {
    method,
    headers: {
      "Idempotency-Key": idempotencyKey,
      ...(revision === undefined ? {} : { "If-Match": String(revision) }),
    },
    body: JSON.stringify({ ...body, idempotency_key: idempotencyKey }),
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
    idempotencyKey: string;
  }) => {
    return mutation<NoteDetail>("/api/v1/notes", "POST", {
      scope_type: input.scopeType,
      scope_id: input.scopeId,
      title: input.title,
      category_id: input.categoryId,
    }, undefined, input.idempotencyKey);
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
  createCategory: (
    scopeType: NoteScopeType,
    scopeId: string,
    name: string,
    idempotencyKey: string,
  ) =>
    mutation<NoteCategory>("/api/v1/note-categories", "POST", {
      scope_type: scopeType,
      scope_id: scopeId,
      name,
    }, undefined, idempotencyKey),
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
  uploadAttachment: (note: NoteDetail, file: File, key = clientRequestId("note-image")) => {
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
