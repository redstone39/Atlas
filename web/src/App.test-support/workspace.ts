import type { RuntimeTraceDetail } from "../features/conversation-audit/index";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurnResult,
} from "../features/workspace/index";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

const controlledText =
  "The synthetic reference target is documented in the example source differential, with tolerance set by the project stackup note.";


const answeredResult = {
  answer_text: "A synthetic document-backed statement.",
  user_reason: "result.answered_from_validated_evidence",
  citations: [
    {
      citation_id: "cit-ev-doc-layout-guidelines-001-001",
      document_id: null,
      document_version_id: "dver-doc-layout-guidelines-001-0001",
      document_title: "Layout Guideline Excerpt",
      locator_label: "Layout Guideline Excerpt, paragraph 1",
      snippet:
        "synthetic reference target from the example source",
      viewer_available: true,
    },
  ],
};

export const conversationSummaries: ConversationSummary[] = [
  {
    conversation_id: "conv-supported-001",
    owner_actor_id: "user-engineer-001",
    title: "Example conversation",
    status: "active",
    response_language: "en",
    reasoning_mode: "standard",
    created_at: "2026-07-09T00:00:00+00:00",
    updated_at: "2026-07-09T00:00:00+00:00",
    last_turn_status: "completed",
  },
];

export const answeredTurn: ConversationTurnResult = {
  conversation_id: "conv-supported-001",
  turn_id: "turn-answer-001",
  role: "assistant",
  source_turn_id: "turn-user-001",
  execution_id: "attempt-answer-001",
  execution_status: "completed",
  reasoning_mode: "standard",
  reasoning_timeline: [],
  response_kind: "grounded_answer",
  verification_status: "verified",
  evidence_review_status: "evidence_aligned",
  evidence_review_reason_codes: ["evidence_aligned"],
  assessment_state: "completed",
  assessment_reason_code: "completed",
  assessment_input_digest: "a".repeat(64),
  assessment_output_digest: "b".repeat(64),
  validation_state: "completed",
  content_state: "available",
  answer_text: answeredResult.answer_text,
  refusal_code: null,
  user_reason: answeredResult.user_reason,
  citations: answeredResult.citations,
  model_claimed_evidence: [],
  response_segments: [{
    segment_id: "seg-answer-001",
    kind: "controlled",
    text: answeredResult.answer_text,
    citation_ids: answeredResult.citations.map((item) => item.citation_id),
    external_unverified: false,
    verification_status: "evidence_supported",
    verification_reason: "supported_by_evidence",
    claims: [{
      claim_id: "claim-answer-001",
      claim_kind: "factual",
      text: answeredResult.answer_text,
      start: 0,
      end: answeredResult.answer_text.length,
      citation_ids: answeredResult.citations.map((item) => item.citation_id),
      verification_status: "evidence_supported",
      verification_reason: "supported_by_evidence",
    }],
  }],
  used_knowledge_refs: [{ knowledge_ref_id: "knowledge-001" }],
  retryable: false,
  audit_event_ref: "audit-conversation-answer",
  runtime_trace_id: "trace-answer-001",
  created_at: "2026-07-09T00:00:01+00:00",
  feedback: null,
};

export const unknownTurn: ConversationTurnResult = {
  ...answeredTurn,
  turn_id: "turn-unknown-001",
  response_kind: "unknown",
  verification_status: null,
  evidence_review_status: null,
  evidence_review_reason_codes: [],
  assessment_state: null,
  assessment_reason_code: null,
  assessment_input_digest: null,
  assessment_output_digest: null,
  answer_text: null,
  refusal_code: "unsupported_claim",
  user_reason: "result.supported_answer_not_established",
  citations: [],
  response_segments: [],
  used_knowledge_refs: [],
  audit_event_ref: "audit-conversation-unknown",
  runtime_trace_id: "trace-unknown-001",
};

export const deniedTurn: ConversationTurnResult = {
  ...unknownTurn,
  turn_id: "turn-denied-001",
  execution_status: "failed_closed",
  response_kind: "refused",
  retryable: true,
  refusal_code: "policy_denied",
  user_reason: "result.knowledge_scope_access_required",
  audit_event_ref: "audit-conversation-denied",
  runtime_trace_id: "trace-denied-001",
};

