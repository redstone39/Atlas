"use client";

import { AgentAccessPage } from "@/components/pages/AgentAccessPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function AgentsScreen() {
  const { session, isAdmin, setNotice, noGlobalRefresh } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return (
    <AgentAccessPage
      projects={session.available_projects}
      onNotice={setNotice}
      onRefresh={noGlobalRefresh}
    />
  );
}
