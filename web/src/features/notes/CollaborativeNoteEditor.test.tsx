import "@testing-library/jest-dom/vitest";

import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "../../i18n";
import { notesApi } from "./api";
import {
  CollaborativeNoteEditor,
  topLevelImageInsertionPosition,
  uploadPendingNoteImage,
} from "./CollaborativeNoteEditor";
import type { CollaborationTicket, NoteDetail } from "./types";

const providerConstructors = vi.hoisted(() => vi.fn());
const collaborationConfigurations = vi.hoisted(() => vi.fn(() => ({ name: "collaboration" })));
const editorConfigurations = vi.hoisted(() => vi.fn());
const editorSetEditable = vi.hoisted(() => vi.fn());
const editorFocus = vi.hoisted(() => vi.fn());
const editorToggleBold = vi.hoisted(() => vi.fn());
const editorCommand = vi.hoisted(() => vi.fn());
const editorRun = vi.hoisted(() => vi.fn());
const editorOn = vi.hoisted(() => vi.fn());
const editorOff = vi.hoisted(() => vi.fn());
const emptyTransaction = {
  steps: [],
  setMeta: vi.fn().mockReturnThis(),
};
const editorState = {
  tr: emptyTransaction,
  doc: {
    descendants: vi.fn(),
    child: vi.fn(),
    content: { size: 0 },
  },
  selection: { from: 0, to: 0, $from: { index: vi.fn(() => 0) } },
};

const editorChain = {
  focus: editorFocus,
  toggleBold: editorToggleBold,
  command: editorCommand,
  run: editorRun,
};

editorFocus.mockReturnValue(editorChain);
editorToggleBold.mockReturnValue(editorChain);
editorCommand.mockReturnValue(editorChain);

