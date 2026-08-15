import "@testing-library/jest-dom/vitest";

import { Editor } from "@tiptap/core";
import { cleanup, createEvent, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "../../i18n";
import { noteExtensions } from "./note-extensions";
import { NoteTableControls } from "./NoteTableControls";

const bubbleMenuState = vi.hoisted(() => ({
  getReferencedVirtualElement: undefined as
    | undefined
    | (() => { getBoundingClientRect: () => DOMRect } | null),
}));

vi.mock("@tiptap/react/menus", () => ({
  BubbleMenu: ({
    editor,
    shouldShow,
    getReferencedVirtualElement,
    children,
  }: {
    editor: Editor;
    shouldShow: (props: { editor: Editor }) => boolean;
    getReferencedVirtualElement?: () => { getBoundingClientRect: () => DOMRect } | null;
    children: ReactNode;
  }) => {
    bubbleMenuState.getReferencedVirtualElement = getReferencedVirtualElement;
    return shouldShow({ editor }) ? <>{children}</> : null;
  },
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
  bubbleMenuState.getReferencedVirtualElement = undefined;
  await i18n.changeLanguage("en");
});

describe("NoteTableControls", () => {
  it("opens from the keyboard without replacing the editor selection", () => {
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

  it("opens the action menu at the pointer coordinates", () => {
    const editor = tableEditor();
    render(<NoteTableControls editor={editor} editable />);
    const trigger = screen.getByRole("button", { name: "Table actions" });
    let contextPoint: { x: number; y: number } | null = null;
    document.addEventListener("contextmenu", (event) => {
      contextPoint = { x: event.clientX, y: event.clientY };
    }, { once: true });

    fireEvent.click(trigger, { clientX: 320, clientY: 240 });

    expect(contextPoint).toEqual({ x: 320, y: 240 });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    editor.destroy();
  });

  it("anchors table actions to the pointer and falls back to selection geometry for keyboard use", () => {
    const editor = tableEditor();
    render(<NoteTableControls editor={editor} editable />);
    const cell = editor.view.dom.querySelector("td");
    expect(cell).not.toBeNull();

    fireEvent(cell as HTMLTableCellElement, new MouseEvent("pointerdown", {
      bubbles: true,
      clientX: 240,
      clientY: 180,
    }));
    const pointerReference = bubbleMenuState.getReferencedVirtualElement?.();
    expect(pointerReference?.getBoundingClientRect()).toMatchObject({
      x: 240,
      y: 180,
      width: 0,
      height: 0,
    });

    fireEvent.keyDown(cell as HTMLTableCellElement, { key: "ArrowRight" });
    expect(bubbleMenuState.getReferencedVirtualElement?.()).toBeNull();
    editor.destroy();
  });

  it("does not expose mutating table actions while read-only", () => {
    const editor = tableEditor();
    render(<NoteTableControls editor={editor} editable={false} />);

    expect(screen.queryByRole("button", { name: "Table actions" })).not.toBeInTheDocument();
    editor.destroy();
  });

  it("closes mutating table actions when the editor becomes read-only", () => {
    const editor = tableEditor();
    const { rerender } = render(<NoteTableControls editor={editor} editable />);

    const trigger = screen.getByRole("button", { name: "Table actions" });
    fireEvent.keyDown(trigger, { key: "Enter", code: "Enter" });
    expect(screen.getByRole("menuitem", { name: "Add row before" })).toBeInTheDocument();

    rerender(<NoteTableControls editor={editor} editable={false} />);

    expect(screen.queryByRole("button", { name: "Table actions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Add row before" })).not.toBeInTheDocument();
    editor.destroy();
  });
});
