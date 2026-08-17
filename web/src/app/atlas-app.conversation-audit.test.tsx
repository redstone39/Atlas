import "@testing-library/jest-dom/vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  StrictMode,
} from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import App from "./atlas-app.test-support";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminSession,
  adminDetailDto,
  answeredTurn,
  conversationDetail,
  cleanupAppTest,
  memberSession,
  mockApi,
  prepareAppTest,
  readyReadiness,
  runtimeTraceDetail,
} from "../App.test-support";
import {
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: conversation-audit", () => {
it("/admin/audit shows safe agent management events without raw token values", async () => {
    window.history.pushState({}, "", "/admin/audit/events");
    mockApi(adminSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect((await screen.findAllByText("Operation history")).length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "Open conversation history" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(await screen.findByText("agent_token_issued")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/audit/events",
      expect.any(Object),
    );
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/admin/conversations"),
      ),
    ).toBe(false);
    expect(screen.getByRole("columnheader", { name: "Time" })).toBeInTheDocument();
    const eventTime = container.querySelector('time[datetime="2026-07-08T00:00:00+00:00"]');
    expect(eventTime).toBeInTheDocument();
    expect(eventTime).not.toHaveTextContent(/^\s*$/);
    expect(screen.queryByText("Example conversation")).not.toBeInTheDocument();
    expect(await screen.findByText("abc123def456")).toBeInTheDocument();
    expect(screen.queryByText("atlas_agent_visible_once")).not.toBeInTheDocument();
  });

it("/admin/audit is a zero-prefetch record type landing", async () => {
    window.history.pushState({}, "", "/admin/audit");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(await screen.findByText("Conversation history")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Review conversations and open runtime details when needed.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Operation history")).toBeInTheDocument();
    expect(screen.getByText("Review recent management activity.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open conversation history" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open operation history" }),
    ).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/admin/conversations"),
      ),
    ).toBe(false);
    expect(
      vi.mocked(global.fetch).mock.calls.some(
        ([input]) => String(input) === "/api/v1/admin/audit/events",
      ),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "Open conversation history" }),
    );
    expect(window.location.pathname).toBe("/admin/audit/conversations");
    expect(
      await screen.findByRole("button", { name: /Example conversation/ }),
    ).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/admin/conversations"),
      ),
    ).toBe(true);
    expect(
      vi.mocked(global.fetch).mock.calls.some(
        ([input]) => String(input) === "/api/v1/admin/audit/events",
      ),
    ).toBe(false);
  });

it("fails closed before protected audit fetches for a non-admin detail route", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect((await screen.findAllByText("Admin access required")).length).toBeGreaterThan(0);
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/admin/conversations"),
      ),
    ).toBe(false);
  });

it("returns from a conversation breadcrumb to the audited directory", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    render(<App />);

    await screen.findByText(
      "What is the approved value for the selected item?",
    );
    fireEvent.click(screen.getByRole("link", { name: "Conversation history" }));
    expect(await screen.findByRole("button", { name: /Example conversation/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/admin/audit/conversations");
  });

it("restores the audit collection and transcript through browser history", async () => {
    window.history.pushState({}, "", "/admin/audit");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Open conversation history" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Example conversation/ }),
    );
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();

    act(() => window.history.back());
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/audit/conversations"),
    );
    expect(
      await screen.findByRole("button", { name: /Example conversation/ }),
    ).toBeInTheDocument();

    const protectedFetchCount = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input).startsWith("/api/v1/admin/conversations") ||
        String(input) === "/api/v1/admin/audit/events",
    ).length;
    act(() => window.history.back());
    await waitFor(() => expect(window.location.pathname).toBe("/admin/audit"));
    expect(
      await screen.findByRole("button", { name: "Open conversation history" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open operation history" }),
    ).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input]) =>
          String(input).startsWith("/api/v1/admin/conversations") ||
          String(input) === "/api/v1/admin/audit/events",
      ),
    ).toHaveLength(protectedFetchCount);

    act(() => window.history.forward());
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/audit/conversations"),
    );
    expect(
      await screen.findByRole("button", { name: /Example conversation/ }),
    ).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/admin/audit/conversations/conv-supported-001/transcript",
      ),
    );
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();
  });

