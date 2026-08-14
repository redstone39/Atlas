import type { DocumentTagRef, DocumentTagSummary } from "./types";

const WORKSPACE_SCOPE_STORAGE_PREFIX = "atlas.workspace.scope.";

export function scopeTagKey(ref: DocumentTagRef) {
  return `${ref.tag_type}:${ref.tag_id}`;
}

export function workspaceScopeStorageKey(actorId: string) {
  return `${WORKSPACE_SCOPE_STORAGE_PREFIX}${actorId}`;
}

export function readStoredScopeSelection(
  storageKey: string,
): DocumentTagSummary[] {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "[]");
    if (!Array.isArray(stored)) return [];
    return stored.filter(
      (item): item is DocumentTagSummary =>
        typeof item === "object" &&
        item !== null &&
        (item.tag_type === "project" || item.tag_type === "team") &&
        typeof item.tag_id === "string" &&
        item.tag_id.length > 0 &&
        typeof item.label === "string" &&
        item.label.length > 0,
    );
  } catch {
    return [];
  }
}

export function storeScopeSelection(
  storageKey: string,
  selection: DocumentTagSummary[],
) {
  try {
    if (selection.length === 0) {
      window.sessionStorage.removeItem(storageKey);
      return;
    }
    window.sessionStorage.setItem(storageKey, JSON.stringify(selection));
  } catch {
    // Storage can be unavailable without blocking conversation creation.
  }
}
