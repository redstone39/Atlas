import { diff_match_patch, DIFF_DELETE, DIFF_EQUAL, DIFF_INSERT } from "@dmsnell/diff-match-patch";
import { TiptapTransformer } from "@hocuspocus/transformer";
import { create as createJsonDiff } from "jsondiffpatch/with-text-diffs";
import type * as Y from "yjs";
import type { AttributeChange, ChangeSet, JsonObject, MarkChange, MoveChange, NodeChange, TextChange } from "./types.js";

const jsonDiff = createJsonDiff({ textDiff: { minLength: 1 } });

export function canonicalBody(document: Y.Doc): JsonObject {
  const value = TiptapTransformer.fromYdoc(document, "default") as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Yjs document does not contain a canonical ProseMirror body");
  return value as JsonObject;
}

function textOf(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const node = value as JsonObject;
  const own = typeof node.text === "string" ? node.text : "";
  const content = Array.isArray(node.content) ? node.content.map(textOf).join("") : "";
  return own + content;
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function structureAnchor(before: unknown, after: unknown): boolean {
  if (same(before, after)) return true;
  if (!before || !after || typeof before !== "object" || typeof after !== "object" || Array.isArray(before) || Array.isArray(after)) {
    return false;
  }
  const left = before as JsonObject;
  const right = after as JsonObject;
  return typeof left.type === "string" && left.type === right.type && textOf(left) === textOf(right);
}

function markMap(value: unknown): Map<string, JsonObject> {
  const node = objectValue(value);
  const marks = Array.isArray(node.marks) ? node.marks.map(objectValue) : [];
  return new Map(marks.filter(mark => typeof mark.type === "string").map(mark => [mark.type as string, mark]));
}

function sameMarkTransition(left: MarkChange, right: MarkChange): boolean {
  return left.change === right.change
    && left.mark_type === right.mark_type
    && same(left.before, right.before)
    && same(left.after, right.after);
}

function collectInlineMarkChanges(before: unknown[], after: unknown[], parentPath: number[], marks: MarkChange[]): boolean {
  const beforeNodes = before.map(objectValue);
  const afterNodes = after.map(objectValue);
  if (
    beforeNodes.length === 0
    || afterNodes.length === 0
    || !beforeNodes.every(node => node.type === "text" && typeof node.text === "string")
    || !afterNodes.every(node => node.type === "text" && typeof node.text === "string")
    || beforeNodes.map(node => node.text as string).join("") !== afterNodes.map(node => node.text as string).join("")
  ) return false;

  const boundaries = new Set<number>([0]);
  let total = 0;
  for (const node of beforeNodes) {
    total += (node.text as string).length;
    boundaries.add(total);
  }
  total = 0;
  for (const node of afterNodes) {
    total += (node.text as string).length;
    boundaries.add(total);
  }
  const offsets = [...boundaries].sort((left, right) => left - right);
  const lastTransitionByMark = new Map<string, { end: number; change: MarkChange }>();
  let beforeIndex = 0;
  let beforeEnd = (beforeNodes[0]!.text as string).length;
  let afterIndex = 0;
  let afterEnd = (afterNodes[0]!.text as string).length;
  for (let segment = 0; segment < offsets.length - 1; segment += 1) {
    const start = offsets[segment]!;
    while (start >= beforeEnd && beforeIndex < beforeNodes.length - 1) {
      beforeIndex += 1;
      beforeEnd += (beforeNodes[beforeIndex]!.text as string).length;
    }
    while (start >= afterEnd && afterIndex < afterNodes.length - 1) {
      afterIndex += 1;
      afterEnd += (afterNodes[afterIndex]!.text as string).length;
    }
    const beforeMarks = markMap(beforeNodes[beforeIndex]);
    const afterMarks = markMap(afterNodes[afterIndex]);
    const markTypes = [...new Set([...beforeMarks.keys(), ...afterMarks.keys()])].sort();
    for (const markType of markTypes) {
      const beforeMark = beforeMarks.get(markType);
      const afterMark = afterMarks.get(markType);
      if (same(beforeMark, afterMark)) continue;
      const change: MarkChange = {
        change: beforeMark === undefined ? "add" : afterMark === undefined ? "remove" : "replace",
        path: [...parentPath, afterIndex],
        mark_type: markType,
        before: beforeMark ? objectValue(beforeMark.attrs) : null,
        after: afterMark ? objectValue(afterMark.attrs) : null,
      };
      const previous = lastTransitionByMark.get(markType);
      if (previous?.end === start && sameMarkTransition(previous.change, change)) {
        previous.end = offsets[segment + 1]!;
        continue;
      }
      marks.push(change);
      lastTransitionByMark.set(markType, { end: offsets[segment + 1]!, change });
    }
  }
  return true;
}

function collectStructure(
  beforeValue: unknown,
  afterValue: unknown,
  path: number[],
  text: TextChange[],
  nodes: NodeChange[],
  marks: MarkChange[],
  attributes: AttributeChange[],
): void {
  const before = objectValue(beforeValue);
  const after = objectValue(afterValue);
  const beforeType = typeof before.type === "string" ? before.type : null;
  const afterType = typeof after.type === "string" ? after.type : null;
  if (beforeType !== afterType) {
    nodes.push({
      change: beforeType === null ? "insert" : afterType === null ? "delete" : "replace",
      path,
      before_type: beforeType,
      after_type: afterType,
    });
  }

  const beforeText = typeof before.text === "string" ? before.text : "";
  const afterText = typeof after.text === "string" ? after.text : "";
  if (beforeText !== afterText && (beforeType === "text" || afterType === "text")) {
    text.push(...deriveTextChanges(beforeText, afterText, path));
  }

  const beforeAttrs = objectValue(before.attrs);
  const afterAttrs = objectValue(after.attrs);
  const attributeNames = [...new Set([...Object.keys(beforeAttrs), ...Object.keys(afterAttrs)])].sort();
  for (const attribute of attributeNames) {
    if (!same(beforeAttrs[attribute], afterAttrs[attribute])) {
      attributes.push({ path, node_type: afterType || beforeType || "unknown", attribute, before: beforeAttrs[attribute] ?? null, after: afterAttrs[attribute] ?? null });
    }
  }

  const beforeMarks = Array.isArray(before.marks) ? before.marks.map(objectValue) : [];
  const afterMarks = Array.isArray(after.marks) ? after.marks.map(objectValue) : [];
  const beforeByType = new Map(beforeMarks.filter(mark => typeof mark.type === "string").map(mark => [mark.type as string, mark]));
  const afterByType = new Map(afterMarks.filter(mark => typeof mark.type === "string").map(mark => [mark.type as string, mark]));
  const markTypes = [...new Set([...beforeByType.keys(), ...afterByType.keys()])].sort();
  for (const markType of markTypes) {
    const beforeMark = beforeByType.get(markType);
    const afterMark = afterByType.get(markType);
    if (same(beforeMark, afterMark)) continue;
    marks.push({
      change: beforeMark === undefined ? "add" : afterMark === undefined ? "remove" : "replace",
      path,
      mark_type: markType,
      before: beforeMark ? objectValue(beforeMark.attrs) : null,
      after: afterMark ? objectValue(afterMark.attrs) : null,
    });
  }

  const beforeContent = Array.isArray(before.content) ? before.content : [];
  const afterContent = Array.isArray(after.content) ? after.content : [];
  collectAlignedContent(beforeContent, afterContent, path, text, nodes, marks, attributes);
}

function collectAlignedContent(
  before: unknown[],
  after: unknown[],
  parentPath: number[],
  text: TextChange[],
  nodes: NodeChange[],
  marks: MarkChange[],
  attributes: AttributeChange[],
): void {
  if (collectInlineMarkChanges(before, after, parentPath, marks)) return;
  const rows = before.length + 1;
  const columns = after.length + 1;
  const lcs = Array.from({ length: rows }, () => Array<number>(columns).fill(0));
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      lcs[left]![right] = structureAnchor(before[left], after[right])
        ? 1 + lcs[left + 1]![right + 1]!
        : Math.max(lcs[left + 1]![right]!, lcs[left]![right + 1]!);
    }
  }

  const anchors: Array<[number, number]> = [];
  let left = 0;
  let right = 0;
  while (left < before.length && right < after.length) {
    if (structureAnchor(before[left], after[right])) {
      anchors.push([left, right]);
      left += 1;
      right += 1;
    } else if (lcs[left + 1]![right]! >= lcs[left]![right + 1]!) {
      left += 1;
    } else {
      right += 1;
    }
  }

  const segments = [...anchors, [before.length, after.length] as [number, number]];
  let beforeStart = 0;
  let afterStart = 0;
  for (const [beforeAnchor, afterAnchor] of segments) {
    const paired = Math.min(beforeAnchor - beforeStart, afterAnchor - afterStart);
    for (let index = 0; index < paired; index += 1) {
      collectStructure(
        before[beforeStart + index],
        after[afterStart + index],
        [...parentPath, afterStart + index],
        text,
        nodes,
        marks,
        attributes,
      );
    }
    for (let index = beforeStart + paired; index < beforeAnchor; index += 1) {
      collectStructure(before[index], undefined, [...parentPath, afterStart + paired], text, nodes, marks, attributes);
    }
    for (let index = afterStart + paired; index < afterAnchor; index += 1) {
      collectStructure(undefined, after[index], [...parentPath, index], text, nodes, marks, attributes);
    }
    if (beforeAnchor < before.length && afterAnchor < after.length) {
      collectStructure(
        before[beforeAnchor],
        after[afterAnchor],
        [...parentPath, afterAnchor],
        text,
        nodes,
        marks,
        attributes,
      );
      beforeStart = beforeAnchor + 1;
      afterStart = afterAnchor + 1;
    }
  }
}

