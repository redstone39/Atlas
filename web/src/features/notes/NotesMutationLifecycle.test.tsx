import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";

import { notesApi } from "./api";
import { NotesListView } from "./NotesListView";
import { NoteSavepointView } from "./NoteSavepointView";
import type { BodyRestoreResult, NoteDetail, NoteSavepointPreview, NoteScope } from "./types";

vi.mock("@tiptap/react", () => ({
  EditorContent: () => <div aria-label="Historical note body" />,
  useEditor: () => ({}),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

const scope: NoteScope = {
  scope_type: "project",
  scope_id: "project-1",
  label: "Project One",
};
const note: NoteDetail = {
  note_id: "note-1",
  scope,
  category_id: null,
  title: "Shared note",
  lifecycle_status: "active",
  metadata_revision: 1,
  accepted_update_head: 2,
  savepoint_head: 1,
  collaboration_epoch: 1,
  updated_actor_id: "actor-1",
  updated_at: "2026-08-13T00:00:00Z",
  created_actor_id: "actor-1",
  created_at: "2026-08-13T00:00:00Z",
  trashed_actor_id: null,
  trashed_at: null,
};
const savepoint: NoteSavepointPreview = {
  savepoint_id: "savepoint-1",
  note_id: note.note_id,
  sequence: 1,
  covered_revision: 2,
  body_digest: "digest",
  aggregate_change_set: { text: [], nodes: [], marks: [], attributes: [], moves: [] },
  contributor_actor_ids: ["actor-1"],
  created_at: "2026-08-13T00:00:00Z",
  canonical_body: { type: "doc", content: [] },
  document_schema: "tiptap-prosemirror-v1",
};

describe("Notes mutation route isolation", () => {
  beforeEach(() => {
    vi.spyOn(notesApi, "listNotes").mockResolvedValue({ items: [] });
    vi.spyOn(notesApi, "listCategories").mockResolvedValue({ items: [] });
  });

  afterEach(async () => {
    cleanup();
    vi.restoreAllMocks();
    await i18n.changeLanguage("en");
  });

  it("does not navigate after a create response resolves for an unmounted list", async () => {
    const creation = deferred<NoteDetail>();
    vi.spyOn(notesApi, "createNote").mockReturnValue(creation.promise);
    const onNavigate = vi.fn();
    const view = render(
      <NotesListView
        scope={scope}
        lifecycle="active"
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Late note" } });
    fireEvent.click(screen.getByRole("button", { name: "Create note" }));
    await waitFor(() => expect(notesApi.createNote).toHaveBeenCalledTimes(1));
    view.unmount();

    await act(async () => creation.resolve(note));
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("does not navigate after a restore response resolves for an unmounted preview", async () => {
    vi.spyOn(notesApi, "getNote").mockResolvedValue(note);
    vi.spyOn(notesApi, "getSavepoint").mockResolvedValue(savepoint);
    const restoration = deferred<BodyRestoreResult>();
    vi.spyOn(notesApi, "restoreBody").mockReturnValue(restoration.promise);
    const onNavigate = vi.fn();
    const view = render(
      <NoteSavepointView
        scope={scope}
        noteId={note.note_id}
        savepointId={savepoint.savepoint_id}
        onNavigate={onNavigate}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Restore this body" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore body as a new revision" }));
    await waitFor(() => expect(notesApi.restoreBody).toHaveBeenCalledTimes(1));
    view.unmount();

    await act(async () => restoration.resolve({
      revision: {
        revision_id: "revision-3",
        note_id: note.note_id,
        sequence: 3,
        server_timestamp: "2026-08-13T00:01:00Z",
        actor_id: "actor-1",
        event_kind: "body_restore",
        before_digest: "before",
        after_digest: "after",
        change_set: { text: [], nodes: [], marks: [], attributes: [], moves: [] },
        restore_source_savepoint_id: savepoint.savepoint_id,
      },
      savepoint,
    }));
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
