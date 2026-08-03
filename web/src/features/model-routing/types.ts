import type { MessageReference } from "../../shared/user-messages";

export type ProviderType = "openai_compatible" | "azure_openai";

export interface ModelRouteRuntimePolicyInput {
  schema_version: "model-route-runtime-policy-v7";
  tokenizer_profile: string;
  max_tool_executions: number;
  max_provider_invocations: number;
  max_reasoning_revision_cycles: number;
  max_catalog_pages: number;
  max_search_rounds: number;
  max_unique_evidence: number;
  max_retrieval_repairs: number;
  max_schema_retries_per_turn: number;
  max_selected_anchor_pages_per_round: number;
  provider_invocation_timeout_seconds: number;
  tool_execution_timeout_seconds: number;
  turn_timeout_seconds: number;
  context_window_tokens: number;
  max_input_tokens_per_invocation: number;
  max_output_tokens_per_invocation: number;
  max_tool_result_tokens_per_execution: number;
  max_total_tokens_per_conversation: number;
}

export interface ModelRouteRuntimePolicy extends ModelRouteRuntimePolicyInput {
  revision: number;
}

export interface ProviderConnectionStatus extends MessageReference {
  connection_id: string;
  display_name: string;
  provider_type: ProviderType;
  endpoint_url: string;
  credential_configured: boolean;
  status:
    | "credential_required"
    | "configured"
    | "verified"
    | "verification_failed"
    | "disabled";
  enabled: boolean;
  linked_model_count: number;
  revision: number;
  last_verified_at: string | null;
  last_rotated_at: string | null;
  audit_event_ref: string;
}

export interface ProviderConnectionListResult {
  connections: ProviderConnectionStatus[];
}

export interface ProviderConnectionTestResult extends MessageReference {
  connection: ProviderConnectionStatus;
  validation_status: "passed" | "failed";
  tested_route_ids: string[];
  audit_event_ref: string;
}

export interface AvailableModelsResult extends MessageReference {
  connection_id: string;
  discovery_status: "available" | "unavailable";
  models: string[];
}

export interface ProviderConnectionCreateInput {
  connectionId: string;
  displayName: string;
  providerType: ProviderType;
  endpointUrl: string;
  apiKey: string;
}

export interface ProviderConnectionUpdateInput {
  connectionId: string;
  displayName?: string;
  endpointUrl?: string;
  apiKey?: string;
  enabled?: boolean;
  expectedRevision: number;
}

export interface ModelRouteStatus extends MessageReference {
  route_id: string;
  display_name: string;
  provider_type: ProviderType;
  model_name: string;
  connection_id: string;
  status: "configured" | "test_passed" | "test_failed" | "disabled";
  enabled: boolean;
  supports_vision: boolean;
  revision: number;
  runtime_policy: ModelRouteRuntimePolicy;
  audit_event_ref: string;
  is_default: boolean;
}

export interface ModelRouteListResult {
  routes: ModelRouteStatus[];
  default_route_id: string | null;
}

export interface ModelRouteConfigInput {
  routeId: string;
  displayName: string;
  modelName: string;
  connectionId: string;
  enabled: boolean;
  supportsVision: boolean;
  runtimePolicy: ModelRouteRuntimePolicyInput;
}

export interface ModelRouteUpdateInput {
  routeId: string;
  displayName?: string;
  modelName?: string;
  connectionId?: string;
  enabled?: boolean;
  supportsVision?: boolean;
  runtimePolicy: ModelRouteRuntimePolicyInput;
  expectedRevision: number;
}

export interface AnswerBehaviorStatus {
  revision: number;
  custom_guidance: string | null;
  guidance_digest: string | null;
  updated_by: string | null;
  updated_at: string | null;
  audit_event_ref: string | null;
}

export interface AnswerBehaviorUpdateInput {
  customGuidance: string | null;
  expectedRevision: number;
}

export interface ModelRoutingFeatureProps {
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}
