"use client";

import { AdminTeamsPage } from "@/components/pages/AdminTeamsPage";
import { ScopedTeamAdminPage } from "@/components/pages/ScopedTeamAdminPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { matchAppRoute, type AppRoute } from "@/shared/routes";
import { useAuthenticatedShell } from "../../layout";

export function TeamsScreen({ route = null }: { route?: AppRoute | null }) {
  const {
    session,
    isAdmin,
    canManageTeams,
    navigate,
    setNotice,
    refreshAdminProjection,
  } = useAuthenticatedShell();
  const match = route ? matchAppRoute(route) : null;
  const detail = match?.kind === "admin-team-detail" ? match : null;
  const canOpenDetail =
    !detail || isAdmin || session.team_roles[detail.teamId] === "admin";
  if (!canManageTeams || !canOpenDetail) return <AdminAccessDenied />;
  if (isAdmin) {
    return (
      <AdminTeamsPage
        detail={detail}
        onNavigate={navigate}
        onNotice={setNotice}
        onRefresh={refreshAdminProjection}
      />
    );
  }
  if (detail?.section === "profile") return <AdminAccessDenied />;
  return (
    <ScopedTeamAdminPage
      detail={detail}
      onNavigate={navigate}
      onNotice={setNotice}
      onRefresh={refreshAdminProjection}
    />
  );
}