function deriveTextChanges(before: string, after: string, path: number[]): TextChange[] {
  const engine = new diff_match_patch();
  const diffs = engine.diff_main(before, after);
  engine.diff_cleanupSemantic(diffs);
  const changes: TextChange[] = [];
  let beforeOffset = 0;
  let index = 0;
  while (index < diffs.length) {
    const [operation, value] = diffs[index]!;
    if (operation === DIFF_EQUAL) {
      beforeOffset += value.length;
      index += 1;
      continue;
    }
    const from = beforeOffset;
    let removed = "";
    let inserted = "";
    while (index < diffs.length && diffs[index]![0] !== DIFF_EQUAL) {
      const [edit, text] = diffs[index]!;
      if (edit === DIFF_DELETE) {
        removed += text;
        beforeOffset += text.length;
      } else if (edit === DIFF_INSERT) {
        inserted += text;
      }
      index += 1;
    }
    changes.push({
      path,
      change: removed.length === 0 ? "insert" : inserted.length === 0 ? "delete" : "replace",
      before: removed,
      after: inserted,
      from_offset: from,
      to_offset: beforeOffset,
    });
  }
  return changes;
}

export function deriveChangeSet(before: JsonObject, after: JsonObject): ChangeSet {
  if (jsonDiff.diff(before, after) === undefined) return { text: [], nodes: [], marks: [], attributes: [], moves: [] };

  const text: TextChange[] = [];
  const nodes: NodeChange[] = [];
  const marks: MarkChange[] = [];
  const attributes: AttributeChange[] = [];
  const moves: MoveChange[] = [];
  const beforeContent = Array.isArray(before.content) ? before.content : [];
  const afterContent = Array.isArray(after.content) ? after.content : [];
  const blockId = (value: unknown): string | null => {
    const attrs = objectValue(objectValue(value).attrs);
    return typeof attrs.block_id === "string" && attrs.block_id.length > 0 ? attrs.block_id : null;
  };
  const beforeIds = beforeContent.map(blockId);
  const afterIds = afterContent.map(blockId);
  const idsAreUsable = beforeIds.every(id => id !== null)
    && afterIds.every(id => id !== null)
    && new Set(beforeIds).size === beforeIds.length
    && new Set(afterIds).size === afterIds.length;

  if (!idsAreUsable) {
    collectStructure(before, after, [], text, nodes, marks, attributes);
    return { text, nodes, marks, attributes, moves };
  }

  const beforeIndex = new Map(beforeIds.map((id, index) => [id as string, index]));
  const afterIndex = new Map(afterIds.map((id, index) => [id as string, index]));

  for (const [id, sourceIndex] of beforeIndex) {
    const targetIndex = afterIndex.get(id);
    if (targetIndex === undefined) {
      collectStructure(beforeContent[sourceIndex], undefined, [sourceIndex], text, nodes, marks, attributes);
      continue;
    }
    collectStructure(beforeContent[sourceIndex], afterContent[targetIndex], [targetIndex], text, nodes, marks, attributes);
  }
  for (const [id, targetIndex] of afterIndex) {
    if (!beforeIndex.has(id)) {
      collectStructure(undefined, afterContent[targetIndex], [targetIndex], text, nodes, marks, attributes);
    }
  }

  // Model insert/delete events first, then emit a deterministic sequence whose
  // paths can be replayed in order to obtain the exact durable after-order.
  const working = (beforeIds as string[]).filter(id => afterIndex.has(id));
  for (let index = 0; index < afterIds.length; index += 1) {
    const id = afterIds[index] as string;
    if (!beforeIndex.has(id)) working.splice(index, 0, id);
  }
  for (let targetIndex = 0; targetIndex < afterIds.length; targetIndex += 1) {
    const id = afterIds[targetIndex] as string;
    const sourceIndex = working.indexOf(id);
    if (sourceIndex < 0) throw new Error("Block move derivation lost a stable identity");
    if (sourceIndex === targetIndex) continue;
    working.splice(sourceIndex, 1);
    working.splice(targetIndex, 0, id);
    moves.push({ block_id: id, from_path: [sourceIndex], to_path: [targetIndex] });
  }
  return { text, nodes, marks, attributes, moves };
}

export function mergeChangeSets(fromCheckpoint: JsonObject, current: JsonObject): ChangeSet {
  return deriveChangeSet(fromCheckpoint, current);
}
