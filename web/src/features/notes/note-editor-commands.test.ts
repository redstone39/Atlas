import { Editor } from "@tiptap/core";
import { describe, expect, it } from "vitest";

import {
  clearCurrentTableCell,
  currentTableCellText,
  deleteSelectedTopLevelBlock,
  duplicateSelectedTopLevelBlock,
  resetCurrentTableColumnWidths,
  selectedTopLevelBlockText,
} from "./note-editor-commands";
import { noteExtensions } from "./note-extensions";

describe("note editor commands", () => {
  it("duplicates a top-level block with a new identity and can delete the duplicate", () => {
    const editor = new Editor({
      extensions: noteExtensions({ noteId: "note-1", live: false }),
      content: {
        type: "doc",
        content: [{
          type: "paragraph",
          attrs: { block_id: "block-original" },
          content: [{ type: "text", text: "Copy me" }],
        }],
      },
    });
    editor.commands.setTextSelection(2);

    expect(selectedTopLevelBlockText(editor)).toBe("Copy me");
    expect(duplicateSelectedTopLevelBlock(editor, "block-copy")).toBe(true);
    expect(editor.getJSON().content?.map((node) => node.attrs?.block_id)).toEqual(["block-original", "block-copy"]);
    expect(deleteSelectedTopLevelBlock(editor)).toBe(true);
    expect(editor.getJSON().content?.map((node) => node.attrs?.block_id)).toEqual(["block-original"]);

    editor.destroy();
  });

  it("resets persisted table widths and clears the current cell", () => {
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
              attrs: { colspan: 1, rowspan: 1, colwidth: [120] },
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

    expect(currentTableCellText(editor)).toBe("Cell value");
    expect(resetCurrentTableColumnWidths(editor)).toBe(true);
    expect(editor.getJSON().content?.[0].content?.[0].content?.[0].attrs?.colwidth).toBeNull();
    expect(clearCurrentTableCell(editor)).toBe(true);
    expect(currentTableCellText(editor)).toBe("");

    editor.destroy();
  });
});
