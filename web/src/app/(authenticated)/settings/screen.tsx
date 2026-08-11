"use client";

import { SettingsPage } from "@/components/pages/SettingsPage";
import { useAuthenticatedShell } from "../layout";

export function SettingsScreen() {
  const { session, managementGroups, navigate } = useAuthenticatedShell();
  return (
    <SettingsPage
      session={session}
      managementGroups={managementGroups}
      onNavigate={navigate}
    />
  );
}
