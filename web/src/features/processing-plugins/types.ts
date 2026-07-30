export type PluginVersionRef = {
  plugin_id: string;
  plugin_version: string;
  package_digest: string;
  runtime_profile: string;
};

export type ProcessingPluginVersion = PluginVersionRef & {
  plugin_kind: "base_parser" | "region_processor";
  status: "uploaded" | "validating" | "quarantined" | "verified" | "disabled" | "rejected";
  trust_provenance: string;
  revision: number;
  diagnostic_code: string | null;
  canary_passed_at: string | null;
  active: boolean;
  descriptor?: {
    signature_key_id?: string | null;
    license_expression?: string;
    sdk_api_version?: number;
    sbom_present?: boolean;
    sbom_spdx_version?: string;
    checksums_verified?: boolean;
  };
};

export type ProcessingProfileRevision = {
  profile_id: string;
  revision: number;
  status: "draft" | "canary" | "active" | "deprecated";
  accepted_media_types: string[];
  base_parser_plugin_ref: PluginVersionRef;
  mandatory_processor_plugin_refs: PluginVersionRef[];
  eligible_processor_plugin_refs: PluginVersionRef[];
  plugin_priority: PluginVersionRef[];
  planner_enabled: boolean;
  planner_model_route_id: string | null;
  max_regions_per_plan: number;
  max_modules_per_region: number;
  max_total_plugin_invocations: number;
};

export type ProcessingProfile = {
  profile_id: string;
  display_name: string;
  revisions: ProcessingProfileRevision[];
};

export type ProcessingRun = {
  run_id: string;
  document_id: string;
  document_version_id: string;
  profile_id: string;
  profile_revision: number;
  status: string;
  attempt: number;
  warning_codes: string[];
  failure_code: string | null;
  updated_at: string;
};

export type ProcessingRunDetail = ProcessingRun & {
  invocations: Array<{ invocation_id: string; status: string; plugin_ref: PluginVersionRef; payload: Record<string, unknown> }>;
  routing_decisions: Array<{ routing_decision_id: string; payload: Record<string, unknown> }>;
  trace: { trace_id: string; payload: { warnings?: string[]; failed_channels?: string[] } } | null;
};