it("shows a neutral unavailable state for a missing audit conversation", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/missing-conversation/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/conversations/missing-conversation") {
        return jsonResponse(
          { error_code: "not_found", message_code: "artifact.is_unavailable" },
          404,
        );
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to directory" }));
    expect(await screen.findByRole("button", { name: /Example conversation/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/admin/audit/conversations");
  });

it("uses mobile cards for audit conversation collections", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("button", {
        name: "Open conversation Example conversation",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

it("uses mobile cards for audit operation collections", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/audit/events");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText("agent_token_issued")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

it("/admin/audit does not duplicate protected reads under StrictMode", async () => {
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    expect(await screen.findByRole("button", { name: /Example conversation/ })).toBeInTheDocument();
    await waitFor(() => {
      const calls = vi.mocked(global.fetch).mock.calls.map(([input]) => {
        return new URL(String(input), "http://localhost").pathname;
      });
      expect(
        calls.filter((path) => path === "/api/v1/admin/conversations"),
      ).toHaveLength(1);
      expect(calls).not.toContain("/api/v1/admin/conversations/conv-supported-001");
    });
  });

it("/admin/audit does not duplicate direct detail reads under StrictMode", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await screen.findByText(
      "What is the approved value for the selected item?",
    );
    const detailCalls = vi.mocked(global.fetch).mock.calls.filter(([input]) => {
      return new URL(String(input), "http://localhost").pathname ===
        "/api/v1/admin/conversations/conv-supported-001";
    });
    expect(detailCalls).toHaveLength(1);
  });

it("/admin/audit does not duplicate operation reads under StrictMode", async () => {
    window.history.pushState({}, "", "/admin/audit/events");
    mockApi(adminSession, readyReadiness);
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await screen.findByText("agent_token_issued");
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input]) => String(input) === "/api/v1/admin/audit/events",
      ),
    ).toHaveLength(1);
  });

it("/admin/audit shows global conversation history with a bounded runtime trace", async () => {
    // acceptance-scenario:SYS-08
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const { container, unmount } = render(<App />);

    expect((await screen.findAllByText("Example conversation")).length).toBeGreaterThan(0);
    expect(container.querySelector('[data-slot="audit-conversation-layout"]')).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    const transcript = container.querySelector('[data-slot="audit-transcript"]');
    expect(transcript).toHaveClass("grid", "gap-4");
    expect(transcript?.querySelector('[data-slot="message-scroller"]')).not.toBeInTheDocument();
    const userMessage = screen.getByText(
      "What is the approved value for the selected item?",
    );
    expect(userMessage.closest('[data-slot="message"]')).toHaveAttribute("data-align", "end");
    expect(
      screen
        .getByText(
          "A synthetic document-backed statement.",
        )
        .closest('[data-slot="message"]'),
    ).toHaveAttribute("data-align", "start");
    fireEvent.click(await screen.findByRole("button", { name: "View runtime trace" }));

    expect(
      await screen.findByRole("button", { name: "Back to conversation" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(await screen.findByText("Runtime budget")).toBeInTheDocument();
    const itemDiagnostic = container.querySelector(
      '[data-slot="model-visible-item-diagnostic"]',
    );
    expect(itemDiagnostic).toHaveTextContent("Model-visible item total");
    expect(itemDiagnostic).toHaveTextContent("24");
    expect(itemDiagnostic).toHaveTextContent("37");
    expect(itemDiagnostic).toHaveTextContent("Within limit");
    expect(screen.getByText("Atlas structured reasoning record")).toBeInTheDocument();
    expect(screen.getByText(/not accuracy, confidence, or factual guarantees/i))
      .toBeInTheDocument();
    expect(screen.getByText("provider_unavailable")).toBeInTheDocument();
    expect(screen.getByText("Plan generation 2")).toBeInTheDocument();
    expect(screen.getByText("Provisional declared-evidence checks")).toBeInTheDocument();
    expect(screen.getByText(/Check 1 · normal · insufficient · revised/)).toBeInTheDocument();
    expect(screen.getByText(/Evaluation 1 · reason declared_evidence_insufficient/)).toBeInTheDocument();
    expect(screen.getAllByText(/research_then_revise/).length).toBeGreaterThan(0);
    expect(screen.getByText(/tool ordinals 2–3/)).toBeInTheDocument();
    const activity = screen.getByRole("region", {
      name: "Model and tool activity",
    });
    const activityRows = within(activity).getAllByRole("row").slice(1);
    expect(activityRows).toHaveLength(3);
    expect(activityRows[0]).toHaveTextContent(
      /1.*Model decision.*search_knowledge.*completed/,
    );
    expect(activityRows[1]).toHaveTextContent(
      /2.*Tool use.*search_knowledge.*completed.*result-discovery-001/,
    );
    expect(activityRows[2]).toHaveTextContent(
      /3.*Model decision.*finalize_answer.*completed.*result-answer-001/,
    );
    expect(activity).toHaveTextContent(
      "Safe action records identify selected actions and tool use; they are not model chain-of-thought.",
    );
    expect(activity).not.toHaveTextContent("8".repeat(64));
    expect(activity).not.toHaveTextContent("9".repeat(64));
    expect(activity).not.toHaveTextContent("0".repeat(64));
    expect(screen.queryByText(/accuracy score/i)).not.toBeInTheDocument();
    expect(screen.getByText("Answer guidance revision").parentElement).toHaveTextContent("3");
    expect(screen.getByText("Answer guidance digest")).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("Document content discovery path")).toBeInTheDocument();
    expect(screen.getByText("example policy")).toBeInTheDocument();
    const discoveryDocument = screen.getByText("Example Document.pdf");
    const discoveryTable = discoveryDocument.closest('[data-slot="table"]');
    expect(discoveryTable).toHaveClass("min-w-[56rem]", "table-fixed");
    expect(screen.queryByText("Retention is seven years.")).not.toBeInTheDocument();
    fireEvent.click(
      within(discoveryDocument.closest('[data-slot="table-cell"]') as HTMLElement)
        .getByRole("button", { name: "Preview" }),
    );
    const discoveryPreviewDialog = await screen.findByRole("dialog", {
      name: "Candidate preview",
    });
    expect(within(discoveryPreviewDialog).getByText("Retention is seven years."))
      .toBeInTheDocument();
    expect(within(discoveryPreviewDialog).getByText("Example Document.pdf · p. 4"))
      .toBeInTheDocument();
    expect(screen.getByText("kh_document_revoked")).toBeInTheDocument();
    expect(screen.getByText("access_required")).toBeInTheDocument();
    expect(await screen.findByText("Durable runtime events")).toBeInTheDocument();
    expect(await screen.findByText("exec-answer-001")).toBeInTheDocument();
    expect(await screen.findByText("execution_allocated")).toBeInTheDocument();
    expect((await screen.findAllByText("terminal_completed")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("result-answer-001")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Redacted payloads")).not.toBeInTheDocument();
    expect(screen.queryByText(/provider.example/)).not.toBeInTheDocument();
    expect(screen.queryByText(/redacted prompt preview/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export" })).not.toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).includes("/export"),
      ),
    ).toBe(false);
    unmount();
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    const populatedRuntimeFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({ ...runtimeTraceDetail, audit_steps: [] });
      }
      return populatedRuntimeFetch(input, init);
    });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "View runtime trace" }),
    );
    expect(
      await screen.findByText(
        "No model or tool action records are available for this execution.",
      ),
    ).toBeInTheDocument();
  });

