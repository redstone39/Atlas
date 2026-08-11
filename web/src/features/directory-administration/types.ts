import type { MessageReference } from "../../shared/user-messages";

export type DirectoryProviderType = "active_directory" | "ldap";
export type DirectoryTlsMode = "ldaps" | "start_tls";

export interface DirectoryConnectionConfig {
  connection_id: string;
  display_name: string;
  priority: number;
  provider_type: DirectoryProviderType;
  host: string;
  port: number;
  tls_mode: DirectoryTlsMode;
  connect_timeout_seconds: number;
  operation_timeout_seconds: number;
  bind_dn: string;
  user_base_dn: string;
  user_object_filter: string;
  login_attribute: string;
  stable_id_attribute: string;
  display_name_attribute: string;
  email_attribute: string;
  groups_attribute: string;
  department_attribute: string;
  title_attribute: string;
  employee_id_attribute: string;
  enabled: boolean;
}

export interface DirectoryConnectionStatus extends DirectoryConnectionConfig {
  bind_password_configured: boolean;
  custom_ca_configured: boolean;
  custom_ca_sha256: string | null;
}

export interface DirectoryConnectionListResult {
  connections: DirectoryConnectionStatus[];
}

export interface DirectoryConnectionCreateInput extends DirectoryConnectionConfig {
  bind_password: string;
  custom_ca_pem?: string;
}

export interface DirectoryConnectionUpdateInput {
  connectionId: string;
  config: Omit<DirectoryConnectionConfig, "connection_id">;
  bindPassword?: string;
  clearBindPassword?: boolean;
  customCaPem?: string;
  clearCustomCa?: boolean;
}

export interface DirectoryConnectionTestResult extends MessageReference {
  validation_status: "passed" | "failed";
}

export interface DirectoryUserCandidate {
  external_subject: string;
  username: string;
  display_name: string;
  email: string | null;
  groups: string[];
  department: string | null;
  title: string | null;
  employee_id: string | null;
  directory_enabled: boolean | null;
}

export interface DirectoryUserSearchResult {
  users: DirectoryUserCandidate[];
}

export interface DirectoryUserImportResult extends MessageReference {
  imported_actor_ids: string[];
  imported_count: number;
}
