"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { LoginPage } from "@/components/pages/LoginPage";
import { firstRunSetupApi } from "@/features/first-run-setup/api";
import { LoadingShell } from "@/shared/product-ui";
import { useAtlasSession } from "../../session-provider";

export function LoginScreen() {
  const router = useRouter();
  const { session, authUnavailable, beginSession } = useAtlasSession();
  const [firstAdminClaimed, setFirstAdminClaimed] = useState(false);
  const [firstAdminState, setFirstAdminState] = useState<
    "checking" | "login" | "unavailable"
  >("checking");
  const [statusAttempt, setStatusAttempt] = useState(0);

  useEffect(() => {
    setFirstAdminClaimed(
      new URLSearchParams(window.location.search).get("setup") === "claimed",
    );
  }, []);

  const retryFirstAdminStatus = useCallback(() => {
    setFirstAdminState("checking");
    setStatusAttempt((attempt) => attempt + 1);
  }, []);

  useEffect(() => {
    if (!session) return;
    if (session.authenticated) {
      setFirstAdminState("login");
      return;
    }
    const controller = new AbortController();
    setFirstAdminState("checking");
    void firstRunSetupApi
      .firstAdminStatus(controller.signal)
      .then((status) => {
        if (status.claim_available) router.replace("/setup");
        else setFirstAdminState("login");
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFirstAdminState("unavailable");
        }
      });
    return () => controller.abort();
  }, [router, session, statusAttempt]);

  if (!session || (!session.authenticated && firstAdminState === "checking")) {
    return <LoadingShell />;
  }

  return (
    <LoginPage
      session={session}
      authUnavailable={authUnavailable}
      firstAdminStatusUnavailable={firstAdminState === "unavailable"}
      firstAdminClaimed={firstAdminClaimed}
      loginAllowed={session.authenticated || firstAdminState === "login"}
      onRetryFirstAdminStatus={retryFirstAdminStatus}
      onLogin={(nextSession) => {
        beginSession(nextSession);
        router.replace("/workspace");
      }}
    />
  );
}