it("/admin/audit shows ordered safe actions for a Standard completed runtime", async () => {
    // acceptance-scenario:SYS-08
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const defaultFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({
          ...runtimeTraceDetail,
          reasoning_mode: "standard",
          reasoning_trace: null,
        });
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "View runtime trace" }),
    );

    const activity = await screen.findByRole("region", {
      name: "Model and tool activity",
    });
    const rows = within(activity).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(/Model decision.*search_knowledge/);
    expect(rows[1]).toHaveTextContent(/Tool use.*search_knowledge/);
    expect(rows[2]).toHaveTextContent(/Model decision.*finalize_answer/);
    expect(screen.getByText("standard")).toBeInTheDocument();
    expect(screen.queryByText("Atlas structured reasoning record"))
      .not.toBeInTheDocument();
    expect(activity).not.toHaveTextContent("8".repeat(64));
    expect(activity).not.toHaveTextContent("9".repeat(64));
    expect(screen.queryByText(/provider.example/)).not.toBeInTheDocument();
    expect(screen.queryByText(/redacted prompt preview/)).not.toBeInTheDocument();
  });

it("/admin/audit highlights model-visible item overflow using the execution-fixed limit", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({
          ...runtimeTraceDetail,
          model_visible_item_count: 44,
          model_visible_item_limit: 37,
          model_visible_item_exceeded: true,
        });
      }
      return normalFetch(input, init);
    });
    const { container } = render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "View runtime trace" }));

    const itemDiagnostic = await waitFor(() => {
      const diagnostic = container.querySelector(
        '[data-slot="model-visible-item-diagnostic"]',
      );
      expect(diagnostic).toBeInTheDocument();
      return diagnostic;
    });
    expect(itemDiagnostic).toHaveTextContent("44");
    expect(itemDiagnostic).toHaveTextContent("37");
    expect(itemDiagnostic).toHaveTextContent("Exceeded");
  });

