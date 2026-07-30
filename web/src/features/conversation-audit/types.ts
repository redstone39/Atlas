import type { DocumentTagType } from "../../shared/document-contracts";
import type { MessageReference } from "../../shared/user-messages";
import type {
  ConversationSummary,
  ExecutionState,
  WorkspaceConversationDetailDto,
  WorkspaceConversationDto,
} from "../workspace";

export interface AdminConversationListDto {
  conversations: WorkspaceConversationDto[];
  next_cursor: string | null;
}

export interface AdminConversationListResult {
  conversations: ConversationSummary[];
  next_cursor: string | null;
}

export interface AuditEvent extends MessageReference {
  event_id: string;
  event_type: string;
  actor_id: string | null;
  target_ref: string | null;
  project_id: string | null;
  scope_type: DocumentTagType | null;
  scope_id: string | null;
  document_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuditEventList {
  events: AuditEvent[];
}

export type AdminConversationDetailDto = WorkspaceConversationDetailDto;

export interface RuntimeBudgetSnapshot {
  tool_invocations: number;
  catalog_pages: number;
  document_candidates: number;
  search_rounds: number;
  unique_evidence: number;
  provider_invocations: number;
  context_tokens: number;
  tool_tokens: number;
}

export interface RuntimeEvent {
  event_id: string;
  execution_id: string;
  sequence: number;
  event_type: string;
  state: ExecutionState;
  invocation_ordinal: number | null;
  result_ref: string | null;
  failure_code: string | null;
  created_at: string;
}

export interface DiscoveryChannelTrace {
  channel: "lexical" | "vector";
  status: "completed" | "failed";
}

export interface DiscoveryCandidateComponent {
  channel: "lexical" | "vector";
  rank: number;
  match_ref: string;
  locator_label: string;
  page_number: number | null;
}

export interface DiscoveryCandidateTrace {
  position: number;
  document_handle: string;
  resolution_status: "resolved" | "access_required";
  fused_score: string | null;
  best_component_rank: number | null;
  components: DiscoveryCandidateComponent[];
  document_ref: string | null;
  lifecycle_epoch: number | null;
  document_version_ref: string | null;
  processing_revision_ref: string | null;
  processing_generation_ref: string | null;
  index_generation_ref: string | null;
  document_display_name: string | null;
  document_version_label: string | null;
  preview: string | null;
  locator_label: string | null;
  page_number: number | null;
}

export interface DocumentDiscoveryTrace {
  invocation_id: string;
  result_ref: string;
  invocation_ordinal: number;
  query_text: string;
  requested_limit: number;
  ranking_contract: "equal-reciprocal-rank-v1";
  channels: DiscoveryChannelTrace[];
  degraded: boolean;
  failure_code: string | null;
  candidates: DiscoveryCandidateTrace[];
}

export interface RuntimeTraceDetail {
  execution_id: string;
  conversation_id: string;
  turn_id: string;
  state: ExecutionState;
  version: number;
  failure_code: string | null;
  applied_guidance_revision: number;
  applied_guidance_digest: string | null;
  budget: RuntimeBudgetSnapshot;
  document_discovery: DocumentDiscoveryTrace[];
  events: RuntimeEvent[];
  created_at: string;
  updated_at: string;
}
