import { Extension } from "@tiptap/core";
import type { Editor } from "@tiptap/react";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

export interface NoteSearchMatch {
  from: number;
  to: number;
}

export interface NoteSearchState {
  query: string;
  caseSensitive: boolean;
  activeIndex: number;
  matches: NoteSearchMatch[];
}

interface NoteSearchMeta {
  query?: string;
  caseSensitive?: boolean;
  activeIndex?: number;
}

export const noteSearchKey = new PluginKey<NoteSearchState>("noteSearch");

function normalizeActiveIndex(index: number, length: number) {
  if (length === 0) return 0;
  return ((index % length) + length) % length;
}

export function findNoteSearchMatches(doc: ProseMirrorNode, query: string, caseSensitive: boolean) {
  const matches: NoteSearchMatch[] = [];
  if (!query) return matches;
  const needle = caseSensitive ? query : query.toLowerCase();
  doc.descendants((node, position) => {
    if (!node.isTextblock) return true;
    let text = "";
    const positions: number[] = [];
    node.descendants((child, childPosition) => {
      if (child.isText && child.text) {
        text += child.text;
        for (let index = 0; index < child.text.length; index += 1) positions.push(position + 1 + childPosition + index);
      } else if (child.isLeaf) {
        text += "\n";
        positions.push(-1);
      }
      return true;
    });
    const haystack = caseSensitive ? text : text.toLowerCase();
    let offset = 0;
    while (offset <= haystack.length - needle.length) {
      const found = haystack.indexOf(needle, offset);
      if (found < 0) break;
      const endIndex = found + needle.length - 1;
      if (positions[found] >= 0 && positions[endIndex] >= 0) {
        matches.push({ from: positions[found], to: positions[endIndex] + 1 });
      }
      offset = found + Math.max(needle.length, 1);
    }
    return false;
  });
  return matches;
}

function nextSearchState(doc: ProseMirrorNode, previous: NoteSearchState, meta?: NoteSearchMeta) {
  const query = meta?.query ?? previous.query;
  const caseSensitive = meta?.caseSensitive ?? previous.caseSensitive;
  const matches = findNoteSearchMatches(doc, query, caseSensitive);
  const activeIndex = normalizeActiveIndex(meta?.activeIndex ?? previous.activeIndex, matches.length);
  return { query, caseSensitive, matches, activeIndex };
}

export const NoteSearch = Extension.create({
  name: "noteSearch",
  addProseMirrorPlugins() {
    return [new Plugin<NoteSearchState>({
      key: noteSearchKey,
      state: {
        init: (_configuration, state) => nextSearchState(
          state.doc,
          { query: "", caseSensitive: false, activeIndex: 0, matches: [] },
        ),
        apply: (transaction, previous) => {
          const meta = transaction.getMeta(noteSearchKey) as NoteSearchMeta | undefined;
          if (!transaction.docChanged && !meta) return previous;
          return nextSearchState(transaction.doc, previous, meta);
        },
      },
      props: {
        decorations(state) {
          const search = noteSearchKey.getState(state);
          if (!search || search.matches.length === 0) return DecorationSet.empty;
          return DecorationSet.create(state.doc, search.matches.map((match, index) => Decoration.inline(
            match.from,
            match.to,
            { class: index === search.activeIndex ? "note-search-match-active" : "note-search-match" },
          )));
        },
      },
    })];
  },
});

export function noteSearchState(editor: Editor) {
  return noteSearchKey.getState(editor.state) ?? { query: "", caseSensitive: false, activeIndex: 0, matches: [] };
}

export function setNoteSearch(editor: Editor, meta: NoteSearchMeta) {
  editor.view.dispatch(editor.state.tr.setMeta(noteSearchKey, meta));
  return noteSearchState(editor);
}

export function replaceCurrentNoteSearchMatch(editor: Editor, replacement: string) {
  const search = noteSearchState(editor);
  const match = search.matches[search.activeIndex];
  if (!match) return false;
  editor.view.dispatch(editor.state.tr.insertText(replacement, match.from, match.to).setMeta(noteSearchKey, {
    activeIndex: search.activeIndex,
  }));
  return true;
}

export function replaceAllNoteSearchMatches(editor: Editor, replacement: string) {
  const search = noteSearchState(editor);
  if (search.matches.length === 0) return 0;
  const transaction = editor.state.tr;
  for (const match of [...search.matches].reverse()) transaction.insertText(replacement, match.from, match.to);
  editor.view.dispatch(transaction.setMeta(noteSearchKey, { activeIndex: 0 }));
  return search.matches.length;
}
