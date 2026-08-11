"use client";

import { AccountMenu } from "@/components/shell/AccountMenu";
import { SidebarHeader } from "@/components/shell/ProductShell";
import { WorkspacePage } from "@/components/pages/WorkspacePage";
import type { ReactNode } from "react";
import { useAuthenticatedShell } from "../layout";

export function WorkspaceScreen({
  activeView = "/workspace",
  conversationId = null,
  libraryContent = null,
}: {
  activeView?: "/workspace" | "/library";
  conversationId?: string | null;
  libraryContent?: ReactNode;
}) {
  const { session, navigate, replace, setNotice, logout } =
    useAuthenticatedShell();

  return (
    <WorkspacePage
      activeView={activeView}
      conversationId={conversationId}
      session={session}
      onNotice={setNotice}
      onNavigate={navigate}
      onReplace={replace}
      libraryContent={libraryContent}
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
    />
  );
}
