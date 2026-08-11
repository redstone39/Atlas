"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";

import { ProductShell } from "@/components/shell/ProductShell";
import type { SessionState } from "@/features/identity-session/index";
import { managementGroupsForCapabilities } from "@/shared/navigation";
import type { ManagementNavGroup } from "@/shared/navigation";
import { LoadErrorState, LoadingShell } from "@/shared/product-ui";
import {
  matchAppRoute,
  type AppDestination,
  type AppRoute,
} from "@/shared/routes";
import { useAtlasSession } from "../session-provider";

export type AuthenticatedShellContextValue = {
  session: SessionState;
  isAdmin: boolean;
  canUseOps: boolean;
  canUseDocumentLibrary: boolean;
  canManageProjects: boolean;
  canManageTeams: boolean;
  managementGroups: ManagementNavGroup[];
  navigate(next: AppDestination): void;
  replace(next: AppRoute): void;
  refreshAdminProjection(): Promise<boolean>;
  noGlobalRefresh(): Promise<void>;
  setNotice(message: string): void;
  logout(): Promise<void>;
};

const AuthenticatedShellContext =
  createContext<AuthenticatedShellContextValue | null>(null);

export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { session, refreshSession, logout } = useAtlasSession();
  const [adminProjectionUnavailable, setAdminProjectionUnavailable] = useState(false);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  useEffect(() => {
    if (session && !session.authenticated) router.replace("/login");
  }, [router, session]);

  if (!session || !session.authenticated) return <LoadingShell />;

  const isAdmin = session.system_role === "admin";
  const canUseOps = isAdmin || session.system_role === "operator";
  const canUseDocumentLibrary =
    isAdmin ||
    Object.values(session.team_roles).some(
      (role) => role === "uploader" || role === "admin",
    ) ||
    session.available_projects.some((project) =>
      ["contributor", "admin"].includes(project.role ?? ""),
    );
  const canManageProjects =
    isAdmin ||
    session.available_projects.some((project) => project.role === "admin");
  const canManageTeams =
    isAdmin || Object.values(session.team_roles).some((role) => role === "admin");
  const managementGroups = managementGroupsForCapabilities(session);

  function navigate(next: AppDestination) {
    router.push(next);
  }

  function replace(next: AppRoute) {
    router.replace(next);
  }

  async function refreshAdminProjection() {
    const originDirectory = adminDetailDirectory(pathnameRef.current);
    let nextSession: SessionState;
    try {
      nextSession = await refreshSession();
      setAdminProjectionUnavailable(false);
    } catch (error) {
      if (originDirectory) {
        if (adminDetailDirectory(pathnameRef.current) === originDirectory) {
          router.replace(originDirectory);
        }
        setAdminProjectionUnavailable(true);
      }
      throw error;
    }

    const currentRoute = pathnameRef.current as AppRoute;
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
      pathnameRef.current = fallback;
      router.replace(fallback);
      return false;
    }
    return true;
  }

  async function noGlobalRefresh() {
    return undefined;
  }

  function setNotice(_message: string) {
    return undefined;
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const context: AuthenticatedShellContextValue = {
    session,
    isAdmin,
    canUseOps,
    canUseDocumentLibrary,
    canManageProjects,
    canManageTeams,
    managementGroups,
    navigate,
    replace,
    refreshAdminProjection,
    noGlobalRefresh,
    setNotice,
    logout: handleLogout,
  };

  return (
    <AuthenticatedShellContext.Provider value={context}>
      <ProductShell
        route={pathname as AppRoute}
        session={session}
        managementGroups={managementGroups}
        onNavigate={navigate}
        onLogout={handleLogout}
      >
        {adminProjectionUnavailable ? (
          <AdminProjectionUnavailable
            onRetry={() => void refreshAdminProjection().catch(() => undefined)}
          />
        ) : (
          children
        )}
      </ProductShell>
    </AuthenticatedShellContext.Provider>
  );
}

export function useAuthenticatedShell(): AuthenticatedShellContextValue {
  const context = useContext(AuthenticatedShellContext);
  if (!context) {
    throw new Error("useAuthenticatedShell must be used within AuthenticatedLayout");
  }
  return context;
}

function adminDetailDirectory(pathname: string): AppRoute | null {
  if (pathname.startsWith("/admin/users/")) return "/admin/users";
  if (pathname.startsWith("/admin/teams/")) return "/admin/teams";
  if (pathname.startsWith("/admin/projects/")) return "/admin/projects";
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
