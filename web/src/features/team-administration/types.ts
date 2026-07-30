import type { TeamScopeRole } from "../../shared/identity-access-contracts";

export interface TeamRecord {
  team_id: string;
  name: string;
  parent_team_id: string | null;
  status: "active" | "retired";
  created_at: string;
  inherit_parent_documents: boolean;
}

export interface TeamMembershipRecord {
  membership_id: string;
  team_id: string;
  member_actor_type: "user" | "service_account";
  member_actor_id: string;
  role: TeamScopeRole;
  status: "active" | "removed";
  created_at: string;
  removed_at: string | null;
}

export interface TeamListResult {
  teams: TeamRecord[];
  memberships: TeamMembershipRecord[];
}

export interface TeamMemberSummary {
  membership_id: string;
  team_id: string;
  subject_type: "user" | "service_account";
  subject_id: string;
  display_name: string;
  display_detail: string | null;
  role: TeamScopeRole;
  status: "active";
  created_at: string;
}

export interface TeamMemberListResult {
  members: TeamMemberSummary[];
}

export interface TeamMemberCandidate {
  subject_type: "user";
  subject_id: string;
  display_name: string;
  display_detail: string | null;
}

export interface TeamMemberCandidatesResult {
  users: TeamMemberCandidate[];
}
