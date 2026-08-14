import "@testing-library/jest-dom/vitest";

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";

import { notesApi } from "./api";
import { NotesScopeFeature } from "./NotesScopeFeature";
import type { CollaborationTicket, NoteDetail, NoteScope } from "./types";

const providerConstructors = vi.hoisted(() => vi.fn());
const editorSetEditable = vi.hoisted(() => vi.fn());
const collaborationConfigurations = vi.hoisted(() => vi.fn());
const providerDestroy = vi.hoisted(() => vi.fn());
const emptyTransaction = { steps: [], setMeta: vi.fn().mockReturnThis() };

vi.mock("@hocuspocus/provider", () => ({
  HocuspocusProvider: class {
    destroy = providerDestroy;
    constructor(configuration: unknown) {
      providerConstructors(configuration);
    }
  },
}));
vi.mock("@tiptap/react", () => ({
  EditorContent: () => <div aria-label="Collaborative note editor" />,
  useEditor: () => ({
    setEditable: editorSetEditable,
    on: vi.fn(),
    off: vi.fn(),
    state: {
      tr: emptyTransaction,
      doc: { descendants: vi.fn(), child: vi.fn(), content: { size: 0 } },
      selection: { from: 0, to: 0, $from: { index: vi.fn(() => 0) } },
    },
    view: { dispatch: vi.fn() },
    isDestroyed: false,
    isEditable: true,
    isActive: vi.fn(() => false),
    getAttributes: vi.fn(() => ({})),
    chain: vi.fn(),
  }),
}));
vi.mock("@tiptap/extension-drag-handle-react", () => ({
  default: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@tiptap/starter-kit", () => ({ default: { configure: vi.fn(() => ({})) } }));
vi.mock("@tiptap/extension-link", () => ({ default: { configure: vi.fn(() => ({})) } }));
vi.mock("@tiptap/extension-collaboration", () => ({
  default: { configure: collaborationConfigurations },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

const scope: NoteScope = { scope_type: "project", scope_id: "project-1", label: "Project One" };
const note: NoteDetail = {
  note_id: "note-1",
  scope,
  category_id: null,
  title: "Authorized note",
  lifecycle_status: "active",
  metadata_revision: 1,
  accepted_update_head: 1,
  savepoint_head: 1,
  collaboration_epoch: 1,
  updated_actor_id: "actor-1",
  updated_at: "2026-08-13T00:00:00Z",
  created_actor_id: "actor-1",
  created_at: "2026-08-13T00:00:00Z",
  trashed_actor_id: null,
  trashed_at: null,
};
const ticket: CollaborationTicket = {
  ticket: "opaque-ticket",
  room_name: "opaque-room",
  websocket_url: "ws://127.0.0.1:8015/collaboration",
  collaboration_epoch: 1,
  read_only: false,
};

describe("Notes editor authorization and sync lifecycle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    providerConstructors.mockClear();
    editorSetEditable.mockClear();
    collaborationConfigurations.mockClear();
    providerDestroy.mockClear();
  });
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
  });

  it("starts the provider only after scope, metadata, and ticket authorization and disables editing when disconnected", async () => {
    const scopes = deferred<{ items: NoteScope[] }>();
    const metadata = deferred<NoteDetail>();
    const collaborationTicket = deferred<CollaborationTicket>();
    vi.spyOn(notesApi, "listScopes").mockReturnValue(scopes.promise);
    vi.spyOn(notesApi, "getNote").mockReturnValue(metadata.promise);
    vi.spyOn(notesApi, "listCategories").mockResolvedValue({ items: [] });
    vi.spyOn(notesApi, "collaborationTicket").mockReturnValue(collaborationTicket.promise);

    render(
      <NotesScopeFeature
        scopeType="project"
        scopeId="project-1"
        surface={{ view: "editor", noteId: "note-1" }}
        onNavigate={vi.fn()}
      />,
    );

    expect(notesApi.getNote).not.toHaveBeenCalled();
    expect(providerConstructors).not.toHaveBeenCalled();

    scopes.resolve({ items: [scope] });
    await waitFor(() => expect(notesApi.getNote).toHaveBeenCalledWith("note-1"));
    expect(notesApi.collaborationTicket).not.toHaveBeenCalled();

    metadata.resolve(note);
    await waitFor(() => expect(notesApi.collaborationTicket).toHaveBeenCalledWith("note-1"));
    expect(providerConstructors).not.toHaveBeenCalled();

    collaborationTicket.resolve(ticket);
    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as {
      document: unknown;
      onAuthenticationFailed: () => void;
      token: () => Promise<string>;
      onSynced: (event: { state: boolean }) => void;
      onStatus: (event: { status: string }) => void;
    };
    expect(configuration.document).toBe(
      (collaborationConfigurations.mock.calls[0][0] as { document: unknown }).document,
    );
    expect(screen.getByLabelText("Collaborative note editor")).toBeInTheDocument();
    expect(editorSetEditable).toHaveBeenLastCalledWith(false);
    await expect(configuration.token()).resolves.toBe(ticket.ticket);
    expect(notesApi.collaborationTicket).toHaveBeenCalledTimes(1);
    await expect(configuration.token()).resolves.toBe(ticket.ticket);
    expect(notesApi.collaborationTicket).toHaveBeenCalledTimes(2);

    act(() => configuration.onSynced({ state: true }));
    await waitFor(() => expect(editorSetEditable).toHaveBeenLastCalledWith(true));
    act(() => {
      configuration.onStatus({ status: "disconnected" });
      expect(editorSetEditable).toHaveBeenLastCalledWith(false);
    });

    act(() => configuration.onAuthenticationFailed());
    await waitFor(() => expect(editorSetEditable).toHaveBeenLastCalledWith(false));
    expect(providerDestroy).toHaveBeenCalledTimes(1);
    act(() => {
      configuration.onStatus({ status: "connected" });
      configuration.onSynced({ state: true });
    });
    await waitFor(() => expect(editorSetEditable).toHaveBeenLastCalledWith(false));
  });

  it("loads a trashed note through a read-only provider without enabling editing", async () => {
    vi.spyOn(notesApi, "listScopes").mockResolvedValue({ items: [scope] });
    vi.spyOn(notesApi, "getNote").mockResolvedValue({
      ...note,
      lifecycle_status: "trashed",
      collaboration_epoch: 2,
    });
    vi.spyOn(notesApi, "listCategories").mockResolvedValue({ items: [] });
    vi.spyOn(notesApi, "collaborationTicket").mockResolvedValue({
      ...ticket,
      collaboration_epoch: 2,
      read_only: true,
    });

    render(
      <NotesScopeFeature
        scopeType="project"
        scopeId="project-1"
        surface={{ view: "editor", noteId: "note-1" }}
        onNavigate={vi.fn()}
      />,
    );

    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as {
      token: () => Promise<string>;
      onSynced: (event: { state: boolean }) => void;
    };
    await expect(configuration.token()).resolves.toBe(ticket.ticket);
    act(() => configuration.onSynced({ state: true }));
    await waitFor(() => expect(editorSetEditable).toHaveBeenLastCalledWith(false));
  });
});
