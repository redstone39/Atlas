import type { DocumentTagType } from "../../shared/document-contracts";
import type { MessageReference } from "../../shared/user-messages";
import type {
  ConversationSummary,
  ExecutionState,
  ReasoningMode,
  ReasoningPhase,
  ReasoningProgressStatus,
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
  model_visible_items: number;
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
  reasoning_phase: ReasoningPhase | null;
  progress_status: ReasoningProgressStatus | null;
  cycle: number | null;
  message_code: string | null;
  message_params: Record<string, string | number | boolean | null>;
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
  reasoning_mode: ReasoningMode;
  reasoning_trace: ReasoningTrace | null;
  failure_code: string | null;
  applied_guidance_revision: number;
  applied_guidance_digest: string | null;
  budget: RuntimeBudgetSnapshot;
  model_visible_item_count: number;
  model_visible_item_limit: number;
  model_visible_item_exceeded: boolean;
  document_discovery: DocumentDiscoveryTrace[];
  events: RuntimeEvent[];
  created_at: string;
  updated_at: string;
}

export interface ReasoningPlanItem {
  item_id: string;
  summary: string;
  status: "pending" | "completed" | "skipped";
}

export interface ProcessScore {
  rubric_version: "atlas-process-rubric-v1";
  plan_coverage: number;
  evidence_handling: number;
  conflict_handling: number;
  gap_resolution: number;
  revision_completion: number;
  total: number;
}

export interface ReasoningEvaluation {
  cycle: number;
  verdict: "accept" | "revise_only" | "research_then_revise" | "unavailable";
  finding_codes: string[];
  summary: string | null;
  score: ProcessScore | null;
  unavailable_reason:
    | "provider_unavailable"
    | "budget_exhausted"
    | "deadline_exceeded"
    | null;
}

export interface ReasoningCorrection {
  cycle: number;
  kind: "revise_only" | "research_then_revise";
  triggering_evaluation: number;
  plan_generation: number | null;
  tool_invocation_start: number | null;
  tool_invocation_end: number | null;
  result_evaluation: number;
  addressed_finding_codes: string[];
  summary: string;
}

export interface ReasoningTrace {
  schema_version: "atlas-reasoning-trace-v3";
  trace_revision: number;
  trace_digest: string;
  parent_trace_digest: string | null;
  mode: "deep";
  status: "planning" | "running" | "completed" | "degraded" | "failed";
  plans: Array<{
    schema_version: "atlas-reasoning-plan-v2";
    generation: number;
    parent_generation: number | null;
    next_objective: string;
    completion_condition: string;
    items: ReasoningPlanItem[];
  }>;
  evaluations: ReasoningEvaluation[];
  corrections: ReasoningCorrection[];
  provisional_evidence_checks: Array<{
    ordinal: number;
    candidate_kind: "normal" | "limit_final";
    linked_evaluation_cycle: number | null;
    consistency: "aligned" | "conflict" | "insufficient" | "not_applicable" | "unavailable";
    reason_code: string;
    candidate_disposition: "pending" | "accepted" | "revised" | "degraded" | "limit_finalized";
    answer_digest: string;
    declared_subset_digest: string;
    assessment_input_digest: string | null;
    assessment_output_digest: string | null;
    visual_image_digests: string[];
  }>;
  limit_finalization: {
    triggering_evaluation: number;
    summary: string;
  } | null;
  termination_reason:
    | "completed"
    | "planner_failed"
    | "replanner_failed"
    | "evaluator_unavailable"
    | "provisional_evidence_unavailable"
    | "correction_limit_reached"
    | "budget_exhausted"
    | "deadline_exceeded"
    | "execution_failed"
    | null;
}
