import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { processingJobApi } from "./api";
import type { ProcessingJobStatus } from "./types";
import { useProcessingJobs } from "./useProcessingJobs";

vi.mock("./api", () => ({
  processingJobApi: {
    list: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
  },
}));

const activeJob: ProcessingJobStatus = {
  job_id: "job-1",
  document_id: "doc-1",
  document_format: "pdf",
  profile_id: null,
  profile_revision: null,
  current_stage: "extracting",
  warning_codes: [],
  failure_code: null,
  status: "processing",
  status_url: "/api/v1/processing/jobs/job-1",
  retry_available: false,
  cancel_available: true,
  review_available: false,
  progress_current: 2,
  progress_total: 10,
  progress_unit: "page",
  elapsed_seconds: 4,
  attempt_started_at: "2026-07-16T04:00:00Z",
  is_current: true,
  created_at: "2026-07-16T04:00:00Z",
  updated_at: "2026-07-16T04:00:04Z",
};

describe("useProcessingJobs", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(processingJobApi.list).mockReset();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls active current jobs and stops polling after a terminal result", async () => {
    vi.mocked(processingJobApi.list)
      .mockResolvedValueOnce({ jobs: [activeJob] })
      .mockResolvedValueOnce({ jobs: [{ ...activeJob, status: "ready", cancel_available: false }] });

    renderHook(() => useProcessingJobs());
    await act(async () => undefined);
    expect(processingJobApi.list).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });
    expect(processingJobApi.list).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });
    expect(processingJobApi.list).toHaveBeenCalledTimes(2);
  });

  it("notifies the consumer when active processing settles", async () => {
    const onProcessingSettled = vi.fn();
    vi.mocked(processingJobApi.list)
      .mockResolvedValueOnce({ jobs: [activeJob] })
      .mockResolvedValueOnce({ jobs: [{ ...activeJob, status: "ready", cancel_available: false }] });

    renderHook(() => useProcessingJobs(onProcessingSettled));
    await act(async () => undefined);
    expect(onProcessingSettled).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });
    expect(onProcessingSettled).toHaveBeenCalledOnce();
  });

  it("does not poll a hidden tab and refreshes when it becomes visible", async () => {
    vi.mocked(processingJobApi.list).mockResolvedValue({ jobs: [activeJob] });
    renderHook(() => useProcessingJobs());
    await act(async () => undefined);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });
    expect(processingJobApi.list).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(processingJobApi.list).toHaveBeenCalledTimes(2);
  });
});
