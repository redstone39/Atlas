import { ScopedTeamAdministrationFeature } from "../features/team-administration/index";
import type { AppRoute, AppRouteMatch } from "../shared/routes";

export function ScopedTeamAdminPage({
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  detail: Extract<AppRouteMatch, { kind: "admin-team-detail" }> | null;
  onNavigate: (route: AppRoute) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return (
    <ScopedTeamAdministrationFeature
      detail={detail}
      onNavigate={onNavigate}
      onNotice={onNotice}
      onRefresh={onRefresh}
    />
  );
}
