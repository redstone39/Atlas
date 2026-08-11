"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  identitySessionApi,
  type SessionState,
} from "@/features/identity-session/index";
import { isAbortError, sessionQueryClient } from "@/shared/session-query-client";

export type AtlasSessionContextValue = {
  session: SessionState | null;
  authUnavailable: boolean;
  refreshSession(signal?: AbortSignal): Promise<SessionState>;
  beginSession(next: SessionState): void;
  logout(): Promise<void>;
};

const AtlasSessionContext = createContext<AtlasSessionContextValue | null>(null);
const ANONYMOUS_SESSION: SessionState = {
  authenticated: false,
  actor: null,
  available_projects: [],
  system_role: null,
  team_roles: {},
};

export function AtlasSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState | null>(null);
  const [authUnavailable, setAuthUnavailable] = useState(false);

  const refreshSession = useCallback(async (signal?: AbortSignal) => {
    const nextSession = await sessionQueryClient.query({
      key: ["identity", "session"],
      signal,
      queryFn: identitySessionApi.session,
    });
    setSession(nextSession);
    setAuthUnavailable(false);
    return nextSession;
  }, []);

  const settleUnavailableSession = useCallback(() => {
    setAuthUnavailable(true);
    setSession(ANONYMOUS_SESSION);
  }, []);

  useEffect(
    () =>
      sessionQueryClient.onSessionInvalidated(() => {
        setSession(null);
        void refreshSession().catch((error) => {
          if (!isAbortError(error)) settleUnavailableSession();
        });
      }),
    [refreshSession, settleUnavailableSession],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshSession(controller.signal).catch((error) => {
      if (!isAbortError(error)) settleUnavailableSession();
    });
    return () => controller.abort();
  }, [refreshSession, settleUnavailableSession]);

  function beginSession(next: SessionState) {
    sessionQueryClient.beginSession(next.actor?.actor_id ?? "anonymous");
    setSession(next);
    setAuthUnavailable(false);
  }

  async function logout() {
    await identitySessionApi.logout();
    sessionQueryClient.resetSession();
    setSession(ANONYMOUS_SESSION);
    setAuthUnavailable(false);
  }

  return (
    <AtlasSessionContext.Provider
      value={{ session, authUnavailable, refreshSession, beginSession, logout }}
    >
      {children}
    </AtlasSessionContext.Provider>
  );
}

export function useAtlasSession(): AtlasSessionContextValue {
  const context = useContext(AtlasSessionContext);
  if (!context) throw new Error("useAtlasSession must be used within AtlasSessionProvider");
  return context;
}
