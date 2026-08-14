import type { Editor } from "@tiptap/react";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { NodeSelection, Selection, TextSelection } from "@tiptap/pm/state";
import { CellSelection, TableMap } from "@tiptap/pm/tables";

export interface TopLevelBlockSelection {
  index: number;
  position: number;
  node: ProseMirrorNode;
}

export interface SelectedNoteImage {
  position: number;
  attachmentRef: string;
  alt: string;
  caption: string;
}

export function selectedTopLevelBlock(editor: Editor): TopLevelBlockSelection | null {
  const index = editor.state.selection.$from.index(0);
  if (index < 0 || index >= editor.state.doc.childCount) return null;
  let position = 0;
  for (let current = 0; current < index; current += 1) {
    position += editor.state.doc.child(current).nodeSize;
  }
  return { index, position, node: editor.state.doc.child(index) };
}

export function moveSelectedTopLevelBlock(editor: Editor, direction: -1 | 1) {
  const selected = selectedTopLevelBlock(editor);
  if (!selected) return false;
  return editor.chain().focus().command(({ tr, dispatch }) => {
    const { index, position, node } = selected;
    if ((direction < 0 && index === 0) || (direction > 0 && index >= tr.doc.childCount - 1)) return false;
    let destination = position;
    if (direction < 0) {
      destination = 0;
      for (let current = 0; current < index - 1; current += 1) destination += tr.doc.child(current).nodeSize;
      tr.delete(position, position + node.nodeSize).insert(destination, node);
    } else {
      const next = tr.doc.child(index + 1);
      destination = position + next.nodeSize;
      tr.delete(position, position + node.nodeSize).insert(destination, node);
    }
    tr.setSelection(Selection.near(tr.doc.resolve(Math.min(destination + 1, tr.doc.content.size))));
    dispatch?.(tr.scrollIntoView());
    return true;
  }).run();
}

export function duplicateSelectedTopLevelBlock(editor: Editor, blockId: string) {
  const selected = selectedTopLevelBlock(editor);
  if (!selected) return false;
  const duplicate = selected.node.type.create(
    { ...selected.node.attrs, block_id: blockId },
    selected.node.content,
    selected.node.marks,
  );
  const position = selected.position + selected.node.nodeSize;
  const transaction = editor.state.tr.insert(position, duplicate);
  transaction.setSelection(Selection.near(transaction.doc.resolve(Math.min(position + 1, transaction.doc.content.size))));
  editor.view.dispatch(transaction.scrollIntoView());
  return true;
}

export function deleteSelectedTopLevelBlock(editor: Editor) {
  const selected = selectedTopLevelBlock(editor);
  if (!selected) return false;
  const transaction = editor.state.tr.delete(selected.position, selected.position + selected.node.nodeSize);
  transaction.setSelection(Selection.near(transaction.doc.resolve(Math.min(selected.position, transaction.doc.content.size))));
  editor.view.dispatch(transaction.scrollIntoView());
  return true;
}

export function selectSelectedTopLevelBlock(editor: Editor) {
  const selected = selectedTopLevelBlock(editor);
  if (!selected) return false;
  const selection = selected.node.isTextblock
    ? TextSelection.create(editor.state.doc, selected.position + 1, selected.position + selected.node.nodeSize - 1)
    : NodeSelection.create(editor.state.doc, selected.position);
  editor.view.dispatch(editor.state.tr.setSelection(selection).scrollIntoView());
  return true;
}

export function selectedTopLevelBlockText(editor: Editor) {
  const selected = selectedTopLevelBlock(editor);
  if (!selected) return "";
  if (selected.node.type.name === "noteImage") {
    return String(selected.node.attrs.caption || selected.node.attrs.alt || "");
  }
  return selected.node.textBetween(0, selected.node.content.size, "\n");
}

