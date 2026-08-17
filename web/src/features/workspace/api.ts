import { API_BASE, requestJson } from "../../shared/api-client";
import { ApiError } from "../../shared/user-messages";
import type {
  ConversationDetail,
  ConversationArchiveResultDto,
  ConversationListResult,
  DocumentTagRef,
  ConversationTurnResult,
  RuntimeStreamEvent,
  TurnAcceptedDto,
  WorkspaceConversationDetailDto,
  WorkspaceConversationDto,
  WorkspaceConversationSummaryDto,
  WorkspaceConversationListDto,
  WorkspaceExecutionStatusDto,
  ProtectedCitationEvidenceDto,
  ProtectedDeclaredEvidenceDto,
  WorkspaceAnswerSegmentDto,
  WorkspaceTurnProjectionDto,
  WorkspaceTagScopeResult,
  ReasoningMode,
  TurnFeedbackUpdateRequest,
  TurnFeedbackUpdateResponse,
} from "./types";

export type DeclaredEvidencePreview =
  | {
      kind: "page";
      mediaType: "application/pdf" | "image/png";
      blob: Blob;
    }
  | {
      kind: "excerpt";
      evidence: ProtectedDeclaredEvidenceDto;
    };

const DECLARED_EVIDENCE_PREVIEW_ACCEPT =
  "application/pdf, image/png, application/json;q=0.5";

export function joinResponseSegmentMarkdown(
  segments: readonly { text: string }[],
) {
  return segments.map((segment) => segment.text).join("\n\n");
}

