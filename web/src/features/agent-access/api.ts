import type { AdminActionResult } from "../../shared/api-contracts";
import { requestJson } from "../../shared/api-client";
import type {
  AgentTokenIssueResult,
  AgentUserCreateResult,
  AgentUserListResult,
} from "./types";

export const agentAccessApi = {
  listAgents: () => requestJson<AgentUserListResult>("/api/v1/admin/agent-users"),
  createAgent: (displayName: string, idempotencyKey: string) =>
    requestJson<AgentUserCreateResult>("/api/v1/admin/agent-users", {
      method: "POST",
      body: JSON.stringify({
        display_name: displayName,
        idempotency_key: idempotencyKey,
      }),
    }),
  updateAgent: (
    actorId: string,
    updates: { displayName?: string; active?: boolean },
  ) =>
    requestJson<AdminActionResult>(`/api/v1/admin/agent-users/${actorId}`, {
      method: "PATCH",
      body: JSON.stringify({
        display_name: updates.displayName,
        active: updates.active,
        idempotency_key: `agent-update-${actorId}`,
      }),
    }),
  issueAgentToken: (actorId: string) =>
    requestJson<AgentTokenIssueResult>(
      `/api/v1/admin/agent-users/${actorId}/tokens`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: `token-${actorId}` }),
      },
    ),
  revokeAgentToken: (tokenId: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/agent-tokens/${tokenId}`, {
      method: "DELETE",
    }),
};
