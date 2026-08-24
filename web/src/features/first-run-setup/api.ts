import { requestJson } from "../../shared/api-client";
import type { SessionState } from "../identity-session";
import type { FirstAdminClaimInput, FirstAdminStatus } from "./types";

export const firstRunSetupApi = {
  firstAdminStatus: (signal?: AbortSignal) =>
    requestJson<FirstAdminStatus>("/api/v1/auth/first-admin", { signal }),
  claimFirstAdmin: (input: FirstAdminClaimInput) =>
    requestJson<SessionState>("/api/v1/auth/first-admin", {
      method: "POST",
      body: JSON.stringify({
        display_name: input.displayName,
        email: input.email,
        password: input.password,
      }),
    }),
};
