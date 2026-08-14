import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";

import {
  NoteSearch,
  noteSearchState,
  replaceAllNoteSearchMatches,
  replaceCurrentNoteSearchMatch,
  setNoteSearch,
} from "./note-search";

describe("note search", () => {
  it("finds text across adjacent marks and navigates matches", () => {
    const editor = new Editor({
      extensions: [StarterKit, NoteSearch],
      content: "<p>Alpha <strong>beta</strong> alpha</p>",
    });

    const search = setNoteSearch(editor, { query: "alpha", caseSensitive: false });
    expect(search.matches).toHaveLength(2);
    expect(setNoteSearch(editor, { activeIndex: 1 }).activeIndex).toBe(1);
    expect(setNoteSearch(editor, { query: "Alpha beta", activeIndex: 0 }).matches).toHaveLength(1);

    editor.destroy();
  });

  it("replaces the current match and all remaining matches", () => {
    const editor = new Editor({
      extensions: [StarterKit, NoteSearch],
      content: "<p>one two one</p>",
    });

    setNoteSearch(editor, { query: "one", caseSensitive: true });
    expect(replaceCurrentNoteSearchMatch(editor, "first")).toBe(true);
    expect(editor.getText()).toBe("first two one");
    expect(noteSearchState(editor).matches).toHaveLength(1);
    expect(replaceAllNoteSearchMatches(editor, "last")).toBe(1);
    expect(editor.getText()).toBe("first two last");

    editor.destroy();
  });
});
