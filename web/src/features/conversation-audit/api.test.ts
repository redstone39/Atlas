import { existsSync, readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { conversationAuditApi } from "./index";


afterEach(() => vi.unstubAllGlobals());


function successfulFetch() {
  const mock = vi.fn().mockImplementation((path: string) => {
    const body = path === "/api/v1/admin/audit/events"
      ? { events: [] }
      : path.includes("/runtime")
        ? {}
        : path === "/api/v1/admin/conversations" || path.includes("?cursor=")
          ? { conversations: [], next_cursor: null }
          : {
              conversation: {
                conversation_id: "conv-a",
                owner_actor_id: "admin-a",
                title: "Audit conversation",
                status: "active",
                created_at: "2026-07-20T00:00:00Z",
                updated_at: "2026-07-20T00:00:00Z",
              },
              turns: [],
            };
    return Promise.resolve({ ok: true, text: async () => JSON.stringify(body) });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}


describe("conversation audit API and feature boundary", () => {
  it("preserves bounded audit and conversation trace request generators", async () => {
    const fetchMock = successfulFetch();
    await conversationAuditApi.listAuditEvents();
    await conversationAuditApi.listAdminConversations();
    await conversationAuditApi.listAdminConversations("cursor value");
    await conversationAuditApi.getAdminConversation("conv a");
    await conversationAuditApi.getAdminConversationRuntime("conv a", "turn a");
    await conversationAuditApi.readAdminDeclaredEvidence(
      "conv a",
      "turn a",
      "declared evidence open a",
    );

    expect(fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
    }))).toEqual([
      { path: "/api/v1/admin/audit/events", method: undefined },
      { path: "/api/v1/admin/conversations", method: undefined },
      {
        path: "/api/v1/admin/conversations?cursor=cursor%20value",
        method: undefined,
      },
      { path: "/api/v1/admin/conversations/conv%20a", method: undefined },
      {
        path: "/api/v1/admin/conversations/conv%20a/turns/turn%20a/runtime",
        method: undefined,
      },
      {
        path: "/api/v1/admin/conversations/conv%20a/turns/turn%20a/declared-evidence/declared%20evidence%20open%20a",
        method: undefined,
      },
    ]);
  });

  it("keeps page and registry on the public feature API without root facades", () => {
    const feature = readFileSync(
      "src/features/conversation-audit/ConversationAuditFeature.tsx",
      "utf8",
    );
    expect(feature).not.toContain('from "../../api"');
    expect(feature).not.toContain('from "../../types"');
    expect(feature).toContain('from "../workspace/index"');

    const page = readFileSync("src/pages/AuditPage.tsx", "utf8");
    expect(page).toContain('from "../features/conversation-audit/index"');
    expect(page).not.toContain("useState");

    expect(existsSync("src/api.ts")).toBe(false);
    expect(existsSync("src/types.ts")).toBe(false);

    const registry = JSON.parse(
      readFileSync("../architecture-boundaries.json", "utf8"),
    );
    const owner = registry.owners.find(
      (item: { id: string }) => item.id === "frontend_features",
    );
    expect(owner.public_contracts).toContain(
      "web/src/features/conversation-audit/index.ts",
    );
  });
});
