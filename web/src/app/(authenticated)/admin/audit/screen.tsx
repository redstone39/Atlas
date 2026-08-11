"use client";

import { AuditPage } from "@/components/pages/AuditPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import type { AppRoute } from "@/shared/routes";
import { useAuthenticatedShell } from "../../layout";

export function AuditScreen({ route = "/admin/audit" }: { route?: AppRoute }) {
  const { isAdmin, navigate } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return <AuditPage route={route} onNavigate={navigate} />;
}
