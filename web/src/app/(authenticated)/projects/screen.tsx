"use client";

import { KnowledgeScopePage } from "@/components/pages/KnowledgeScopePage";
import { useAuthenticatedShell } from "../layout";

export function ProjectsScreen({
  projectId = null,
}: {
  projectId?: string | null;
}) {
  const { navigate } = useAuthenticatedShell();
  return (
    <KnowledgeScopePage
      scopeType="project"
      scopeId={projectId}
      onNavigate={navigate}
    />
  );
}