export const conversationDetail: ConversationDetail = {
  conversation_id: "conv-supported-001",
  owner_actor_id: "user-engineer-001",
  title: "Example conversation",
  status: "active",
  response_language: "en",
  reasoning_mode: "standard",
  created_at: "2026-07-09T00:00:00+00:00",
  updated_at: "2026-07-09T00:00:01+00:00",
  turns: [
    {
      turn_id: "turn-user-001",
      conversation_id: "conv-supported-001",
      role: "user",
      input_text: "What is the approved value for the selected item?",
      answer_text: null,
      execution_status: "completed",
      reasoning_mode: "standard",
      reasoning_timeline: [],
      response_kind: "dialogue",
      verification_status: null,
      evidence_review_status: null,
      evidence_review_reason_codes: [],
      assessment_state: null,
      assessment_reason_code: null,
      assessment_input_digest: null,
      assessment_output_digest: null,
      content_state: "available",
      refusal_code: null,
      user_reason: "conversation.submitted_by_user",
      citations: [],
      model_claimed_evidence: [],
      response_segments: [],
      validation_state: "not_applicable",
      used_knowledge_refs: [],
      source_turn_id: null,
      execution_id: "attempt-answer-001",
      retryable: false,
      runtime_trace_id: null,
      audit_event_ref: null,
      created_at: "2026-07-09T00:00:00+00:00",
      feedback: null,
    },
    {
      ...answeredTurn,
      role: "assistant",
      input_text: null,
    },
  ],
};

function workspaceConversationDto(
  title = conversationDetail.title,
  responseLanguage: "zh-TW" | "en" = "en",
) {
  return {
    conversation_id: conversationDetail.conversation_id,
    owner_actor_id: conversationDetail.owner_actor_id,
    title,
    status: conversationDetail.status,
    response_language: responseLanguage,
    reasoning_mode: conversationDetail.reasoning_mode,
    created_at: conversationDetail.created_at,
    updated_at: conversationDetail.updated_at,
  };
}

export function workspaceProjectionDto(
  turn: ConversationTurnResult,
  userInput = "What is the approved value for the selected item?",
) {
  const terminalFailed = turn.execution_status === "failed_closed";
  return {
    turn_id: turn.turn_id,
    execution_id: turn.execution_id,
    ordinal: 1,
    user_input: userInput,
    execution_status: terminalFailed ? "terminal_failed" : "terminal_completed",
    reasoning_mode: turn.reasoning_mode,
    reasoning_timeline: turn.reasoning_timeline,
    retrieval_status: terminalFailed
      ? "access_denied"
      : turn.citations.length > 0
        ? "evidence_found"
        : "no_evidence",
    evidence_review_status: terminalFailed ? null : turn.evidence_review_status,
    evidence_review_reason_codes: terminalFailed
      ? []
      : turn.evidence_review_reason_codes,
    assessment_state: terminalFailed ? null : turn.assessment_state,
    assessment_reason_code: terminalFailed ? null : turn.assessment_reason_code,
    assessment_input_digest: terminalFailed ? null : turn.assessment_input_digest,
    assessment_output_digest: terminalFailed ? null : turn.assessment_output_digest,
    segments: turn.response_segments.map((segment) => ({
      segment_id: segment.segment_id,
      text: segment.text,
    })),
    citations: turn.citations.map((citation) => ({
      citation_ref: citation.citation_id,
      segment_id: turn.response_segments[0]?.segment_id ?? "segment-none",
      claim_id: turn.response_segments[0]?.claims[0]?.claim_id ?? "claim-none",
    })),
    model_claimed_evidence: turn.model_claimed_evidence,
    failure_code: terminalFailed ? turn.refusal_code : null,
    feedback: turn.feedback,
    created_at: turn.created_at,
  };
}

export function workspaceDetailDto(turn: ConversationTurnResult = answeredTurn) {
  return {
    conversation: workspaceConversationDto(),
    turns: [workspaceProjectionDto(turn)],
  };
}

export function adminDetailDto(detail: ConversationDetail = conversationDetail) {
  const userInput = detail.turns.find((turn) => turn.role === "user")?.input_text ?? "Input unavailable";
  return {
    conversation: {
      conversation_id: detail.conversation_id,
      owner_actor_id: detail.owner_actor_id,
      title: detail.title,
      status: detail.status,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    },
    turns: detail.turns
      .filter((turn) => turn.role === "assistant")
      .map((turn) => workspaceProjectionDto(turn as ConversationTurnResult, userInput)),
  };
}

