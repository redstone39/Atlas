"use client";

import { AdminPluginsPage } from "@/components/pages/AdminPluginsPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function PluginsScreen({
  initialTab,
  requestedRunId,
}: {
  initialTab: "plugins" | "runs";
  requestedRunId: string | null;
}) {
  const { isAdmin } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return (
    <AdminPluginsPage
      initialTab={initialTab}
      requestedRunId={requestedRunId}
    />
  );
}
