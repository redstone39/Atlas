"use client";

import { OpsPage } from "@/components/pages/OpsPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function OpsScreen() {
  const { isAdmin, canUseOps, navigate } = useAuthenticatedShell();
  if (!canUseOps) return <AdminAccessDenied operatorAllowed />;
  return <OpsPage canManageSetup={isAdmin} onNavigate={navigate} />;
}