vi.mock("@hocuspocus/provider", () => ({
  HocuspocusProvider: class {
    destroy = vi.fn();
    constructor(configuration: unknown) {
      providerConstructors(configuration);
    }
  },
}));
vi.mock("@tiptap/react", () => ({
  EditorContent: () => <div aria-label="Collaborative note editor" />,
  useEditor: (configuration: unknown) => {
    editorConfigurations(configuration);
    return ({
    setEditable: editorSetEditable,
    on: editorOn,
    off: editorOff,
    state: editorState,
    view: { dispatch: vi.fn() },
    isDestroyed: false,
    isEditable: true,
    isActive: vi.fn(() => false),
    getAttributes: vi.fn(() => ({})),
    chain: vi.fn(() => editorChain),
    });
  },
}));
vi.mock("@tiptap/react/menus", () => ({
  BubbleMenu: () => null,
}));
vi.mock("@tiptap/extension-drag-handle-react", () => ({
  default: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@tiptap/starter-kit", () => ({ default: { configure: vi.fn(() => ({})) } }));
vi.mock("@tiptap/extension-link", () => ({ default: { configure: vi.fn(() => ({})) } }));
vi.mock("@tiptap/extension-collaboration", () => ({
  default: { configure: collaborationConfigurations },
}));

const note: NoteDetail = {
  note_id: "note-toolbar",
  scope: { scope_type: "project", scope_id: "project-toolbar", label: "Toolbar" },
  category_id: null,
  title: "Toolbar note",
  lifecycle_status: "active",
  metadata_revision: 1,
  accepted_update_head: 1,
  savepoint_head: 1,
  collaboration_epoch: 1,
  updated_actor_id: "actor-toolbar",
  updated_at: "2026-08-13T00:00:00Z",
  created_actor_id: "actor-toolbar",
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

describe("CollaborativeNoteEditor toolbar", () => {
  beforeEach(async () => {
    cleanup();
    vi.restoreAllMocks();
    providerConstructors.mockClear();
    collaborationConfigurations.mockClear();
    editorConfigurations.mockClear();
    editorSetEditable.mockClear();
    editorFocus.mockClear();
    editorToggleBold.mockClear();
    editorCommand.mockClear();
    editorRun.mockClear();
    editorOn.mockClear();
    editorOff.mockClear();
    editorFocus.mockReturnValue(editorChain);
    editorToggleBold.mockReturnValue(editorChain);
    editorCommand.mockReturnValue(editorChain);
    await i18n.changeLanguage("en");
    vi.spyOn(notesApi, "collaborationTicket").mockResolvedValue(ticket);
  });

  it("preserves the editor selection before running a bold command", async () => {
    render(<CollaborativeNoteEditor note={note} />);

    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as {
      onSynced: (event: { state: boolean }) => void;
    };
    act(() => configuration.onSynced({ state: true }));

    const bold = await screen.findByRole("button", { name: "Bold" });
    await waitFor(() => expect(bold).toBeEnabled());
    const mouseDown = createEvent.mouseDown(bold, { button: 0 });
    fireEvent(bold, mouseDown);
    expect(mouseDown.defaultPrevented).toBe(true);

    fireEvent.click(bold);
    expect(editorFocus).toHaveBeenCalledTimes(1);
    expect(editorToggleBold).toHaveBeenCalledTimes(1);
    expect(editorRun).toHaveBeenCalledTimes(1);
  });

  it("keeps common actions visible and moves detailed actions into labeled groups", async () => {
    render(<CollaborativeNoteEditor note={note} />);

    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as {
      onSynced: (event: { state: boolean }) => void;
    };
    act(() => configuration.onSynced({ state: true }));

    expect(await screen.findByRole("button", { name: "Paragraph" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Blocks and lists" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Insert" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Heading 1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add row" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Find and replace" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Block actions" })).toBeEnabled();
    expect(screen.getByLabelText("Insert image")).toHaveAttribute("accept", "image/png,image/jpeg,image/webp");
  });


  it("mounts the mutating UniqueID extension only after the first provider sync", async () => {
    render(<CollaborativeNoteEditor note={note} />);
    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const extensionNames = () => (editorConfigurations.mock.calls.at(-1)?.[0] as { extensions: Array<{ name?: string }> })
      .extensions.map((extension) => extension?.name);
    expect(extensionNames()).toContain("stableBlockIdProjection");
    expect(extensionNames()).not.toContain("uniqueID");

    const configuration = providerConstructors.mock.calls[0][0] as {
      onSynced: (event: { state: boolean }) => void;
    };
    act(() => configuration.onSynced({ state: true }));
    await waitFor(() => expect(extensionNames()).toContain("uniqueID"));
    expect(extensionNames()).not.toContain("stableBlockIdProjection");
  });

  it("keeps shared document synchronization enabled without remote caret state", async () => {
    render(<CollaborativeNoteEditor note={note} />);

    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    expect(collaborationConfigurations).toHaveBeenCalledWith({ document: expect.anything() });
    expect(editorConfigurations).toHaveBeenLastCalledWith(expect.objectContaining({
      extensions: expect.arrayContaining([{ name: "collaboration" }]),
    }));
  });

  it("reuses the original image idempotency key after a lost response", async () => {
    const file = new File([new Uint8Array([1])], "clipboard.png", { type: "image/png" });
    const pending = { id: "placeholder-1", key: "note-image-stable", file, fallbackPosition: 3 };
    vi.spyOn(notesApi, "uploadAttachment")
      .mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce({
        attachment_ref: "attachment-1",
        mime_type: "image/png",
        byte_size: 1,
        sha256: "a".repeat(64),
        width: 1,
        height: 1,
        state: "ready",
      });

    await expect(uploadPendingNoteImage(note, pending)).rejects.toThrow("lost response");
    await expect(uploadPendingNoteImage(note, pending)).resolves.toMatchObject({ attachment_ref: "attachment-1" });
    expect(notesApi.uploadAttachment).toHaveBeenNthCalledWith(1, note, file, "note-image-stable");
    expect(notesApi.uploadAttachment).toHaveBeenNthCalledWith(2, note, file, "note-image-stable");
  });

  it("places a pasted image after the selected top-level block", () => {
    const after = vi.fn(() => 17);
    const resolve = vi.fn(() => ({ depth: 4, after }));
    const targetEditor = {
      state: { doc: { content: { size: 40 }, resolve } },
    } as unknown as Parameters<typeof topLevelImageInsertionPosition>[0];

    expect(topLevelImageInsertionPosition(targetEditor, 12)).toBe(17);
    expect(resolve).toHaveBeenCalledWith(12);
    expect(after).toHaveBeenCalledWith(1);
  });

  it("keeps the desktop drag grip inside the ProseMirror gutter", async () => {
    render(<CollaborativeNoteEditor note={note} />);
    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as { onSynced: (event: { state: boolean }) => void };
    act(() => configuration.onSynced({ state: true }));

    const editorConfiguration = editorConfigurations.mock.calls.at(-1)?.[0] as {
      editorProps: { attributes: { class: string } };
    };
    expect(editorConfiguration.editorProps.attributes.class.split(" ")).toEqual(
      expect.arrayContaining(["p-4", "ps-12"]),
    );
    expect(document.querySelector("[data-note-drag-handle]")).toHaveProperty("tagName", "SPAN");
    expect(screen.queryByRole("button", { name: "Drag block" })).not.toBeInTheDocument();
  });

  it("keeps block movement reachable by keyboard buttons and touch pointer gestures", async () => {
    render(<CollaborativeNoteEditor note={note} />);
    await waitFor(() => expect(providerConstructors).toHaveBeenCalledTimes(1));
    const configuration = providerConstructors.mock.calls[0][0] as { onSynced: (event: { state: boolean }) => void };
    act(() => configuration.onSynced({ state: true }));

    const moveUp = await screen.findByRole("button", { name: "Move block up" });
    await waitFor(() => expect(moveUp).toBeEnabled());
    fireEvent.click(moveUp);
    expect(editorCommand).toHaveBeenCalledTimes(1);

    const touchHandle = screen.getByRole("button", { name: "Drag selected block up or down" });
    fireEvent.pointerDown(touchHandle, { pointerId: 1, clientY: 100 });
    fireEvent.pointerMove(touchHandle, { pointerId: 1, clientY: 150 });
    expect(editorCommand).toHaveBeenCalledTimes(2);
  });
});
