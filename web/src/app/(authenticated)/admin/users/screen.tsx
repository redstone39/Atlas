"use client";

import { AdminUsersPage } from "@/components/pages/AdminUsersPage";
import { AdminAccessDenied } from "@/shared/product-ui";
import { matchAppRoute, type AppRoute } from "@/shared/routes";
import { useAuthenticatedShell } from "../../layout";

export function UsersScreen({ route = null }: { route?: AppRoute | null }) {
  const {
    session,
    isAdmin,
    navigate,
    setNotice,
    refreshAdminProjection,
  } = useAuthenticatedShell();
  if (!isAdmin) return <AdminAccessDenied />;
  const match = route ? matchAppRoute(route) : null;
  const detail = match?.kind === "admin-user-detail" ? match : null;
  return (
    <AdminUsersPage
      currentActorId={session.actor?.actor_id ?? null}
      detail={detail}
      onNavigate={navigate}
      onNotice={setNotice}
      onRefresh={async () => {
        await refreshAdminProjection();
      }}
    />
  );
}
