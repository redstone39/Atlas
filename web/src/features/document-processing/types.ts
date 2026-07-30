export type ProcessingPublicStatus =
  | "queued"
  | "processing"
  | "waiting_retry"
  | "publishing"
  | "ready"
  | "ready_with_warnings"
  | "failed"
  | "cancelled";

export interface ProcessingJobStatus {
  job_id: string;
  document_id: string;
  document_format: string;
  profile_id: string | null;
  profile_revision: number | null;
  current_stage: string | null;
  warning_codes: string[];
  failure_code: string | null;
  status: ProcessingPublicStatus;
  status_url: string;
  retry_available: boolean;
  cancel_available: boolean;
  review_available: boolean;
  progress_current: number;
  progress_total: number | null;
  progress_unit: "page" | "batch";
  elapsed_seconds: number;
  attempt_started_at: string;
  is_current: boolean;
  created_at: string;
  updated_at: string;
  audit_event_ref?: string;
}

export const ACTIVE_PROCESSING_STATUSES: ReadonlySet<ProcessingPublicStatus> = new Set([
  "queued",
  "processing",
  "waiting_retry",
  "publishing",
]);
