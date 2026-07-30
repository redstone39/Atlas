import { UserAdministrationFeature } from "../features/user-administration/index";
import type { AppRoute, AppRouteMatch } from "../shared/routes";

export function AdminUsersPage({
  currentActorId,
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  currentActorId: string | null;
  detail: Extract<AppRouteMatch, { kind: "admin-user-detail" }> | null;
  onNavigate: (route: AppRoute) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return (
    <UserAdministrationFeature
      currentActorId={currentActorId}
      detail={detail}
      onNavigate={onNavigate}
      onNotice={onNotice}
      onRefresh={onRefresh}
    />
  );
}
