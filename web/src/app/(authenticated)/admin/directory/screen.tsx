"use client";

import { AdminDirectoryPage } from "@/components/pages/AdminDirectoryPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function DirectoryScreen() {
  const { isAdmin, setNotice, noGlobalRefresh } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return (
    <AdminDirectoryPage onNotice={setNotice} onRefresh={noGlobalRefresh} />
  );
}
