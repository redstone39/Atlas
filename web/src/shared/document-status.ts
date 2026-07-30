import {
  localizedStatusLabel,
  type StatusSemantic,
} from "./product-ui";

export type DocumentLibraryProductStatus =
  | "processing"
  | "searchable"
  | "failed"
  | "updating";

const ACTIVE_PROCESSING_STATUSES = new Set([
  "queued",
  "processing",
  "processing_queued",
  "waiting_retry",
  "publishing",
]);
const READY_INTAKE_STATUSES = new Set([
  "evidence_ready",
  "ready",
  "ready_with_warnings",
]);
const FAILED_INTAKE_STATUSES = new Set([
  "cancelled",
  "extraction_failed",
  "failed",
]);

export function intakeStatusLabel(
  status: string,
  t: (key: string, options?: { defaultValue?: string }) => string,
) {
  return localizedStatusLabel(status, t);
}

export function intakeStatusSemantic(status: string): StatusSemantic {
  if (["evidence_ready", "ready", "ready_with_warnings"].includes(status)) {
    return "success";
  }
  if (["extraction_failed", "failed"].includes(status)) return "failure";
  if (["processing", "queued", "waiting_retry", "publishing"].includes(status)) {
    return "progress";
  }
  if (status === "cancelled") return "inactive";
  return "unknown";
}

export function documentLibraryProductStatus({
  intakeStatus,
  evidenceCount,
  processingStatus,
}: {
  intakeStatus: string;
  evidenceCount: number;
  processingStatus?: string | null;
}): DocumentLibraryProductStatus {
  const hasSearchableCurrent =
    evidenceCount > 0 || READY_INTAKE_STATUSES.has(intakeStatus);
  const effectiveProcessingStatus = processingStatus ?? intakeStatus;

  if (ACTIVE_PROCESSING_STATUSES.has(effectiveProcessingStatus)) {
    return hasSearchableCurrent ? "updating" : "processing";
  }
  if (hasSearchableCurrent) return "searchable";
  if (FAILED_INTAKE_STATUSES.has(effectiveProcessingStatus)) return "failed";
  return "processing";
}

export function documentLibraryProductStatusLabel(
  status: DocumentLibraryProductStatus,
  t: (key: string) => string,
) {
  return t(`documentLibrary.status.${status}`);
}

export function documentLibraryProductStatusSemantic(
  status: DocumentLibraryProductStatus,
): StatusSemantic {
  if (status === "searchable") return "success";
  if (status === "failed") return "failure";
  return "progress";
}
