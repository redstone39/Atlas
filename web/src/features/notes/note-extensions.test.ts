import { Editor } from "@tiptap/core";
import { describe, expect, it, vi } from "vitest";

import {
  addImageUploadPlaceholder,
  ensureTopLevelBlockIds,
  findImageUploadPlaceholder,
  noteExtensions,
  removeImageUploadPlaceholder,
} from "./note-extensions";

describe("Notes editor extension contract", () => {
  it("keeps previews passive while live editors own stable IDs and file events", () => {
    const onFiles = vi.fn();
    const preview = noteExtensions({ noteId: "note-1", live: false });
    const live = noteExtensions({ noteId: "note-1", live: true, onFiles });

    expect(preview.map((extension) => extension.name)).toContain("stableBlockIdProjection");
    expect(preview.map((extension) => extension.name)).not.toContain("uniqueID");
    expect(preview.map((extension) => extension.name)).not.toContain("fileHandler");
    expect(live.map((extension) => extension.name)).toEqual(expect.arrayContaining([
      "uniqueID",
      "fileHandler",
      "tableKit",
      "taskList",
      "noteImage",
    ]));

    const editor = { state: { selection: { from: 17 } } } as Editor;
    const files = [new File([new Uint8Array([1])], "clipboard.png", { type: "image/png" })];
    const fileHandler = live.find((extension) => extension.name === "fileHandler");
    expect(fileHandler).toBeDefined();
    (fileHandler?.options.onPaste as (editor: Editor, files: File[]) => void)(editor, files);
    expect(onFiles).toHaveBeenCalledWith(editor, files, 17);
  });

  it("normalizes missing and duplicate top-level IDs immediately after sync", () => {
    const editor = new Editor({
      extensions: noteExtensions({ noteId: "note-1", live: false }),
      content: {
        type: "doc",
        content: [
          { type: "paragraph", attrs: { block_id: "same" }, content: [{ type: "text", text: "one" }] },
          { type: "heading", attrs: { block_id: "same", level: 2 }, content: [{ type: "text", text: "two" }] },
          { type: "paragraph", content: [{ type: "text", text: "three" }] },
        ],
      },
    });
    let generated = 0;
    expect(ensureTopLevelBlockIds(editor, () => `generated-${++generated}`)).toBe(true);
    expect(editor.getJSON().content?.map((node) => node.attrs?.block_id)).toEqual([
      "same",
      "generated-1",
      "generated-2",
    ]);
    expect(ensureTopLevelBlockIds(editor, () => "unexpected")).toBe(false);
    editor.destroy();
  });

  it("maps the local image placeholder through intervening transactions", () => {
    const editor = new Editor({
      extensions: noteExtensions({ noteId: "note-1", live: true, onFiles: vi.fn() }),
      content: {
        type: "doc",
        content: [
          { type: "paragraph", attrs: { block_id: "one" }, content: [{ type: "text", text: "one" }] },
          { type: "paragraph", attrs: { block_id: "two" }, content: [{ type: "text", text: "two" }] },
        ],
      },
    });
    const originalPosition = editor.state.doc.child(0).nodeSize;
    addImageUploadPlaceholder(editor, "upload-1", originalPosition);
    editor.commands.insertContentAt(0, { type: "paragraph", content: [{ type: "text", text: "before" }] });
    expect(findImageUploadPlaceholder(editor, "upload-1")).toBeGreaterThan(originalPosition);
    removeImageUploadPlaceholder(editor, "upload-1");
    expect(findImageUploadPlaceholder(editor, "upload-1")).toBeNull();
    editor.destroy();
  });
});
