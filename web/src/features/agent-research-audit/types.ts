export type AgentResearchAuditListItem =
  | {
      kind: "accepted";
      research_id: string;
      execution_id: string;
      actor_id: string;
      status: "accepted" | "completed";
      output_mode: "evidence_packet" | "evidence_packet_and_answer";
      occurred_at: string;
      completed_at: string | null;
    }
  | {
      kind: "denied";
      event_id: string;
      actor_id: string | null;
      message_code: string;
      reason: string;
      occurred_at: string;
    };

export interface AgentResearchAuditList {
  items: AgentResearchAuditListItem[];
  next_cursor: string | null;
}

export interface ResearchEvidenceDescriptor {
  evidence_id: string;
  kind: "text" | "visual" | "native";
  title: string;
  page: number | null;
  locator: string;
  available_representations: Array<"text" | "visual" | "native">;
  lineage_digest: string;
}

export interface ResearchPacket {
  packet_digest: string;
  findings: Array<{
    finding_id: string;
    text: string;
    evidence_ids: string[];
    evidence_assessment: "aligned" | "conflict" | "insufficient";
  }>;
  unresolved_questions: string[];
  research_limits: Array<{ code: string; detail: string }>;
  evidence: ResearchEvidenceDescriptor[];
}

export interface AgentResearchAuditDetail {
  research_id: string;
  execution_id: string;
  actor_id: string;
  question: string;
  accepted_scope: {
    scope_ref: string;
    scope_digest: string;
    project_ids: string[];
    requested_refs: Array<{ kind: "project" | "team"; id: string }>;
  };
  output_mode: "evidence_packet" | "evidence_packet_and_answer";
  status: "accepted" | "completed";
  packet: ResearchPacket | null;
  answer: {
    status: "not_requested" | "available" | "unavailable";
    packet_ref: string;
    packet_digest: string;
    governed_answer: {
      segments: Array<{ segment_id: string; text: string }>;
      digest: string;
    } | null;
    citations: { digest: string } | null;
  } | null;
  business_events: Array<{
    event_id: string;
    event_type: string;
    message_code: string;
    created_at: string;
  }>;
  accepted_at: string;
  completed_at: string | null;
}

export interface AgentResearchRuntimeDetail {
  research_id: string;
  execution_id: string;
  state: string;
  version: number;
  reasoning_mode: "standard" | "deep";
  failure_code: string | null;
  budget: {
    tool_invocations: number;
    catalog_pages: number;
    document_candidates: number;
    search_rounds: number;
    model_visible_items: number;
    provider_invocations: number;
    context_tokens: number;
    tool_tokens: number;
  };
  events: Array<{
    event_id: string;
    sequence: number;
    event_type: string;
    state: string;
    failure_code: string | null;
    message_code: string | null;
    created_at: string;
  }>;
  events_truncated: boolean;
  audit_steps: Array<{
    ordinal: number;
    step_kind: string;
    operation: string;
    status: string;
    input_tokens: number;
    output_tokens: number;
    evidence_count: number;
  }>;
  created_at: string;
  updated_at: string;
}

export interface AgentResearchEvidenceContent {
  research_id: string;
  evidence_id: string;
  representation: "text" | "visual" | "native";
  media_type: "text/plain" | "image/png" | "application/pdf";
  text: string | null;
  content_base64: string | null;
}
