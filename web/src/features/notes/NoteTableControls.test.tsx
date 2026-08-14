import "@testing-library/jest-dom/vitest";

import { Editor } from "@tiptap/core";
import { cleanup, createEvent, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "../../i18n";
import { noteExtensions } from "./note-extensions";
import { NoteTableControls } from "./NoteTableControls";

vi.mock("@tiptap/react/menus", () => ({
  BubbleMenu: ({
    editor,
    shouldShow,
    children,
  }: {
    editor: Editor;
    shouldShow: (props: { editor: Editor }) => boolean;
    children: ReactNode;
  }) => shouldShow({ editor }) ? <>{children}</> : null,
}));

function tableEditor() {
  const editor = new Editor({
    extensions: noteExtensions({ noteId: "note-1", live: false }),
    content: {
      type: "doc",
      content: [{
        type: "table",
        attrs: { block_id: "table-1" },
        content: [{
          type: "tableRow",
          content: [{
            type: "tableCell",
            content: [{ type: "paragraph", content: [{ type: "text", text: "Cell value" }] }],
          }],
        }],
      }],
    },
  });
  let textPosition = 0;
  editor.state.doc.descendants((node, position) => {
    if (node.isText && textPosition === 0) textPosition = position;
  });
  editor.commands.setTextSelection(textPosition);
  return editor;
}

afterEach(() => cleanup());

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

describe("NoteTableControls", () => {
  it("opens above an active table selection without replacing the editor selection", () => {
    const editor = tableEditor();
    render(<NoteTableControls editor={editor} editable />);

    const trigger = screen.getByRole("button", { name: "Table actions" });
    const selection = editor.state.selection;
    const mouseDown = createEvent.mouseDown(trigger, { button: 0 });
    fireEvent(trigger, mouseDown);
    expect(mouseDown.defaultPrevented).toBe(true);

    fireEvent.keyDown(trigger, { key: "Enter", code: "Enter" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(editor.state.selection.eq(selection)).toBe(true);

    editor.destroy();
  });

  it("does not expose mutating table actions while read-only", () => {
    const editor = tableEditor();
    render(<NoteTableControls editor={editor} editable={false} />);

    expect(screen.queryByRole("button", { name: "Table actions" })).not.toBeInTheDocument();
    editor.destroy();
  });
});
