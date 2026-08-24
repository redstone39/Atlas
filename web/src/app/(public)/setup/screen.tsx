"use client";

import { FirstRunSetupFeature } from "@/features/first-run-setup/FirstRunSetupFeature";
import { LoadingShell } from "@/shared/product-ui";
import { useAtlasSession } from "../../session-provider";

export function SetupScreen() {
  const { session, beginSession, refreshSession } = useAtlasSession();
  if (!session) return <LoadingShell />;
  return (
    <FirstRunSetupFeature
      session={session}
      beginSession={beginSession}
      refreshSession={refreshSession}
    />
  );
}
