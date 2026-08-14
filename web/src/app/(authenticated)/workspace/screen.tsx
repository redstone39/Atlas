"use client";

import type { ReactNode } from "react";

import { AccountMenu } from "@/components/shell/AccountMenu";
import { SidebarHeader } from "@/components/shell/ProductShell";
import { KnowledgeScopePage } from "@/components/pages/KnowledgeScopePage";
import { WorkspacePage } from "@/components/pages/WorkspacePage";
import { useAuthenticatedShell } from "../layout";

export function WorkspaceScreen({
  conversationId = null,
  initialKnowledgeSurface = null,
  children,
}: {
  conversationId?: string | null;
  initialKnowledgeSurface?: {
    scopeType: "project" | "team";
    scopeId: string | null;
  } | null;
  children?: ReactNode;
}) {
  const { session, navigate, replace, setNotice, logout } =
    useAuthenticatedShell();

  return (
    <WorkspacePage
      conversationId={conversationId}
      initialKnowledgeSurface={initialKnowledgeSurface}
      session={session}
      onNotice={setNotice}
      onNavigate={navigate}
      onReplace={replace}
      renderSidebarHeader={(options) => (
        <SidebarHeader
          onNavigate={navigate}
          onOpenWorkspace={options.onOpenWorkspace}
          onCollapseSidebar={options.onCollapseSidebar}
          presentation={options.presentation}
        />
      )}
      renderAccountMenu={(options) => (
        <AccountMenu
          session={session}
          onNavigate={navigate}
          onLogout={logout}
          {...options}
        />
      )}
      renderKnowledgeScope={(options) =>
        children ?? <KnowledgeScopePage {...options} workspace />
      }
    />
  );
}
