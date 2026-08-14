import { existsSync, readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { conversationDetail, workspaceApi } from "./index";
import type { WorkspaceTurnProjectionDto } from "./types";


afterEach(() => {
  vi.unstubAllGlobals();
});

const readBlobBytes = (blob: Blob) => new Promise<Uint8Array>((resolve, reject) => {
  const reader = new FileReader();
  reader.onerror = () => reject(reader.error ?? new Error("Failed to read Blob bytes"));
  reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
  reader.readAsArrayBuffer(blob);
});


const conversation = {
  conversation_id: "conv-a",
  owner_actor_id: "user-a",
  title: "Question",
  status: "active",
  response_language: "en",
  reasoning_mode: "standard",
  created_at: "2026-07-20T00:00:00+00:00",
  updated_at: "2026-07-20T00:00:01+00:00",
} as const;


const projection: WorkspaceTurnProjectionDto = {
  turn_id: "turn-a",
  execution_id: "execution-a",
  ordinal: 1,
  user_input: "What changed?",
  execution_status: "terminal_completed",
  reasoning_mode: "deep",
  reasoning_timeline: [{
    event_id: "event-reasoning",
    sequence: 2,
    phase: "planning",
    status: "completed",
    cycle: null,
    message_code: "reasoning.planning_completed",
    message_params: { plan_items: 2 },
    created_at: "2026-07-20T00:00:01+00:00",
  }],
  retrieval_status: "evidence_found",
  evidence_review_status: "evidence_aligned",
  evidence_review_reason_codes: ["evidence_aligned"],
  assessment_state: "completed",
  assessment_reason_code: "completed",
  assessment_input_digest: "a".repeat(64),
  assessment_output_digest: "b".repeat(64),
  segments: [{
    segment_id: "segment-a",
    text: "Complete answer",
  }],
  citations: [{ citation_ref: "citation-a", segment_id: "segment-a", claim_id: "claim-a" }],
  model_claimed_evidence: [{
    position: 1,
    handle: "kh_evidence_a",
    resolution_status: "resolved",
    duplicate_of_position: null,
    handle_kind: "evidence",
    evidence_ref: "evidence-a",
    result_ref: "result-a",
    invocation_ordinal: 2,
    document_ref: "document-a",
    document_handle: "kh_document_a",
    lifecycle_epoch: 1,
    document_version_ref: "document-version-a",
    processing_revision_ref: "processing-revision-a",
    processing_generation_ref: "processing-generation-a",
    index_generation_ref: "index-generation-a",
    document_display_name: "Policy.pdf",
    document_version_label: "v1",
    page_number: 2,
    locator_label: "Page 2",
    review_resolution_reason: "resolved",
    protected_open_ref: "declared-evidence-open-a",
  }],
  failure_code: null,
  created_at: "2026-07-20T00:00:01+00:00",
};

const detail = { conversation, turns: [projection] };
const accepted = {
  turn_id: "turn-a",
  execution_id: "execution-a",
  status: "accepted",
  status_url: "/api/v1/workspace/turn-executions/execution-a",
  events_url: "/api/v1/workspace/turn-executions/execution-a/events",
};
const terminalStatus = {
  execution_id: "execution-a",
  turn_id: "turn-a",
  conversation_id: "conv-a",
  state: "terminal_completed",
  version: 8,
  reasoning_mode: "deep",
  reasoning_timeline: projection.reasoning_timeline,
  failure_code: null,
  updated_at: "2026-07-20T00:00:02+00:00",
};

function response(body: unknown, ok = true, status = 200) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok,
    status,
    text: async () => text,
    json: async () => JSON.parse(text || "null"),
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text));
        controller.close();
      },
    }),
  };
}


function terminalReplay() {
  return [
    "id: event-1",
    "event: execution_accepted",
    'data: {"event_id":"event-1","execution_id":"execution-a","sequence":1,"event_type":"execution_accepted","state":"accepted","created_at":"2026-07-20T00:00:00+00:00"}',
    "",
    "id: event-reasoning",
    "event: reasoning_progressed",
    'data: {"event_id":"event-reasoning","execution_id":"execution-a","sequence":2,"event_type":"reasoning_progressed","state":"awaiting_model_action","reasoning_phase":"planning","progress_status":"completed","cycle":null,"message_code":"reasoning.planning_completed","message_params":{"plan_items":2},"created_at":"2026-07-20T00:00:01+00:00"}',
    "",
    "id: event-2",
    "event: terminal_completed",
    'data: {"event_id":"event-2","execution_id":"execution-a","sequence":3,"event_type":"terminal_completed","state":"terminal_completed","created_at":"2026-07-20T00:00:02+00:00"}',
    "",
  ].join("\r\n");
}