it("/admin/audit renders assistant answers as one Markdown document", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const defaultFetch = global.fetch;
    const markdown = [
      "## Audit result",
      "",
      "- API is healthy",
      "- Web is healthy",
      "",
      "| Service | Status |",
      "| --- | --- |",
      "| Atlas | Ready |",
    ].join("\n");
    const markdownAssistant = {
      ...answeredTurn,
      answer_text: markdown,
      response_segments: [{
        ...answeredTurn.response_segments[0],
        text: markdown,
      }],
    };
    const detail = {
      ...conversationDetail,
      turns: conversationDetail.turns.map((turn) =>
        turn.role === "assistant"
          ? { ...markdownAssistant, role: "assistant" as const, input_text: null }
          : turn,
      ),
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(adminDetailDto(detail));
      }
      return defaultFetch(input, init);
    });
    const { container } = render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Audit result", level: 2 }),
    ).toBeInTheDocument();
    const transcript = container.querySelector('[data-slot="audit-transcript"]') as HTMLElement;
    expect(within(transcript).getByRole("list")).toBeInTheDocument();
    expect(within(transcript).getByRole("table")).toBeInTheDocument();
    expect(within(transcript).getByRole("cell", { name: "Ready" })).toBeInTheDocument();
  });

it("/admin/audit presents the same model-claimed evidence trace", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const defaultFetch = global.fetch;
    const claimedAssistant = {
      ...answeredTurn,
      model_claimed_evidence: [
        {
          position: 1,
          handle: "kh_visual_admin_claim",
          resolution_status: "resolved" as const,
          duplicate_of_position: null,
          handle_kind: "visual" as const,
          evidence_ref: "visual-evidence-admin-001",
          result_ref: "result-admin-001",
          invocation_ordinal: 2,
          document_ref: "document-admin-001",
          document_handle: "kh_document_admin",
          lifecycle_epoch: 3,
          document_version_ref: "document-version-admin-001",
          processing_revision_ref: "processing-revision-admin-001",
          processing_generation_ref: "processing-generation-admin-001",
          index_generation_ref: "index-generation-admin-001",
          document_display_name: "Admin Evidence.pdf",
          document_version_label: "v3",
          page_number: 7,
          locator_label: "Page 7",
          review_resolution_reason: "resolved",
          protected_open_ref: "declared-evidence-open-admin",
        },
      ],
    };
    const detail = {
      ...conversationDetail,
      turns: conversationDetail.turns.map((turn) =>
        turn.role === "assistant"
          ? { ...claimedAssistant, role: "assistant" as const, input_text: null }
          : turn,
      ),
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(adminDetailDto(detail));
      }
      if (
        url.pathname ===
        "/api/v1/admin/conversations/conv-supported-001/turns/turn-answer-001/declared-evidence/declared-evidence-open-admin"
      ) {
        return jsonResponse({
          evidence_handle: "kh_visual_admin_claim",
          locator_label: "Page 7",
          snippet: "Authorized admin excerpt",
          content: "Protected declared evidence for admin review.",
          modality: "figure",
        });
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Source check details" }));
    fireEvent.click(screen.getByRole("button", { name: "Evidence details" }));
    expect(
      await screen.findByRole("region", {
        name: "Model-declared evidence",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("kh_visual_admin_claim")).toBeInTheDocument();
    expect(screen.getByText("visual-evidence-admin-001")).toBeInTheDocument();
    expect(screen.getByText("Sources aligned")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-review-assessment")).toHaveTextContent(
      "completed",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open declared evidence" }),
    );
    expect(
      await screen.findByText("Protected declared evidence for admin review."),
    ).toBeInTheDocument();
    window.history.pushState({}, "", "/admin/audit/events");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await screen.findByText("agent_token_issued");
    expect(
      screen.queryByText("Protected declared evidence for admin review."),
    ).not.toBeInTheDocument();
  });

it("/admin/audit distinguishes failed and completed assistant attempts in one request", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    const assistantTurn = conversationDetail.turns.find((turn) => turn.role === "assistant")!;
    const failedAttempt = {
      ...assistantTurn,
      turn_id: "turn-retry-attempt-001",
      source_turn_id: "treq-retry-001",
      execution_id: "attempt-retry-001",
      execution_status: "failed_closed" as const,
      response_kind: "unknown" as const,
      verification_status: null,
      answer_text: null,
      citations: [],
      response_segments: [],
      validation_state: "not_applicable" as const,
      retryable: true,
      runtime_trace_id: "trace-retry-attempt-001",
      created_at: "2026-07-09T00:00:01+00:00",
    };
    const completedAttempt = {
      ...assistantTurn,
      turn_id: "turn-retry-attempt-002",
      source_turn_id: "treq-retry-001",
      execution_id: "attempt-retry-002",
      created_at: "2026-07-09T00:00:02+00:00",
    };
    const missingLineageAttempt = {
      ...assistantTurn,
      turn_id: "turn-missing-lineage",
      source_turn_id: null,
      execution_id: null,
      answer_text: "This historical assistant attempt has no recorded lineage.",
      citations: [],
      response_segments: [],
      runtime_trace_id: null,
      created_at: "2026-07-09T00:00:03+00:00",
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(adminDetailDto({
          ...conversationDetail,
          turns: [
            conversationDetail.turns[0],
            completedAttempt,
            failedAttempt,
            missingLineageAttempt,
          ],
        }));
      }
      return normalFetch(input, init);
    });
    render(<App />);

    (await screen.findAllByRole("button", { name: "Technical details" })).forEach((button) => {
      fireEvent.click(button);
    });
    const lineagePanels = await screen.findAllByTestId("assistant-attempt-lineage");
    expect(lineagePanels).toHaveLength(3);

    const failedPanel = lineagePanels.find((panel) =>
      within(panel).queryByText("attempt-retry-001"),
    )!;
    expect(within(failedPanel).getByText("Attempt 1 of 1")).toBeInTheDocument();
    expect(within(failedPanel).getByText("Failed safely")).toBeInTheDocument();
    expect(within(failedPanel).getByText("turn-retry-attempt-001")).toBeInTheDocument();

    const completedPanel = lineagePanels.find((panel) =>
      within(panel).queryByText("attempt-retry-002"),
    )!;
    expect(within(completedPanel).getByText("Attempt 1 of 1")).toBeInTheDocument();
    expect(within(completedPanel).getByText("Completed")).toBeInTheDocument();
    expect(within(completedPanel).getByText("turn-retry-attempt-002")).toBeInTheDocument();

    const missingPanel = lineagePanels.find((panel) =>
      within(panel).queryByText("Attempt lineage incomplete"),
    )!;
    expect(within(missingPanel).getByText("Attempt lineage incomplete")).toBeInTheDocument();
    expect(within(missingPanel).getAllByText("Not reported")).toHaveLength(1);
  });

