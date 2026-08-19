import type { TeamScopeRole } from "../../shared/identity-access-contracts";
import type { MessageReference } from "../../shared/user-messages";

export type SystemRole = "user" | "admin" | "operator";
export type EditableSystemRole = Extract<SystemRole, "user" | "admin">;

export interface UserInviteSummary {
  invite_id: string;
  actor_id: string;
  email: string;
  display_name: string;
  system_role: SystemRole;
  status: "pending" | "accepted" | "revoked" | "expired";
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  scope_type: "team" | "project" | null;
  scope_id: string | null;
  scope_role: TeamScopeRole | null;
}

export interface LocalPilotInviteAcceptance {
  mode: "copy_link";
  acceptance_token: string;
  acceptance_url: string;
}

export interface UserInviteCreateResult extends MessageReference {
  request_id: string;
  status: "applied" | "rejected" | "access_denied";
  invite: UserInviteSummary;
  audit_event_ref: string;
  local_pilot_acceptance: LocalPilotInviteAcceptance | null;
}

export interface UserInviteListResult {
  invites: UserInviteSummary[];
}

export interface DirectoryProfileSummary {
  connection_id: string;
  connection_display_name: string;
  username: string;
  email: string | null;
  groups: string[];
  department: string | null;
  title: string | null;
  employee_id: string | null;
  status: "current" | "stale" | "missing" | "disabled";
  last_refreshed_at: string;
}

export interface UserAdminFilters {
  q?: string;
  account_source?: "local" | "directory";
  directory_connection_id?: string;
  active?: boolean;
  directory_profile_status?: DirectoryProfileSummary["status"];
  directory_group?: string;
  department?: string;
  title?: string;
  employee_id?: string;
}

export interface UserAdminSummary {
  actor_id: string;
  actor_type: "user" | "service_account";
  display_name: string;
  email: string | null;
  system_role: SystemRole;
  active: boolean;
  created_at: string;
  invite_status: "pending" | "accepted" | "revoked" | "expired" | null;
  invite_id: string | null;
  account_source: "local" | "directory";
  directory_profile: DirectoryProfileSummary | null;
}

export interface UserAdminListResult {
  users: UserAdminSummary[];
}