export const runtimeTraceDetail: RuntimeTraceDetail = {
  execution_id: "exec-answer-001",
  conversation_id: "conv-supported-001",
  turn_id: "turn-answer-001",
  state: "terminal_completed",
  version: 9,
  reasoning_mode: "deep",
  reasoning_trace: {
    schema_version: "atlas-reasoning-trace-v4",
    prompt_skill_catalog: {
      category: "planner",
      catalog_revision: 3,
      catalog_digest: "8".repeat(64),
    },
    skill_selections: [{
      node: "deep_initial_planner",
      plan_generation: 1,
      status: "selected",
      selected_skills: [{
        category: "planner",
        name: "evidence-review",
        revision: 2,
        content_digest: "9".repeat(64),
      }],
      fallback_code: null,
    }, {
      node: "deep_replanner",
      plan_generation: 2,
      status: "baseline_fallback",
      selected_skills: [],
      fallback_code: "selector_unavailable",
    }],
    trace_revision: 3,
    trace_digest: "d".repeat(64),
    parent_trace_digest: "c".repeat(64),
    mode: "deep",
    status: "degraded",
    plans: [{
      schema_version: "atlas-reasoning-plan-v2",
      generation: 1,
      parent_generation: null,
      next_objective: "Review the request and available evidence.",
      completion_condition: "The candidate is ready for evaluation.",
      items: [{
        item_id: "plan-1",
        summary: "Review the request and available evidence.",
        status: "pending",
      }],
    }, {
      schema_version: "atlas-reasoning-plan-v2",
      generation: 2,
      parent_generation: 1,
      next_objective: "Find the missing controlled-impedance evidence.",
      completion_condition: "The evidence gap is resolved or disclosed.",
      items: [{
        item_id: "plan-1",
        summary: "Review the request and available evidence.",
        status: "completed",
      }, {
        item_id: "plan-2",
        summary: "Search for the missing evidence.",
        status: "pending",
      }],
    }],
    evaluations: [{
      cycle: 1,
      verdict: "research_then_revise",
      finding_codes: ["evidence_gap"],
      summary: "More evidence is required.",
      score: {
        rubric_version: "atlas-process-rubric-v1",
        plan_coverage: 2,
        evidence_handling: 1,
        conflict_handling: 2,
        gap_resolution: 0,
        revision_completion: 1,
        total: 6,
      },
      unavailable_reason: null,
    }, {
      cycle: 2,
      verdict: "unavailable",
      finding_codes: [],
      summary: null,
      score: null,
      unavailable_reason: "provider_unavailable",
    }],
    corrections: [{
      cycle: 1,
      kind: "research_then_revise",
      triggering_evaluation: 1,
      plan_generation: 2,
      tool_invocation_start: 2,
      tool_invocation_end: 3,
      result_evaluation: 2,
      addressed_finding_codes: ["evidence_gap"],
      summary: "More evidence is required.",
    }],
    provisional_evidence_checks: [{
      ordinal: 1,
      candidate_kind: "normal",
      linked_evaluation_cycle: 1,
      consistency: "insufficient",
      reason_code: "declared_evidence_insufficient",
      candidate_disposition: "revised",
      answer_digest: "1".repeat(64),
      declared_subset_digest: "2".repeat(64),
      assessment_input_digest: "3".repeat(64),
      assessment_output_digest: "4".repeat(64),
      visual_image_digests: [],
    }, {
      ordinal: 2,
      candidate_kind: "normal",
      linked_evaluation_cycle: 2,
      consistency: "unavailable",
      reason_code: "provider_failed",
      candidate_disposition: "degraded",
      answer_digest: "5".repeat(64),
      declared_subset_digest: "6".repeat(64),
      assessment_input_digest: null,
      assessment_output_digest: null,
      visual_image_digests: ["7".repeat(64)],
    }],
    limit_finalization: null,
    termination_reason: "evaluator_unavailable",
  },
  prompt_skill_catalogs: [{
    category: "understanding",
    catalog_revision: 1,
    catalog_digest: "7".repeat(64),
  }, {
    category: "planner",
    catalog_revision: 3,
    catalog_digest: "8".repeat(64),
  }, {
    category: "answer",
    catalog_revision: 2,
    catalog_digest: "6".repeat(64),
  }],
  prompt_skill_selections: [{
    category: "understanding",
    node: "resolver",
    candidate_ordinal: null,
    candidate_kind: null,
    status: "selected",
    selected_skills: [{
      category: "understanding",
      name: "resolve-followups",
      revision: 1,
      content_digest: "5".repeat(64),
    }],
    fallback_code: null,
  }, {
    category: "answer",
    node: "answer_candidate",
    candidate_ordinal: 1,
    candidate_kind: "normal",
    status: "selected",
    selected_skills: [{
      category: "answer",
      name: "evidence-answer",
      revision: 2,
      content_digest: "4".repeat(64),
    }],
    fallback_code: null,
  }, {
    category: "answer",
    node: "answer_candidate",
    candidate_ordinal: 2,
    candidate_kind: "normal",
    status: "baseline_fallback",
    selected_skills: [],
    fallback_code: "selector_unavailable",
  }],
  failure_code: null,
  applied_guidance_revision: 3,
  applied_guidance_digest: "a".repeat(64),
  budget: {
    tool_invocations: 1,
    catalog_pages: 1,
    document_candidates: 4,
    search_rounds: 1,
    model_visible_items: 2,
    provider_invocations: 2,
    context_tokens: 180,
    tool_tokens: 64,
  },
  model_visible_item_count: 24,
  model_visible_item_limit: 37,
  model_visible_item_exceeded: false,
  audit_steps: [
    {
      ordinal: 1,
      step_kind: "model",
      operation: "search_knowledge",
      status: "completed",
      safe_input_digest: "8".repeat(64),
      result_ref: null,
      result_digest: null,
      input_tokens: 48,
      output_tokens: 12,
      evidence_count: 0,
    },
    {
      ordinal: 2,
      step_kind: "tool",
      operation: "search_knowledge",
      status: "completed",
      safe_input_digest: "9".repeat(64),
      result_ref: "result-discovery-001",
      result_digest: "0".repeat(64),
      input_tokens: 0,
      output_tokens: 64,
      evidence_count: 2,
    },
    {
      ordinal: 3,
      step_kind: "model",
      operation: "finalize_answer",
      status: "completed",
      safe_input_digest: "7".repeat(64),
      result_ref: "result-answer-001",
      result_digest: "6".repeat(64),
      input_tokens: 72,
      output_tokens: 96,
      evidence_count: 0,
    },
  ],
  document_discovery: [
    {
      invocation_id: "invocation-discovery-001",
      result_ref: "result-discovery-001",
      invocation_ordinal: 1,
      query_text: "example policy",
      requested_limit: 20,
      ranking_contract: "equal-reciprocal-rank-v1",
      channels: [
        { channel: "lexical", status: "completed" },
        { channel: "vector", status: "completed" },
      ],
      degraded: false,
      failure_code: null,
      candidates: [
        {
          position: 1,
          document_handle: "kh_document_policy",
          resolution_status: "resolved",
          fused_score: "2/1",
          best_component_rank: 1,
          components: [
            {
              channel: "lexical",
              rank: 1,
              match_ref: "match-lexical-001",
              locator_label: "p. 4",
              page_number: 4,
            },
          ],
          document_ref: "document-policy-001",
          lifecycle_epoch: 1,
          document_version_ref: "document-version-policy-001",
          processing_revision_ref: "processing-revision-policy-001",
          processing_generation_ref: "processing-generation-policy-001",
          index_generation_ref: "index-generation-policy-001",
          document_display_name: "Example Document.pdf",
          document_version_label: "2026",
          preview: "Retention is seven years.",
          locator_label: "p. 4",
          page_number: 4,
        },
        {
          position: 2,
          document_handle: "kh_document_revoked",
          resolution_status: "access_required",
          fused_score: null,
          best_component_rank: null,
          components: [],
          document_ref: null,
          lifecycle_epoch: null,
          document_version_ref: null,
          processing_revision_ref: null,
          processing_generation_ref: null,
          index_generation_ref: null,
          document_display_name: null,
          document_version_label: null,
          preview: null,
          locator_label: null,
          page_number: null,
        },
      ],
    },
  ],
  events: [
    {
      event_id: "event-0001",
      execution_id: "exec-answer-001",
      sequence: 1,
      event_type: "execution_allocated",
      state: "allocated",
      invocation_ordinal: null,
      result_ref: null,
      failure_code: null,
      reasoning_phase: null,
      progress_status: null,
      cycle: null,
      message_code: null,
      message_params: {},
      created_at: "2026-07-09T00:00:00+00:00",
    },
    {
      event_id: "event-0002",
      execution_id: "exec-answer-001",
      sequence: 2,
      event_type: "terminal_completed",
      state: "terminal_completed",
      invocation_ordinal: null,
      result_ref: "result-answer-001",
      failure_code: null,
      reasoning_phase: null,
      progress_status: null,
      cycle: null,
      message_code: null,
      message_params: {},
      created_at: "2026-07-09T00:00:01+00:00",
    },
  ],
  created_at: "2026-07-09T00:00:00+00:00",
  updated_at: "2026-07-09T00:00:01+00:00",
};

