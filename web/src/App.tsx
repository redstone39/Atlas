import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import "./i18n";
import { AccountMenu } from "./app/AccountMenu";
import { ProductShell, SidebarHeader } from "./app/ProductShell";
import { Toaster } from "./components/ui/sonner";
import {
  identitySessionApi,
  type SessionState,
} from "./features/identity-session/index";
import { AdminResourceUnavailable } from "./shared/admin-detail";
import { AdminAccessDenied, LoadErrorState, LoadingShell } from "./shared/product-ui";
import { isAbortError, sessionQueryClient } from "./shared/session-query-client";
import {
  managementGroupsForCapabilities,
} from "./shared/navigation";
import {
  matchAppRoute,
  normalizeRoute,
  workspaceConversationRoute,
  type AppDestination,
  type AppRoute,
} from "./shared/routes";
import { AtlasThemeProvider } from "./shared/theme";
import { AdminDocumentLibraryPage } from "./pages/AdminDocumentLibraryPage";
import { AdminDirectoryPage } from "./pages/AdminDirectoryPage";
import { AdminModelsPage } from "./pages/AdminModelsPage";
import { AdminPluginsPage } from "./pages/AdminPluginsPage";
import { AdminProjectsPage } from "./pages/AdminProjectsPage";
import { AdminTeamsPage } from "./pages/AdminTeamsPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AcceptInvitePage } from "./pages/AcceptInvitePage";
import { AgentAccessPage } from "./pages/AgentAccessPage";
import { AuditPage } from "./pages/AuditPage";
import { LoginPage } from "./pages/LoginPage";
import { KnowledgeLibraryPage } from "./pages/KnowledgeLibraryPage";
import { OpsPage } from "./pages/OpsPage";
import { ScopedTeamAdminPage } from "./pages/ScopedTeamAdminPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkspacePage } from "./pages/WorkspacePage";

export default function App() {
  return (
    <AtlasThemeProvider>
      <AtlasApplication />
    </AtlasThemeProvider>
  );
}

