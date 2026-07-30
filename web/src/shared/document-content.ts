import { API_BASE } from "./api-client";
import { ApiError } from "./user-messages";

export function documentContentPath(documentId: string) {
  return `/api/v1/library/documents/${encodeURIComponent(documentId)}/content`;
}

export function safeDocumentFilename(filename: string) {
  const leaf = filename.replaceAll("\\", "/").split("/").at(-1) ?? "";
  const safe = leaf
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .replace(/[<>:"/\\|?*]+/g, "_")
    .trim()
    .slice(0, 180);
  return safe || "atlas-document";
}

export async function downloadDocumentContent(documentId: string, filename: string) {
  const path = documentContentPath(documentId);
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    method: "HEAD",
  });
  if (!response.ok) {
    throw new ApiError(null, response.status);
  }
  const anchor = window.document.createElement("a");
  anchor.href = `${API_BASE}${path}`;
  anchor.download = safeDocumentFilename(filename);
  anchor.click();
}
