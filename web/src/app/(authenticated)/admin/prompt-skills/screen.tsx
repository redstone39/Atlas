"use client";

import { AdminPromptSkillsPage } from "@/components/pages/AdminPromptSkillsPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { useAuthenticatedShell } from "../../layout";

export function PromptSkillsScreen() {
  const { isAdmin } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  return <AdminPromptSkillsPage />;
}
