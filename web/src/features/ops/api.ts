import { requestJson } from "../../shared/api-client";
import type { ReadinessState } from "./types";

export const opsApi = {
  readiness: (signal?: AbortSignal) =>
    requestJson<ReadinessState>("/api/v1/ops/readiness", { signal }),
};
