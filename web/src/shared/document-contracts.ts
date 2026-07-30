export type DocumentTagType = "project" | "team";

export interface DocumentTagRef {
  tag_type: DocumentTagType;
  tag_id: string;
}

export interface DocumentTagSummary extends DocumentTagRef {
  label: string;
}
