"use client";

import { AdminModelsPage } from "@/components/pages/AdminModelsPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function ModelsScreen() {
  const { isAdmin, setNotice, noGlobalRefresh } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return <AdminModelsPage onNotice={setNotice} onRefresh={noGlobalRefresh} />;
}
