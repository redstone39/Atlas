import type { DocumentTagRef } from "@/shared/document-contracts";
import { DocumentLibraryScreen } from "./screen";

export default async function DocumentLibraryRoute({
  searchParams,
}: {
  searchParams: Promise<{
    scope_type?: string | string[];
    scope_id?: string | string[];
  }>;
}) {
  const { scope_type: scopeType, scope_id: scopeId } = await searchParams;
  const initialScope: DocumentTagRef | null =
    (scopeType === "team" || scopeType === "project") &&
    typeof scopeId === "string" &&
    scopeId.length > 0
      ? { tag_type: scopeType, tag_id: scopeId }
      : null;
  return <DocumentLibraryScreen initialScope={initialScope} />;
}
