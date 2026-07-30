import type { TeamScopeRole } from "../../shared/identity-access-contracts";
import type { MessageReference } from "../../shared/user-messages";

export interface UserInviteSummary {
  invite_id: string;
  actor_id: string;
  email: string;
  display_name: string;
  system_role: "user" | "admin" | "operator";
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

export interface UserAdminSummary {
  actor_id: string;
  actor_type: "user" | "service_account";
  display_name: string;
  email: string | null;
  system_role: string;
  active: boolean;
  created_at: string;
  invite_status: "pending" | "accepted" | "revoked" | "expired" | null;
  invite_id: string | null;
}

export interface UserAdminListResult {
  users: UserAdminSummary[];
}
