import { useCallback, useEffect, useRef, useState } from "react";

import { processingJobApi } from "./api";
import { ACTIVE_PROCESSING_STATUSES, type ProcessingJobStatus } from "./types";

const POLL_INTERVAL_MS = 2_000;

export function useProcessingJobs(onProcessingSettled?: () => void) {
  const [jobs, setJobs] = useState<ProcessingJobStatus[]>([]);
  const [error, setError] = useState("");
  const requestInFlight = useRef(false);
  const activeJobIds = useRef<Set<string>>(new Set());
  const onProcessingSettledRef = useRef(onProcessingSettled);
  onProcessingSettledRef.current = onProcessingSettled;
  const refresh = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    try {
      const result = await processingJobApi.list();
      const nextActiveJobIds = new Set(
        result.jobs
          .filter((job) => job.is_current && ACTIVE_PROCESSING_STATUSES.has(job.status))
          .map((job) => job.job_id),
      );
      const processingSettled = [...activeJobIds.current].some(
        (jobId) => !nextActiveJobIds.has(jobId),
      );
      activeJobIds.current = nextActiveJobIds;
      setJobs(result.jobs);
      setError("");
      if (processingSettled) onProcessingSettledRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "processing_jobs_unavailable");
    } finally {
      requestInFlight.current = false;
    }
  }, []);

  const hasActiveCurrentJob = jobs.some(
    (job) => job.is_current && ACTIVE_PROCESSING_STATUSES.has(job.status),
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!hasActiveCurrentJob) return;
    const poll = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const interval = window.setInterval(poll, POLL_INTERVAL_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [hasActiveCurrentJob, refresh]);

  return { jobs, error, refresh };
}