export const workspaceApi = {
  workspaceTagScope: () =>
    requestJson<WorkspaceTagScopeResult>("/api/v1/workspace/tag-scope"),
  listWorkspaceConversations: async (): Promise<ConversationListResult> => {
    const result = await requestJson<WorkspaceConversationListDto>(
      "/api/v1/workspace/conversations",
    );
    return { conversations: result.conversations.map(conversationSummary) };
  },
  createWorkspaceConversation: async (
    title: string,
    responseLanguage: "zh-TW" | "en",
    tagRefs: DocumentTagRef[],
    signal?: AbortSignal,
  ): Promise<ConversationDetail> => {
    const canonicalTagRefs = [...tagRefs].sort((left, right) =>
      left.tag_type.localeCompare(right.tag_type) ||
      left.tag_id.localeCompare(right.tag_id)
    );
    const result = await requestJson<WorkspaceConversationDetailDto>(
      "/api/v1/workspace/conversations",
      {
        method: "POST",
        signal,
        body: JSON.stringify({
          title,
          response_language: responseLanguage,
          tag_refs: canonicalTagRefs,
        }),
      },
    );
    return conversationDetail(result);
  },
  getWorkspaceConversation: async (
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationDetail> => conversationDetail(
    await requestJson<WorkspaceConversationDetailDto>(
      `/api/v1/workspace/conversations/${conversationId}`,
      { signal },
    ),
  ),
  archiveWorkspaceConversation: (
    conversationId: string,
    idempotencyKey: string,
  ) => requestJson<ConversationArchiveResultDto>(
    `/api/v1/workspace/conversations/${encodeURIComponent(conversationId)}/archive`,
    {
      method: "POST",
      body: JSON.stringify({ idempotency_key: idempotencyKey }),
    },
  ),
  updateTurnFeedback: (
    conversationId: string,
    turnId: string,
    payload: TurnFeedbackUpdateRequest,
    signal?: AbortSignal,
  ) => requestJson<TurnFeedbackUpdateResponse>(
    `/api/v1/workspace/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/feedback`,
    {
      method: "PUT",
      signal,
      body: JSON.stringify(payload),
    },
  ),
  readCitation: (
    conversationId: string,
    turnId: string,
    citationRef: string,
  ) => requestJson<ProtectedCitationEvidenceDto>(
    `/api/v1/workspace/conversations/${conversationId}/turns/${turnId}/citations/${citationRef}`,
  ),
  readDeclaredEvidence: (
    conversationId: string,
    turnId: string,
    protectedOpenRef: string,
  ) => requestJson<ProtectedDeclaredEvidenceDto>(
    `/api/v1/workspace/conversations/${conversationId}/turns/${turnId}/declared-evidence/${protectedOpenRef}`,
  ),
  readDeclaredEvidencePreview: async (
    conversationId: string,
    turnId: string,
    protectedOpenRef: string,
  ): Promise<DeclaredEvidencePreview> => {
    const response = await fetch(
      `${API_BASE}/api/v1/workspace/conversations/${conversationId}/turns/${turnId}/declared-evidence/${protectedOpenRef}`,
      {
        credentials: "include",
        headers: { Accept: DECLARED_EVIDENCE_PREVIEW_ACCEPT },
      },
    );
    if (!response.ok) throw await apiError(response);

    const mediaType = response.headers.get("Content-Type")
      ?.split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (mediaType === "application/json") {
      return {
        kind: "excerpt",
        evidence: await response.json() as ProtectedDeclaredEvidenceDto,
      };
    }
    if (mediaType === "application/pdf" || mediaType === "image/png") {
      return { kind: "page", mediaType, blob: await response.blob() };
    }
    throw new ApiError(
      { error_code: "unsupported_declared_evidence_content_type" },
      response.status,
    );
  },
  createConversationTurn: (
    conversationId: string,
    inputText: string,
    idempotencyKey: string,
    reasoningMode: ReasoningMode,
    signal?: AbortSignal,
  ) =>
    requestJson<TurnAcceptedDto>(
      `/api/v1/workspace/conversations/${conversationId}/turns`,
      {
        method: "POST",
        signal,
        body: JSON.stringify({
          input_text: inputText,
          idempotency_key: idempotencyKey,
          reasoning_mode: reasoningMode,
        }),
      },
    ),
  streamConversationTurn: async (
    conversationId: string,
    inputText: string,
    idempotencyKey: string,
    reasoningMode: ReasoningMode,
    onEvent?: (event: RuntimeStreamEvent, eventType: string) => void,
    signal?: AbortSignal,
  ) => {
    const accepted = await workspaceApi.createConversationTurn(
      conversationId,
      inputText,
      idempotencyKey,
      reasoningMode,
      signal,
    );
    if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    onEvent?.(acceptedEvent(accepted), "turn_accepted");
    return awaitExecution(accepted, onEvent, conversationId, signal);
  },
  reconnectConversationTurn: async (
    conversationId: string,
    executionId: string,
    onEvent?: (event: RuntimeStreamEvent, eventType: string) => void,
    signal?: AbortSignal,
  ) => awaitExecution(
    {
      turn_id: "pending",
      execution_id: executionId,
      status: "accepted",
      status_url: `/api/v1/workspace/turn-executions/${executionId}`,
      events_url: `/api/v1/workspace/turn-executions/${executionId}/events`,
    },
    onEvent,
    conversationId,
    signal,
  ),
  retryConversationTurn: async (
    conversationId: string,
    turnId: string,
    idempotencyKey: string,
    onEvent?: (event: RuntimeStreamEvent, eventType: string) => void,
    signal?: AbortSignal,
  ) => {
    const accepted = await requestJson<TurnAcceptedDto>(
      `/api/v1/workspace/turns/${turnId}/retry`,
      {
        method: "POST",
        signal,
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
      },
    );
    if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    onEvent?.(acceptedEvent(accepted), "turn_accepted");
    return awaitExecution(accepted, onEvent, conversationId, signal);
  },
};

async function awaitExecution(
  accepted: TurnAcceptedDto,
  onEvent?: (event: RuntimeStreamEvent, eventType: string) => void,
  expectedConversationId?: string,
  signal?: AbortSignal,
): Promise<ConversationTurnResult> {
  let lastEventId: string | undefined;
  while (true) {
    lastEventId = await readRuntimeEvents(accepted, lastEventId, onEvent, signal);
    const status = await requestJson<WorkspaceExecutionStatusDto>(
      accepted.status_url,
      { signal },
    );
    if (expectedConversationId && status.conversation_id !== expectedConversationId) {
      throw new Error("Execution does not belong to the requested conversation.");
    }
    if (status.state === "terminal_completed" || status.state === "terminal_failed") {
      const detail = await requestJson<WorkspaceConversationDetailDto>(
        `/api/v1/workspace/conversations/${status.conversation_id}`,
        { signal },
      );
      const projection = detail.turns.find(
        (turn) => turn.execution_id === status.execution_id && turn.turn_id === status.turn_id,
      );
      if (!projection) throw new Error("Terminal execution is missing from conversation projection.");
      return assistantTurn(projection, status.conversation_id);
    }
    await pollDelay(signal);
  }
}

async function readRuntimeEvents(
  accepted: TurnAcceptedDto,
  lastEventId: string | undefined,
  onEvent?: (event: RuntimeStreamEvent, eventType: string) => void,
  signal?: AbortSignal,
): Promise<string | undefined> {
  const response = await fetch(`${API_BASE}${accepted.events_url}`, {
    credentials: "include",
    headers: lastEventId ? { "Last-Event-ID": lastEventId } : undefined,
    signal,
  });
  if (!response.ok) throw await apiError(response);
  if (!response.body) throw new Error("Execution event stream is unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consume = (block: string) => {
    block = block.replace(/\r/g, "");
    const eventType = block.split("\n").find((line) => line.startsWith("event:"))?.slice(6).trim() ?? "message";
    const wireEventId = block.split("\n").find((line) => line.startsWith("id:"))?.slice(3).trim();
    const dataText = block.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n");
    if (!dataText) return;
    const raw = JSON.parse(dataText) as RuntimeStreamEvent;
    const event: RuntimeStreamEvent = {
      ...raw,
      execution_id: raw.execution_id || accepted.execution_id,
      phase: eventType === "reasoning_progressed" && raw.reasoning_phase
        ? raw.reasoning_phase
        : progressPhase(eventType),
      retryable: eventType === "terminal_failed",
    };
    lastEventId = wireEventId || event.event_id || lastEventId;
    onEvent?.(event, eventType);
  };
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary = /\r?\n\r?\n/.exec(buffer);
    while (boundary?.index !== undefined) {
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      consume(block);
      boundary = /\r?\n\r?\n/.exec(buffer);
    }
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  return lastEventId;
}

function acceptedEvent(accepted: TurnAcceptedDto): RuntimeStreamEvent {
  return {
    event_id: `accepted:${accepted.execution_id}`,
    execution_id: accepted.execution_id,
    sequence: 0,
    state: accepted.status,
  };
}

export function conversationSummary(
  value: WorkspaceConversationDto | WorkspaceConversationSummaryDto,
): ConversationListResult["conversations"][number] {
  return {
    ...value,
    reasoning_mode: value.reasoning_mode ?? "standard",
    last_turn_status:
      "last_turn_status" in value ? value.last_turn_status : null,
  };
}

export function conversationDetail(value: WorkspaceConversationDetailDto): ConversationDetail {
  return {
    ...value.conversation,
    reasoning_mode: value.conversation.reasoning_mode ?? "standard",
    turns: value.turns.flatMap((turn) => {
      const assistant = assistantTurn(turn, value.conversation.conversation_id);
      return [
        userTurn(turn, value.conversation.conversation_id),
        { ...assistant, input_text: null },
      ];
    }),
  };
}

function userTurn(turn: WorkspaceTurnProjectionDto, conversationId: string): ConversationDetail["turns"][number] {
  return {
    turn_id: `${turn.turn_id}:user`,
    conversation_id: conversationId,
    role: "user",
    input_text: turn.user_input,
    answer_text: null,
    execution_status: "completed",
    reasoning_mode: turn.reasoning_mode ?? "standard",
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
    user_reason: "workspace.submitted",
    citations: [],
    model_claimed_evidence: [],
    response_segments: [],
    validation_state: "not_applicable",
    used_knowledge_refs: [],
    source_turn_id: null,
    execution_id: turn.execution_id,
    retryable: false,
    runtime_trace_id: turn.execution_id,
    audit_event_ref: null,
    created_at: turn.created_at,
    feedback: null,
  };
}

function assistantTurn(turn: WorkspaceTurnProjectionDto, conversationId: string): ConversationTurnResult {
  const failed = turn.execution_status === "terminal_failed";
  const complete = turn.execution_status === "terminal_completed";
  const evidenceReviewStatus = complete ? turn.evidence_review_status : null;
  const segments = turn.segments.map(responseSegment);
  const citations = [...new Map(turn.citations.map((citation) => [
    citation.citation_ref,
    {
      citation_id: citation.citation_ref,
      document_title: citation.citation_ref,
      locator_label: "Protected citation reference",
      snippet: "",
      viewer_available: true,
    },
  ])).values()];
  return {
    conversation_id: conversationId,
    turn_id: turn.turn_id,
    role: "assistant",
    source_turn_id: turn.turn_id,
    execution_id: turn.execution_id,
    execution_status: failed ? "failed_closed" : complete ? "completed" : "processing",
    reasoning_mode: turn.reasoning_mode ?? "standard",
    reasoning_timeline: turn.reasoning_timeline ?? [],
    response_kind: failed
      ? "refused"
      : complete && segments.length === 0
        ? "unknown"
        : complete
          ? "answer"
          : "unknown",
    verification_status: null,
    evidence_review_status: evidenceReviewStatus,
    evidence_review_reason_codes: complete ? turn.evidence_review_reason_codes : [],
    assessment_state: complete ? turn.assessment_state : null,
    assessment_reason_code: complete ? turn.assessment_reason_code : null,
    assessment_input_digest: complete ? turn.assessment_input_digest : null,
    assessment_output_digest: complete ? turn.assessment_output_digest : null,
    content_state: "available",
    answer_text: joinResponseSegmentMarkdown(segments) || null,
    refusal_code: turn.failure_code,
    user_reason: turn.retrieval_status === "access_denied"
      ? "result.knowledge_scope_access_required"
      : turn.failure_code ?? (
      complete && segments.length === 0
        ? "result.supported_answer_not_established"
        : complete
          ? "result.completed"
          : "workspace.processing"
      ),
    citations,
    model_claimed_evidence: turn.model_claimed_evidence ?? [],
    response_segments: segments,
    validation_state: complete ? "completed" : "not_applicable",
    used_knowledge_refs: [],
    retryable: failed,
    audit_event_ref: null,
    runtime_trace_id: turn.execution_id,
    created_at: turn.created_at,
    feedback: turn.feedback,
  };
}

function responseSegment(
  segment: WorkspaceAnswerSegmentDto,
): ConversationTurnResult["response_segments"][number] {
  return {
    segment_id: segment.segment_id,
    kind: "unknown",
    text: segment.text,
    citation_ids: [],
    external_unverified: false,
    verification_status: "not_applicable",
    verification_reason: "not_applicable",
    claims: [],
  };
}

function progressPhase(eventType: string): RuntimeStreamEvent["phase"] {
  if (eventType === "tool_started" || eventType === "tool_completed") return "executing_tools";
  if (eventType === "governance_started") return "validating_claims";
  if (eventType === "terminal_completed" || eventType === "terminal_failed") return "finalizing";
  if (eventType === "model_action_requested") return "generating";
  return "understanding";
}

async function apiError(response: Response): Promise<ApiError> {
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  return new ApiError(data, response.status);
}

function pollDelay(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new DOMException("The operation was aborted.", "AbortError"));
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, 250);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
