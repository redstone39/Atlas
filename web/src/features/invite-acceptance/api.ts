import { requestJson } from "../../shared/api-client";
import type { InviteAcceptResult } from "./types";

export const inviteAcceptanceApi = {
  acceptInvite: (inviteToken: string, password: string) =>
    requestJson<InviteAcceptResult>("/api/v1/auth/invitations/accept", {
      method: "POST",
      body: JSON.stringify({
        invite_token: inviteToken,
        password,
        idempotency_key: `accept-${inviteToken.slice(0, 12)}`,
      }),
    }),
};
