import type { ProjectSummary } from "../identity-session/index";
import type { MessageReference } from "../../shared/user-messages";

export interface AgentTokenStatus {
  token_id: string;
  token_fingerprint: string;
  status: "active" | "revoked";
  created_at: string;
  revoked_at: string | null;
}

export interface AgentProjectGrantStatus {
  grant_id: string;
  project_id: string;
  role: "viewer" | "contributor" | "admin";
  effect: "allow" | "deny";
  status: "active";
}

export interface AgentUserStatus {
  actor_id: string;
  actor_type: "service_account";
  display_name: string;
  status: "active" | "inactive";
  tokens: AgentTokenStatus[];
  project_grants: AgentProjectGrantStatus[];
}

export interface AgentUserListResult {
  agents: AgentUserStatus[];
}

export interface AgentUserCreateResult extends MessageReference {
  request_id: string;
  status: "applied" | "rejected" | "access_denied";
  agent: AgentUserStatus;
  audit_event_ref: string;
}

export interface AgentTokenIssueResult extends MessageReference {
  request_id: string;
  status: "applied" | "rejected" | "access_denied";
  raw_token: string;
  token: AgentTokenStatus;
  audit_event_ref: string;
}

export interface AgentAccessFeatureProps {
  projects: ProjectSummary[];
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}