export function convertSelectedBlock(
  editor: Editor,
  type: "paragraph" | "heading1" | "heading2" | "heading3" | "blockquote" | "codeBlock",
) {
  const chain = editor.chain().focus().clearNodes();
  if (type === "paragraph") return chain.setParagraph().run();
  if (type === "heading1") return chain.setHeading({ level: 1 }).run();
  if (type === "heading2") return chain.setHeading({ level: 2 }).run();
  if (type === "heading3") return chain.setHeading({ level: 3 }).run();
  if (type === "blockquote") return chain.toggleBlockquote().run();
  return chain.toggleCodeBlock().run();
}

export function selectedNoteImage(editor: Editor): SelectedNoteImage | null {
  const selection = editor.state.selection;
  if (!(selection instanceof NodeSelection) || selection.node.type.name !== "noteImage") return null;
  return {
    position: selection.from,
    attachmentRef: String(selection.node.attrs.attachment_ref ?? ""),
    alt: String(selection.node.attrs.alt ?? ""),
    caption: String(selection.node.attrs.caption ?? ""),
  };
}

interface TableContext {
  table: ProseMirrorNode;
  tablePosition: number;
  cellPosition: number;
  cell: ProseMirrorNode;
}

function tableContext(editor: Editor): TableContext | null {
  const { $from } = editor.state.selection;
  let tableDepth = -1;
  let cellDepth = -1;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const name = $from.node(depth).type.name;
    if (cellDepth < 0 && (name === "tableCell" || name === "tableHeader")) cellDepth = depth;
    if (name === "table") {
      tableDepth = depth;
      break;
    }
  }
  if (tableDepth < 0 || cellDepth < 0) return null;
  return {
    table: $from.node(tableDepth),
    tablePosition: $from.start(tableDepth),
    cellPosition: $from.before(cellDepth),
    cell: $from.node(cellDepth),
  };
}

export function selectCurrentTablePart(editor: Editor, part: "row" | "column" | "table") {
  const context = tableContext(editor);
  if (!context) return false;
  const map = TableMap.get(context.table);
  const relativeCell = context.cellPosition - context.tablePosition;
  const rect = map.findCell(relativeCell);
  let anchor = 0;
  let head = 0;
  if (part === "row") {
    anchor = map.positionAt(rect.top, 0, context.table);
    head = map.positionAt(rect.top, map.width - 1, context.table);
  } else if (part === "column") {
    anchor = map.positionAt(0, rect.left, context.table);
    head = map.positionAt(map.height - 1, rect.left, context.table);
  } else {
    anchor = map.positionAt(0, 0, context.table);
    head = map.positionAt(map.height - 1, map.width - 1, context.table);
  }
  const selection = CellSelection.create(
    editor.state.doc,
    context.tablePosition + anchor,
    context.tablePosition + head,
  );
  editor.view.dispatch(editor.state.tr.setSelection(selection).scrollIntoView());
  return true;
}

export function resetCurrentTableColumnWidths(editor: Editor) {
  const context = tableContext(editor);
  if (!context) return false;
  const transaction = editor.state.tr;
  context.table.descendants((node, position) => {
    if (node.type.name === "tableCell" || node.type.name === "tableHeader") {
      transaction.setNodeMarkup(context.tablePosition + position, undefined, { ...node.attrs, colwidth: null });
    }
  });
  if (transaction.steps.length === 0) return false;
  editor.view.dispatch(transaction.scrollIntoView());
  return true;
}

export function currentTableCellText(editor: Editor) {
  const context = tableContext(editor);
  return context?.cell.textBetween(0, context.cell.content.size, "\n") ?? "";
}

export function clearCurrentTableCell(editor: Editor) {
  const context = tableContext(editor);
  if (!context) return false;
  const paragraph = editor.schema.nodes.paragraph?.createAndFill();
  if (!paragraph) return false;
  const transaction = editor.state.tr.replaceWith(
    context.cellPosition + 1,
    context.cellPosition + context.cell.nodeSize - 1,
    paragraph,
  );
  editor.view.dispatch(transaction.scrollIntoView());
  return true;
}