describe("workspace execution API contract", () => {
  it("reads citation evidence through the turn-scoped protected route", async () => {
    const evidence = {
      citation_ref: "citation-a",
      locator_label: "Page 2",
      snippet: "Authorized excerpt",
      content: "Authorized evidence content",
      modality: "text" as const,
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(response(evidence));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      workspaceApi.readCitation("conv-a", "turn-a", "citation-a"),
    ).resolves.toEqual(evidence);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workspace/conversations/conv-a/turns/turn-a/citations/citation-a",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("reads declared evidence through the turn-scoped protected route", async () => {
    const evidence = {
      evidence_handle: "kh_evidence_a",
      locator_label: "Page 2",
      snippet: "Authorized excerpt",
      content: "Authorized evidence content",
      modality: "text" as const,
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(response(evidence));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      workspaceApi.readDeclaredEvidence(
        "conv-a",
        "turn-a",
        "declared-evidence-open-a",
      ),
    ).resolves.toEqual(evidence);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workspace/conversations/conv-a/turns/turn-a/declared-evidence/declared-evidence-open-a",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it.each([
    ["application/pdf", "application/pdf"],
    ["image/png; charset=binary", "image/png"],
  ] as const)("reads a supported %s declared-evidence page preview", async (
    contentType,
    mediaType,
  ) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response("page bytes", {
        headers: { "Content-Type": contentType },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const preview = await workspaceApi.readDeclaredEvidencePreview(
      "conv-a",
      "turn-a",
      "declared-evidence-open-a",
    );

    expect(preview.kind).toBe("page");
    if (preview.kind === "page") {
      expect(preview.mediaType).toBe(mediaType);
      expect(preview.blob).toBeInstanceOf(Blob);
      expect(Array.from(await readBlobBytes(preview.blob))).toEqual(
        Array.from(new TextEncoder().encode("page bytes")),
      );
    }
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workspace/conversations/conv-a/turns/turn-a/declared-evidence/declared-evidence-open-a",
      {
        credentials: "include",
        headers: {
          Accept: "application/pdf, image/png, application/json;q=0.5",
        },
      },
    );
  });

  it("reads a JSON declared-evidence excerpt when the content type includes charset", async () => {
    const evidence = {
      evidence_handle: "kh_evidence_a",
      locator_label: "Page 2",
      snippet: "Authorized excerpt",
      content: "Authorized evidence content",
      modality: "text" as const,
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify(evidence), {
        headers: { "Content-Type": "application/json; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(workspaceApi.readDeclaredEvidencePreview(
      "conv-a",
      "turn-a",
      "declared-evidence-open-a",
    )).resolves.toEqual({ kind: "excerpt", evidence });
  });

  it("rejects non-ok and unsupported declared-evidence preview responses", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ error_code: "evidence_access_denied" }),
        {
          status: 403,
          headers: { "Content-Type": "application/json" },
        },
      ))
      .mockResolvedValueOnce(new Response("plain text", {
        headers: { "Content-Type": "text/plain" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(workspaceApi.readDeclaredEvidencePreview(
      "conv-a",
      "turn-a",
      "declared-evidence-open-a",
    )).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      errorCode: "evidence_access_denied",
    });
    await expect(workspaceApi.readDeclaredEvidencePreview(
      "conv-a",
      "turn-a",
      "declared-evidence-open-a",
    )).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      errorCode: "unsupported_declared_evidence_content_type",
    });
  });

  it("submits a turn, reads durable events and status, then maps the nested projection", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(accepted))
      .mockResolvedValueOnce(response(terminalReplay()))
      .mockResolvedValueOnce(response(terminalStatus))
      .mockResolvedValueOnce(response(detail));
    vi.stubGlobal("fetch", fetchMock);
    const observed: Array<[string, string]> = [];
    const controller = new AbortController();

    const result = await workspaceApi.streamConversationTurn(
      "conv-a",
      "What changed?",
      "key-a",
      "deep",
      (event, type) => observed.push([type, event.execution_id]),
      controller.signal,
    );

    expect(result).toMatchObject({
      turn_id: "turn-a",
      source_turn_id: "turn-a",
      execution_id: "execution-a",
      execution_status: "completed",
      response_kind: "answer",
      verification_status: null,
      evidence_review_status: "evidence_aligned",
      answer_text: "Complete answer",
    });
    expect(result.response_segments[0]).toMatchObject({
      verification_status: "not_applicable",
      citation_ids: [],
    });
    expect(result.model_claimed_evidence).toEqual(projection.model_claimed_evidence);
    expect(result.reasoning_mode).toBe("deep");
    expect(result.reasoning_timeline).toEqual(projection.reasoning_timeline);
    expect(observed).toEqual([
      ["turn_accepted", "execution-a"],
      ["execution_accepted", "execution-a"],
      ["reasoning_progressed", "execution-a"],
      ["terminal_completed", "execution-a"],
    ]);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/workspace/conversations/conv-a/turns",
      "/api/v1/workspace/turn-executions/execution-a/events",
      "/api/v1/workspace/turn-executions/execution-a",
      "/api/v1/workspace/conversations/conv-a",
    ]);
    expect(fetchMock.mock.calls.every((call) =>
      call[1]?.signal === controller.signal
    )).toBe(true);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      input_text: "What changed?",
      idempotency_key: "key-a",
      reasoning_mode: "deep",
    });
  });

  it.each([
    { status: "evidence_aligned" as const, citations: [] },
    {
      status: "questionable" as const,
      citations: [{ citation_ref: "citation-a", segment_id: "segment-a", claim_id: "claim-a" }],
    },
  ])("maps authoritative $status without inferring answer kind from citations", ({
    status,
    citations,
  }) => {
    const mapped = conversationDetail({
      conversation,
      turns: [{
        ...projection,
        evidence_review_status: status,
        citations,
      }],
    });
    const answer = mapped.turns[1];
    expect(mapped.turns[0].model_claimed_evidence).toEqual([]);

    expect(answer).toMatchObject({
      execution_status: "completed",
      response_kind: "answer",
      verification_status: null,
      evidence_review_status: status,
      validation_state: "completed",
    });
  });

  it("keeps complete ordered text without promoting soft claims into verified segments", () => {
    const mixedProjection: WorkspaceTurnProjectionDto = {
      ...projection,
      evidence_review_status: "questionable",
      evidence_review_reason_codes: ["answer_item_failed"],
      segments: [
        {
          segment_id: "segment-a",
          text: "Supported claim. Unsupported detail.",
        },
        {
          segment_id: "segment-b",
          text: "Closing context.",
        },
      ],
      citations: [
        { citation_ref: "citation-a", segment_id: "segment-a", claim_id: "claim-a" },
        { citation_ref: "citation-a", segment_id: "segment-a", claim_id: "claim-a" },
      ],
    };

    const answer = conversationDetail({ conversation, turns: [mixedProjection] }).turns[1];

    expect(answer.answer_text).toBe("Supported claim. Unsupported detail.\n\nClosing context.");
    expect(answer.response_kind).toBe("answer");
    expect(answer.evidence_review_status).toBe("questionable");
    expect(answer.response_segments[0].verification_status).toBe("not_applicable");
    expect(answer.response_segments[0].claims).toEqual([]);
    expect(answer.citations.map((citation) => citation.citation_id)).toEqual(["citation-a"]);
    expect(answer.response_segments[0].citation_ids).toEqual([]);
  });

  it("does not synthesize answer status from non-terminal segments or citations", () => {
    const answer = conversationDetail({
      conversation,
      turns: [{
        ...projection,
        execution_status: "governing_result",
        evidence_review_status: "evidence_aligned",
      }],
    }).turns[1];

    expect(answer).toMatchObject({
      execution_status: "processing",
      response_kind: "unknown",
      verification_status: null,
      evidence_review_status: null,
      validation_state: "not_applicable",
    });
  });

  it("reconnects by execution identity and retries from the source turn identity", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(terminalReplay()))
      .mockResolvedValueOnce(response(terminalStatus))
      .mockResolvedValueOnce(response(detail))
      .mockResolvedValueOnce(response(accepted))
      .mockResolvedValueOnce(response(terminalReplay()))
      .mockResolvedValueOnce(response(terminalStatus))
      .mockResolvedValueOnce(response(detail));
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    await workspaceApi.reconnectConversationTurn(
      "conv-a",
      "execution-a",
      undefined,
      controller.signal,
    );
    await workspaceApi.retryConversationTurn(
      "conv-a",
      "turn-a",
      "retry-key",
      undefined,
      controller.signal,
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/workspace/turn-executions/execution-a/events",
    );
    expect(fetchMock.mock.calls.slice(0, 3).every((call) =>
      call[1]?.signal === controller.signal
    )).toBe(true);
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/workspace/turns/turn-a/retry");
    expect(fetchMock.mock.calls.slice(3).every((call) =>
      call[1]?.signal === controller.signal
    )).toBe(true);
    expect(JSON.parse(String(fetchMock.mock.calls[3][1].body))).toEqual({
      idempotency_key: "retry-key",
    });
  });

  it("maps conversation DTOs and sends canonical create-only scope", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ conversations: [conversation] }))
      .mockResolvedValueOnce(response(detail))
      .mockResolvedValueOnce(response(detail))
      .mockResolvedValueOnce(response(detail));
    vi.stubGlobal("fetch", fetchMock);

    const list = await workspaceApi.listWorkspaceConversations();
    const created = await workspaceApi.createWorkspaceConversation(
      "Question",
      "en",
      [],
    );
    await workspaceApi.createWorkspaceConversation(
      "Scoped question",
      "en",
      [
        { tag_type: "team", tag_id: "team-b" },
        { tag_type: "project", tag_id: "project-a" },
      ],
    );
    const loaded = await workspaceApi.getWorkspaceConversation("conv-a");

    expect(list.conversations[0]).not.toHaveProperty("scope_mode");
    expect(list.conversations[0]).not.toHaveProperty("tag_refs");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
      title: "Question",
      response_language: "en",
      tag_refs: [],
    });
    expect(JSON.parse(String(fetchMock.mock.calls[2][1].body))).toEqual({
      title: "Scoped question",
      response_language: "en",
      tag_refs: [
        { tag_type: "project", tag_id: "project-a" },
        { tag_type: "team", tag_id: "team-b" },
      ],
    });
    expect(created).not.toHaveProperty("tag_refs");
    expect(created.turns.map((turn) => turn.role)).toEqual(["user", "assistant"]);
    expect(loaded.turns[1].source_turn_id).toBe("turn-a");
  });

  it("archives a conversation through the explicit retained-data action", async () => {
    const archived = { ...conversation, status: "archived" as const };
    const fetchMock = vi.fn().mockResolvedValueOnce(response({
      conversation: archived,
      audit_event_ref: "audit-conversation-archive",
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      workspaceApi.archiveWorkspaceConversation("conv a", "archive-key"),
    ).resolves.toEqual({
      conversation: archived,
      audit_event_ref: "audit-conversation-archive",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workspace/conversations/conv%20a/archive",
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        body: JSON.stringify({ idempotency_key: "archive-key" }),
      }),
    );
  });

  it("surfaces typed errors from the durable event endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      response({ message_code: "common.rejected", message_params: {} }, false, 409),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      workspaceApi.reconnectConversationTurn("conv-a", "execution-a"),
    ).rejects.toThrow("common.rejected");
  });

  it("maps terminal failure state into the current retryable UI model", async () => {
    const failedProjection = {
      ...projection,
      execution_status: "terminal_failed",
      retrieval_status: null,
      evidence_review_status: null,
      segments: [],
      citations: [],
      failure_code: "execution_carrier_lost",
    } as const;
    const failedStatus = {
      ...terminalStatus,
      state: "terminal_failed",
      failure_code: "execution_carrier_lost",
    } as const;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(accepted))
      .mockResolvedValueOnce(response(terminalReplay().replaceAll("terminal_completed", "terminal_failed")))
      .mockResolvedValueOnce(response(failedStatus))
      .mockResolvedValueOnce(response({ conversation, turns: [failedProjection] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      workspaceApi.streamConversationTurn(
        "conv-a",
        "Question",
        "failed-key",
        "standard",
      ),
    ).resolves.toMatchObject({
      execution_status: "failed_closed",
      response_kind: "refused",
      refusal_code: "execution_carrier_lost",
      retryable: true,
      source_turn_id: "turn-a",
    });
  });
});


describe("workspace feature boundary", () => {
  it("uses the registered public feature without root facades", () => {
    const feature = readFileSync("src/features/workspace/WorkspaceFeature.tsx", "utf8");
    for (const forbidden of [
      'from "../../api"',
      'from "../../types"',
      'from "../../pages',
      'from "../../app',
    ]) {
      expect(feature).not.toContain(forbidden);
    }

    const page = readFileSync("src/components/pages/WorkspacePage.tsx", "utf8");
    expect(page).toContain('from "../../features/workspace/index"');
    for (const forbidden of ["useState", "useEffect", "fetch(", "api."]) {
      expect(page).not.toContain(forbidden);
    }

    expect(existsSync("src/api.ts")).toBe(false);
    expect(existsSync("src/types.ts")).toBe(false);

    const registry = JSON.parse(
      readFileSync("../architecture-boundaries.json", "utf8"),
    );
    const owner = registry.owners.find(
      (entry: { id: string }) => entry.id === "frontend_features",
    );
    expect(owner.public_contracts).toContain(
      "web/src/features/workspace/index.ts",
    );
  });
});
