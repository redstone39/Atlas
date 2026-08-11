"use client";

import { useRouter } from "next/navigation";

import { LoginPage } from "@/components/pages/LoginPage";
import { LoadingShell } from "@/shared/product-ui";
import { useAtlasSession } from "../../session-provider";

export function LoginScreen() {
  const router = useRouter();
  const { session, authUnavailable, beginSession } = useAtlasSession();

  if (!session) return <LoadingShell />;

  return (
    <LoginPage
      session={session}
      authUnavailable={authUnavailable}
      onLogin={(nextSession) => {
        beginSession(nextSession);
        router.replace("/workspace");
      }}
    />
  );
}
