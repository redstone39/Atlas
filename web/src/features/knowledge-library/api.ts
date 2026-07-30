import { requestJson } from "../../shared/api-client";
import { downloadDocumentContent } from "../../shared/document-content";
import type { KnowledgeDocumentListResult } from "./types";

export const knowledgeLibraryApi = {
  listKnowledgeDocuments: () =>
    requestJson<KnowledgeDocumentListResult>("/api/v1/library/documents"),
  downloadKnowledgeDocument: downloadDocumentContent,
};
