import type { DocumentTagType } from "../../shared/document-contracts";

export interface KnowledgeScopeSummary {
  scope_type: DocumentTagType;
  scope_id: string;
  scope_label: string;
}

export interface KnowledgeDocumentSummary {
  document_id: string;
  title: string;
  document_format?: string;
  description: string | null;
  authorized_scopes: KnowledgeScopeSummary[];
  source_filename: string | null;
  source_byte_size: number | null;
  uploaded_at: string | null;
  download_available: boolean;
}

export interface KnowledgeDocumentListResult {
  documents: KnowledgeDocumentSummary[];
}
