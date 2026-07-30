import type { TeamScopeRole } from "../../shared/identity-access-contracts";

export type { TeamScopeRole } from "../../shared/identity-access-contracts";

export interface ActorContext {
  actor_id: string;
  actor_type: "user" | "service_account" | "system_task";
  issuer: string;
  display_name: string;
  groups: string[];
  correlation_id: string;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  membership_status: "active" | "revoked" | "missing";
  role: "viewer" | "contributor" | "admin" | null;
}

export interface SessionState {
  authenticated: boolean;
  actor: ActorContext | null;
  available_projects: ProjectSummary[];
  system_role: "user" | "admin" | "operator" | null;
  team_roles: Record<string, TeamScopeRole>;
}
