import { describe, expect, it } from "vitest";

import {
  createRuntimePolicyDraft,
  parseRuntimePolicy,
  type RuntimePolicyDraft,
} from "./runtimePolicy";

function draftWithCapacity(
  maxToolExecutions: number,
  maxReasoningRevisionCycles: number,
  maxProviderInvocations: number,
): RuntimePolicyDraft {
  return {
    ...createRuntimePolicyDraft,
    max_tool_executions: String(maxToolExecutions),
    max_reasoning_revision_cycles: String(maxReasoningRevisionCycles),
    max_provider_invocations: String(maxProviderInvocations),
  };
}

describe("runtime policy provider capacity", () => {
  it("defaults max_provider_invocations to 33", () => {
    expect(createRuntimePolicyDraft.max_provider_invocations).toBe("33");
    expect(parseRuntimePolicy(createRuntimePolicyDraft)?.max_provider_invocations).toBe(33);
  });

  it.each([
    { label: "zero revision cycles", tools: 4, cycles: 0, threshold: 13 },
    { label: "multiple revision cycles", tools: 2, cycles: 3, threshold: 29 },
  ])(
    "rejects threshold minus one and accepts the exact threshold for $label",
    ({ tools, cycles, threshold }) => {
      expect(parseRuntimePolicy(draftWithCapacity(tools, cycles, threshold - 1))).toBeNull();

      const parsed = parseRuntimePolicy(draftWithCapacity(tools, cycles, threshold));
      expect(parsed).not.toBeNull();
      expect(parsed).toMatchObject({
        max_tool_executions: tools,
        max_reasoning_revision_cycles: cycles,
        max_provider_invocations: threshold,
      });
    },
  );
});
