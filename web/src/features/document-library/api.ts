import type { AdminActionResult } from "../../shared/api-contracts";
import { requestJson } from "../../shared/api-client";
import { downloadDocumentContent } from "../../shared/document-content";
import type { DocumentTagRef } from "../../shared/document-contracts";
import type { AuditEventList } from "../conversation-audit/index";
import type { DocumentLibraryListResult, DocumentLibraryMutationResult } from "./types";

export const documentLibraryApi = {
  listDocumentLibrary: (scope?: DocumentTagRef) =>
    requestJson<DocumentLibraryListResult>(
      scope
        ? `/api/v1/admin/document-library?${new URLSearchParams({
            scope_type: scope.tag_type,
            scope_id: scope.tag_id,
          }).toString()}`
        : "/api/v1/admin/document-library",
    ),
  uploadDocumentLibraryFile: (input: {
    documentId: string;
    scopeType: "team" | "project";
    scopeId: string;
    tagRefs: DocumentTagRef[];
    file: File;
    description: string;
    allowMemberDownload: boolean;
  }) => {
    const form = new FormData();
    form.set("document_id", input.documentId);
    form.set("scope_type", input.scopeType);
    form.set("scope_id", input.scopeId);
    form.set("tag_refs", JSON.stringify(input.tagRefs));
    form.set("allow_member_download", String(input.allowMemberDownload));
    form.set("idempotency_key", `doclib-${input.documentId}`);
    form.set("file", input.file);
    if (input.description.trim()) form.set("description", input.description.trim());
    return requestJson<DocumentLibraryMutationResult>("/api/v1/admin/document-library", {
      method: "POST",
      body: form,
    });
  },
  updateDocumentLibrary: (
    documentId: string,
    updates: { description?: string; allowMemberDownload?: boolean },
  ) =>
    requestJson<DocumentLibraryMutationResult>(`/api/v1/admin/document-library/${documentId}`, {
      method: "PATCH",
      body: JSON.stringify({
        description: updates.description,
        allow_member_download: updates.allowMemberDownload,
        idempotency_key: `doclib-update-${documentId}`,
      }),
    }),
  refreshDocumentLibraryContent: (documentId: string) =>
    requestJson<AdminActionResult>(
      `/api/v1/admin/document-library/${documentId}/refresh-searchable-content`,
      {
        method: "POST",
        headers: { "Idempotency-Key": globalThis.crypto.randomUUID() },
      },
    ),
  disableDocumentLibraryItem: (documentId: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/document-library/${documentId}/disable`, {
      method: "POST",
    }),
  restoreDocumentLibraryItem: (documentId: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/document-library/${documentId}/restore`, {
      method: "POST",
    }),
  listDocumentLibraryEvents: (documentId: string) =>
    requestJson<AuditEventList>(`/api/v1/admin/document-library/${documentId}/events`),
  downloadDocument: downloadDocumentContent,
};
