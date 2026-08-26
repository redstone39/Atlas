import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import {
  adminSession,
  cleanupAppTest,
  memberSession,
  mockApi,
  prepareAppTest,
  readyReadiness,
} from "../App.test-support";
import { sessionQueryClient } from "../shared/session-query-client";
import App from "./atlas-app.test-support";
import { jsonResponse } from "./atlas-app.test-helpers";

const basePath = "/api/v1/admin/audit/agent-research";
const listPayload = {
  items: [
    {
      kind: "accepted",
      research_id: "research-public-001",
      execution_id: "execution-public-001",
      actor_id: "agent-public-001",
      status: "completed",
      output_mode: "evidence_packet_and_answer",
      occurred_at: "2026-08-20T10:00:00Z",
      completed_at: "2026-08-20T10:01:00Z",
    },
    {
      kind: "accepted",
      research_id: "research-public-002",
      execution_id: "execution-public-002",
      actor_id: "agent-public-002",
      status: "completed",
      output_mode: "evidence_packet",
      occurred_at: "2026-08-20T11:00:00Z",
      completed_at: "2026-08-20T11:01:00Z",
    },
    {
      kind: "denied",
      event_id: "event-public-denied-001",
      actor_id: "agent-public-denied",
      message_code: "authorization.scope_denied",
      reason: "Requested project is unavailable.",
      occurred_at: "2026-08-20T12:00:00Z",
    },
  ],
  next_cursor: null,
};

function detailPayload(researchId = "research-public-001", question = "Which public controls are verified?") {
  return {
    research_id: researchId,
    execution_id: researchId.replace("research", "execution"),
    actor_id: "agent-public-001",
    question,
    accepted_scope: {
      scope_ref: "scope-public-001",
      scope_digest: "a".repeat(64),
      project_ids: ["project-public-001"],
      requested_refs: [{ kind: "project", id: "project-public-001" }],
    },
    output_mode: "evidence_packet_and_answer",
    status: "completed",
    packet: {
      packet_digest: "b".repeat(64),
      findings: [{
        finding_id: "finding-public-001",
        text: "The public control is verified by the selected evidence.",
        evidence_ids: ["evidence-public-001"],
        evidence_assessment: "aligned",
      }],
      unresolved_questions: [],
      research_limits: [{ code: "PUBLIC_SCOPE", detail: "Only the selected public project was searched." }],
      evidence: [{
        evidence_id: "evidence-public-001",
        kind: "text",
        title: "Public control record",
        page: 1,
        locator: "page 1",
        available_representations: ["text"],
        lineage_digest: "c".repeat(64),
      }],
    },
    answer: {
      status: "available",
      packet_ref: "packet-public-001",
      packet_digest: "b".repeat(64),
      governed_answer: {
        segments: [{ segment_id: "segment-public-001", text: "The governed public answer." }],
        digest: "d".repeat(64),
      },
      citations: { digest: "e".repeat(64) },
    },
    business_events: [{
      event_id: "event-public-001",
      event_type: "agent_research_completed",
      message_code: "agent.research_completed",
      created_at: "2026-08-20T10:01:00Z",
    }],
    accepted_at: "2026-08-20T10:00:00Z",
    completed_at: "2026-08-20T10:01:00Z",
  };
}

const runtimePayload = {
  research_id: "research-public-001",
  execution_id: "execution-public-001",
  state: "completed",
  version: 4,
  reasoning_mode: "deep",
  failure_code: null,
  budget: {
    tool_invocations: 2,
    catalog_pages: 1,
    document_candidates: 1,
    search_rounds: 1,
    model_visible_items: 1,
    provider_invocations: 1,
    context_tokens: 120,
    tool_tokens: 40,
  },
  events: [{
    event_id: "runtime-event-public-001",
    sequence: 1,
    event_type: "research_packet_committed",
    state: "completed",
    failure_code: null,
    message_code: "agent.research_completed",
    created_at: "2026-08-20T10:01:00Z",
  }],
  events_truncated: false,
  audit_steps: [{
    ordinal: 1,
    step_kind: "retrieval",
    operation: "search_selected_scope",
    status: "completed",
    input_tokens: 20,
    output_tokens: 10,
    evidence_count: 1,
  }],
  created_at: "2026-08-20T10:00:00Z",
  updated_at: "2026-08-20T10:01:00Z",
};

type ResearchHandler = (url: URL) => Promise<Response> | Response;

