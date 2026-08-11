import { cleanup } from "@testing-library/react";
import { vi } from "vitest";

import { agentList, auditEvents } from "./App.test-agent-fixtures";
import type { RuntimeTraceDetail } from "./features/conversation-audit/index";
import type {
  DirectoryConnectionStatus,
  DirectoryUserCandidate,
} from "./features/directory-administration";
import type { SessionState } from "./features/identity-session/index";
import type {
  AnswerBehaviorStatus,
  ModelRouteStatus,
  ProviderConnectionStatus,
} from "./features/model-routing/index";
import type { ModelRouteRuntimePolicy } from "./features/model-routing/types";
import type { ReadinessState } from "./features/ops/index";
import type {
  ProjectAccessGrant,
  ProjectMemberRole,
} from "./features/project-governance/index";
import type {
  TeamMemberSummary,
  TeamMembershipRecord,
} from "./features/team-administration/index";
import type { UserAdminSummary } from "./features/user-administration/types";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurnResult,
} from "./features/workspace/index";
import i18n, { LANGUAGE_STORAGE_KEY } from "./i18n";
import { THEME_STORAGE_KEY } from "./shared/theme";

export const unauthenticated: SessionState = {
  authenticated: false,
  actor: null,
  available_projects: [],
  system_role: null,
  team_roles: {},
};

export const adminSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-admin-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Atlas Admin",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [],
  system_role: "admin",
  team_roles: {},
};

export const memberSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-engineer-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Engineer One",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [
    {
      project_id: "proj-signal-integrity-alpha",
      name: "Signal Integrity Alpha",
      membership_status: "active",
      role: "viewer",
    },
  ],
  system_role: "user",
  team_roles: {},
};

export const memberWithUnauthorizedProjectSession: SessionState = {
  ...memberSession,
  available_projects: [
    ...memberSession.available_projects,
    {
      project_id: "proj-revoked-lab",
      name: "Revoked Lab",
      membership_status: "revoked",
      role: "viewer",
    },
  ],
};

export const adminWithProjectSession: SessionState = {
  ...adminSession,
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "admin",
    },
    {
      project_id: "proj-signal-integrity-alpha",
      name: "Signal Integrity Alpha",
      membership_status: "active",
      role: "viewer",
    },
  ],
};

export const memberWithoutProjects: SessionState = {
  ...memberSession,
  available_projects: [],
};

export const projectAdminSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-project-admin-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Project Admin",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "admin",
    },
  ],
  system_role: "user",
  team_roles: {},
};

export const projectUploaderSession: SessionState = {
  ...projectAdminSession,
  actor: {
    ...projectAdminSession.actor!,
    actor_id: "user-project-uploader-001",
    display_name: "Project Uploader",
  },
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "contributor",
    },
  ],
};

export const teamAdminSession: SessionState = {
  ...memberWithoutProjects,
  actor: {
    ...memberSession.actor!,
    actor_id: "user-team-admin-001",
    display_name: "Team Admin",
  },
  team_roles: { "team-si": "admin" },
};

export const teamUploaderSession: SessionState = {
  ...memberWithoutProjects,
  actor: {
    ...memberSession.actor!,
    actor_id: "user-team-uploader-001",
    display_name: "Team Uploader",
  },
  team_roles: { "team-si": "uploader", "team-platform": "member" },
};

export const operatorSession: SessionState = {
  ...adminSession,
  actor: {
    ...adminSession.actor!,
    actor_id: "user-operator-001",
    display_name: "Ops Operator",
  },
  system_role: "operator",
};

const controlledText =
  "The synthetic reference target is documented in the example source differential, with tolerance set by the project stackup note.";

export const incompleteReadiness: ReadinessState = {
  ready: false,
  health: "degraded",
  setup_blockers: [
    "ops.create_project",
    "ops.grant_active_project_permission",
    "ops.prepare_searchable_evidence",
    "ops.configure_and_test_model_route",
  ],
  evidence_ready_projects: [],
  message_code: "common.setup_is_incomplete", message_params: {},
};

export const readyReadiness: ReadinessState = {
  ready: true,
  health: "ok",
  setup_blockers: [],
  evidence_ready_projects: ["proj-signal-integrity-alpha"],
  message_code: "workspace.is_ready", message_params: {},
};

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
    schema_version: "atlas-reasoning-trace-v3",
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

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