export function runtimeEventStream(executionId: string, state: "terminal_completed" | "terminal_failed"): Response {
  return new Response(
    `id: evt-terminal\nevent: ${state}\ndata: ${JSON.stringify({
      event_id: "evt-terminal",
      execution_id: executionId,
      sequence: 8,
      event_type: state,
      state,
      created_at: "2026-07-20T00:00:00Z",
    })}\n\n`,
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

export function createWorkspaceHandler(): MockApiHandler {
  let feedback = answeredTurn.feedback;
  const workspaceExecutions = new Map<string, ConversationTurnResult>();
  return ({ url, method, init }) => {
    if (url.pathname === "/api/v1/workspace/conversations" && method === "GET") {
      return jsonResponse({ conversations: conversationSummaries });
    }
    if (url.pathname === "/api/v1/workspace/conversations" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse(
        {
          conversation: workspaceConversationDto(
            body.title ?? conversationDetail.title,
            body.response_language === "zh-TW" ? "zh-TW" : "en",
          ),
          turns: [],
        },
        201,
      );
    }
    if (url.pathname === "/api/v1/workspace/conversations/conv-supported-001" && method === "GET") {
      const latest = [...workspaceExecutions.values()].at(-1) ?? answeredTurn;
      return jsonResponse(workspaceDetailDto({ ...latest, feedback }));
    }
    if (
      url.pathname ===
        "/api/v1/workspace/conversations/conv-supported-001/turns/turn-answer-001/feedback" &&
      method === "PUT"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      feedback = {
        feedback: body.feedback,
        revision: (feedback?.revision ?? 0) + 1,
        updated_at: "2026-07-20T00:00:04+00:00",
      };
      return jsonResponse(feedback);
    }
    if (
      url.pathname === "/api/v1/workspace/conversations/conv-supported-001/turns" &&
      method === "POST"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const turn = body.input_text.includes("root cause")
        ? unknownTurn
        : body.input_text.includes("revoked")
          ? deniedTurn
          : answeredTurn;
      const executionId = `${turn.execution_id}-${workspaceExecutions.size + 1}`;
      const acceptedTurn = { ...turn, execution_id: executionId };
      workspaceExecutions.set(executionId, acceptedTurn);
      return jsonResponse({
        turn_id: acceptedTurn.turn_id,
        execution_id: executionId,
        status: "accepted",
        status_url: `/api/v1/workspace/turn-executions/${executionId}`,
        events_url: `/api/v1/workspace/turn-executions/${executionId}/events`,
      }, 202);
    }
    const eventMatch = url.pathname.match(/^\/api\/v1\/workspace\/turn-executions\/([^/]+)\/events$/);
    if (eventMatch && method === "GET") {
      const executionId = eventMatch[1];
      return Promise.resolve(runtimeEventStream(executionId, "terminal_completed"));
    }
    const statusMatch = url.pathname.match(/^\/api\/v1\/workspace\/turn-executions\/([^/]+)$/);
    if (statusMatch && method === "GET") {
      const executionId = statusMatch[1];
      const turn = workspaceExecutions.get(executionId) ?? answeredTurn;
      return jsonResponse({
        execution_id: executionId,
        turn_id: turn.turn_id,
        conversation_id: "conv-supported-001",
        state: turn.execution_status === "failed_closed" ? "terminal_failed" : "terminal_completed",
        version: 8,
        failure_code: turn.execution_status === "failed_closed" ? turn.refusal_code : null,
        updated_at: turn.created_at,
      });
    }
    if (url.pathname.includes("/citations/")) {
      return jsonResponse({
        citation_id: "cit-ev-doc-layout-guidelines-001-001",
        copy_text:
          "synthetic reference target from the example source",
        document_title: "Layout Guideline Excerpt",
        locator_label: "Layout Guideline Excerpt, paragraph 1",
        access_decision_id: "access-copy-001",
        audit_event_ref: "audit-p0-citation-copy",
      });
    }
    return undefined;
  };
}