function installResearchApi(handler: ResearchHandler) {
  const fallback = global.fetch;
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.startsWith(basePath) && (init?.method ?? "GET") === "GET") {
      return handler(url);
    }
    return fallback(input, init);
  });
}

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas public Web: Agent Research Audit", () => {
  it("keeps the audit landing zero-prefetch and opens the research directory on demand", async () => {
    window.history.pushState({}, "", "/admin/audit");
    mockApi(adminSession, readyReadiness);
    installResearchApi(() => jsonResponse(listPayload));
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(screen.getByText("Agent research")).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        new URL(String(input), "http://localhost").pathname.startsWith(basePath),
      ),
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Open Agent research" }));
    expect(window.location.pathname).toBe("/admin/audit/agent-research");
    expect((await screen.findAllByText("agent-public-001")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Requested project is unavailable.")).toHaveLength(2);
    expect(
      vi.mocked(global.fetch).mock.calls.filter(([input]) =>
        new URL(String(input), "http://localhost").pathname === basePath,
      ),
    ).toHaveLength(1);
  });

  it("opens packet-first detail, protected evidence, and the bounded runtime route", async () => {
    window.history.pushState({}, "", `${basePath.replace("/api/v1", "")}/research-public-001`);
    mockApi(adminSession, readyReadiness);
    installResearchApi((url) => {
      if (url.pathname.endsWith("/runtime")) return jsonResponse(runtimePayload);
      if (url.pathname.includes("/evidence/")) {
        return jsonResponse({
          research_id: "research-public-001",
          evidence_id: "evidence-public-001",
          representation: "text",
          media_type: "text/plain",
          text: "Public synthetic evidence content.",
          content_base64: null,
        });
      }
      return jsonResponse(detailPayload());
    });
    render(<App />);

    expect(await screen.findByText("Which public controls are verified?")).toBeInTheDocument();
    expect(screen.getByText("The public control is verified by the selected evidence.")).toBeInTheDocument();
    expect(screen.getByText("The governed public answer.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open text" }));
    expect(await screen.findByText("Public synthetic evidence content.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByText("Public synthetic evidence content.")).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "View runtime trace" }));
    expect(window.location.pathname).toBe(
      "/admin/audit/agent-research/research-public-001/runtime",
    );
    expect(await screen.findByText("Research runtime")).toBeInTheDocument();
    expect(screen.getByText("research_packet_committed")).toBeInTheDocument();
  });

  it("ignores a stale detail response after navigation", async () => {
    window.history.pushState({}, "", "/admin/audit/agent-research");
    mockApi(adminSession, readyReadiness);
    let resolveFirst!: (response: Response) => void;
    const firstDetail = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    installResearchApi((url) => {
      if (url.pathname === basePath) return jsonResponse(listPayload);
      if (url.pathname.endsWith("research-public-001")) return firstDetail;
      return jsonResponse(detailPayload("research-public-002", "Which second public control is verified?"));
    });
    render(<App />);

    const openButtons = await screen.findAllByRole("button", { name: "Open research" });
    fireEvent.click(openButtons[0]);
    await waitFor(() => expect(window.location.pathname).toContain("research-public-001"));
    act(() => window.history.back());
    await waitFor(() => expect(window.location.pathname).toBe("/admin/audit/agent-research"));
    fireEvent.click((await screen.findAllByRole("button", { name: "Open research" }))[1]);
    expect(await screen.findByText("Which second public control is verified?")).toBeInTheDocument();

    await act(async () => {
      resolveFirst(jsonResponse(detailPayload("research-public-001", "Stale public result")));
      await firstDetail;
    });
    expect(screen.queryByText("Stale public result")).not.toBeInTheDocument();
    expect(screen.getByText("Which second public control is verified?")).toBeInTheDocument();
  });

  it("fails closed before protected research fetches for a non-admin", async () => {
    window.history.pushState({}, "", "/admin/audit/agent-research/research-public-001");
    mockApi(memberSession, readyReadiness);
    installResearchApi(() => jsonResponse(detailPayload()));
    render(<App />);

    expect((await screen.findAllByText("Admin access required")).length).toBeGreaterThan(0);
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        new URL(String(input), "http://localhost").pathname.startsWith(basePath),
      ),
    ).toBe(false);
  });

  it("shows a neutral unavailable state for a missing research record", async () => {
    window.history.pushState({}, "", "/admin/audit/agent-research/missing-public");
    mockApi(adminSession, readyReadiness);
    installResearchApi(() => jsonResponse(
      { error_code: "not_found", message_code: "artifact.is_unavailable" },
      404,
    ));
    render(<App />);

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to directory" }));
    expect(window.location.pathname).toBe("/admin/audit/agent-research");
  });
});
