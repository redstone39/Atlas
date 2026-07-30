import { afterEach, describe, expect, it, vi } from "vitest";

import { agentAccessApi } from "./index";


afterEach(() => {
  vi.unstubAllGlobals();
});


function successfulFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    text: async () => "{}",
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}


describe("agent access API contract", () => {
  it("preserves all Agent request paths, methods, and generated bodies", async () => {
    const fetchMock = successfulFetch();

    await agentAccessApi.listAgents();
    await agentAccessApi.createAgent("agent-a", "Agent A");
    await agentAccessApi.updateAgent("agent-a", { displayName: "Agent A2" });
    await agentAccessApi.updateAgent("agent-a", { active: false });
    await agentAccessApi.issueAgentToken("agent-a");
    await agentAccessApi.revokeAgentToken("token-a");

    const calls = fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }));
    expect(calls).toEqual([
      {
        path: "/api/v1/admin/agent-users",
        method: undefined,
        body: null,
      },
      {
        path: "/api/v1/admin/agent-users",
        method: "POST",
        body: {
          actor_id: "agent-a",
          display_name: "Agent A",
          idempotency_key: "agent-agent-a",
        },
      },
      {
        path: "/api/v1/admin/agent-users/agent-a",
        method: "PATCH",
        body: {
          display_name: "Agent A2",
          idempotency_key: "agent-update-agent-a",
        },
      },
      {
        path: "/api/v1/admin/agent-users/agent-a",
        method: "PATCH",
        body: {
          active: false,
          idempotency_key: "agent-update-agent-a",
        },
      },
      {
        path: "/api/v1/admin/agent-users/agent-a/tokens",
        method: "POST",
        body: { idempotency_key: "token-agent-a" },
      },
      {
        path: "/api/v1/admin/agent-tokens/token-a",
        method: "DELETE",
        body: null,
      },
    ]);
  });
});
