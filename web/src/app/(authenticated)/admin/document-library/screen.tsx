"use client";

import { AdminDocumentLibraryPage } from "@/components/pages/AdminDocumentLibraryPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import type { DocumentTagRef } from "@/shared/document-contracts";
import { useAuthenticatedShell } from "../../layout";

export function DocumentLibraryScreen({
  initialScope,
}: {
  initialScope: DocumentTagRef | null;
}) {
  const {
    session,
    canUseDocumentLibrary,
    setNotice,
    noGlobalRefresh,
  } = useAuthenticatedShell();
  if (!canUseDocumentLibrary) return <AdminAccessDenied />;
  return (
    <AdminDocumentLibraryPage
      session={session}
      initialScope={initialScope}
      onNotice={setNotice}
      onRefresh={noGlobalRefresh}
    />
  );
}
