import { afterEach, describe, expect, it, vi } from "vitest";

import { opsApi, type ReadinessState } from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Ops public API", () => {
  it("keeps the readiness request path, credentials, and response shape", async () => {
    const expected: ReadinessState = {
      ready: false,
      health: "degraded",
      setup_blockers: ["model route missing"],
      evidence_ready_projects: ["proj-a"],
      message_code: "common.setup_is_incomplete", message_params: {},
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(expected),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(opsApi.readiness()).resolves.toEqual(expected);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/ops/readiness",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
