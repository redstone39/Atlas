import type { MessageReference } from "../../shared/user-messages";
import type { SessionState } from "../identity-session/index";
import type { AppDestination, AppRouteMatch } from "../../shared/routes";
import type {
  ScopedDirectoryConnectionListResult,
  ScopedDirectoryMemberImportResult,
  ScopedDirectoryUserSearchResult,
} from "../directory-administration";

export type ProjectRole = "viewer" | "contributor" | "admin";
export type ProjectMemberRole = ProjectRole;
export type ProjectMemberEffect = "allow" | "deny";
export type ProjectMemberSubjectType = "user" | "team" | "service_account";

export interface ProjectAdminSummary {
  project_id: string;
  name: string;
  policy_profile_id: string;
  status: "active" | "retired";
}

export interface ProjectAdminListResult {
  projects: ProjectAdminSummary[];
}

export interface ProjectAccessGrant {
  grant_id: string;
  project_id: string;
  subject_type: ProjectMemberSubjectType;
  subject_id: string;
  effect: ProjectMemberEffect;
  role: ProjectMemberRole;
  status: "active" | "revoked";
  created_at: string;
  revoked_at?: string | null;
}

export interface ProjectMemberCandidate {
  subject_type: ProjectMemberSubjectType;
  subject_id: string;
  display_name: string;
  display_detail: string | null;
}

export interface ProjectAccessGrantListResult {
  grants: ProjectAccessGrant[];
  subjects: ProjectMemberCandidate[];
}

export interface ProjectMemberCandidatesResult {
  users: ProjectMemberCandidate[];
  teams: ProjectMemberCandidate[];
  service_accounts: ProjectMemberCandidate[];
}

export type ProjectDirectoryConnectionListResult = ScopedDirectoryConnectionListResult;
export type ProjectDirectoryUserSearchResult = ScopedDirectoryUserSearchResult;
export type ProjectDirectoryMemberImportResult = ScopedDirectoryMemberImportResult;

export interface ProjectInviteResult extends MessageReference {
  local_pilot_acceptance: { acceptance_url: string } | null;
}

export interface ProjectGovernanceFeatureProps {
  session: SessionState;
  canManageProjectProfile: boolean;
  detail: Extract<AppRouteMatch, { kind: "admin-project-detail" }> | null;
  onNavigate: (route: AppDestination) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<boolean>;
  createInvite: (
    name: string,
    email: string,
    scope: {
      scopeType: "project";
      scopeId: string;
      scopeRole: ProjectMemberRole;
    },
  ) => Promise<ProjectInviteResult>;
}
