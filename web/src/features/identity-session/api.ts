import { requestJson } from "../../shared/api-client";
import type { SessionState } from "./types";

export const identitySessionApi = {
  session: (signal?: AbortSignal) =>
    requestJson<SessionState>("/api/v1/auth/session", { signal }),
  login: (email: string, password: string) =>
    requestJson<SessionState>("/api/v1/auth/sessions", {
      method: "POST",
      body: JSON.stringify({ email, password, idempotency_key: `login-${email}` }),
    }),
  logout: () =>
    requestJson<void>("/api/v1/auth/session", {
      method: "DELETE",
    }),
};
