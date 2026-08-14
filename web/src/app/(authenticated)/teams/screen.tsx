"use client";

import { KnowledgeScopePage } from "@/components/pages/KnowledgeScopePage";
import { useAuthenticatedShell } from "../layout";

export function TeamsScreen({ teamId = null }: { teamId?: string | null }) {
  const { navigate } = useAuthenticatedShell();
  return (
    <KnowledgeScopePage
      scopeType="team"
      scopeId={teamId}
      onNavigate={navigate}
    />
  );
}
