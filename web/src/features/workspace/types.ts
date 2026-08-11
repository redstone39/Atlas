import type { ReactNode } from "react";

import type {
  DocumentTagRef,
  DocumentTagSummary,
} from "../../shared/document-contracts";
import type { MessageParams } from "../../shared/user-messages";
import type { SessionState } from "../identity-session/index";
import type { AppRoute } from "../../shared/routes";

export type { DocumentTagRef, DocumentTagSummary };

export interface WorkspaceFeatureProps {
  activeView: "/workspace" | "/library";
  conversationId: string | null;
  session: SessionState;
  onNotice: (message: string) => void;
  onNavigate: (route: AppRoute) => void;
  onReplace: (route: AppRoute) => void;
  libraryContent: ReactNode;
  renderSidebarHeader: (options: WorkspaceSidebarHeaderOptions) => ReactNode;
  renderAccountMenu: (options: WorkspaceAccountMenuOptions) => ReactNode;
}

export interface WorkspaceSidebarHeaderOptions {
  presentation: "full" | "compact";
  onOpenWorkspace: () => void;
  onCollapseSidebar?: () => void;
}

export interface WorkspaceAccountMenuOptions {
  presentation: "full" | "compact";
  className?: string;
  menuAlign?: "start" | "center" | "end";
  menuSide?: "top" | "right" | "bottom" | "left";
}

export interface WorkspaceTagScopeResult {
  tags: DocumentTagSummary[];
}

export interface CitationCard {
  citation_id: string;
  document_title: string;
  locator_label: string;
  snippet: string;
  viewer_available: boolean;
  document_format?: string;
  evidence_modality?: string;
}

export interface ConversationSummary {
  conversation_id: string;
  owner_actor_id: string;
  title: string;
  status: "active" | "archived";
  response_language: "zh-TW" | "en";
  reasoning_mode: ReasoningMode;
  created_at: string;
  updated_at: string;
  last_turn_status: string | null;
}

export interface ConversationListResult {
  conversations: ConversationSummary[];
}

export interface ConversationArchiveResultDto {
  conversation: WorkspaceConversationDto;
  audit_event_ref: string;
}

export interface ConversationTurn {
  turn_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  input_text: string | null;
  answer_text: string | null;
  execution_status: "submitted" | "processing" | "completed" | "failed_closed";
  reasoning_mode: ReasoningMode;
  reasoning_timeline: ReasoningProgress[];
  response_kind: "answer" | "dialogue" | "clarification" | "grounded_answer" | "external_unverified" | "mixed_answer" | "unknown" | "refused";
  verification_status: VerificationStatus | null;
  evidence_review_status: EvidenceReviewStatus | null;
  evidence_review_reason_codes: EvidenceReviewReasonCode[];
  assessment_state: AssessmentState | null;
  assessment_reason_code: AssessmentReasonCode | null;
  assessment_input_digest: string | null;
  assessment_output_digest: string | null;
  content_state: "available" | "access_required";
  refusal_code: string | null;
  user_reason: string;
  citations: CitationCard[];
  model_claimed_evidence: ClaimedEvidenceTrace[];
  response_segments: ResponseSegment[];
  validation_state: "not_applicable" | "completed" | "degraded";
  used_knowledge_refs: { knowledge_ref_id: string }[];
  source_turn_id: string | null;
  execution_id: string | null;
  retryable: boolean;
  runtime_trace_id: string | null;
  audit_event_ref: string | null;
  created_at: string;
}

export interface ConversationDetail {
  conversation_id: string;
  owner_actor_id: string;
  title: string;
  status: "active" | "archived";
  response_language: "zh-TW" | "en";
  reasoning_mode: ReasoningMode;
  created_at: string;
  updated_at: string;
  turns: ConversationTurn[];
}