it("/admin/audit reaches every bounded conversation page and refreshes current state", async () => {
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let listRequests = 0;
    const firstSummary = {
      conversation_id: conversationDetail.conversation_id,
      owner_actor_id: conversationDetail.owner_actor_id,
      title: conversationDetail.title,
      status: conversationDetail.status,
      created_at: conversationDetail.created_at,
      updated_at: conversationDetail.updated_at,
      last_turn_status: "completed",
    };
    const olderSummary = {
      ...firstSummary,
      conversation_id: "conv-older-001",
      title: "Archived hardware review",
      status: "archived",
      updated_at: "2026-07-08T00:00:00+00:00",
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/conversations" && method === "GET") {
        listRequests += 1;
        return url.searchParams.get("cursor") === "page-2"
          ? jsonResponse({ conversations: [olderSummary], next_cursor: null })
          : jsonResponse({ conversations: [firstSummary], next_cursor: "page-2" });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Load more conversations" }),
    );
    expect(
      await screen.findByRole("button", { name: /Archived hardware review/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more conversations" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Refresh conversations" }),
    );
    await waitFor(() => expect(listRequests).toBe(3));
    expect(
      screen.queryByRole("button", { name: /Archived hardware review/ }),
    ).not.toBeInTheDocument();
  });

it("/admin/audit explains runtime detail failures and allows retry", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/runtime/turn-answer-001",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let runtimeAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname ===
          "/api/v1/admin/conversations/conv-supported-001/turns/turn-answer-001/runtime" &&
        (init?.method ?? "GET") === "GET"
      ) {
        runtimeAttempts += 1;
        if (runtimeAttempts === 1) {
          return jsonResponse(
            {
              error_code: "runtime_unavailable",
              message_code: "artifact.is_unavailable",
              message_params: {},
            },
            500,
          );
        }
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByText("Runtime details could not be loaded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Durable runtime events")).toBeInTheDocument();
    expect(runtimeAttempts).toBe(2);
  });

