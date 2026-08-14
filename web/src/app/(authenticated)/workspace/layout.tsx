"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { matchAppRoute, type AppRoute } from "@/shared/routes";
import { WorkspaceScreen } from "./screen";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const match = matchAppRoute(pathname as AppRoute);

  const conversationId =
    match.kind === "workspace-conversation" ? match.conversationId : null;
  const initialKnowledgeSurface =
    match.kind === "scope-notes" && match.workspace
      ? { scopeType: match.scopeType, scopeId: match.scopeId }
      : match.kind === "workspace-projects"
        ? { scopeType: "project" as const, scopeId: match.projectId }
        : match.kind === "workspace-teams"
          ? { scopeType: "team" as const, scopeId: match.teamId }
          : null;
  return (
    <WorkspaceScreen
      conversationId={conversationId}
      initialKnowledgeSurface={initialKnowledgeSurface}
    >
      {match.kind === "scope-notes" && match.workspace ? children : null}
    </WorkspaceScreen>
  );
}