export interface ConversationTurnResult {
  conversation_id: string;
  turn_id: string;
  role: "assistant";
  source_turn_id: string;
  execution_id: string;
  execution_status: ConversationTurn["execution_status"];
  reasoning_mode: ReasoningMode;
  reasoning_timeline: ReasoningProgress[];
  response_kind: ConversationTurn["response_kind"];
  verification_status: VerificationStatus | null;
  evidence_review_status: EvidenceReviewStatus | null;
  evidence_review_reason_codes: EvidenceReviewReasonCode[];
  assessment_state: AssessmentState | null;
  assessment_reason_code: AssessmentReasonCode | null;
  assessment_input_digest: string | null;
  assessment_output_digest: string | null;
  content_state: ConversationTurn["content_state"];
  answer_text: string | null;
  refusal_code: string | null;
  user_reason: string;
  citations: CitationCard[];
  model_claimed_evidence: ClaimedEvidenceTrace[];
  response_segments: ResponseSegment[];
  validation_state: ConversationTurn["validation_state"];
  used_knowledge_refs: { knowledge_ref_id: string }[];
  retryable: boolean;
  audit_event_ref: string | null;
  runtime_trace_id: string | null;
  created_at: string;
}

export type ExecutionState =
  | "allocated"
  | "accepted"
  | "context_ready"
  | "awaiting_model_action"
  | "tool_pending"
  | "tool_completed"
  | "governing_result"
  | "materializing_terminal"
  | "terminal_completed"
  | "terminal_failed";

export type ReasoningMode = "standard" | "deep";

export type ReasoningPhase =
  | "understanding"
  | "planning"
  | "researching"
  | "drafting"
  | "evaluating"
  | "revising"
  | "governing"
  | "finalizing"
  | "completed"
  | "failed";

export type ReasoningProgressStatus =
  | "started"
  | "completed"
  | "degraded"
  | "failed";

export interface ReasoningProgress {
  event_id: string;
  sequence: number;
  phase: ReasoningPhase;
  status: ReasoningProgressStatus;
  cycle: number | null;
  message_code: string;
  message_params: MessageParams;
  created_at: string;
}

export type RetrievalStatus =
  | "not_used"
  | "evidence_found"
  | "no_evidence"
  | "access_denied"
  | "tool_failed"
  | "budget_exhausted";

export type VerificationStatus = "verified" | "partially_verified" | "unverified";
export type EvidenceReviewStatus = "evidence_aligned" | "questionable";
export type EvidenceReviewReasonCode =
  | "evidence_aligned"
  | "empty_declaration"
  | "assessment_not_completed"
  | "answer_item_failed";
export type AssessmentState = "completed" | "unavailable" | "not_attempted";
export type AssessmentReasonCode =
  | "completed"
  | "empty_declaration"
  | "no_resolved_declared_evidence"
  | "deadline_elapsed"
  | "route_unavailable"
  | "provider_contract_unavailable"
  | "physical_limit_rejected"
  | "tokenizer_unavailable"
  | "provider_timeout"
  | "provider_failed"
  | "provider_refused"
  | "provider_incomplete"
  | "invalid_output";