export function mockApi(
  initialSession: SessionState,
  readiness: ReadinessState,
  options: { modelRoutes?: ModelRouteStatus[] } = {},
) {
  let session = initialSession;
  let processingJobStatus: "failed" | "queued" | "cancelled" = "failed";
  let libraryProcessingJobStatus: "processing" | "queued" | "cancelled" = "processing";
  const workspaceExecutions = new Map<string, ConversationTurnResult>();
  let answerBehavior: AnswerBehaviorStatus = {
    revision: 0,
    custom_guidance: null,
    guidance_digest: null,
    updated_by: null,
    updated_at: null,
    audit_event_ref: null,
  };
  let projectAccessGrants: ProjectAccessGrant[] = [
    {
      grant_id: "grant-project-member-proj-admin-live-user-user-project-admin-001",
      project_id: "proj-admin-live",
      subject_type: "user",
      subject_id: "user-project-admin-001",
      role: "admin",
      effect: "allow",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
      revoked_at: null,
    },
    {
      grant_id: "grant-agent-layout-review-001-signal-integrity-alpha",
      project_id: "proj-signal-integrity-alpha",
      subject_type: "service_account",
      subject_id: "agent-layout-review-001",
      role: "viewer",
      effect: "allow",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
      revoked_at: null,
    },
  ];
  const projectSubjectDirectory: Record<
    string,
    { display_name: string; display_detail: string | null }
  > = {
    "user:user-project-admin-001": {
      display_name: "Project Admin",
      display_detail: "project-admin@example.test",
    },
    "service_account:agent-layout-review-001": {
      display_name: "Layout Review Agent",
      display_detail: null,
    },
    "user:user-engineer-001": {
      display_name: "Engineer One",
      display_detail: "engineer@example.test",
    },
    "user:user-pending-001": {
      display_name: "Invited Engineer",
      display_detail: "pending@example.test",
    },
    "team:team-platform": {
      display_name: "Platform",
      display_detail: null,
    },
    "team:team-si": {
      display_name: "Signal Integrity",
      display_detail: null,
    },
  };
  let directoryConnections: DirectoryConnectionStatus[] = [];
  const directoryCandidates: DirectoryUserCandidate[] = [
    {
      external_subject: "subject-ada",
      username: "ada",
      display_name: "Ada Lovelace",
      email: "ada@example.test",
      groups: ["Research"],
      department: "Engineering",
      title: "Programmer",
      employee_id: "E-100",
      directory_enabled: true,
    },
    {
      external_subject: "subject-grace",
      username: "grace",
      display_name: "Grace Hopper",
      email: "grace@example.test",
      groups: ["Compiler"],
      department: "Engineering",
      title: "Admiral",
      employee_id: "E-101",
      directory_enabled: true,
    },
  ];
  let adminUsers: UserAdminSummary[] = [
    {
      actor_id: "user-admin-001",
      actor_type: "user",
      display_name: "Atlas Admin",
      email: "admin@example.test",
      system_role: "admin",
      active: true,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: null,
      invite_id: null,
      account_source: "local",
      directory_profile: null,
    },
    {
      actor_id: "user-engineer-001",
      actor_type: "user",
      display_name: "Engineer One",
      email: "engineer@example.test",
      system_role: "user",
      active: true,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: "accepted",
      invite_id: "inv-engineer",
      account_source: "local",
      directory_profile: null,
    },
    {
      actor_id: "user-pending-001",
      actor_type: "user",
      display_name: "Invited Engineer",
      email: "pending@example.test",
      system_role: "user",
      active: false,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: "pending",
      invite_id: "inv-pending",
      account_source: "local",
      directory_profile: null,
    },
  ];

  let providerConnections: ProviderConnectionStatus[] = [
    {
      connection_id: "connection-openai-primary",
      display_name: "OpenAI production",
      provider_type: "openai_compatible",
      endpoint_url: "https://api.openai.com/v1",
      api_version: null,
      credential_configured: true,
      status: "verified",
      enabled: true,
      linked_model_count: 2,
      revision: 2,
      last_verified_at: "2026-07-10T01:00:00Z",
      last_rotated_at: "2026-07-10T01:00:00Z",
      message_code: "provider.connection_is_verified", message_params: {},
      audit_event_ref: "audit-provider-connection-primary",
    },
    {
      connection_id: "connection-migrated-required",
      display_name: "Migrated provider",
      provider_type: "openai_compatible",
      endpoint_url: "https://provider.example/v1",
      api_version: null,
      credential_configured: false,
      status: "credential_required",
      enabled: false,
      linked_model_count: 0,
      revision: 1,
      last_verified_at: null,
      last_rotated_at: null,
      message_code: "provider.api_key_is_required", message_params: {},
      audit_event_ref: "audit-provider-connection-migrated",
    },
    {
      connection_id: "connection-manual-entry",
      display_name: "Manual provider",
      provider_type: "azure_openai",
      endpoint_url: "https://example.openai.azure.com",
      api_version: "2024-10-21",
      credential_configured: true,
      status: "configured",
      enabled: false,
      linked_model_count: 0,
      revision: 1,
      last_verified_at: null,
      last_rotated_at: "2026-07-10T01:30:00Z",
      message_code: "model.provider_model_discovery_is_unavailable", message_params: {},
      audit_event_ref: "audit-provider-connection-manual",
    },
  ];
  const runtimePolicy = (
    revision: number,
    overrides: Partial<Omit<ModelRouteRuntimePolicy, "revision">> = {},
  ) => ({
    schema_version: "model-route-runtime-policy-v8" as const,
    tokenizer_profile: "cl100k_base",
    max_tool_executions: 3,
    max_provider_invocations: 20,
    max_reasoning_revision_cycles: 2,
    max_catalog_pages: 5,
    max_search_rounds: 6,
    max_model_visible_items_per_turn: 40,
  max_retrieval_repairs: 3,
  max_schema_retries_per_turn: 3,
  max_selected_anchor_pages_per_round: 20,
    provider_invocation_timeout_seconds: 30,
    tool_execution_timeout_seconds: 20,
    turn_timeout_seconds: 90,
    context_window_tokens: 16_000,
    max_input_tokens_per_invocation: 8_000,
    max_output_tokens_per_invocation: 2_000,
    max_tool_result_tokens_per_execution: 4_000,
    max_total_tokens_per_conversation: 20_000,
    ...overrides,
    revision,
  });
  let modelRoutes: ModelRouteStatus[] = options.modelRoutes ?? [
    {
      route_id: "route-primary-provider",
      display_name: "Primary provider",
      provider_type: "openai_compatible",
      model_name: "gpt-4.1-mini",
      connection_id: "connection-openai-primary",
      status: "test_passed",
      message_code: "model.provider_model_route_passed_the_controlled_test", message_params: {},
      enabled: true,
      supports_vision: false,
      revision: 2,
      runtime_policy: runtimePolicy(2),
      audit_event_ref: "audit-model-route-primary",
      is_default: true,
    },
    {
      route_id: "route-secondary-provider",
      display_name: "Secondary provider",
      provider_type: "openai_compatible",
      model_name: "gpt-4.1",
      connection_id: "connection-openai-primary",
      status: "configured",
      message_code: "model.route_is_configured", message_params: {},
      enabled: true,
      supports_vision: false,
      revision: 1,
      runtime_policy: runtimePolicy(1, {
        tokenizer_profile: "o200k_base",
        max_tool_executions: 2,
        max_provider_invocations: 20,
        max_catalog_pages: 5,
        max_search_rounds: 6,
        max_model_visible_items_per_turn: 40,
  max_retrieval_repairs: 3,
  max_schema_retries_per_turn: 3,
  max_selected_anchor_pages_per_round: 20,
        provider_invocation_timeout_seconds: 45,
        tool_execution_timeout_seconds: 30,
        turn_timeout_seconds: 120,
        context_window_tokens: 32_000,
        max_input_tokens_per_invocation: 24_000,
        max_output_tokens_per_invocation: 4_000,
        max_tool_result_tokens_per_execution: 8_000,
        max_total_tokens_per_conversation: 48_000,
      }),
      audit_event_ref: "audit-model-route-secondary",
      is_default: false,
    },
  ];
  let teamMemberships: TeamMembershipRecord[] = [
    {
      membership_id: "team-member-pending",
      team_id: "team-platform",
      member_actor_type: "user",
      member_actor_id: "user-pending-001",
      role: "member",
      status: "active",
      created_at: "2026-07-08T00:00:00Z",
      removed_at: null,
    },
    {
      membership_id: "team-member-engineer",
      team_id: "team-si",
      member_actor_type: "user",
      member_actor_id: "user-engineer-001",
      role: "member",
      status: "active",
      created_at: "2026-07-08T00:00:00Z",
      removed_at: null,
    },
  ];
  let scopedTeamMembers: TeamMemberSummary[] = [
    {
      membership_id: "tm-team-si-user-team-admin-001",
      team_id: "team-si",
      subject_type: "user",
      subject_id: "user-team-admin-001",
      display_name: "Team Admin",
      display_detail: "team-admin@example.test",
      role: "admin",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
    },
    {
      membership_id: "tm-team-si-agent-layout-review-001",
      team_id: "team-si",
      subject_type: "service_account",
      subject_id: "agent-layout-review-001",
      display_name: "Layout Review Agent",
      display_detail: null,
      role: "member",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
    },
  ];
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    if (url.pathname === "/api/v1/auth/session" && method === "GET") {
      return jsonResponse(session);
    }
    if (url.pathname === "/api/v1/auth/sessions" && method === "POST") {
      session = adminSession;
      return jsonResponse(session);
    }
    if (url.pathname === "/api/v1/auth/session" && method === "DELETE") {
      session = unauthenticated;
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url.pathname === "/api/v1/ops/readiness") {
      return jsonResponse(readiness);
    }
    if (url.pathname === "/api/v1/workspace/tag-scope") {
      const teamLabels: Record<string, string> = {
        "team-platform": "Platform",
        "team-si": "Signal Integrity",
      };
      const teamIds = new Set(["team-platform", ...Object.keys(session.team_roles)]);
      return jsonResponse({
        tags: [
          ...session.available_projects
            .filter((project) => project.membership_status === "active")
            .map((project) => ({
              tag_type: "project",
              tag_id: project.project_id,
              label: project.name,
            })),
          ...[...teamIds].map((teamId) => ({
            tag_type: "team",
            tag_id: teamId,
            label: teamLabels[teamId] ?? teamId,
          })),
        ],
      });
    }
    if (url.pathname === "/api/v1/library/documents" && method === "GET") {
      const hasKnowledgeScope =
        session.system_role === "admin" ||
        session.available_projects.some((project) => project.membership_status === "active") ||
        Object.keys(session.team_roles).length > 0;
      return jsonResponse({
        documents: hasKnowledgeScope
          ? [
              {
                document_id: "doc-member-guide",
                title: "Signal Integrity Guide",
                document_format: "docx",
                description: "Controlled layout guidance available to your current access.",
                authorized_scopes: [
                  {
                    scope_type: "project",
                    scope_id: "proj-signal-integrity-alpha",
                    scope_label: "Signal Integrity Alpha",
                  },
                ],
                source_filename: "signal-integrity-guide.docx",
                source_byte_size: 4096,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: true,
              },
              {
                document_id: "doc-view-only",
                title: "Protected Fabrication Note",
                document_format: "pdf",
                description: null,
                authorized_scopes: [
                  {
                    scope_type: "team",
                    scope_id: "team-si",
                    scope_label: "Signal Integrity",
                  },
                ],
                source_filename: "fabrication-note.pdf",
                source_byte_size: 2048,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: false,
              },
            ]
          : [],
      });
    }
    if (
      url.pathname.match(/^\/api\/v1\/library\/documents\/[^/]+\/content$/) &&
      (method === "GET" || method === "HEAD")
    ) {
      return Promise.resolve(
        new Response(method === "HEAD" ? null : new Blob(["original document"], { type: "application/octet-stream" }), {
          status: 200,
          headers: { "Content-Type": "application/octet-stream" },
        }),
      );
    }
    if (url.pathname === "/api/v1/admin/user-invites" && method === "GET") {
      return jsonResponse({
        invites: [
          {
            invite_id: "inv-engineer",
            actor_id: "user-engineer-001",
            email: "engineer@example.test",
            display_name: "Engineer One",
            system_role: "user",
            status: "pending",
            created_at: "2026-07-08T00:00:00Z",
            expires_at: "2026-07-15T00:00:00Z",
            accepted_at: null,
            revoked_at: null,
          },
          {
            invite_id: "inv-pending",
            actor_id: "user-pending-001",
            email: "pending@example.test",
            display_name: "Invited Engineer",
            system_role: "user",
            status: "pending",
            created_at: "2026-07-08T00:00:00Z",
            expires_at: "2026-07-15T00:00:00Z",
            accepted_at: null,
            revoked_at: null,
          },
        ],
      });
    }
    if (url.pathname === "/api/v1/admin/user-invites" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        request_id: `invite-${body.email ?? "engineer@example.test"}`,
        status: "applied",
        invite: {
          invite_id: "inv-engineer",
          actor_id: "user-invited-project-001",
          email: body.email ?? "engineer@example.test",
          display_name: body.display_name ?? "Engineer One",
          system_role: "user",
          status: "pending",
          created_at: "2026-07-08T00:00:00Z",
          expires_at: "2026-07-15T00:00:00Z",
          accepted_at: null,
          revoked_at: null,
          scope_type: body.scope_type ?? null,
          scope_id: body.scope_id ?? null,
          scope_role: body.scope_role ?? null,
        },
        message_code: "invite.is_ready_copy_the_local_acceptance_link", message_params: {},
        audit_event_ref: "audit-invite-created",
        local_pilot_acceptance: {
          mode: "copy_link",
          acceptance_token: "atlas_invite_visible_once",
          acceptance_url: "/accept-invite?token=atlas_invite_visible_once",
        },
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/user-invites/inv-engineer/revoke" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "revoke-inv-engineer",
        status: "applied",
        target_ref: "invite:inv-engineer",
        message_code: "invite.has_been_revoked", message_params: {},
        audit_event_ref: "audit-invite-revoked",
      });
    }
    if (
      url.pathname === "/api/v1/admin/user-invites/inv-pending/revoke" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "revoke-inv-pending",
        status: "applied",
        target_ref: "invite:inv-pending",
        message_code: "invite.has_been_revoked", message_params: {},
        audit_event_ref: "audit-invite-revoked",
      });
    }
    if (url.pathname === "/api/v1/admin/directory-connections" && method === "GET") {
      return jsonResponse({ connections: directoryConnections });
    }
    if (url.pathname === "/api/v1/admin/directory-connections" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const connection: DirectoryConnectionStatus = {
        connection_id: body.connection_id,
        display_name: body.display_name,
        priority: body.priority,
        provider_type: body.provider_type,
        host: body.host,
        port: body.port,
        tls_mode: body.tls_mode,
        connect_timeout_seconds: body.connect_timeout_seconds,
        operation_timeout_seconds: body.operation_timeout_seconds,
        bind_dn: body.bind_dn,
        user_base_dn: body.user_base_dn,
        user_object_filter: body.user_object_filter,
        login_attribute: body.login_attribute,
        stable_id_attribute: body.stable_id_attribute,
        display_name_attribute: body.display_name_attribute,
        email_attribute: body.email_attribute,
        groups_attribute: body.groups_attribute,
        department_attribute: body.department_attribute,
        title_attribute: body.title_attribute,
        employee_id_attribute: body.employee_id_attribute,
        enabled: body.enabled,
        bind_password_configured: Boolean(body.bind_password),
        custom_ca_configured: Boolean(body.custom_ca_pem),
        custom_ca_sha256: body.custom_ca_pem ? "a".repeat(64) : null,
      };
      directoryConnections = [...directoryConnections, connection];
      return jsonResponse(connection, 201);
    }
    const directoryConnectionMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)$/,
    );
    if (directoryConnectionMatch && method === "PATCH") {
      const connectionId = decodeURIComponent(directoryConnectionMatch[1]);
      const body = JSON.parse(String(init?.body ?? "{}"));
      const current = directoryConnections.find(
        (connection) => connection.connection_id === connectionId,
      )!;
      const updated: DirectoryConnectionStatus = {
        ...current,
        ...body,
        connection_id: connectionId,
        bind_password_configured: body.clear_bind_password
          ? false
          : Boolean(body.bind_password) || current.bind_password_configured,
        custom_ca_configured: body.clear_custom_ca
          ? false
          : Boolean(body.custom_ca_pem) || current.custom_ca_configured,
        custom_ca_sha256: body.clear_custom_ca
          ? null
          : body.custom_ca_pem
            ? "b".repeat(64)
            : current.custom_ca_sha256,
      };
      delete (updated as unknown as Record<string, unknown>).bind_password;
      delete (updated as unknown as Record<string, unknown>).custom_ca_pem;
      directoryConnections = directoryConnections.map((connection) =>
        connection.connection_id === connectionId ? updated : connection,
      );
      return jsonResponse(updated);
    }
    const directoryTestMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/test$/,
    );
    if (directoryTestMatch && method === "POST") {
      return jsonResponse({
        validation_status: "passed",
        message_code: "directory.connection_test_passed",
        message_params: {},
      });
    }
    const directorySearchMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/users\/search$/,
    );
    if (directorySearchMatch && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const query = String(body.query ?? "").toLowerCase();
      return jsonResponse({
        users: directoryCandidates.filter((candidate) =>
          [candidate.display_name, candidate.username, candidate.email ?? ""]
            .join(" ")
            .toLowerCase()
            .includes(query),
        ),
      });
    }
    const directoryImportMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/users\/import$/,
    );
    if (directoryImportMatch && method === "POST") {
      const connectionId = decodeURIComponent(directoryImportMatch[1]);
      const body = JSON.parse(String(init?.body ?? "{}"));
      const selected = directoryCandidates.filter((candidate) =>
        body.external_subjects.includes(candidate.external_subject),
      );
      const connection = directoryConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      )!;
      const importedUsers: UserAdminSummary[] = selected.map((candidate) => ({
        actor_id: `user-directory-${candidate.username}`,
        actor_type: "user",
        display_name: candidate.display_name,
        email: candidate.email,
        system_role: "user",
        active: true,
        created_at: "2026-08-10T00:00:00Z",
        invite_status: null,
        invite_id: null,
        account_source: "directory",
        directory_profile: {
          connection_id: connectionId,
          connection_display_name: connection.display_name,
          username: candidate.username,
          email: candidate.email,
          groups: candidate.groups,
          department: candidate.department,
          title: candidate.title,
          employee_id: candidate.employee_id,
          status: "current",
          last_refreshed_at: "2026-08-10T00:00:00Z",
        },
      }));
      adminUsers = [...adminUsers, ...importedUsers];
      return jsonResponse({
        imported_actor_ids: importedUsers.map((user) => user.actor_id),
        imported_count: importedUsers.length,
        message_code: "directory.users_imported",
        message_params: {},
      });
    }
    const directoryRefreshMatch = url.pathname.match(
      /^\/api\/v1\/admin\/users\/([^/]+)\/directory-profile\/refresh$/,
    );
    if (directoryRefreshMatch && method === "POST") {
      const actorId = decodeURIComponent(directoryRefreshMatch[1]);
      const user = adminUsers.find((candidate) => candidate.actor_id === actorId)!;
      return jsonResponse(user.directory_profile);
    }
    if (url.pathname === "/api/v1/admin/users" && method === "GET") {
      const q = url.searchParams.get("q")?.toLowerCase();
      const source = url.searchParams.get("account_source");
      const active = url.searchParams.get("active");
      const profileStatus = url.searchParams.get("directory_profile_status");
      const connectionId = url.searchParams.get("directory_connection_id");
      const group = url.searchParams.get("directory_group")?.toLowerCase();
      const department = url.searchParams.get("department")?.toLowerCase();
      const title = url.searchParams.get("title")?.toLowerCase();
      const employeeId = url.searchParams.get("employee_id")?.toLowerCase();
      const users = adminUsers.filter((user) => {
        const profile = user.directory_profile;
        const searchable = [
          user.display_name,
          user.email ?? "",
          profile?.username ?? "",
          profile?.email ?? "",
          profile?.department ?? "",
          profile?.title ?? "",
          profile?.employee_id ?? "",
          ...(profile?.groups ?? []),
        ].join(" ").toLowerCase();
        return (
          (!q || searchable.includes(q)) &&
          (!source || user.account_source === source) &&
          (!active || user.active === (active === "true")) &&
          (!profileStatus || profile?.status === profileStatus) &&
          (!connectionId || profile?.connection_id === connectionId) &&
          (!group || profile?.groups.some((value) => value.toLowerCase() === group)) &&
          (!department || profile?.department?.toLowerCase().includes(department)) &&
          (!title || profile?.title?.toLowerCase().includes(title)) &&
          (!employeeId || profile?.employee_id?.toLowerCase().includes(employeeId))
        );
      });
      return jsonResponse({ users });
    }
    if (
      url.pathname === "/api/v1/admin/users/user-engineer-001" &&
      method === "PATCH"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        request_id: "user-engineer-001-update",
        status: "applied",
        target_ref: "user:user-engineer-001",
        message_code: body.active === false ? "identity.user_has_been_removed" : "processing.user_profile_is_updated", message_params: {},
        audit_event_ref: "audit-user-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/users/user-admin-001" &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "user-admin-001-update",
        status: "applied",
        target_ref: "user:user-admin-001",
        message_code: "processing.user_profile_is_updated", message_params: {},
        audit_event_ref: "audit-user-updated",
      });
    }
    if (url.pathname === "/api/v1/auth/invitations/accept" && method === "POST") {
      return jsonResponse({
        request_id: "accept-atlas",
        status: "applied",
        target_ref: "user:user-engineer-001",
        message_code: "invite.accepted_sign_in_with_your_email_and_new_password", message_params: {},
        audit_event_ref: "audit-invite-accepted",
      });
    }
    if (url.pathname === "/api/v1/admin/agent-users" && method === "GET") {
      return jsonResponse({
        agents: agentList.agents.map((agent) => ({
          ...agent,
          project_grants: projectAccessGrants
            .filter(
              (grant) =>
                grant.subject_type === "service_account" &&
                grant.subject_id === agent.actor_id &&
                grant.status === "active",
            )
            .map((grant) => ({
              grant_id: grant.grant_id,
              project_id: grant.project_id,
              role: grant.role,
              effect: grant.effect,
              status: "active" as const,
            })),
        })),
      });
    }
    if (url.pathname === "/api/v1/admin/agent-users" && method === "POST") {
      return jsonResponse({
        request_id: "agent-layout-review",
        status: "applied",
        agent: { ...agentList.agents[0], tokens: [], project_grants: [] },
        message_code: "agent.user_is_ready_for_token_issue", message_params: {},
        audit_event_ref: "audit-0001",
      }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/agent-users/") &&
      !url.pathname.endsWith("/tokens") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "agent-update",
        status: "applied",
        target_ref: "agent:agent-layout-review-001",
        message_code: "agent.user_is_updated", message_params: {},
        audit_event_ref: "audit-agent-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/agent-users/agent-layout-review-001/tokens" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "token-layout-review",
        status: "applied",
        raw_token: "atlas_agent_visible_once",
        token: agentList.agents[0].tokens[0],
        message_code: "agent.token_has_been_issued_copy_it_now", message_params: {},
        audit_event_ref: "audit-0002",
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/agent-tokens/agtok-layout-review" &&
      method === "DELETE"
    ) {
      return jsonResponse({
        request_id: "revoke-agtok-layout-review",
        status: "applied",
        target_ref: "agent-token:agtok-layout-review",
        message_code: "agent.token_has_been_revoked", message_params: {},
        audit_event_ref: "audit-0003",
      });
    }
    if (url.pathname === "/api/v1/admin/audit/events") {
      return jsonResponse(auditEvents);
    }
    if (url.pathname === "/api/v1/admin/conversations" && method === "GET") {
      return jsonResponse({
        conversations: conversationSummaries.map(({ last_turn_status: _status, ...item }) => item),
        next_cursor: null,
      });
    }
    if (url.pathname === "/api/v1/admin/conversations/conv-supported-001" && method === "GET") {
      return jsonResponse(adminDetailDto());
    }
    if (
      url.pathname === "/api/v1/admin/conversations/conv-supported-001/turns/turn-answer-001/runtime" &&
      method === "GET"
    ) {
      return jsonResponse(runtimeTraceDetail);
    }
    if (url.pathname === "/api/v1/admin/projects" && method === "GET") {
      const projects = [
        {
          project_id: "proj-admin-live",
          name: "Admin Live Project",
          policy_profile_id: "policy-default-governed",
        },
        {
          project_id: "proj-signal-integrity-alpha",
          name: "Signal Integrity Alpha",
          policy_profile_id: "policy-default-governed",
        },
      ];
      return jsonResponse({
        projects:
          session.system_role === "admin"
            ? projects
            : projects.filter((project) =>
                session.available_projects.some(
                  (availableProject) =>
                    availableProject.project_id === project.project_id &&
                    availableProject.role === "admin",
                ),
              ),
      });
    }
    const projectMembersMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/members$/,
    );
    if (projectMembersMatch && method === "GET") {
      const grants = projectAccessGrants.filter(
        (grant) => grant.project_id === projectMembersMatch[1],
      );
      return jsonResponse({
        grants,
        subjects: grants.flatMap((grant) => {
          const subject = projectSubjectDirectory[
            `${grant.subject_type}:${grant.subject_id}`
          ];
          return subject
            ? [{
                subject_type: grant.subject_type,
                subject_id: grant.subject_id,
                ...subject,
              }]
            : [];
        }),
      });
    }
    const projectCandidatesMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/member-candidates$/,
    );
    if (projectCandidatesMatch && method === "GET") {
      const projectId = projectCandidatesMatch[1];
      const activeSubjects = new Set(
        projectAccessGrants
          .filter((grant) => grant.project_id === projectId && grant.status === "active")
          .map((grant) => `${grant.subject_type}:${grant.subject_id}`),
      );
      return jsonResponse({
        users: [
          {
            subject_type: "user",
            subject_id: "user-engineer-001",
            display_name: "Engineer One",
            display_detail: "engineer@example.test",
          },
          {
            subject_type: "user",
            subject_id: "user-pending-001",
            display_name: "Invited Engineer",
            display_detail: "pending@example.test",
          },
        ].filter((candidate) => !activeSubjects.has(`${candidate.subject_type}:${candidate.subject_id}`)),
        teams: [
          {
            subject_type: "team",
            subject_id: "team-platform",
            display_name: "Platform",
            display_detail: null,
          },
          {
            subject_type: "team",
            subject_id: "team-si",
            display_name: "Signal Integrity",
            display_detail: null,
          },
        ].filter((candidate) => !activeSubjects.has(`${candidate.subject_type}:${candidate.subject_id}`)),
        service_accounts: [],
      });
    }
    if (projectMembersMatch && method === "POST") {
      const projectId = projectMembersMatch[1];
      const body = JSON.parse(String(init?.body ?? "{}"));
      const subjectType =
        body.subject_type === "team"
          ? "team"
          : body.subject_type === "service_account"
            ? "service_account"
            : "user";
      const role: ProjectMemberRole =
        body.role === "admin" ? "admin" : body.role === "contributor" ? "contributor" : "viewer";
      const grant: ProjectAccessGrant = {
        grant_id: `grant-project-member-${projectId}-${subjectType}-${body.subject_id}`,
        project_id: projectId,
        subject_type: subjectType,
        subject_id: body.subject_id,
        role,
        effect: body.effect === "deny" ? "deny" : "allow",
        status: "active",
        created_at: "2026-07-09T00:00:00Z",
        revoked_at: null,
      };
      projectAccessGrants = [
        ...projectAccessGrants.filter((candidate) => candidate.grant_id !== grant.grant_id),
        grant,
      ];
      return jsonResponse(grant, 201);
    }
    const projectMemberDetailMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/members\/([^/]+)$/,
    );
    if (projectMemberDetailMatch && method === "PATCH") {
      const [, projectId, grantId] = projectMemberDetailMatch;
      const body = JSON.parse(String(init?.body ?? "{}"));
      const role: ProjectMemberRole =
        body.role === "admin" ? "admin" : body.role === "contributor" ? "contributor" : "viewer";
      const effect = body.effect === "deny" ? "deny" : "allow";
      projectAccessGrants = projectAccessGrants.map((grant) =>
        grant.project_id === projectId && grant.grant_id === grantId
          ? { ...grant, role, effect }
          : grant,
      );
      return jsonResponse(
        projectAccessGrants.find(
          (grant) => grant.project_id === projectId && grant.grant_id === grantId,
        ),
      );
    }
    if (projectMemberDetailMatch && method === "DELETE") {
      const [, projectId, grantId] = projectMemberDetailMatch;
      projectAccessGrants = projectAccessGrants.map((grant) =>
        grant.project_id === projectId && grant.grant_id === grantId
          ? { ...grant, status: "revoked", revoked_at: "2026-07-10T00:00:00Z" }
          : grant,
      );
      return jsonResponse(
        projectAccessGrants.find(
          (grant) => grant.project_id === projectId && grant.grant_id === grantId,
        ),
      );
    }
    if (url.pathname === "/api/v1/admin/projects" && method === "POST") {
      return jsonResponse({ status: "applied", message_code: "project.is_ready_for_membership_setup", message_params: {} }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/projects/") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "project-update",
        status: "applied",
        target_ref: "project:proj-admin-live",
        message_code: "project.is_updated", message_params: {},
        audit_event_ref: "audit-project-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/config/provider-connections" &&
      method === "GET"
    ) {
      return jsonResponse({ connections: providerConnections });
    }
    if (url.pathname === "/api/v1/admin/answer-behavior" && method === "GET") {
      return jsonResponse(answerBehavior);
    }
    if (url.pathname === "/api/v1/admin/answer-behavior" && method === "PUT") {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      if (payload.expected_revision !== answerBehavior.revision) {
        return jsonResponse(
          {
            error_code: "revision_conflict",
            message_code: "answer_behavior.revision_changed_before_update",
            message_params: {},
          },
          409,
        );
      }
      const customGuidance =
        typeof payload.custom_guidance === "string"
          ? payload.custom_guidance.trim() || null
          : null;
      answerBehavior = {
        revision: answerBehavior.revision + 1,
        custom_guidance: customGuidance,
        guidance_digest: "a".repeat(64),
        updated_by: "user-admin-001",
        updated_at: "2026-07-27T08:00:00Z",
        audit_event_ref: `audit-answer-behavior-${answerBehavior.revision + 1}`,
      };
      return jsonResponse(answerBehavior);
    }
    if (
      url.pathname === "/api/v1/admin/config/provider-connections" &&
      method === "POST"
    ) {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const connection: ProviderConnectionStatus = {
        connection_id: payload.connection_id,
        display_name: payload.display_name,
        provider_type: payload.provider_type,
        endpoint_url: payload.endpoint_url,
        api_version: payload.api_version ?? null,
        credential_configured: true,
        status: "verified",
        enabled: true,
        linked_model_count: 0,
        revision: 1,
        last_verified_at: "2026-07-10T02:00:00Z",
        last_rotated_at: "2026-07-10T02:00:00Z",
        message_code: "provider.connection_is_verified", message_params: {},
        audit_event_ref: "audit-provider-connection-created",
      };
      providerConnections = [...providerConnections, connection];
      return jsonResponse(connection, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      method === "PATCH"
    ) {
      const connectionId = url.pathname.split("/").at(-1) ?? "";
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const current = providerConnections.find(
        (connection) => connection.connection_id === connectionId,
      )!;
      const updated: ProviderConnectionStatus = {
        ...current,
        display_name: payload.display_name ?? current.display_name,
        endpoint_url: payload.endpoint_url ?? current.endpoint_url,
        api_version:
          payload.api_version === undefined ? current.api_version : payload.api_version,
        credential_configured: current.credential_configured || Boolean(payload.api_key),
        status: payload.api_key ? "verified" : current.status,
        enabled: payload.enabled ?? current.enabled,
        revision: current.revision + 1,
        last_rotated_at: payload.api_key
          ? "2026-07-10T03:00:00Z"
          : current.last_rotated_at,
        message_code: "provider.connection_is_updated", message_params: {},
        audit_event_ref: "audit-provider-connection-updated",
      };
      providerConnections = providerConnections.map((connection) =>
        connection.connection_id === connectionId ? updated : connection,
      );
      return jsonResponse(updated);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      url.pathname.endsWith("/test") &&
      method === "POST"
    ) {
      const connectionId = url.pathname.split("/").at(-2) ?? "";
      const connection = providerConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      )!;
      return jsonResponse({
        connection,
        validation_status: "passed",
        tested_route_ids: modelRoutes
          .filter((route) => route.connection_id === connectionId)
          .map((route) => route.route_id),
        message_code: "provider.connection_test_passed", message_params: {},
        audit_event_ref: "audit-provider-connection-tested",
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      url.pathname.endsWith("/available-models") &&
      method === "GET"
    ) {
      const connectionId = url.pathname.split("/").at(-2) ?? "";
      const connection = providerConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      );
      if (
        !connection?.credential_configured ||
        connectionId === "connection-manual-entry"
      ) {
        return jsonResponse({
          connection_id: connectionId,
          discovery_status: "unavailable",
          models: [],
          message_code: "model.provider_model_discovery_is_unavailable", message_params: {},
        });
      }
      return jsonResponse({
        connection_id: connectionId,
        discovery_status: "available",
        models: ["gpt-4.1", "gpt-4.1-mini"],
        message_code: "model.provider_models_are_available", message_params: {},
      });
    }
    if (url.pathname === "/api/v1/admin/config/model-routes" && method === "GET") {
      return jsonResponse({
        default_route_id:
          modelRoutes.find((route) => route.is_default)?.route_id ?? null,
        routes: modelRoutes,
      });
    }
    if (url.pathname === "/api/v1/admin/config/model-routes" && method === "POST") {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const route: ModelRouteStatus = {
        route_id: payload.route_id,
        display_name: payload.display_name,
        provider_type:
          providerConnections.find(
            (connection) => connection.connection_id === payload.connection_id,
          )?.provider_type ?? "openai_compatible",
        model_name: payload.model_name,
        connection_id: payload.connection_id,
        status: "configured",
        message_code: "model.route_is_configured", message_params: {},
        enabled: payload.enabled,
        supports_vision: payload.supports_vision ?? false,
        revision: 1,
        runtime_policy: { ...payload.runtime_policy, revision: 1 },
        audit_event_ref: "audit-model-route-configured",
        is_default: false,
      };
      modelRoutes = [...modelRoutes, route];
      return jsonResponse(route, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/model-routes/") &&
      !url.pathname.endsWith("/test") &&
      !url.pathname.endsWith("/default") &&
      method === "PATCH"
    ) {
      const routeId = url.pathname.split("/").at(-1) ?? "";
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const current = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      const connectionId = payload.connection_id ?? current.connection_id;
      const updated: ModelRouteStatus = {
        ...current,
        display_name: payload.display_name ?? current.display_name,
        model_name: payload.model_name ?? current.model_name,
        connection_id: connectionId,
        provider_type:
          providerConnections.find(
            (connection) => connection.connection_id === connectionId,
          )?.provider_type ?? current.provider_type,
        enabled: payload.enabled ?? current.enabled,
        supports_vision: payload.supports_vision ?? current.supports_vision,
        revision: current.revision + 1,
        runtime_policy: {
          ...payload.runtime_policy,
          revision: current.runtime_policy.revision + 1,
        },
        message_code: "model.route_is_updated", message_params: {},
        audit_event_ref: "audit-model-route-updated",
      };
      modelRoutes = modelRoutes.map((route) =>
        route.route_id === routeId ? updated : route,
      );
      return jsonResponse(updated);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/model-routes/") &&
      url.pathname.endsWith("/test") &&
      method === "POST"
    ) {
      const routeId = url.pathname.split("/").at(-2) ?? "";
      const route = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      const nextRoute = {
        ...route,
        status: "test_passed" as const,
        revision: route.revision + 1,
        message_code: "model.provider_model_route_passed_the_controlled_test", message_params: {},
        audit_event_ref: "audit-model-route-test-passed",
      };
      modelRoutes = modelRoutes.map((candidate) =>
        candidate.route_id === routeId ? nextRoute : candidate,
      );
      return jsonResponse(nextRoute);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/model-routes/") &&
      url.pathname.endsWith("/default") &&
      method === "POST"
    ) {
      const routeId = url.pathname.split("/").at(-2) ?? "";
      const currentRoute = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      if (currentRoute.status !== "test_passed") {
        return jsonResponse(
          {
            ...currentRoute,
            message_code: "model.test_this_route_before_making_it_default", message_params: {},
            audit_event_ref: "audit-model-route-default-rejected",
          },
          422,
        );
      }
      modelRoutes = modelRoutes.map((route) => ({
        ...route,
        is_default: route.route_id === routeId,
      }));
      const route = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      return jsonResponse({
        ...route,
        message_code: "model.default_model_route_is_updated", message_params: {},
        audit_event_ref: "audit-model-route-default-updated",
      });
    }
    if (url.pathname === "/api/v1/admin/teams" && method === "GET") {
      const allTeams = [
        {
          team_id: "team-platform",
          name: "Platform",
          parent_team_id: null,
          status: "active" as const,
          created_at: "2026-07-08T00:00:00Z",
          inherit_parent_documents: true,
        },
        {
          team_id: "team-si",
          name: "Signal Integrity",
          parent_team_id: "team-platform",
          status: "active" as const,
          created_at: "2026-07-08T00:00:00Z",
          inherit_parent_documents: true,
        },
      ];
      const visibleTeamIds =
        session.system_role === "admin"
          ? new Set(allTeams.map((team) => team.team_id))
          : new Set(
              Object.entries(session.team_roles)
                .filter(([, role]) => role === "admin")
                .map(([teamId]) => teamId),
            );
      if (session.system_role !== "admin" && visibleTeamIds.size === 0) {
        return jsonResponse({ message_code: "team.admin_access_is_required", message_params: {} }, 403);
      }
      return jsonResponse({
        teams: allTeams.filter((team) => visibleTeamIds.has(team.team_id)),
        memberships: teamMemberships.filter(
          (membership) =>
            membership.status === "active" && visibleTeamIds.has(membership.team_id),
        ),
      });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members" &&
      method === "GET"
    ) {
      return jsonResponse({ members: scopedTeamMembers });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/member-candidates" &&
      method === "GET"
    ) {
      const memberIds = new Set(scopedTeamMembers.map((member) => member.subject_id));
      return jsonResponse({
        users: [
          {
            subject_type: "user",
            subject_id: "user-team-candidate-001",
            display_name: "Team Candidate",
            display_detail: "candidate@example.test",
          },
        ].filter((candidate) => !memberIds.has(candidate.subject_id)),
      });
    }
    if (url.pathname === "/api/v1/admin/teams" && method === "POST") {
      return jsonResponse({
        request_id: "team-signal-integrity",
        status: "applied",
        target_ref: "team:team-signal-integrity",
        message_code: "team.is_ready", message_params: {},
        audit_event_ref: "audit-team-created",
      }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/teams/") &&
      !url.pathname.includes("/members") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "team-update",
        status: "applied",
        target_ref: "team:team-si",
        message_code: "team.is_updated", message_params: {},
        audit_event_ref: "audit-team-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members" &&
      method === "POST"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const memberActorId = String(body.member_actor_id ?? "");
      const memberActorType =
        body.member_actor_type === "service_account" ? "service_account" : "user";
      const role = body.role === "uploader" || body.role === "admin" ? body.role : "member";
      if (session.system_role !== "admin") {
        const existing = scopedTeamMembers.find((member) => member.subject_id === memberActorId);
        if (existing) {
          scopedTeamMembers = scopedTeamMembers.map((member) =>
            member.subject_id === memberActorId ? { ...member, role } : member,
          );
        } else {
          scopedTeamMembers = [
            ...scopedTeamMembers,
            {
              membership_id: `tm-team-si-${memberActorId}`,
              team_id: "team-si",
              subject_type: "user",
              subject_id: memberActorId,
              display_name: "Team Candidate",
              display_detail: "candidate@example.test",
              role,
              status: "active",
              created_at: "2026-07-09T00:00:00Z",
            },
          ];
        }
        return jsonResponse({
          request_id: `team-member-${memberActorId}`,
          status: "applied",
          target_ref: `team-membership:tm-team-si-${memberActorId}`,
          message_code: existing ? "team.member_role_is_updated" : "team.member_is_active", message_params: {},
          audit_event_ref: "audit-team-member-scoped",
        }, existing ? 200 : 201);
      }
      const existingMembership = teamMemberships.find(
        (membership) =>
          membership.team_id === "team-si" &&
          membership.member_actor_id === memberActorId,
      );
      if (existingMembership) {
        teamMemberships = teamMemberships.map((membership) =>
          membership.membership_id === existingMembership.membership_id
            ? { ...membership, role, status: "active", removed_at: null }
            : membership,
        );
      } else {
        teamMemberships = [
          ...teamMemberships,
          {
            membership_id: `team-member-team-si-${memberActorId}`,
            team_id: "team-si",
            member_actor_type: memberActorType,
            member_actor_id: memberActorId,
            role,
            status: "active",
            created_at: "2026-07-09T00:00:00Z",
            removed_at: null,
          },
        ];
      }
      return jsonResponse({
        request_id: "team-member-user-engineer-001",
        status: "applied",
        target_ref: "team-membership:team-si:user-engineer-001",
        message_code: "team.member_is_active", message_params: {},
        audit_event_ref: "audit-team-member-added",
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-platform/members" &&
      method === "POST"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const memberActorId = String(body.member_actor_id ?? "");
      const memberActorType =
        body.member_actor_type === "service_account" ? "service_account" : "user";
      const role = body.role === "uploader" || body.role === "admin" ? body.role : "member";
      teamMemberships = [
        ...teamMemberships,
        {
          membership_id: `team-member-team-platform-${memberActorId}`,
          team_id: "team-platform",
          member_actor_type: memberActorType,
          member_actor_id: memberActorId,
          role,
          status: "active",
          created_at: "2026-07-09T00:00:00Z",
          removed_at: null,
        },
      ];
      return jsonResponse({
        request_id: "team-member-user-engineer-001",
        status: "applied",
        target_ref: "team-membership:team-platform:user-engineer-001",
        message_code: "team.member_is_active", message_params: {},
        audit_event_ref: "audit-team-member-added",
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members/team-member-engineer" &&
      method === "DELETE"
    ) {
      teamMemberships = teamMemberships.map((membership) =>
        membership.membership_id === "team-member-engineer"
          ? { ...membership, status: "removed", removed_at: "2026-07-09T00:00:00Z" }
          : membership,
      );
      return jsonResponse({
        request_id: "remove-team-member-engineer",
        status: "applied",
        target_ref: "team-membership:team-member-engineer",
        message_code: "team.member_has_been_removed", message_params: {},
        audit_event_ref: "audit-team-member-removed",
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/teams/team-si/members/tm-team-si-") &&
      method === "DELETE"
    ) {
      const membershipId = url.pathname.split("/").at(-1) ?? "";
      scopedTeamMembers = scopedTeamMembers.filter(
        (member) => member.membership_id !== membershipId,
      );
      return jsonResponse({
        request_id: `remove-${membershipId}`,
        status: "applied",
        target_ref: `team-membership:${membershipId}`,
        message_code: "team.member_has_been_removed", message_params: {},
        audit_event_ref: "audit-team-member-scoped-removed",
      });
    }
    if (url.pathname === "/api/v1/admin/document-library" && method === "GET") {
      const scopeType = url.searchParams.get("scope_type");
      const scopeId = url.searchParams.get("scope_id");
      const documents = [
        {
          document_id: "doc-team-uploader-owned",
          title: "Uploader-owned Team note",
          description: "Maintained by the signed-in uploader.",
          intake_status: libraryProcessingJobStatus,
          document_format: "docx",
          profile_id: "default-office",
          profile_revision: 1,
          current_stage: libraryProcessingJobStatus === "queued" ? "queued" : "parsing",
          warning_codes: ["office_preview_unavailable"],
          failure_code: null,
          job_id: "job-team-uploader-owned",
          lifecycle_status: "active",
          uploader_actor_id: "user-team-uploader-001",
          scope_type: "team",
          scope_id: "team-si",
          direct_tags: [
            { tag_type: "team", tag_id: "team-si", label: "Signal Integrity" },
            { tag_type: "project", tag_id: "proj-admin-live", label: "Admin Live Project" },
          ],
          allow_member_download: false,
          download_available:
            session.system_role === "admin" ||
            session.team_roles["team-si"] === "admin",
          source_filename: "uploader-note.docx",
          source_byte_size: 2048,
          content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          raw_sha256: "sha256:test-only",
          uploaded_at: "2026-07-09T00:00:00Z",
          disabled_at: null,
          restored_at: null,
          evidence_count: 3,
        },
        {
          document_id: "doc-project-uploader-owned",
          title: "Uploader-owned Project note",
          description: "Maintained in an authorized Project.",
          intake_status: "registered",
          document_format: "pdf",
          profile_id: null,
          profile_revision: null,
          current_stage: null,
          warning_codes: [],
          failure_code: null,
          job_id: null,
          lifecycle_status: "active",
          uploader_actor_id: "user-project-uploader-001",
          scope_type: "project",
          scope_id: "proj-admin-live",
          direct_tags: [
            { tag_type: "project", tag_id: "proj-admin-live", label: "Admin Live Project" },
          ],
          allow_member_download: false,
          download_available:
            session.system_role === "admin" ||
            session.available_projects.some(
              (project) =>
                project.project_id === "proj-admin-live" &&
                project.membership_status === "active" &&
                project.role === "admin",
            ),
          source_filename: "project-uploader-note.pdf",
          source_byte_size: 3072,
          content_type: "application/pdf",
          raw_sha256: "sha256:test-only",
          uploaded_at: "2026-07-09T00:00:00Z",
          disabled_at: null,
          restored_at: null,
          evidence_count: 0,
        },
        ...(session.system_role === "admin"
          ? [
              {
                document_id: "doc-team-disabled",
                title: "Disabled Team note",
                description: "Disabled document fixture.",
                intake_status: "ready",
                document_format: "pdf",
                profile_id: "default-pdf",
                profile_revision: 1,
                current_stage: "completed",
                warning_codes: [],
                failure_code: null,
                job_id: "job-team-disabled",
                lifecycle_status: "disabled",
                uploader_actor_id: "user-admin-001",
                scope_type: "team",
                scope_id: "team-si",
                direct_tags: [
                  { tag_type: "team", tag_id: "team-si", label: "Signal Integrity" },
                ],
                allow_member_download: false,
                download_available: false,
                source_filename: "disabled-note.pdf",
                source_byte_size: 1024,
                content_type: "application/pdf",
                raw_sha256: "sha256:disabled-test-only",
                uploaded_at: "2026-07-09T00:00:00Z",
                disabled_at: "2026-07-10T00:00:00Z",
                restored_at: null,
                evidence_count: 1,
              },
            ]
          : []),
      ];
      return jsonResponse({
        documents: documents.filter(
          (document) =>
            (!scopeType || !scopeId || document.direct_tags.some(
              (tag) => tag.tag_type === scopeType && tag.tag_id === scopeId,
            )),
        ),
      });
    }
    if (url.pathname === "/api/v1/admin/document-library" && method === "POST") {
      return jsonResponse(
        {
          request_id: "doclib-upload-test",
          status: "applied",
          target_ref: "document:doc-uploaded-test",
          message_code: "document.upload_is_accepted_for_asynchronous_processing", message_params: {},
          audit_event_ref: "audit-doclib-upload-test",
          document: null,
        },
        201,
      );
    }
    if (
      url.pathname.startsWith("/api/v1/admin/document-library/") &&
      url.pathname.endsWith("/events") &&
      method === "GET"
    ) {
      return jsonResponse({
        events: [
          {
            event_id: "audit-doclib-event-test",
            event_type: "document_library_uploaded",
            actor_id: "user-admin-001",
            target_ref: "document:doc-team-uploader-owned",
            project_id: null,
            scope_type: "team",
            scope_id: "team-si",
            document_id: "doc-team-uploader-owned",
            message_code: "document.upload_is_accepted_for_asynchronous_processing", message_params: {},
            metadata: {},
            created_at: "2026-07-10T00:00:00Z",
          },
        ],
      });
    }
    if (url.pathname.startsWith("/api/v1/admin/document-library/") && method === "PATCH") {
      return jsonResponse({
        request_id: "doclib-update-test",
        status: "applied",
        target_ref: "document:doc-team-uploader-owned",
        message_code: "document.settings_are_updated", message_params: {},
        audit_event_ref: "audit-doclib-update-test",
        document: null,
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/document-library/") &&
      ["refresh-searchable-content", "disable", "restore"].some((action) =>
        url.pathname.endsWith(`/${action}`),
      ) &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "doclib-action-test",
        status: "applied",
        target_ref: "document:doc-team-uploader-owned",
        message_code: "document.settings_are_updated", message_params: {},
        audit_event_ref: "audit-doclib-action-test",
      });
    }
    if (url.pathname === "/api/v1/admin/processing-runs" && method === "GET") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/processing/jobs" && method === "GET") {
      return jsonResponse({
        jobs: [
          {
            document_id: "doc-team-uploader-owned",
            document_format: "docx",
            profile_id: "default-office",
            profile_revision: 1,
            current_stage: libraryProcessingJobStatus === "queued" ? "queued" : "parsing",
            warning_codes: ["office_preview_unavailable"],
            failure_code: null,
            job_id: "job-team-uploader-owned",
            status: libraryProcessingJobStatus,
            status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
            retry_available: libraryProcessingJobStatus === "cancelled",
            cancel_available: libraryProcessingJobStatus !== "cancelled",
            review_available: true,
            progress_current: 3,
            progress_total: 10,
            progress_unit: "page",
            elapsed_seconds: 45,
            attempt_started_at: "2026-07-15T00:00:00Z",
            is_current: true,
            created_at: "2026-07-15T00:00:00Z",
            updated_at: "2026-07-15T00:00:45Z",
          },
          {
            document_id: "doc-failed-office-handbook",
            document_format: "docx",
            profile_id: "default-office",
            profile_revision: 1,
            current_stage: processingJobStatus === "queued" ? "queued" : "failed",
            warning_codes: [],
            failure_code: processingJobStatus === "failed" ? "no_searchable_evidence" : null,
            job_id: "job-failed-office-handbook",
            status: processingJobStatus,
            status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
            retry_available: processingJobStatus !== "queued",
            cancel_available: processingJobStatus === "queued",
            review_available: true,
            progress_current: 0,
            progress_total: 12,
            progress_unit: "page",
            elapsed_seconds: 65,
            attempt_started_at: "2026-07-15T00:00:00Z",
            is_current: true,
            created_at: "2026-07-15T00:00:00Z",
            updated_at: "2026-07-15T00:00:00Z",
          },
        ],
      });
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-team-uploader-owned/retry" &&
      method === "POST"
    ) {
      libraryProcessingJobStatus = "queued";
      return jsonResponse({
        document_id: "doc-team-uploader-owned",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "queued",
        warning_codes: ["office_preview_unavailable"],
        failure_code: null,
        job_id: "job-team-uploader-owned",
        status: "queued",
        status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
        retry_available: false,
        cancel_available: true,
        review_available: true,
        progress_current: 3,
        progress_total: 10,
        progress_unit: "page",
        elapsed_seconds: 0,
        attempt_started_at: "2026-07-15T00:01:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:01:00Z",
      }, 202);
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-team-uploader-owned/cancel" &&
      method === "POST"
    ) {
      libraryProcessingJobStatus = "cancelled";
      return jsonResponse({
        document_id: "doc-team-uploader-owned",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "parsing",
        warning_codes: ["office_preview_unavailable"],
        failure_code: null,
        job_id: "job-team-uploader-owned",
        status: "cancelled",
        status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
        retry_available: true,
        cancel_available: false,
        review_available: true,
        progress_current: 3,
        progress_total: 10,
        progress_unit: "page",
        elapsed_seconds: 47,
        attempt_started_at: "2026-07-15T00:00:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:00:47Z",
      });
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-failed-office-handbook/retry" &&
      method === "POST"
    ) {
      processingJobStatus = "queued";
      return jsonResponse(
        {
          document_id: "doc-failed-office-handbook",
          document_format: "docx",
          profile_id: "default-office",
          profile_revision: 1,
          current_stage: "queued",
          warning_codes: [],
          failure_code: null,
          job_id: "job-failed-office-handbook",
          status: "queued",
          status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
          retry_available: false,
          cancel_available: true,
          review_available: true,
          progress_current: 0,
          progress_total: 12,
          progress_unit: "page",
          elapsed_seconds: 0,
          attempt_started_at: "2026-07-15T00:01:00Z",
          is_current: true,
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:01:00Z",
        },
        202,
      );
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-failed-office-handbook/cancel" &&
      method === "POST"
    ) {
      processingJobStatus = "cancelled";
      return jsonResponse({
        document_id: "doc-failed-office-handbook",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "queued",
        warning_codes: [],
        failure_code: null,
        job_id: "job-failed-office-handbook",
        status: "cancelled",
        status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
        retry_available: true,
        cancel_available: false,
        review_available: true,
        progress_current: 0,
        progress_total: 12,
        progress_unit: "page",
        elapsed_seconds: 1,
        attempt_started_at: "2026-07-15T00:01:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:01:01Z",
      });
    }
    if (url.pathname === "/api/v1/admin/processing-plugins" && method === "GET") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/admin/processing-profiles" && method === "GET") {
      return jsonResponse({ items: [] });
    }
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
      return jsonResponse(workspaceDetailDto(latest));
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
    return jsonResponse({ message_code: "common.rejected", message_params: {} }, 404);
  });
}

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

export async function prepareAppTest() {
  window.innerWidth = 1024;
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: 1024,
  });
  vi.spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  if (!window.HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  }
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:atlas-test"),
    });
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  }
  if (typeof window.localStorage.setItem === "function") {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  }
  document.documentElement.classList.remove("dark");
  document.documentElement.classList.add("light");
  await i18n.changeLanguage("en");
  window.history.pushState({}, "", "/login");
}

export function cleanupAppTest() {
  cleanup();
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  document.documentElement.classList.remove("light", "dark");
  vi.useRealTimers();
  vi.restoreAllMocks();
}
