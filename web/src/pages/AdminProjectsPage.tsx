import type { SessionState } from "../features/identity-session/index";
import { ProjectGovernanceFeature } from "../features/project-governance/index";
import { userAdministrationApi } from "../features/user-administration/index";
import type { AppDestination, AppRouteMatch } from "../shared/routes";


export function AdminProjectsPage({
  session,
  canManageProjectProfile,
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  session: SessionState;
  canManageProjectProfile: boolean;
  detail: Extract<AppRouteMatch, { kind: "admin-project-detail" }> | null;
  onNavigate: (route: AppDestination) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return (
    <ProjectGovernanceFeature
      session={session}
      canManageProjectProfile={canManageProjectProfile}
      detail={detail}
      onNavigate={onNavigate}
      onNotice={onNotice}
      onRefresh={onRefresh}
      createInvite={userAdministrationApi.createInvite}
    />
  );
}
