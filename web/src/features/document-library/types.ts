import type { AdminActionResult } from "../../shared/api-contracts";
import type { DocumentTagSummary, DocumentTagType } from "../../shared/document-contracts";

export interface DocumentProjectView {
  project_id: string;
  name: string;
  membership_status: "active" | "revoked" | "missing";
  role: "viewer" | "contributor" | "admin" | null;
}

export interface DocumentTeamView {
  team_id: string;
  name: string;
  status: string;
}

export interface DocumentLibrarySummary {
  document_id: string;
  title: string;
  description: string | null;
  intake_status: string;
  document_format: string;
  profile_id: string | null;
  profile_revision: number | null;
  current_stage: string | null;
  warning_codes: string[];
  failure_code: string | null;
  job_id: string | null;
  lifecycle_status: "active" | "disabled" | "restoring";
  uploader_actor_id: string | null;
  scope_type: DocumentTagType;
  scope_id: string;
  direct_tags: DocumentTagSummary[];
  allow_member_download: boolean;
  download_available: boolean;
  source_filename: string | null;
  source_byte_size: number | null;
  content_type: string | null;
  raw_sha256: string | null;
  uploaded_at: string | null;
  disabled_at: string | null;
  restored_at: string | null;
  evidence_count: number;
}

export interface DocumentLibraryListResult {
  documents: DocumentLibrarySummary[];
}

export interface DocumentLibraryMutationResult extends AdminActionResult {
  document: DocumentLibrarySummary | null;
}

export interface DocumentLibrarySessionView {
  actor: { actor_id: string } | null;
  available_projects: DocumentProjectView[];
  system_role: "user" | "admin" | "operator" | null;
  team_roles: Record<string, "member" | "uploader" | "admin">;
}

export type LoadDocumentTeams = () => Promise<{ teams: DocumentTeamView[] }>;
export type LoadWorkspaceDocumentScope = () => Promise<{ tags: DocumentTagSummary[] }>;
