import { TeamAdministrationFeature } from "../features/team-administration/index";
import type { AppRoute, AppRouteMatch } from "../shared/routes";

export function AdminTeamsPage({
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
    <TeamAdministrationFeature
      detail={detail}
      onNavigate={onNavigate}
      onNotice={onNotice}
      onRefresh={onRefresh}
    />
  );
}