function AtlasApplication() {
  const [route, setRoute] = useState<AppRoute | null>(() =>
    normalizeRoute(window.location.pathname),
  );
  const [session, setSession] = useState<SessionState | null>(null);
  const [authUnavailable, setAuthUnavailable] = useState(false);
  const [adminProjectionUnavailable, setAdminProjectionUnavailable] = useState(false);

  async function refreshSession(signal?: AbortSignal) {
    const nextSession = await sessionQueryClient.query({
      key: ["identity", "session"],
      signal,
      queryFn: identitySessionApi.session,
    });
    setSession(nextSession);
    setAuthUnavailable(false);
    setAdminProjectionUnavailable(false);
    return nextSession;
  }

  async function refreshAdminProjection() {
    const originDirectory = adminDetailDirectory(window.location.pathname);
    let nextSession: SessionState;
    try {
      nextSession = await refreshSession();
    } catch (error) {
      if (originDirectory) {
        const currentDetailDirectory = adminDetailDirectory(window.location.pathname);
        if (currentDetailDirectory === originDirectory) {
          window.history.replaceState({}, "", originDirectory);
          setRoute(originDirectory);
        }
        setAdminProjectionUnavailable(true);
      }
      throw error;
    }
    const currentRoute = normalizeRoute(window.location.pathname);
    if (!currentRoute) return;
    const currentMatch = matchAppRoute(currentRoute);
    const nextIsAdmin = nextSession.system_role === "admin";
    let fallback: AppRoute | null = null;

    if (
      currentMatch.kind === "admin-project-detail" &&
      !nextIsAdmin &&
      !nextSession.available_projects.some(
        (project) =>
          project.project_id === currentMatch.projectId && project.role === "admin",
      )
    ) {
      fallback = "/admin/projects";
    } else if (
      currentMatch.kind === "admin-team-detail" &&
      !nextIsAdmin &&
      nextSession.team_roles[currentMatch.teamId] !== "admin"
    ) {
      fallback = "/admin/teams";
    }

    if (fallback) {
      window.history.replaceState({}, "", fallback);
      setRoute(fallback);
    }
  }

  function settleUnavailableSession() {
    setAuthUnavailable(true);
    setSession({
      authenticated: false,
      actor: null,
      available_projects: [],
      system_role: null,
      team_roles: {},
    });
  }

  useEffect(() => sessionQueryClient.onSessionInvalidated(() => {
    setSession(null);
    void refreshSession().catch((error) => {
      if (!isAbortError(error)) settleUnavailableSession();
    });
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    refreshSession(controller.signal).catch((error) => {
      if (!isAbortError(error)) {
        settleUnavailableSession();
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const onPop = () => setRoute(normalizeRoute(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    if (session && !session.authenticated && route !== "/login" && route !== "/accept-invite") {
      navigate("/login");
    }
  }, [session, route]);

  function setNotice(_message: string) {
    return undefined;
  }

  async function noGlobalRefresh() {
    return undefined;
  }

  function navigate(next: AppDestination) {
    const destination = new URL(next, window.location.origin);
    const nextRoute = normalizeRoute(destination.pathname);
    if (!nextRoute) return;
    if (
      route === nextRoute &&
      window.location.pathname + window.location.search === next
    ) {
      return;
    }
    window.history.pushState({}, "", next);
    setRoute(nextRoute);
  }

  function replace(next: AppRoute) {
    if (route === next && window.location.pathname === next) return;
    window.history.replaceState({}, "", next);
    setRoute(next);
  }

  async function handleLogout() {
    await identitySessionApi.logout();
    sessionQueryClient.resetSession();
    setSession({
      authenticated: false,
      actor: null,
      available_projects: [],
      system_role: null,
      team_roles: {},
    });
    navigate("/login");
  }

  if (!session) {
    return (
      <>
        <LoadingShell />
        <Toaster richColors position="top-right" />
      </>
    );
  }

  if (route === "/accept-invite") {
    return (
      <>
        <AcceptInvitePage onDone={() => navigate("/login")} />
        <Toaster richColors position="top-right" />
      </>
    );
  }

  if (!session.authenticated || route === "/login") {
    return (
      <>
        <LoginPage
          session={session}
          authUnavailable={authUnavailable}
          onLogin={(nextSession) => {
            sessionQueryClient.beginSession(nextSession.actor?.actor_id ?? "anonymous");
            setSession(nextSession);
            setAuthUnavailable(false);
            setNotice("");
            navigate("/workspace");
          }}
        />
        <Toaster richColors position="top-right" />
      </>
    );
  }

  const isAdmin = session.system_role === "admin";
  const canUseOps = isAdmin || session.system_role === "operator";
  const canUseDocumentLibrary =
    isAdmin ||
    Object.values(session.team_roles).some((role) => role === "uploader" || role === "admin") ||
    session.available_projects.some((project) =>
      ["contributor", "admin"].includes(project.role ?? ""),
    );
  const canManageProjects =
    isAdmin ||
    session.available_projects.some((project) =>
      project.role === "admin",
    );
  const canManageTeams =
    isAdmin || Object.values(session.team_roles).some((role) => role === "admin");
  const managementGroups = managementGroupsForCapabilities(session);
  if (adminProjectionUnavailable) {
    return (
      <>
        <ProductShell
          route={route ?? unavailableRouteFamily(window.location.pathname)}
          session={session}
          managementGroups={managementGroups}
          onNavigate={navigate}
          onLogout={handleLogout}
        >
          <AdminProjectionUnavailable
            onRetry={() => {
              void refreshSession().catch(() => undefined);
            }}
          />
        </ProductShell>
        <Toaster richColors position="top-right" />
      </>
    );
  }
  const routeMatch = route ? matchAppRoute(route) : null;
  const projectDetail =
    routeMatch?.kind === "admin-project-detail" ? routeMatch : null;
  const teamDetail = routeMatch?.kind === "admin-team-detail" ? routeMatch : null;
  const userDetail = routeMatch?.kind === "admin-user-detail" ? routeMatch : null;
  const auditRoute =
    routeMatch?.kind === "admin-audit-section" ||
    routeMatch?.kind === "admin-audit-conversation"
      ? routeMatch
      : null;
  const workspaceConversation =
    routeMatch?.kind === "workspace-conversation" ? routeMatch : null;
  const canOpenProjectDetail =
    !projectDetail ||
    isAdmin ||
    session.available_projects.some(
      (project) => project.project_id === projectDetail.projectId && project.role === "admin",
    );
  const canOpenTeamDetail =
    !teamDetail ||
    isAdmin ||
    session.team_roles[teamDetail.teamId] === "admin";

  return (
    <>
      <ProductShell
        route={route ?? unavailableRouteFamily(window.location.pathname)}
        session={session}
        managementGroups={managementGroups}
        onNavigate={navigate}
        onLogout={handleLogout}
      >
        {(route === "/workspace" || route === "/library" || workspaceConversation) && (
          <WorkspacePage
            activeView={route === "/library" ? "/library" : "/workspace"}
            conversationId={workspaceConversation?.conversationId ?? null}
            session={session}
            onNotice={setNotice}
            onNavigate={navigate}
            onReplace={replace}
            libraryContent={route === "/library" ? <KnowledgeLibraryPage /> : null}
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
                onLogout={handleLogout}
                {...options}
              />
            )}
          />
        )}
        {route === "/settings" && (
          <SettingsPage
            session={session}
            managementGroups={managementGroups}
            onNavigate={navigate}
          />
        )}
        {(route === "/admin/projects" || projectDetail) && (
          canManageProjects && canOpenProjectDetail ? (
            <AdminProjectsPage
              session={session}
              canManageProjectProfile={isAdmin}
              detail={projectDetail}
              onNavigate={navigate}
              onNotice={setNotice}
              onRefresh={refreshAdminProjection}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/document-library" && (
          canUseDocumentLibrary ? (
            <AdminDocumentLibraryPage
              session={session}
              onNotice={setNotice}
              onRefresh={noGlobalRefresh}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/directory" && (
          isAdmin ? (
            <AdminDirectoryPage
              onNotice={setNotice}
              onRefresh={noGlobalRefresh}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/models" && (
          isAdmin ? (
            <AdminModelsPage
              onNotice={setNotice}
              onRefresh={noGlobalRefresh}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/plugins" && (
          isAdmin ? <AdminPluginsPage /> : <AdminAccessDenied />
        )}
        {(route === "/admin/users" || userDetail) && (
          isAdmin ? (
            <AdminUsersPage
              currentActorId={session.actor?.actor_id ?? null}
              detail={userDetail}
              onNavigate={navigate}
              onNotice={setNotice}
              onRefresh={refreshAdminProjection}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {(route === "/admin/teams" || teamDetail) && (
          canManageTeams && canOpenTeamDetail ? (
            isAdmin ? (
              <AdminTeamsPage
                detail={teamDetail}
                onNavigate={navigate}
                onNotice={setNotice}
                onRefresh={refreshAdminProjection}
              />
            ) : (
              teamDetail?.section === "profile" ? (
                <AdminAccessDenied />
              ) : (
                <ScopedTeamAdminPage
                  detail={teamDetail}
                  onNavigate={navigate}
                  onNotice={setNotice}
                  onRefresh={refreshAdminProjection}
                />
              )
            )
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/agents" && (
          isAdmin ? (
            <AgentAccessPage
              projects={session.available_projects}
              onNotice={setNotice}
              onRefresh={noGlobalRefresh}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {(route === "/admin/audit" || auditRoute) && (
          isAdmin ? (
            <AuditPage
              route={route!}
              onNavigate={navigate}
            />
          ) : (
            <AdminAccessDenied />
          )
        )}
        {route === "/admin/ops" && (
          canUseOps ? (
            <OpsPage
              canManageSetup={isAdmin}
              onNavigate={navigate}
            />
          ) : (
            <AdminAccessDenied operatorAllowed />
          )
        )}
        {route === null && (
          <AdminResourceUnavailable
            onBack={() => {
              const pathname = window.location.pathname;
              if (pathname.startsWith("/admin/users/")) navigate("/admin/users");
              else if (pathname.startsWith("/admin/teams/")) navigate("/admin/teams");
              else if (pathname.startsWith("/admin/projects/")) navigate("/admin/projects");
              else if (pathname.startsWith("/admin/audit/")) navigate("/admin/audit");
              else navigate("/settings");
            }}
          />
        )}
      </ProductShell>
      <Toaster richColors position="top-right" />
    </>
  );
}

function unavailableRouteFamily(pathname: string): AppRoute {
  if (pathname.startsWith("/admin/users/")) return "/admin/users";
  if (pathname.startsWith("/admin/teams/")) return "/admin/teams";
  if (pathname.startsWith("/admin/projects/")) return "/admin/projects";
  if (pathname.startsWith("/admin/audit/")) return "/admin/audit";
  return "/settings";
}

function adminDetailDirectory(pathname: string): AppRoute | null {
  const route = normalizeRoute(pathname);
  if (!route) return null;
  const match = matchAppRoute(route);
  if (match.kind === "admin-user-detail") return "/admin/users";
  if (match.kind === "admin-team-detail") return "/admin/teams";
  if (match.kind === "admin-project-detail") return "/admin/projects";
  return null;
}

function AdminProjectionUnavailable({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <LoadErrorState
      title={t("admin.listLoadFailed")}
      description={t("admin.resourceUnavailableDescription")}
      retryLabel={t("admin.retry")}
      onRetry={onRetry}
    />
  );
}