export interface WorkspaceConversationDto {
  conversation_id: string;
  owner_actor_id: string;
  title: string;
  status: "active" | "archived";
  response_language: "zh-TW" | "en";
  reasoning_mode: ReasoningMode;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceConversationSummaryDto extends WorkspaceConversationDto {
  last_turn_status: "processing" | "completed" | "failed_closed" | null;
}

export interface WorkspaceAnswerSegmentDto {
  segment_id: string;
  text: string;
}

export interface WorkspaceCitationDto {
  citation_ref: string;
  segment_id: string;
  claim_id: string;
}

export interface ClaimedEvidenceTrace {
  position: number;
  handle: string;
  resolution_status: "resolved" | "unresolved" | "access_required";
  duplicate_of_position: number | null;
  handle_kind: "evidence" | "visual" | null;
  evidence_ref: string | null;
  result_ref: string | null;
  invocation_ordinal: number | null;
  document_ref: string | null;
  document_handle: string | null;
  lifecycle_epoch: number | null;
  document_version_ref: string | null;
  processing_revision_ref: string | null;
  processing_generation_ref: string | null;
  index_generation_ref: string | null;
  document_display_name: string | null;
  document_version_label: string | null;
  page_number: number | null;
  locator_label: string | null;
  review_resolution_reason: string | null;
  protected_open_ref: string | null;
}

export interface ProtectedCitationEvidenceDto {
  citation_ref: string;
  locator_label: string;
  snippet: string;
  content: string;
  modality: "text" | "table" | "figure";
}

export interface ProtectedDeclaredEvidenceDto {
  evidence_handle: string;
  locator_label: string;
  snippet: string;
  content: string;
  modality: "text" | "table" | "figure";
}

export interface WorkspaceTurnProjectionDto {
  turn_id: string;
  execution_id: string;
  ordinal: number;
  user_input: string;
  execution_status: ExecutionState;
  reasoning_mode: ReasoningMode;
  reasoning_timeline: ReasoningProgress[];
  retrieval_status: RetrievalStatus | null;
  evidence_review_status: EvidenceReviewStatus | null;
  evidence_review_reason_codes: EvidenceReviewReasonCode[];
  assessment_state: AssessmentState | null;
  assessment_reason_code: AssessmentReasonCode | null;
  assessment_input_digest: string | null;
  assessment_output_digest: string | null;
  segments: WorkspaceAnswerSegmentDto[];
  citations: WorkspaceCitationDto[];
  model_claimed_evidence: ClaimedEvidenceTrace[];
  failure_code: string | null;
  created_at: string;
}

export interface WorkspaceConversationDetailDto {
  conversation: WorkspaceConversationDto;
  turns: WorkspaceTurnProjectionDto[];
}

export interface WorkspaceConversationListDto {
  conversations: WorkspaceConversationSummaryDto[];
}

export interface TurnAcceptedDto {
  turn_id: string;
  execution_id: string;
  status: "allocated" | "accepted" | "context_ready" | "awaiting_model_action";
  status_url: string;
  events_url: string;
}

export interface WorkspaceExecutionStatusDto {
  execution_id: string;
  turn_id: string;
  conversation_id: string;
  state: ExecutionState;
  version: number;
  reasoning_mode: ReasoningMode;
  reasoning_timeline: ReasoningProgress[];
  failure_code: string | null;
  updated_at: string;
}

export interface ResponseSegment {
  segment_id: string;
  kind: "controlled" | "external_unverified" | "evidence_conflict" | "mixed_evidence" | "dialogue" | "clarification" | "unknown" | "refusal";
  text: string;
  citation_ids: string[];
  external_unverified: boolean;
  verification_status: "evidence_supported" | "unverified_inference" | "conflict" | "mixed" | "not_applicable";
  verification_reason: "supported_by_evidence" | "not_supported_or_inferred" | "contradicted_by_evidence" | "evidence_insufficient" | "validator_unavailable" | "mixed_claim_statuses" | "not_applicable";
  claims: ResponseClaim[];
}

export interface ResponseClaim {
  claim_id: string;
  claim_kind: "factual" | "comparison_conclusion" | "gap";
  text: string;
  start: number;
  end: number;
  citation_ids: string[];
  verification_status: "evidence_supported" | "unverified_inference" | "conflict";
  verification_reason: "supported_by_evidence" | "not_supported_or_inferred" | "contradicted_by_evidence" | "validator_unavailable";
}

export type RuntimeProgressPhase =
  | ReasoningPhase
  | "searching_knowledge"
  | "executing_tools"
  | "generating"
  | "repairing_format"
  | "validating_claims"
  | "finalizing";

export interface RuntimeStreamEvent {
  event_id: string;
  execution_id: string;
  sequence: number;
  event_type?: string;
  state?: ExecutionState;
  invocation_ordinal?: number | null;
  result_ref?: string | null;
  created_at?: string;
  phase?: RuntimeProgressPhase;
  reasoning_phase?: ReasoningPhase | null;
  progress_status?: ReasoningProgressStatus | null;
  cycle?: number | null;
  turn?: ConversationTurnResult;
  failure_code?: string;
  message_code?: string;
  message_params?: MessageParams;
  retryable?: boolean;
}