it("ignores a pending runtime failure after returning to the transcript", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/runtime/turn-answer-001",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveRuntime: ((response: Response) => void) | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        (init?.method ?? "GET") === "GET"
      ) {
        return new Promise<Response>((resolve) => {
          resolveRuntime = resolve;
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    await waitFor(() => expect(resolveRuntime).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Back to conversation" }));
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();

    await act(async () => {
      resolveRuntime?.(
        new Response(
          JSON.stringify({
            error_code: "not_found",
            message_code: "artifact.is_unavailable",
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      );
      await Promise.resolve();
    });
    expect(screen.queryByText("This item is unavailable")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
  });

it("restores a valid transcript after a runtime is unavailable", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(
          { error_code: "not_found", message_code: "artifact.is_unavailable" },
          404,
        );
      }
      return normalFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "View runtime trace" }));
    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    act(() => window.history.back());
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("This item is unavailable")).not.toBeInTheDocument();
  });

it("/admin/audit explains conversation detail failures and allows retry", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let detailAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        detailAttempts += 1;
        if (detailAttempts === 1) {
          return jsonResponse(
            { message_code: "artifact.is_unavailable", message_params: {} },
            500,
          );
        }
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(
      await screen.findByText("Conversation details could not be loaded"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();
    expect(detailAttempts).toBe(2);
  });

it("/admin/audit derives runtime availability from the strict execution identity", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(adminDetailDto({
          ...conversationDetail,
          turns: conversationDetail.turns.map((turn) =>
            turn.role === "assistant" ? { ...turn, runtime_trace_id: null } : turn,
          ),
        }));
      }
      return normalFetch(input, init);
    });
    render(<App />);

    await screen.findByText(
      "What is the approved value for the selected item?",
    );
    expect(screen.getByRole("button", { name: "View runtime trace" })).toBeInTheDocument();
  });

it("/admin/audit names an empty transcript instead of leaving a blank detail pane", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(adminDetailDto({ ...conversationDetail, turns: [] }));
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(
      await screen.findByText("This conversation has no recorded turns."),
    ).toBeInTheDocument();
  });

