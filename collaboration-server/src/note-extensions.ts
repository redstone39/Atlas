import { Node, type Extensions } from "@tiptap/core";
import { TableKit } from "@tiptap/extension-table";
import { TaskItem } from "@tiptap/extension-task-item";
import { TaskList } from "@tiptap/extension-task-list";
import { Underline } from "@tiptap/extension-underline";
import { UniqueID } from "@tiptap/extension-unique-id";
import { StarterKit } from "@tiptap/starter-kit";

export const TOP_LEVEL_BLOCK_TYPES = [
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

export const NoteImage = Node.create({
  name: "noteImage",
  group: "block",
  atom: true,
  draggable: true,
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
  renderHTML({ HTMLAttributes }) {
    return ["figure", { ...HTMLAttributes, "data-note-image": "" }];
  },
});

export const NOTE_EXTENSIONS: Extensions = [
  StarterKit.configure({ underline: false }),
  Underline,
  TaskList,
  TaskItem.configure({ nested: true }),
  TableKit.configure({ table: { resizable: true } }),
  NoteImage,
  UniqueID.configure({
    attributeName: "block_id",
    types: [...TOP_LEVEL_BLOCK_TYPES],
    updateDocument: false,
  }),
];

export const NOTE_DOCUMENT_SCHEMA = "tiptap-prosemirror-v2";
