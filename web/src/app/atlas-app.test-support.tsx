import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";

import AuthenticatedLayout from "./(authenticated)/layout";
import UnavailableRoute from "./(authenticated)/[...unavailable]/page";
import { AgentsScreen } from "./(authenticated)/admin/agents/screen";
import { AuditScreen } from "./(authenticated)/admin/audit/screen";
import { AuditConversationsScreen } from "./(authenticated)/admin/audit/conversations/screen";
import { AuditRuntimeScreen } from "./(authenticated)/admin/audit/conversations/[conversationId]/runtime/[turnId]/screen";
import { AuditTranscriptScreen } from "./(authenticated)/admin/audit/conversations/[conversationId]/transcript/screen";
import { AuditEventsScreen } from "./(authenticated)/admin/audit/events/screen";
import { DirectoryScreen } from "./(authenticated)/admin/directory/screen";
import { DocumentLibraryScreen } from "./(authenticated)/admin/document-library/screen";
import { ModelsScreen } from "./(authenticated)/admin/models/screen";
import { OpsScreen } from "./(authenticated)/admin/ops/screen";
import { PluginsScreen } from "./(authenticated)/admin/plugins/screen";
import { ProjectsScreen } from "./(authenticated)/admin/projects/screen";
import { TeamsScreen } from "./(authenticated)/admin/teams/screen";
import { UsersScreen } from "./(authenticated)/admin/users/screen";
import { LibraryScreen } from "./(authenticated)/library/screen";
import { SettingsScreen } from "./(authenticated)/settings/screen";
import { WorkspaceScreen } from "./(authenticated)/workspace/screen";
import { AcceptInviteScreen } from "./(public)/accept-invite/screen";
import { LoginScreen } from "./(public)/login/screen";
import { Providers } from "./providers";
import type { DocumentTagRef } from "@/shared/document-contracts";
import { matchAppRoute, type AppRoute } from "@/shared/routes";

export default function AtlasTestApp() {
  const pathname = usePathname();
  if (pathname === "/") {
    return (
      <Providers>
        <RootRedirect />
      </Providers>
    );
  }
  if (pathname === "/login") {
    return (
      <Providers>
        <LoginScreen />
      </Providers>
    );
  }
  if (pathname === "/accept-invite") {
    const token = new URLSearchParams(window.location.search).get("token") ?? "";
    return (
      <Providers>
        <AcceptInviteScreen token={token} />
      </Providers>
    );
  }

  return (
    <Providers>
      <AuthenticatedLayout>{authenticatedScreen(pathname)}</AuthenticatedLayout>
    </Providers>
  );
}

function RootRedirect() {
  const router = useRouter();
  useEffect(() => router.replace("/workspace"), [router]);
  return null;
}

function authenticatedScreen(pathname: string): ReactNode {
  switch (pathname) {
    case "/workspace":
      return <WorkspaceScreen />;
    case "/library":
      return <LibraryScreen />;
    case "/settings":
      return <SettingsScreen />;
    case "/admin/document-library": {
      const params = new URLSearchParams(window.location.search);
      const scopeType = params.get("scope_type");
      const scopeId = params.get("scope_id");
      const initialScope: DocumentTagRef | null =
        (scopeType === "team" || scopeType === "project") && scopeId
          ? { tag_type: scopeType, tag_id: scopeId }
          : null;
      return <DocumentLibraryScreen initialScope={initialScope} />;
    }
    case "/admin/directory":
      return <DirectoryScreen />;
    case "/admin/users":
      return <UsersScreen />;
    case "/admin/teams":
      return <TeamsScreen />;
    case "/admin/projects":
      return <ProjectsScreen />;
    case "/admin/models":
      return <ModelsScreen />;
    case "/admin/plugins": {
      const params = new URLSearchParams(window.location.search);
      const requestedRunId = params.get("run") || null;
      return (
        <PluginsScreen
          initialTab={requestedRunId || params.get("tab") === "runs" ? "runs" : "plugins"}
          requestedRunId={requestedRunId}
        />
      );
    }
    case "/admin/agents":
      return <AgentsScreen />;
    case "/admin/audit":
      return <AuditScreen />;
    case "/admin/audit/conversations":
      return <AuditConversationsScreen route={pathname} />;
    case "/admin/audit/events":
      return <AuditEventsScreen route={pathname} />;
    case "/admin/ops":
      return <OpsScreen />;
  }

  const route = pathname as AppRoute;
  if (/^\/workspace\/conversations\/[^/]+$/.test(pathname)) {
    const match = matchAppRoute(route);
    return (
      <WorkspaceScreen
        conversationId={
          match.kind === "workspace-conversation" ? match.conversationId : null
        }
      />
    );
  }
  if (/^\/admin\/users\/[^/]+$/.test(pathname)) {
    return <UsersScreen route={route} />;
  }
  if (/^\/admin\/teams\/[^/]+\/(profile|members)$/.test(pathname)) {
    return <TeamsScreen route={route} />;
  }
  if (/^\/admin\/projects\/[^/]+\/(profile|access)$/.test(pathname)) {
    return <ProjectsScreen route={route} />;
  }
  if (/^\/admin\/audit\/conversations\/[^/]+\/transcript$/.test(pathname)) {
    return <AuditTranscriptScreen route={route} />;
  }
  if (/^\/admin\/audit\/conversations\/[^/]+\/runtime\/[^/]+$/.test(pathname)) {
    return <AuditRuntimeScreen route={route} />;
  }
  return <UnavailableRoute />;
}