it("/admin/audit ignores a stale conversation response after a faster reselection", async () => {
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveSecondary: ((response: Response) => void) | undefined;
    const secondaryDetail = {
      ...conversationDetail,
      conversation_id: "conv-secondary-001",
      title: "Secondary audit conversation",
      turns: conversationDetail.turns.map((turn) => ({
        ...turn,
        conversation_id: "conv-secondary-001",
        input_text: turn.role === "user" ? "Secondary transcript content" : turn.input_text,
      })),
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/conversations" && method === "GET") {
        return jsonResponse({
          conversations: [
            {
              conversation_id: conversationDetail.conversation_id,
              owner_actor_id: conversationDetail.owner_actor_id,
              title: conversationDetail.title,
              status: conversationDetail.status,
              created_at: conversationDetail.created_at,
              updated_at: conversationDetail.updated_at,
              last_turn_status: "completed",
            },
            {
              conversation_id: secondaryDetail.conversation_id,
              owner_actor_id: secondaryDetail.owner_actor_id,
              title: secondaryDetail.title,
              status: secondaryDetail.status,
              created_at: secondaryDetail.created_at,
              updated_at: secondaryDetail.updated_at,
              last_turn_status: "completed",
            },
          ],
        });
      }
      if (
        url.pathname === "/api/v1/admin/conversations/conv-secondary-001" &&
        method === "GET"
      ) {
        return new Promise<Response>((resolve) => {
          resolveSecondary = resolve;
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Secondary audit conversation/ }),
    );
    await waitFor(() => expect(resolveSecondary).toBeDefined());
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(
      await screen.findByText(
        "What is the approved value for the selected item?",
      ),
    ).toBeInTheDocument();

    await act(async () => {
      resolveSecondary?.(
        new Response(JSON.stringify(adminDetailDto(secondaryDetail)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await Promise.resolve();
    });

    expect(screen.queryByText("Secondary transcript content")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
  });

it("/admin/audit ignores a stale runtime response after a faster turn selection", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveFirstRuntime: ((response: Response) => void) | undefined;
    const assistantTurn = conversationDetail.turns.find((turn) => turn.role === "assistant");
    expect(assistantTurn).toBeDefined();
    const secondAssistantTurn = {
      ...assistantTurn!,
      turn_id: "turn-answer-002",
      answer_text: "Second assistant answer",
      runtime_trace_id: "trace-answer-002",
    };
    const latestRuntime = {
      ...runtimeTraceDetail,
      execution_id: "trace-answer-002",
      turn_id: "turn-answer-002",
      events: [
        {
          ...runtimeTraceDetail.events[0],
          event_id: "latest-runtime-step",
          execution_id: "trace-answer-002",
          event_type: "tool_completed",
          result_ref: "Latest runtime summary",
        },
      ],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        method === "GET"
      ) {
        return jsonResponse(adminDetailDto({
          ...conversationDetail,
          turns: [...conversationDetail.turns, secondAssistantTurn],
        }));
      }
      if (
        url.pathname.endsWith("/turns/turn-answer-001/runtime") &&
        method === "GET"
      ) {
        return new Promise<Response>((resolve) => {
          resolveFirstRuntime = resolve;
        });
      }
      if (
        url.pathname.endsWith("/turns/turn-answer-002/runtime") &&
        method === "GET"
      ) {
        return jsonResponse(latestRuntime);
      }
      return normalFetch(input, init);
    });
    render(<App />);

    const runtimeButtons = await screen.findAllByRole("button", {
      name: "View runtime trace",
    });
    fireEvent.click(runtimeButtons[0]);
    await waitFor(() => expect(resolveFirstRuntime).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Back to conversation" }));
    const refreshedRuntimeButtons = await screen.findAllByRole("button", {
      name: "View runtime trace",
    });
    fireEvent.click(refreshedRuntimeButtons[1]);
    expect(await screen.findByText("Latest runtime summary")).toBeInTheDocument();

    await act(async () => {
      resolveFirstRuntime?.(
        new Response(JSON.stringify(runtimeTraceDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await Promise.resolve();
    });

    expect(screen.getByText("Latest runtime summary")).toBeInTheDocument();
  });
});
