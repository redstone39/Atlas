"use client";

import { AdminProjectsPage } from "@/components/pages/AdminProjectsPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { matchAppRoute, type AppRoute } from "@/shared/routes";
import { useAuthenticatedShell } from "../../layout";

export function ProjectsScreen({ route = null }: { route?: AppRoute | null }) {
  const {
    session,
    isAdmin,
    canManageProjects,
    navigate,
    setNotice,
    refreshAdminProjection,
  } = useAuthenticatedShell();
  const match = route ? matchAppRoute(route) : null;
  const detail = match?.kind === "admin-project-detail" ? match : null;
  const canOpenDetail =
    !detail ||
    isAdmin ||
    session.available_projects.some(
      (project) =>
        project.project_id === detail.projectId && project.role === "admin",
    );
  if (!canManageProjects || !canOpenDetail) return <AdminAccessDenied />;
  return (
    <AdminProjectsPage
      session={session}
      canManageProjectProfile={isAdmin}
      detail={detail}
      onNavigate={navigate}
      onNotice={setNotice}
      onRefresh={refreshAdminProjection}
    />
  );
}
