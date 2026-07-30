import { requestJson } from "../../shared/api-client";
import type { ProcessingJobStatus } from "./types";

export const processingJobApi = {
  list: () =>
    requestJson<{ jobs: ProcessingJobStatus[] }>("/api/v1/processing/jobs"),
  cancel: (jobId: string) =>
    requestJson<ProcessingJobStatus>(
      `/api/v1/processing/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    ),
  retry: (jobId: string) =>
    requestJson<ProcessingJobStatus>(
      `/api/v1/processing/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" },
    ),
};
