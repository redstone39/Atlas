import { Extension, Node, type Editor, type Extensions, mergeAttributes } from "@tiptap/core";
import FileHandler from "@tiptap/extension-file-handler";
import Link from "@tiptap/extension-link";
import { TableKit } from "@tiptap/extension-table";
import { TaskItem } from "@tiptap/extension-task-item";
import { TaskList } from "@tiptap/extension-task-list";
import Underline from "@tiptap/extension-underline";
import UniqueID from "@tiptap/extension-unique-id";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import StarterKit from "@tiptap/starter-kit";

import { isAllowedNoteLink } from "./link-policy";

export const NOTE_BLOCK_TYPES = [
  "paragraph",
  "heading",
  "blockquote",
  "codeBlock",
  "bulletList",
  "orderedList",
  "taskList",
  "horizontalRule",
  "table",
  "noteImage",
] as const;

const NoteImage = Node.create<{ noteId: string }>({
  name: "noteImage",
  group: "block",
  atom: true,
  draggable: true,
  addOptions() {
    return { noteId: "" };
  },
  addAttributes() {
    return {
      block_id: { default: null },
      attachment_ref: { default: null },
      alt: { default: "" },
      caption: { default: "" },
      width: { default: null },
      height: { default: null },
    };
  },
  parseHTML() {
    return [{ tag: "figure[data-note-image]" }];
  },
  renderHTML({ node, HTMLAttributes }) {
    const ref = String(node.attrs.attachment_ref ?? "");
    const src = `/api/v1/notes/${encodeURIComponent(this.options.noteId)}/attachments/${encodeURIComponent(ref)}/content`;
    const imageAttrs = {
      src,
      alt: String(node.attrs.alt ?? ""),
      width: node.attrs.width ?? undefined,
      height: node.attrs.height ?? undefined,
      loading: "lazy",
      draggable: "false",
    };
    return [
      "figure",
      mergeAttributes(HTMLAttributes, { "data-note-image": "", class: "note-image" }),
      ["img", imageAttrs],
      ["figcaption", {}, String(node.attrs.caption ?? "")],
    ];
  },
});

const StableBlockIdProjection = Extension.create({
  name: "stableBlockIdProjection",
  addGlobalAttributes() {
    return [{
      types: [...NOTE_BLOCK_TYPES],
      attributes: { block_id: { default: null } },
    }];
  },
});

const uploadPlaceholderKey = new PluginKey<DecorationSet>("noteImageUploadPlaceholder");

const UploadPlaceholder = Extension.create({
  name: "noteImageUploadPlaceholder",
  addProseMirrorPlugins() {
    return [new Plugin({
      key: uploadPlaceholderKey,
      state: {
        init: () => DecorationSet.empty,
        apply(transaction, decorations) {
          let next = decorations.map(transaction.mapping, transaction.doc);
          const action = transaction.getMeta(uploadPlaceholderKey) as
            | { add: { id: string; pos: number } }
            | { remove: { id: string } }
            | undefined;
          if (action && "add" in action) {
            const widget = Decoration.widget(action.add.pos, () => {
              const element = document.createElement("span");
              element.className = "note-image-upload-placeholder";
              element.setAttribute("aria-hidden", "true");
              return element;
            }, { id: action.add.id });
            next = next.add(transaction.doc, [widget]);
          } else if (action && "remove" in action) {
            next = next.remove(next.find(undefined, undefined, (spec) => spec.id === action.remove.id));
          }
          return next;
        },
      },
      props: {
        decorations: (state) => uploadPlaceholderKey.getState(state),
      },
    })];
  },
});

export function addImageUploadPlaceholder(editor: Editor, id: string, pos: number) {
  editor.view.dispatch(editor.state.tr.setMeta(uploadPlaceholderKey, { add: { id, pos } }));
}

export function findImageUploadPlaceholder(editor: Editor, id: string) {
  return uploadPlaceholderKey.getState(editor.state)?.find(
    undefined,
    undefined,
    (spec) => spec.id === id,
  )[0]?.from ?? null;
}

export function removeImageUploadPlaceholder(editor: Editor, id: string) {
  if (editor.isDestroyed) return;
  editor.view.dispatch(editor.state.tr.setMeta(uploadPlaceholderKey, { remove: { id } }));
}

export function ensureTopLevelBlockIds(editor: Editor, generateId: () => string) {
  const seen = new Set<string>();
  const transaction = editor.state.tr;
  editor.state.doc.descendants((node, pos, parent) => {
    if (parent?.type.name !== "doc" || !NOTE_BLOCK_TYPES.includes(node.type.name as (typeof NOTE_BLOCK_TYPES)[number])) {
      return true;
    }
    const current = node.attrs.block_id as string | null;
    if (!current || seen.has(current)) {
      transaction.setNodeMarkup(pos, undefined, { ...node.attrs, block_id: generateId() });
    } else {
      seen.add(current);
    }
    return false;
  });
  if (transaction.steps.length === 0) return false;
  transaction.setMeta("addToHistory", false);
  editor.view.dispatch(transaction);
  return true;
}

type UploadFiles = (editor: Editor, files: File[], position: number) => void;

export function noteExtensions(options: {
  noteId: string;
  live: boolean;
  onFiles?: UploadFiles;
}): Extensions {
  const extensions: Extensions = [
    StarterKit.configure({ undoRedo: false, link: false, underline: false }),
    Link.configure({
      openOnClick: !options.live,
      autolink: options.live,
      linkOnPaste: options.live,
      isAllowedUri: isAllowedNoteLink,
    }),
    Underline,
    TaskList,
    TaskItem.configure({ nested: true }),
    TableKit.configure({ table: { resizable: options.live, renderWrapper: true } }),
    NoteImage.configure({ noteId: options.noteId }),
  ];
  extensions.push(options.live
    ? UniqueID.configure({
        attributeName: "block_id",
        types: [...NOTE_BLOCK_TYPES],
        updateDocument: true,
      })
    : StableBlockIdProjection);
  if (options.live && options.onFiles) {
    extensions.push(UploadPlaceholder);
    extensions.push(FileHandler.configure({
      allowedMimeTypes: ["image/png", "image/jpeg", "image/webp"],
      consumePasteEvent: true,
      onPaste: (editor, files) => options.onFiles?.(editor, files, editor.state.selection.from),
      onDrop: (editor, files, position) => options.onFiles?.(editor, files, position),
    }));
  }
  return extensions;
}
