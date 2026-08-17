import "@testing-library/jest-dom/vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import App from "./atlas-app.test-support";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminSession,
  adminWithProjectSession,
  answeredTurn,
  conversationDetail,
  conversationSummaries,
  cleanupAppTest,
  incompleteReadiness,
  memberSession,
  memberWithoutProjects,
  mockApi,
  operatorSession,
  projectAdminSession,
  projectUploaderSession,
  prepareAppTest,
  readyReadiness,
  teamAdminSession,
  teamUploaderSession,
  workspaceDetailDto,
  runtimeEventStream,
} from "../App.test-support";
import {
  jsonResponse,
  openAccountMenu,
} from "./atlas-app.test-helpers";
import {
  claimsInPresentationOrder,
  MessageSources,
  sliceCodePoints,
} from "../features/workspace/WorkspaceFeature";
import { ConversationThread } from "../features/workspace/WorkspaceConversationViews";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: workspace-conversation", () => {
it("uses backend code-point offsets for non-BMP claim text", () => {
  expect(sliceCodePoints("😀 facts", 2, 7)).toBe("facts");
});

it("uses the same offset order for claim markers, status, and citations", () => {
  const claims = claimsInPresentationOrder({
    segment_id: "segment-reversed",
    kind: "mixed_evidence",
    text: "First. Second.",
    citation_ids: ["citation-first"],
    external_unverified: true,
    verification_status: "mixed",
    verification_reason: "mixed_claim_statuses",
    claims: [
      {
        claim_id: "claim-second",
        claim_kind: "gap",
        text: "Second.",
        start: 7,
        end: 14,
        citation_ids: [],
        verification_status: "unverified_inference",
        verification_reason: "not_supported_or_inferred",
      },
      {
        claim_id: "claim-first",
        claim_kind: "factual",
        text: "First.",
        start: 0,
        end: 6,
        citation_ids: ["citation-first"],
        verification_status: "evidence_supported",
        verification_reason: "supported_by_evidence",
      },
    ],
  });
  expect(claims.map((claim) => claim.claim_id)).toEqual([
    "claim-first",
    "claim-second",
  ]);
  expect(claims[0]?.citation_ids).toEqual(["citation-first"]);
});

it("lists each response citation once in the aggregate source section", () => {
  const citation = {
    citation_id: "citation-1",
    document_title: "citation-1",
    locator_label: "Protected citation reference",
    snippet: "",
    viewer_available: true,
  };
  render(<MessageSources citations={[citation, citation]} onOpen={vi.fn()} />);
  expect(screen.getByText("Evidence cited in this response")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /Open evidence/i })).toHaveLength(1);
});

it("/workspace presents pure chat and keeps management out of the default shell", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.queryByText(
      "Atlas selects and retrieves relevant sources in multiple steps from the documents you can currently access.",
    )).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Knowledge scope" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New conversation" })).toBeInTheDocument();
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
    const composer = screen.getByLabelText("Message");
    expect(composer).toHaveAttribute(
      "placeholder",
      "For example: summarize a document or compare information across related sources.",
    );
    await waitFor(() => expect(composer).toHaveFocus());
    expect(composer.parentElement).toHaveAttribute("data-slot", "message-composer");
    expect(screen.queryByText("Finish workspace setup")).not.toBeInTheDocument();
    expect(screen.queryByText("Knowledge status")).not.toBeInTheDocument();
    expect(screen.queryByText("Safety checks")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Permissions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
    const workspaceSidebar = document.querySelector('[data-slot="workspace-context-sidebar"]');
    expect(workspaceSidebar).toHaveClass("w-72");
    const sidebarHeader = workspaceSidebar?.querySelector(
      '[data-slot="contextual-sidebar-header"]',
    );
    expect(within(sidebarHeader as HTMLElement).getByRole("button", { name: "Atlas" }))
      .toHaveAttribute("title", "Workspace");
    expect(within(sidebarHeader as HTMLElement).getByRole("button", {
      name: "Collapse conversation history",
    }))
      .toBeInTheDocument();
    expect(within(sidebarHeader as HTMLElement).queryByRole("button", { name: "Downloads" }))
      .not.toBeInTheDocument();
    const conversationControls = document.querySelector(
      '[data-slot="workspace-conversation-controls"]',
    );
    const composerSurface = document.querySelector('[data-slot="workspace-composer"]');
    expect(conversationControls).not.toHaveClass("border-b");
    expect(composerSurface).not.toHaveClass("border-t");
    const conversationContent = document.querySelector(
      '[data-slot="message-scroller-content"]',
    );
    expect(conversationContent).toHaveClass("py-4");
    expect(conversationContent).not.toHaveClass("py-6");
    expect(sidebarHeader).toHaveClass("border-b");
    const newConversation = screen.getByRole("button", { name: "New conversation" });
    expect(newConversation.nextElementSibling).toHaveAccessibleName("Projects");
    expect(newConversation.nextElementSibling?.nextElementSibling)
      .toHaveAccessibleName("Teams");
    const workspaceFooter = workspaceSidebar?.querySelector(
      '[data-slot="contextual-sidebar-footer"]',
    );
    expect(within(workspaceFooter as HTMLElement).getByRole("button", { name: "Account menu" }))
      .toHaveAttribute("data-presentation", "full");
    expect(workspaceFooter).toHaveClass("border-t");
    const accountMenu = await openAccountMenu();
    expect(screen.queryByRole("menuitem", { name: "Knowledge Library" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /^Downloads/ })).not.toBeInTheDocument();
    expect(accountMenu.settings).toBeInTheDocument();
    expect(accountMenu.signOut).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("menu", { name: "Account menu" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu", { name: "Account menu" })).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Collapse conversation history" }));
    expect(workspaceSidebar).toHaveClass("w-14");
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Atlas" }))
      .toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).queryByRole("button", { name: "Downloads" }))
      .not.toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Projects" }))
      .toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Teams" }))
      .toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).queryByRole("button", { name: "Knowledge Library" }))
      .not.toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Account menu" }))
      .toHaveAttribute("data-presentation", "compact");
    fireEvent.click(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Atlas" }));
    expect(workspaceSidebar).toHaveClass("w-72");

    fireEvent.click(screen.getByRole("button", { name: "Open conversation history" }));
    const mobileHistory = await screen.findByRole("dialog", { name: "Conversations" });
    expect(within(mobileHistory).getByRole("button", { name: "New conversation" })).toBeInTheDocument();
    expect(within(mobileHistory).getByRole("button", { name: "Projects" })).toBeInTheDocument();
    expect(within(mobileHistory).getByRole("button", { name: "Teams" })).toBeInTheDocument();
    expect(within(mobileHistory).queryByRole("button", { name: "Knowledge Library" }))
      .not.toBeInTheDocument();
    expect(within(mobileHistory).getByRole("button", { name: "Atlas" })).toBeInTheDocument();
    expect(within(mobileHistory).getByRole("button", { name: "Account menu" }))
      .toHaveAttribute("data-presentation", "full");
    expect(screen.getAllByRole("button", { name: "Account menu" })).toHaveLength(1);
  });

it("/workspace names unresolved history data instead of showing an empty chat state", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return new Promise<Response>(() => {});
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Loading workspace data")).toBeInTheDocument();
    expect(screen.getByText("Loading conversations")).toBeInTheDocument();
    expect(screen.queryByText("New chats appear here after you send a message."))
      .not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading workspace data" }))
      .toHaveAttribute("aria-busy", "true");
  });

it("/workspace reports a failed history request and retries without showing a false empty state", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let historyAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations" &&
        (init?.method ?? "GET") === "GET"
      ) {
        historyAttempts += 1;
        if (historyAttempts === 1) {
          return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Conversation history could not be loaded"))
      .toBeInTheDocument();
    expect(screen.queryByText("New chats appear here after you send a message."))
      .not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry conversation history" }));
    const answeredHistory = await screen.findByRole("button", { name: /Example conversation/ });
    expect(answeredHistory).toBeInTheDocument();
    expect(answeredHistory.querySelector('[data-slot="badge"]')).not.toBeInTheDocument();
    expect(historyAttempts).toBe(2);
  });

it("/workspace protects IME confirmation and keeps stable message geometry at the bottom", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    const composer = await screen.findByLabelText("Message");
    fireEvent.change(composer, { target: { value: "你知道" } });
    const conversationCreateCalls = () => vi.mocked(global.fetch).mock.calls.filter(
      ([input, init]) =>
        String(input) === "/api/v1/workspace/conversations" && init?.method === "POST",
    );

    fireEvent.compositionStart(composer);
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", keyCode: 13 });
    expect(conversationCreateCalls()).toHaveLength(0);
    expect(composer).toHaveValue("你知道");

    fireEvent.compositionEnd(composer);
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", keyCode: 13 });
    expect(conversationCreateCalls()).toHaveLength(0);

    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", keyCode: 229 });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", shiftKey: true });
    expect(fireEvent.keyDown(composer, {
      key: "Enter",
      code: "Enter",
      altKey: true,
    })).toBe(true);
    expect(conversationCreateCalls()).toHaveLength(0);

    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", keyCode: 13 });
    await waitFor(() => expect(conversationCreateCalls()).toHaveLength(1));

    const userMessage = Array.from(
      container.querySelectorAll<HTMLElement>('[data-slot="bubble-content"]'),
    ).find((element) => element.textContent === "你知道");
    if (!userMessage) throw new Error("Expected the submitted user bubble.");
    const userBubble = userMessage.closest('[data-slot="bubble"]');
    const userGroup = userMessage.closest('[data-slot="message-group"]');
    const userItem = userMessage.closest('[data-slot="message-scroller-item"]');
    expect(userBubble).toHaveClass("max-w-[80%]");
    expect(userGroup).toHaveClass("w-full", "items-end");
    expect(userItem).toHaveAttribute("data-scroll-anchor", "false");
    expect(userItem).not.toHaveClass(
      "[contain-intrinsic-size:auto_10rem]",
      "[content-visibility:auto]",
    );
    expect(container.querySelector('[data-slot="message-scroller-content"]')).toHaveClass(
      "justify-end",
    );

    const workspaceThreadSource = readFileSync(
      "src/features/workspace/WorkspaceConversationViews.tsx",
      "utf8",
    );
    const messageScrollerSource = readFileSync("src/components/ui/message-scroller.tsx", "utf8");
    expect(workspaceThreadSource).toContain("<MessageScrollerProvider autoScroll>");
    expect(workspaceThreadSource).not.toMatch(/<MessageScrollerItem[^>]*scrollAnchor/);
    expect(messageScrollerSource).not.toContain("contain-intrinsic-size:auto_10rem");
    expect(messageScrollerSource).not.toContain("content-visibility:auto");
  });

it("/workspace shows disabled, answered, refused, failed-closed, and copy states", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    const ask = await screen.findByRole("button", { name: /^send$/i });
    expect(ask).toBeDisabled();
    const composer = ask.closest<HTMLElement>('[data-slot="message-composer"]');
    expect(composer).not.toBeNull();
    expect(within(composer!).getByLabelText("Message")).toBeInTheDocument();
    expect(
      within(composer!).getByRole("radiogroup", { name: "Answer style" }),
    ).toBeInTheDocument();
    const composerControls = composer!.querySelector<HTMLElement>(
      '[data-slot="message-composer-controls"]',
    );
    const scopeTrigger = within(composer!).getByRole("button", {
      name: "Knowledge scope",
    });
    const reasoningControls = composer!.querySelector<HTMLElement>(
      '[data-slot="reasoning-controls"]',
    );
    expect(composerControls).toContainElement(scopeTrigger);
    expect(scopeTrigger.closest('[data-slot="field"]')).toHaveClass("w-auto");
    expect(reasoningControls).toHaveClass("ml-auto");
    expect(within(composer!).getByRole("button", { name: /^send$/i }))
      .toBe(ask);

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the approved value for the selected item?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(
      await screen.findByText(
        "A synthetic document-backed statement.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What changed in the routing policy?" },
    });
    expect(
      screen.getByRole("region", {
        name: "Answer sources",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Sources aligned")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open evidence/i })).not.toBeInTheDocument();

    expect(screen.queryByText("Safety checks")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refusal check/i })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the full board-level root cause of the intermittent boot failure?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(/could not establish a supported answer/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "revoked membership check" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(/do not currently have access/i)).toBeInTheDocument();
  });

it("/workspace lets the model discover authorized documents without project membership", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberWithoutProjects, readyReadiness);
    const { unmount } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    const emptyScopeTrigger = await screen.findByRole("button", {
      name: "Knowledge scope",
    });
    await waitFor(() => expect(emptyScopeTrigger).toBeEnabled());
    fireEvent.click(emptyScopeTrigger);
    expect(
      screen.getByRole("dialog", { name: "Select knowledge scope" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /^send$/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    expect(screen.getByRole("button", { name: /^send$/i })).toBeEnabled();
    unmount();

    mockApi(memberSession, readyReadiness);
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Message"), {
      target: { value: "slow pcie question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
  });

it("/workspace creates a default-all conversation when no scope is selected", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    const deepMode = screen.getByText("In-depth", { selector: "button" });
    await waitFor(() => expect(deepMode).toBeEnabled());
    fireEvent.click(deepMode);
    await waitFor(() => expect(deepMode).toHaveAttribute("data-state", "on"));
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the approved value for the selected item?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(
      await screen.findByText(
        "A synthetic document-backed statement.",
      ),
    ).toBeInTheDocument();
    const createConversationCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/workspace/conversations" && init?.method === "POST",
    );
    expect(createConversationCall).toBeDefined();
    const body = JSON.parse(String(createConversationCall![1]!.body));
    expect(Object.keys(body)).toEqual(["title", "response_language", "tag_refs"]);
    expect(body.title).toBe("What is the approved value for the selected item?");
    expect(body.response_language).toBe("en");
    expect(body.tag_refs).toEqual([]);
    const createTurnCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/turns") && init?.method === "POST",
    );
    expect(JSON.parse(String(createTurnCall![1]!.body))).toEqual(
      expect.objectContaining({ reasoning_mode: "deep" }),
    );
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
  });

it("/workspace freezes selected project and team refs on conversation create", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    const scopeTrigger = await screen.findByRole("button", {
      name: "Knowledge scope",
    });
    await waitFor(() => expect(scopeTrigger).toBeEnabled());
    fireEvent.click(scopeTrigger);

    let scopeDialog = screen.getByRole("dialog", {
      name: "Select knowledge scope",
    });
    const scopeSearch = within(scopeDialog).getByLabelText(
      "Search knowledge scope",
    );
    fireEvent.change(scopeSearch, { target: { value: "not-a-scope" } });
    expect(
      within(scopeDialog).getByText("No matching knowledge scopes"),
    ).toBeInTheDocument();
    fireEvent.change(scopeSearch, {
      target: { value: "Signal Integrity Alpha" },
    });
    expect(
      within(scopeDialog).getByText("Signal Integrity Alpha"),
    ).toBeInTheDocument();
    expect(within(scopeDialog).queryByText("Platform")).not.toBeInTheDocument();
    fireEvent.click(
      within(scopeDialog).getByRole("checkbox", {
        name: /Signal Integrity Alpha/,
      }),
    );
    fireEvent.click(within(scopeDialog).getByRole("button", { name: "Apply" }));

    fireEvent.click(scopeTrigger);
    scopeDialog = screen.getByRole("dialog", {
      name: "Select knowledge scope",
    });
    expect(
      within(scopeDialog).getByRole("checkbox", {
        name: /Signal Integrity Alpha/,
      }),
    ).toBeChecked();
    fireEvent.change(
      within(scopeDialog).getByLabelText("Search knowledge scope"),
      { target: { value: "Platform" } },
    );
    fireEvent.click(
      within(scopeDialog).getByRole("checkbox", { name: /Platform/ }),
    );
    fireEvent.click(within(scopeDialog).getByRole("button", { name: "Apply" }));
    expect(scopeTrigger).toHaveTextContent("2 scopes selected");

    fireEvent.click(scopeTrigger);
    scopeDialog = screen.getByRole("dialog", {
      name: "Select knowledge scope",
    });
    fireEvent.click(
      within(scopeDialog).getByRole("checkbox", {
        name: /Signal Integrity Alpha/,
      }),
    );
    fireEvent.click(within(scopeDialog).getByRole("button", { name: "Cancel" }));

    fireEvent.click(scopeTrigger);
    scopeDialog = screen.getByRole("dialog", {
      name: "Select knowledge scope",
    });
    expect(
      within(scopeDialog).getByRole("checkbox", {
        name: /Signal Integrity Alpha/,
      }),
    ).toBeChecked();
    expect(
      within(scopeDialog).getByRole("checkbox", { name: /Platform/ }),
    ).toBeChecked();
    fireEvent.click(within(scopeDialog).getByRole("button", { name: "Cancel" }));

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the approved value?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();

    const createConversationCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/workspace/conversations" &&
        init?.method === "POST",
    );
    expect(createConversationCall).toBeDefined();
    expect(JSON.parse(String(createConversationCall![1]!.body))).toEqual({
      title: "What is the approved value?",
      response_language: "en",
      tag_refs: [
        {
          tag_type: "project",
          tag_id: "proj-signal-integrity-alpha",
        },
        { tag_type: "team", tag_id: "team-platform" },
      ],
    });
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
    expect(screen.queryByLabelText("Knowledge scope")).not.toBeInTheDocument();
  });


it("/workspace keeps default-all create available when scope options fail", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v1/workspace/tag-scope") {
        return Promise.reject(new Error("scope unavailable"));
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Use every source I can access" },
    });
    expect(screen.getByRole("button", { name: /^send$/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();

    const createConversationCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/workspace/conversations" &&
        init?.method === "POST",
    );
    expect(JSON.parse(String(createConversationCall![1]!.body)).tag_refs)
      .toEqual([]);
  });

it("offers feedback only for completed nonblank assistant answers", () => {
  const assistant = conversationDetail.turns[1]!;
  render(
    <ConversationThread
      turns={[
        assistant,
        {
          ...assistant,
          turn_id: "turn-processing",
          execution_status: "processing",
        },
        {
          ...assistant,
          turn_id: "turn-failed",
          execution_status: "failed_closed",
        },
        {
          ...assistant,
          turn_id: "turn-blank",
          answer_text: "   ",
          response_segments: [{
            ...assistant.response_segments[0]!,
            text: "   ",
          }],
        },
      ]}
      loading={false}
      locale="en"
      onOpenDeclaredEvidence={vi.fn()}
      onRetry={vi.fn()}
      onFeedbackChange={vi.fn()}
      pendingFeedbackTurnIds={new Set()}
      runtimeProgress=""
      liveReasoningTimeline={[]}
      streamingSegments={[]}
    />,
  );

  expect(screen.getAllByText("Did this answer solve your problem?")).toHaveLength(1);
  expect(screen.getAllByRole("radio", { name: "Helpful" })).toHaveLength(1);
  expect(screen.getAllByRole("radio", { name: "Not helpful" })).toHaveLength(1);
});

it("confirms feedback on the server, switches revisions, and reloads the projection", async () => {
  window.history.pushState(
    {},
    "",
    "/workspace/conversations/conv-supported-001",
  );
  mockApi(memberSession, readyReadiness);
  const normalFetch = global.fetch;
  const feedbackBodies: Array<Record<string, unknown>> = [];
  let currentFeedback = answeredTurn.feedback;
  let resolveFirstFeedback: (() => void) | null = null;
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    if (
      url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
      method === "GET"
    ) {
      return jsonResponse(workspaceDetailDto({ ...answeredTurn, feedback: currentFeedback }));
    }
    if (
      url.pathname ===
        "/api/v1/workspace/conversations/conv-supported-001/turns/turn-answer-001/feedback" &&
      method === "PUT"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      feedbackBodies.push(body);
      const nextFeedback = {
        feedback: body.feedback,
        revision: (currentFeedback?.revision ?? 0) + 1,
        updated_at: "2026-07-20T00:00:04+00:00",
      };
      if (feedbackBodies.length === 1) {
        return new Promise<Response>((resolve) => {
          resolveFirstFeedback = () => {
            currentFeedback = nextFeedback;
            resolve(jsonResponse(nextFeedback));
          };
        });
      }
      currentFeedback = nextFeedback;
      return jsonResponse(nextFeedback);
    }
    return normalFetch(input, init);
  });

  const firstRender = render(<App />);
  const helpful = await screen.findByRole("radio", { name: "Helpful" });
  const notHelpful = screen.getByRole("radio", { name: "Not helpful" });
  expect(helpful).toHaveAttribute("aria-checked", "false");

  fireEvent.click(helpful);
  await waitFor(() => expect(resolveFirstFeedback).not.toBeNull());
  expect(helpful).toHaveAttribute("aria-checked", "false");
  expect(helpful).toBeDisabled();
  expect(notHelpful).toBeDisabled();

  act(() => resolveFirstFeedback?.());
  await waitFor(() => expect(helpful).toHaveAttribute("aria-checked", "true"));
  expect(feedbackBodies[0]).toMatchObject({
    feedback: "helpful",
    expected_revision: 0,
  });

  fireEvent.click(helpful);
  expect(feedbackBodies).toHaveLength(1);

  fireEvent.click(notHelpful);
  await waitFor(() => expect(notHelpful).toHaveAttribute("aria-checked", "true"));
  expect(feedbackBodies[1]).toMatchObject({
    feedback: "not_helpful",
    expected_revision: 1,
  });

  firstRender.unmount();
  render(<App />);
  expect(await screen.findByRole("radio", { name: "Not helpful" }))
    .toHaveAttribute("aria-checked", "true");
});

it("aborts pending feedback before a conversation route change can receive it", async () => {
  window.history.pushState(
    {},
    "",
    "/workspace/conversations/conv-supported-001",
  );
  mockApi(memberSession, readyReadiness);
  const normalFetch = global.fetch;
  let feedbackSignal: AbortSignal | undefined;
  let resolveFeedback: (() => void) | null = null;
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (
      url.pathname.endsWith("/turn-answer-001/feedback") &&
      (init?.method ?? "GET") === "PUT"
    ) {
      feedbackSignal = init?.signal ?? undefined;
      return new Promise<Response>((resolve) => {
        resolveFeedback = () => resolve(jsonResponse({
          feedback: "helpful",
          revision: 1,
          updated_at: "2026-07-20T00:00:04+00:00",
        }));
      });
    }
    return normalFetch(input, init);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("radio", { name: "Helpful" }));
  await waitFor(() => expect(feedbackSignal).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

  await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
  expect(feedbackSignal?.aborted).toBe(true);
  act(() => resolveFeedback?.());
  expect(screen.queryByText("Did this answer solve your problem?"))
    .not.toBeInTheDocument();
});

it("reloads canonical feedback after a revision conflict", async () => {
  window.history.pushState(
    {},
    "",
    "/workspace/conversations/conv-supported-001",
  );
  mockApi(memberSession, readyReadiness);
  const normalFetch = global.fetch;
  let detailReads = 0;
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    if (
      url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
      method === "GET"
    ) {
      detailReads += 1;
      const feedback = detailReads === 1
        ? null
        : {
            feedback: "helpful" as const,
            revision: 2,
            updated_at: "2026-07-20T00:00:05+00:00",
          };
      return jsonResponse(workspaceDetailDto({ ...answeredTurn, feedback }));
    }
    if (url.pathname.endsWith("/turn-answer-001/feedback") && method === "PUT") {
      return jsonResponse({
        message_code: "conversation.feedback_revision_changed_before_update",
        message_params: {},
      }, 409);
    }
    return normalFetch(input, init);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("radio", { name: "Not helpful" }));

  await waitFor(() => expect(detailReads).toBe(2));
  expect(await screen.findByRole("radio", { name: "Helpful" }))
    .toHaveAttribute("aria-checked", "true");
  expect(screen.getByText(
    "Feedback changed elsewhere. The latest response was reloaded.",
  )).toBeInTheDocument();
});

it("keeps feedback unchanged and localizes typed save failures", async () => {
  window.history.pushState(
    {},
    "",
    "/workspace/conversations/conv-supported-001",
  );
  mockApi(memberSession, readyReadiness);
  const normalFetch = global.fetch;
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (
      url.pathname.endsWith("/turn-answer-001/feedback") &&
      (init?.method ?? "GET") === "PUT"
    ) {
      return jsonResponse({
        message_code: "conversation.feedback_history_is_invalid",
        message_params: {},
      }, 503);
    }
    return normalFetch(input, init);
  });

  render(<App />);
  const helpful = await screen.findByRole("radio", { name: "Helpful" });
  fireEvent.click(helpful);

  expect(await screen.findByText("Feedback is temporarily unavailable."))
    .toBeInTheDocument();
  expect(helpful).toHaveAttribute("aria-checked", "false");
});

it("loads a canonical Workspace conversation route directly", async () => {
    window.history.pushState(
      {},

      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    const activeConversationButton = screen.getByRole("button", {
      name: /Example conversation/,
    });
    expect(activeConversationButton).toHaveAttribute("aria-current", "page");
    expect(activeConversationButton.closest('[data-slot="workspace-conversation-item"]'))
      .toHaveClass("bg-secondary");
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
  });

it.each(["missing-conversation", "foreign-conversation", "non-active-conversation"])(
    "fails closed from unavailable canonical Workspace route %s",
    async (conversationId) => {
      window.history.pushState(
        {},
        "",
        `/workspace/conversations/${conversationId}`,
      );
      mockApi(memberSession, readyReadiness);
      const normalFetch = global.fetch;
      global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        if (
          url.pathname === `/api/v1/workspace/conversations/${conversationId}` &&
          (init?.method ?? "GET") === "GET"
        ) {
          return jsonResponse(
            { message_code: "artifact.is_unavailable", message_params: {} },
            404,
          );
        }
        return normalFetch(input, init);
      });
      const replaceState = vi.spyOn(window.history, "replaceState");

      render(<App />);

      await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
      expect(replaceState).toHaveBeenCalledWith({}, "", "/workspace");
      expect(await screen.findByLabelText("Message")).toHaveValue("");
      expect(screen.queryByText(
        "A synthetic document-backed statement.",
      )).not.toBeInTheDocument();
    },
  );

it("keeps New conversation and browser history aligned with the canonical route", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    const newConversationButton = screen.getByRole("button", {
      name: "New conversation",
    });
    expect(newConversationButton).toHaveAttribute("data-variant", "ghost");
    expect(newConversationButton).not.toHaveAttribute("aria-current");
    fireEvent.click(newConversationButton);
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(newConversationButton).toHaveAttribute("data-variant", "secondary");
    expect(newConversationButton).toHaveAttribute("aria-current", "page");
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();

    act(() => window.history.back());
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/workspace/conversations/conv-supported-001",
      ),
    );
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    expect(newConversationButton).toHaveAttribute("data-variant", "ghost");
    expect(newConversationButton).not.toHaveAttribute("aria-current");

    act(() => window.history.forward());
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(newConversationButton).toHaveAttribute("data-variant", "secondary");
    expect(newConversationButton).toHaveAttribute("aria-current", "page");
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();
  });

it("cancels an in-flight live turn before a new route can receive its result", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let turnSignal: AbortSignal | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001/turns" &&
        (init?.method ?? "GET") === "POST"
      ) {
        turnSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          turnSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Do not show this answer in the next route" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => expect(turnSignal).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(turnSignal?.aborted).toBe(true);
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText("Do not show this answer in the next route")).not.toBeInTheDocument();
    expect(screen.queryByText("Message failed")).not.toBeInTheDocument();
  });

it("cancels an in-flight failed-turn retry when route authority changes", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    const failedTurn = {
      ...answeredTurn,
      source_turn_id: "turn-request-failed-001",
      execution_status: "failed_closed" as const,
      response_kind: "unknown" as const,
      verification_status: null,
      evidence_review_status: null,
      evidence_review_reason_codes: [],
      assessment_state: null,
      assessment_reason_code: null,
      assessment_input_digest: null,
      assessment_output_digest: null,
      answer_text: null,
      citations: [],
      model_claimed_evidence: [],
      response_segments: [],
      validation_state: "not_applicable" as const,
      refusal_code: "provider_failed",
      retryable: true,
    };
    let retrySignal: AbortSignal | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(workspaceDetailDto(failedTurn));
      }
      if (
        url.pathname === `/api/v1/workspace/turns/${failedTurn.turn_id}/retry` &&
        (init?.method ?? "GET") === "POST"
      ) {
        retrySignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          retrySignal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Retry message" }));
    await waitFor(() => expect(retrySignal).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(retrySignal?.aborted).toBe(true);
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText("Message failed")).not.toBeInTheDocument();
  });

it("shows live progress before a reconnected nonterminal turn completes", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    const processingDetail = workspaceDetailDto(answeredTurn);
    processingDetail.turns[0] = {
      ...processingDetail.turns[0],
      execution_status: "awaiting_model_action",
      evidence_review_status: null,
      evidence_review_reason_codes: [],
      assessment_state: null,
      assessment_reason_code: null,
      assessment_input_digest: null,
      assessment_output_digest: null,
      segments: [],
      citations: [],
      model_claimed_evidence: [],
    };
    let detailReads = 0;
    let emitStreamingSegment: (() => void) | undefined;
    let completeRuntimeStream: (() => void) | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        detailReads += 1;
        return jsonResponse(
          detailReads === 1 ? processingDetail : workspaceDetailDto(answeredTurn),
        );
      }
      if (url.pathname.endsWith(`/${answeredTurn.execution_id}/events`)) {
        const encoder = new TextEncoder();
        return Promise.resolve(new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode(
                `id: evt-planning\nevent: reasoning_progressed\ndata: ${JSON.stringify({
                  event_id: "evt-planning",
                  execution_id: answeredTurn.execution_id,
                  sequence: 7,
                  event_type: "reasoning_progressed",
                  state: "awaiting_model_action",
                  reasoning_phase: "planning",
                  progress_status: "started",
                  cycle: null,
                  message_code: "reasoning.planning_started",
                  message_params: {},
                  created_at: "2026-07-20T00:00:00Z",
                })}\n\n`,
              ));
              emitStreamingSegment = () => {
                controller.enqueue(encoder.encode(
                  `id: evt-segment\nevent: segment_delta\ndata: ${JSON.stringify({
                    event_id: "evt-segment",
                    execution_id: answeredTurn.execution_id,
                    sequence: 7,
                    event_type: "segment_delta",
                    state: "awaiting_model_action",
                    segment: {
                      ...answeredTurn.response_segments[0],
                      text: "Partial answer received after reconnect.",
                    },
                    created_at: "2026-07-20T00:00:01Z",
                  })}\n\n`,
                ));
              };
              completeRuntimeStream = () => {
                controller.enqueue(encoder.encode(
                  `id: evt-terminal\nevent: terminal_completed\ndata: ${JSON.stringify({
                    event_id: "evt-terminal",
                    execution_id: answeredTurn.execution_id,
                    sequence: 8,
                    event_type: "terminal_completed",
                    state: "terminal_completed",
                    created_at: "2026-07-20T00:00:01Z",
                  })}\n\n`,
                ));
                controller.close();
              };
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ));
      }
      if (url.pathname.endsWith(`/${answeredTurn.execution_id}`)) {
        return jsonResponse({
          execution_id: answeredTurn.execution_id,
          turn_id: answeredTurn.turn_id,
          conversation_id: "conv-supported-001",
          state: "terminal_completed",
          version: 8,
          failure_code: null,
          updated_at: answeredTurn.created_at,
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findAllByText("Planning the work")).toHaveLength(2);
    expect(screen.queryByText("In progress")).not.toBeInTheDocument();
    expect(detailReads).toBe(1);
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();

    act(() => emitStreamingSegment?.());
    expect(await screen.findByText(
      "Partial answer received after reconnect.",
    )).toBeInTheDocument();
    expect(screen.getByText("Planning the work")).toBeInTheDocument();
    expect(detailReads).toBe(1);

    act(() => completeRuntimeStream?.());

    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    expect(detailReads).toBe(2);
    expect(screen.getAllByText(
      "A synthetic document-backed statement.",
    )).toHaveLength(1);
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

it("cancels a nonterminal reconnect when leaving its canonical route", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    const processingDetail = workspaceDetailDto(answeredTurn);
    processingDetail.turns[0] = {
      ...processingDetail.turns[0],
      execution_status: "awaiting_model_action",
      evidence_review_status: null,
      evidence_review_reason_codes: [],
      assessment_state: null,
      assessment_reason_code: null,
      assessment_input_digest: null,
      assessment_output_digest: null,
      segments: [],
      citations: [],
      model_claimed_evidence: [],
    };
    let reconnectSignal: AbortSignal | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(processingDetail);
      }
      if (url.pathname.endsWith(`/${answeredTurn.execution_id}/events`)) {
        reconnectSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          reconnectSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);

    await waitFor(() => expect(reconnectSignal).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(reconnectSignal?.aborted).toBe(true);
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

it("keeps reconnect transport failures retryable without inventing a failed turn", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    const processingDetail = workspaceDetailDto(answeredTurn);
    processingDetail.turns[0] = {
      ...processingDetail.turns[0],
      execution_status: "awaiting_model_action",
      evidence_review_status: null,
      evidence_review_reason_codes: [],
      assessment_state: null,
      assessment_reason_code: null,
      assessment_input_digest: null,
      assessment_output_digest: null,
      segments: [],
      citations: [],
      model_claimed_evidence: [],
    };
    let detailReads = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        detailReads += 1;
        return jsonResponse(
          detailReads === 1 ? processingDetail : workspaceDetailDto(answeredTurn),
        );
      }
      if (url.pathname.endsWith(`/${answeredTurn.execution_id}/events`)) {
        return jsonResponse(
          { message_code: "artifact.is_unavailable", message_params: {} },
          503,
        );
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Message failed")).toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
    expect(screen.getByText("Processing")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry message" }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    expect(detailReads).toBe(2);
  });

it("/workspace supports multi-turn conversation replay without exposing evidence internals", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "New conversation" })).toBeInTheDocument();
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
    expect((await screen.findAllByText("Example conversation")).length).toBeGreaterThan(0);
    expect(screen.getByText("Chat with Atlas")).toBeInTheDocument();
    expect(
      screen.queryByText("What is the approved value for the selected item?"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Knowledge scope" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Example conversation/i }));
    expect(
      await screen.findByText("What is the approved value for the selected item?"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Knowledge scope" })).not.toBeInTheDocument();
    expect(container.querySelector('time[datetime="2026-07-09T00:00:01+00:00"]')).not.toBeNull();

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the approved value for the selected item?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("You")).toBeInTheDocument();
    expect(await screen.findAllByText("Atlas")).not.toHaveLength(0);
    expect(
      await screen.findAllByText(
        "A synthetic document-backed statement.",
      ),
    ).not.toHaveLength(0);
    expect(
      (await screen.findAllByRole("region", {
        name: "Answer sources",
      })).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Sources aligned")).not.toBeInTheDocument();
    expect(screen.queryByText("Evidence cited in this response")).not.toBeInTheDocument();
    expect(container.querySelector("[data-claim-id]")).toBeNull();
    expect(screen.queryByText(/citation-binding|cit-ev-doc/)).not.toBeInTheDocument();
    expect(container.querySelector('time[datetime="2026-07-09T00:00:01+00:00"]')).not.toBeNull();
    expect(container).not.toHaveTextContent(/trace-answer|prompt-p0|mi-0001|ppay-answer/i);
  });

it("/workspace keeps a zero-declaration answer complete and marks it questionable", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const defaultFetch = global.fetch;
    const dialogueTurn = {
      ...answeredTurn,
      answer_text: "Hello. I can help you explore authorized documents.",
      response_kind: "dialogue" as const,
      verification_status: "unverified" as const,
      evidence_review_status: "questionable" as const,
      evidence_review_reason_codes: ["empty_declaration" as const],
      assessment_state: "not_attempted" as const,
      assessment_reason_code: "empty_declaration" as const,
      assessment_input_digest: null,
      assessment_output_digest: null,
      citations: [],
      response_segments: [
        {
          segment_id: "segment-dialogue-001",
          kind: "dialogue" as const,
          text: "Hello. I can help you explore authorized documents.",
          citation_ids: [],
          external_unverified: false,
          verification_status: "not_applicable" as const,
          verification_reason: "not_applicable" as const,
          claims: [],
        },
      ],
      validation_state: "not_applicable" as const,
      used_knowledge_refs: [],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(workspaceDetailDto(dialogueTurn));
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/i }));

    expect(await screen.findByText("Check sources")).toBeInTheDocument();
    expect(screen.queryByText("Evidence supported")).not.toBeInTheDocument();
    expect(screen.queryByText("Evidence cited in this response")).not.toBeInTheDocument();
  });

it("/workspace renders a complete assistant answer as one Markdown document", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const defaultFetch = global.fetch;
    const markdown = [
      "## Deployment result",
      "",
      "- API is healthy",
      "- Web is healthy",
      "",
      "| Service | Status |",
      "| --- | --- |",
      "| Atlas | Ready |",
    ].join("\n");
    const markdownTurn = {
      ...answeredTurn,
      answer_text: markdown,
      response_segments: [
        {
          ...answeredTurn.response_segments[0],
          text: "## Deployment result\n\n- API is healthy",
        },
        {
          ...answeredTurn.response_segments[0],
          segment_id: "seg-answer-002",
          text: "- Web is healthy\n\n| Service | Status |\n| --- | --- |\n| Atlas | Ready |",
        },
      ],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001"
        && (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(workspaceDetailDto(markdownTurn));
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/i }));

    expect(
      await screen.findByRole("heading", { name: "Deployment result", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByText("Sources aligned")).not.toBeInTheDocument();
  });

it("/workspace integrates the answer verdict with compact cited-document labels", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const defaultFetch = global.fetch;
    const claimedTurn = {
      ...answeredTurn,
      model_claimed_evidence: [
        {
          position: 1,
          handle: "kh_evidence_workspace_claim",
          resolution_status: "resolved" as const,
          duplicate_of_position: null,
          handle_kind: "evidence" as const,
          evidence_ref: "evidence-workspace-001",
          result_ref: "result-workspace-001",
          invocation_ordinal: 1,
          document_ref: "document-workspace-001",
          document_handle: "kh_document_workspace",
          lifecycle_epoch: 1,
          document_version_ref: "document-version-workspace-001",
          processing_revision_ref: "processing-revision-workspace-001",
          processing_generation_ref: "processing-generation-workspace-001",
          index_generation_ref: "index-generation-workspace-001",
          document_display_name: "Workspace Evidence.pdf",
          document_version_label: "v1",
          page_number: 4,
          locator_label: "Page 4",
          review_resolution_reason: "resolved",
          protected_open_ref: "declared-evidence-open-workspace",
        },
      ],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse(workspaceDetailDto(claimedTurn));
      }
      if (
        url.pathname ===
        "/api/v1/workspace/conversations/conv-supported-001/turns/turn-answer-001/declared-evidence/declared-evidence-open-workspace"
      ) {
        expect(new Headers(init?.headers).get("Accept")).toBe(
          "application/pdf, image/png, application/json;q=0.5",
        );
        return jsonResponse({
          evidence_handle: "kh_evidence_workspace_claim",
          locator_label: "Page 4",
          snippet: "Authorized workspace excerpt",
          content: "Protected declared evidence for workspace review.",
          modality: "text",
        });
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/i }));

    expect(
      await screen.findByRole("region", {
        name: "Answer sources",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Workspace Evidence.pdf · Page 4")).toBeInTheDocument();
    expect(screen.queryByText("kh_evidence_workspace_claim")).not.toBeInTheDocument();
    expect(screen.queryByText("evidence-workspace-001")).not.toBeInTheDocument();
    vi.spyOn(Date.prototype, "toISOString")
      .mockReturnValueOnce("2026-07-29T10:20:30.000Z")
      .mockReturnValueOnce("2026-07-29T10:21:30.000Z");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open cited document Workspace Evidence.pdf · Page 4",
      }),
    );
    expect(
      await screen.findByText("Protected declared evidence for workspace review."),
    ).toBeInTheDocument();
    const watermark = screen.getByTestId("evidence-watermark");
    expect(watermark).toHaveAttribute("aria-hidden", "true");
    expect(
      within(watermark).getAllByText(
        "Engineer One · user-engineer-001 · 2026-07-29T10:20:30.000Z",
      ),
    ).toHaveLength(12);
    expect(screen.queryByText(
      /visual watermark is for identification only/,
    )).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(
        screen.queryByText("Protected declared evidence for workspace review."),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open cited document Workspace Evidence.pdf · Page 4",
      }),
    );
    expect(
      await screen.findByText("Protected declared evidence for workspace review."),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("evidence-watermark")).getAllByText(
        "Engineer One · user-engineer-001 · 2026-07-29T10:21:30.000Z",
      ),
    ).toHaveLength(12);
  });

it("/workspace renders mixed provenance with one answer-level result", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const defaultFetch = global.fetch;
    const mixedTurn = {
      ...answeredTurn,
      response_kind: "mixed_answer" as const,
      verification_status: "partially_verified" as const,
      evidence_review_status: "questionable" as const,
      evidence_review_reason_codes: ["answer_item_failed" as const],
      answer_text: `${answeredTurn.answer_text}\n\nGeneral provider comparison.`,
      response_segments: [
        ...answeredTurn.response_segments,
        {
          segment_id: "seg-external-001",
          kind: "external_unverified" as const,
          text: "General provider comparison.",
          citation_ids: [],
          external_unverified: true,
          verification_status: "unverified_inference" as const,
          verification_reason: "not_supported_or_inferred" as const,
          claims: [],
        },
      ],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/workspace/conversations/conv-supported-001" && method === "GET") {
        return jsonResponse(workspaceDetailDto(mixedTurn));
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/i }));
    const evidenceStatus = (await screen.findByText("Check sources"))
      .closest('[data-slot="badge"]');
    expect(evidenceStatus).toHaveAttribute("data-status-semantic", "attention");
    expect(screen.queryByText("mixed verified and unverified sources")).not.toBeInTheDocument();
    expect(screen.getByText(/General provider comparison\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open evidence/i })).not.toBeInTheDocument();
  });

it("/workspace shows questionable for history and the completed stream", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const defaultFetch = global.fetch;
    const degradedTurn = {
      ...answeredTurn,
      turn_id: "turn-degraded-001",
      validation_state: "completed" as const,
      response_kind: "external_unverified" as const,
      verification_status: "unverified" as const,
      evidence_review_status: "questionable" as const,
      evidence_review_reason_codes: ["assessment_not_completed" as const],
      assessment_state: "unavailable" as const,
      assessment_reason_code: "provider_failed" as const,
      assessment_input_digest: null,
      assessment_output_digest: null,
      answer_text: "Candidate answer kept visible.",
      citations: [],
      response_segments: [{
        segment_id: "seg-degraded-001",
        kind: "external_unverified" as const,
        text: "Candidate answer kept visible.",
        citation_ids: [],
        external_unverified: true,
        verification_status: "unverified_inference" as const,
        verification_reason: "validator_unavailable" as const,
        claims: [],
      }],
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/workspace/conversations/conv-supported-001" && method === "GET") {
        return jsonResponse(workspaceDetailDto(degradedTurn));
      }
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001/turns"
        && method === "POST"
      ) {
        return jsonResponse({
          turn_id: degradedTurn.turn_id,
          execution_id: degradedTurn.execution_id,
          status: "accepted",
          status_url: `/api/v1/workspace/turn-executions/${degradedTurn.execution_id}`,
          events_url: `/api/v1/workspace/turn-executions/${degradedTurn.execution_id}/events`,
        }, 202);
      }
      if (url.pathname.endsWith(`/${degradedTurn.execution_id}/events`)) {
        return Promise.resolve(runtimeEventStream(degradedTurn.execution_id, "terminal_completed"));
      }
      if (url.pathname.endsWith(`/${degradedTurn.execution_id}`)) {
        return jsonResponse({
          execution_id: degradedTurn.execution_id,
          turn_id: degradedTurn.turn_id,
          conversation_id: "conv-supported-001",
          state: "terminal_completed",
          version: 8,
          failure_code: null,
          updated_at: degradedTurn.created_at,
        });
      }
      return defaultFetch(input, init);
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/i }));
    expect(await screen.findByText(
      "Check sources",
    )).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Run degraded validation" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => expect(screen.getAllByText(
      "Check sources",
    ).length).toBeGreaterThanOrEqual(2));
  });

it("/workspace shows conversation failure and retries the submitted question", async () => {
    window.history.pushState({}, "", "/workspace");
    let turnAttempts = 0;
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = new URL(String(input), "http://localhost");
        const method = init?.method ?? "GET";
        if (url.pathname === "/api/v1/auth/session" && method === "GET") {
          return jsonResponse(adminWithProjectSession);
        }
        if (url.pathname === "/api/v1/ops/readiness") {
          return jsonResponse(readyReadiness);
        }
        if (url.pathname === "/api/v1/workspace/tag-scope") {
          return jsonResponse({ tags: [] });
        }
        if (url.pathname === "/api/v1/workspace/conversations" && method === "GET") {
          return jsonResponse({ conversations: [] });
        }
        if (url.pathname === "/api/v1/workspace/conversations" && method === "POST") {
          return jsonResponse({
            conversation: {
              conversation_id: "conv-retry-001",
              owner_actor_id: "user-admin-001",
              title: "network failure question",
              status: "active",
              created_at: "2026-07-11T00:00:00+00:00",
              updated_at: "2026-07-11T00:00:00+00:00",
            },
            turns: [],
          }, 201);
        }
        if (
          url.pathname === "/api/v1/workspace/conversations/conv-retry-001/turns" &&
          method === "POST"
        ) {
          turnAttempts += 1;
          if (turnAttempts === 1) {
            return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
          }
          return jsonResponse({
            turn_id: answeredTurn.turn_id,
            execution_id: answeredTurn.execution_id,
            status: "accepted",
            status_url: `/api/v1/workspace/turn-executions/${answeredTurn.execution_id}`,
            events_url: `/api/v1/workspace/turn-executions/${answeredTurn.execution_id}/events`,
          }, 202);
        }
        if (url.pathname.endsWith(`/${answeredTurn.execution_id}/events`)) {
          return Promise.resolve(runtimeEventStream(answeredTurn.execution_id, "terminal_completed"));
        }
        if (url.pathname.endsWith(`/${answeredTurn.execution_id}`)) {
          return jsonResponse({
            execution_id: answeredTurn.execution_id,
            turn_id: answeredTurn.turn_id,
            conversation_id: "conv-retry-001",
            state: "terminal_completed",
            version: 8,
            failure_code: null,
            updated_at: answeredTurn.created_at,
          });
        }
        if (url.pathname === "/api/v1/workspace/conversations/conv-retry-001") {
          const dto = workspaceDetailDto(answeredTurn);
          return jsonResponse({
            conversation: { ...dto.conversation, conversation_id: "conv-retry-001" },
            turns: dto.turns,
          });
        }
        return jsonResponse({ message_code: "common.rejected", message_params: {} }, 404);
      },
    );
    global.fetch = fetchMock;
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Message"), {
      target: { value: "network failure question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Message failed")).toBeInTheDocument();
    expect(screen.getByText("The requested data is unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Tried question: network failure question")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "changed after failure" },
    });
    fireEvent.click(screen.getByRole("button", { name: /retry message/i }));

    expect(
      await screen.findByText(
        "A synthetic document-backed statement.",
      ),
    ).toBeInTheDocument();
    const turnCalls = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/v1/workspace/conversations/conv-retry-001/turns",
    );
    expect(turnCalls).toHaveLength(2);
    expect(String(turnCalls[1][1]?.body)).toContain(
      '"input_text":"network failure question"',
    );
    expect(String(turnCalls[1][1]?.body)).not.toContain("changed after failure");
  });

it("/admin pages keep a fixed management sidebar for switching", async () => {
    window.history.pushState({}, "", "/admin/users");
    mockApi(adminWithProjectSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    const management = screen.getByRole("navigation", { name: "Management" });
    const footer = management.parentElement?.querySelector(
      '[data-slot="contextual-sidebar-footer"]',
    );
    expect(within(footer as HTMLElement).getByRole("button", { name: "Account menu" })).toHaveClass(
      "w-full",
    );
    expect(within(management).getByRole("button", { name: "Users" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(management).getByRole("button", { name: "Teams" })).toBeInTheDocument();
    expect(within(management).getByRole("button", { name: "Models" })).toBeInTheDocument();
    const groups = Array.from(
      management.querySelectorAll<HTMLElement>('[data-slot="management-nav-group"]'),
    );
    expect(groups.map((group) => group.querySelector("[data-slot=management-nav-group-label]")?.textContent)).toEqual([
      "Identity & Access",
      "Knowledge Content",
      "AI & Automation",
      "System Operations",
    ]);
    expect(within(groups[0]).getByRole("button", { name: "Users" })).toBeInTheDocument();
    expect(within(groups[0]).getByRole("button", { name: "Projects" })).toBeInTheDocument();
    expect(
      within(groups[1]).getByRole("button", { name: "Document Library" }),
    ).toBeInTheDocument();
    expect(within(groups[2]).getByRole("button", { name: "Agents" })).toBeInTheDocument();
    expect(within(groups[3]).getByRole("button", { name: "Audit" })).toBeInTheDocument();
    expect(
      within(groups[3]).getByRole("button", { name: "System Status" }),
    ).toBeInTheDocument();

    fireEvent.click(within(management).getByRole("button", { name: "Models" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/models"));
    expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Management" })).getByRole("button", {
        name: "Models",
      }),
    ).toHaveAttribute("aria-current", "page");

    fireEvent.click(screen.getByRole("button", { name: "Atlas" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
  });

it("ignores a pending conversation failure after returning to the audit directory", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveDetail: ((response: Response) => void) | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return new Promise<Response>((resolve) => {
          resolveDetail = resolve;
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    await waitFor(() => expect(resolveDetail).toBeDefined());
    fireEvent.click(screen.getByRole("link", { name: "Conversation history" }));
    expect(await screen.findByRole("button", { name: /Example conversation/ })).toBeInTheDocument();

    await act(async () => {
      resolveDetail?.(
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
    expect(window.location.pathname).toBe("/admin/audit/conversations");
  });

it("opens Workspace Notes in the shared workspace shell without the conversation composer", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/projects/proj-admin-live/notes",
    );
    mockApi(adminWithProjectSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText("Architecture decisions")).toBeInTheDocument();
    expect(document.querySelector('[data-slot="workspace-knowledge-content"]'))
      .toBeInTheDocument();
    const workspaceSidebar = document.querySelector(
      '[data-slot="workspace-context-sidebar"]',
    );
    expect(workspaceSidebar).toBeInTheDocument();
    expect(
      within(workspaceSidebar as HTMLElement).getByRole("button", {
        name: "Projects",
      }),
    ).toHaveAttribute("aria-current", "page");
    expect(document.querySelector('[data-slot="workspace-composer"]'))
      .toHaveClass("hidden");
    expect(screen.getByRole("navigation", { name: "Scope sections" }))
      .toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Release checklist" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create note" }));

    await waitFor(() =>
      expect(window.location.pathname).toMatch(
        /^\/workspace\/projects\/proj-admin-live\/notes\/[^/]+$/,
      ),
    );
  });

it("shows a spinner only beside conversations whose latest turn is processing", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({
          conversations: [
            {
              conversation_id: "conv-processing-001",
              owner_actor_id: "user-engineer-001",
              title: "Processing conversation",
              status: "active",
              response_language: "en",
              created_at: "2026-07-31T00:00:00Z",
              updated_at: "2026-07-31T00:01:00Z",
              last_turn_status: "processing",
            },
            {
              conversation_id: "conv-completed-001",
              owner_actor_id: "user-engineer-001",
              title: "Completed conversation",
              status: "active",
              response_language: "en",
              created_at: "2026-07-31T00:00:00Z",
              updated_at: "2026-07-31T00:00:30Z",
              last_turn_status: "completed",
            },
          ],
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);

    const processingButton = (await screen.findByText("Processing conversation"))
      .closest("button")!;
    const completedButton = screen.getByText("Completed conversation").closest("button")!;
    const processingItem = processingButton.closest(
      '[data-slot="workspace-conversation-item"]',
    ) as HTMLElement;
    const completedItem = completedButton.closest(
      '[data-slot="workspace-conversation-item"]',
    ) as HTMLElement;
    expect(processingItem.querySelector(
      '[data-slot="conversation-processing-indicator"]',
    )).toHaveClass("animate-spin", "end-10");
    expect(processingButton.querySelector(
      '[data-slot="conversation-processing-indicator"]',
    )).not.toBeInTheDocument();
    expect(completedItem.querySelector(
      '[data-slot="conversation-processing-indicator"]',
    )).not.toBeInTheDocument();
    expect(processingItem.querySelector('[data-slot="badge"]')).not.toBeInTheDocument();
    expect(completedItem.querySelector('[data-slot="badge"]')).not.toBeInTheDocument();
    expect(processingButton).toHaveClass("pr-16");
    expect(completedButton).toHaveClass("pr-11");

    fireEvent.keyDown(within(processingItem).getByRole("button", {
      name: "Conversation actions",
    }), { key: "Enter", code: "Enter" });
    expect(await screen.findByRole("menuitem", { name: "Delete" })).toHaveAttribute(
      "data-disabled",
    );
  });

it("deletes an idle conversation from the extensible ellipsis menu", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let archiveCalls = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname ===
          "/api/v1/workspace/conversations/conv-supported-001/archive" &&
        (init?.method ?? "GET") === "POST"
      ) {
        archiveCalls += 1;
        return jsonResponse({
          conversation: {
            ...conversationDetail,
            status: "archived",
          },
          audit_event_ref: "audit-conversation-archive",
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    const conversationButton = await screen.findByRole("button", {
      name: /Example conversation/,
    });
    const conversationItem = conversationButton.closest(
      '[data-slot="workspace-conversation-item"]',
    );
    const actionsButton = screen.getByRole("button", {
      name: "Conversation actions",
    });
    expect(conversationItem).toHaveClass("relative");
    expect(conversationButton).toHaveClass("w-full", "pr-11");
    expect(actionsButton).toHaveClass(
      "absolute",
      "end-1",
      "cursor-pointer",
      "opacity-0",
      "group-hover:opacity-70",
    );
    fireEvent.keyDown(actionsButton, { key: "Enter", code: "Enter" });
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(1);
    const deleteMenuItem = within(menu).getByRole("menuitem", { name: "Delete" });
    expect(deleteMenuItem).toHaveClass("cursor-pointer");
    fireEvent.click(deleteMenuItem);

    let confirmDialog = await screen.findByRole("alertdialog");
    expect(within(confirmDialog).getByText(
      /hidden from your conversation list but retained for administrator audit/,
    )).toBeInTheDocument();
    expect(archiveCalls).toBe(0);
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

    fireEvent.keyDown(actionsButton, { key: "Enter", code: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    confirmDialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(archiveCalls).toBe(1));
    await waitFor(() => expect(
      screen.queryByRole("button", { name: /Example conversation/ }),
    ).not.toBeInTheDocument());
    expect(window.location.pathname).toBe("/workspace");
  });

it("deleting the open conversation replaces the route and clears the thread", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname ===
          "/api/v1/workspace/conversations/conv-supported-001/archive" &&
        (init?.method ?? "GET") === "POST"
      ) {
        return jsonResponse({
          conversation: {
            ...conversationDetail,
            status: "archived",
          },
          audit_event_ref: "audit-conversation-archive",
        });
      }
      return normalFetch(input, init);
    });
    const replaceState = vi.spyOn(window.history, "replaceState");
    render(<App />);

    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", {
      name: "Conversation actions",
    }), { key: "Enter", code: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole(
      "button",
      { name: "Delete" },
    ));

    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(replaceState).toHaveBeenCalledWith({}, "", "/workspace");
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

it("does not replace a newer conversation route when archive settles late", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let settleArchive: (response: Response) => void = () => undefined;
    const delayedArchive = new Promise<Response>((resolve) => {
      settleArchive = resolve;
    });
    const secondarySummary = {
      ...conversationSummaries[0],
      conversation_id: "conv-secondary-001",
      title: "Secondary conversation",
    };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/workspace/conversations" && method === "GET") {
        return jsonResponse({
          conversations: [conversationSummaries[0], secondarySummary],
        });
      }
      if (
        url.pathname ===
          "/api/v1/workspace/conversations/conv-supported-001/archive" &&
        method === "POST"
      ) {
        return delayedArchive;
      }
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-secondary-001" &&
        method === "GET"
      ) {
        return jsonResponse({
          ...conversationDetail,
          conversation_id: "conv-secondary-001",
          title: "Secondary conversation",
          turns: [],
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    const activeTitle = await screen.findByRole("button", {
      name: /Example conversation/,
    });
    fireEvent.keyDown(
      within(activeTitle.closest('[data-slot="workspace-conversation-item"]')!)
        .getByRole("button", { name: "Conversation actions" }),
      { key: "Enter", code: "Enter" },
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole(
      "button",
      { name: "Delete" },
    ));
    fireEvent.click(await screen.findByRole("button", {
      name: /Secondary conversation/,
    }));
    await waitFor(() => expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-secondary-001",
    ));

    await act(async () => {
      settleArchive(await jsonResponse({
        conversation: { ...conversationDetail, status: "archived" },
        audit_event_ref: "audit-conversation-archive",
      }));
      await Promise.resolve();
    });

    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-secondary-001",
    );
    expect(await screen.findByRole("button", { name: /Secondary conversation/ }))
      .toBeInTheDocument();
  });

it("clears a route opened while that conversation archive is pending", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let settleArchive: (response: Response) => void = () => undefined;
    const delayedArchive = new Promise<Response>((resolve) => {
      settleArchive = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname ===
          "/api/v1/workspace/conversations/conv-supported-001/archive" &&
        (init?.method ?? "GET") === "POST"
      ) {
        return delayedArchive;
      }
      return normalFetch(input, init);
    });
    render(<App />);

    const title = await screen.findByRole("button", { name: /Example conversation/ });
    fireEvent.keyDown(
      within(title.closest('[data-slot="workspace-conversation-item"]')!)
        .getByRole("button", { name: "Conversation actions" }),
      { key: "Enter", code: "Enter" },
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole(
      "button",
      { name: "Delete" },
    ));
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();

    await act(async () => {
      settleArchive(await jsonResponse({
        conversation: { ...conversationDetail, status: "archived" },
        audit_event_ref: "audit-conversation-archive",
      }));
      await Promise.resolve();
    });

    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();
  });

it("keeps a new conversation blank when an older selection settles later", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let settleConversation: (response: Response) => void = () => undefined;
    const delayedConversation = new Promise<Response>((resolve) => {
      settleConversation = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return delayedConversation;
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/ }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/workspace/conversations/conv-supported-001",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByLabelText("Message")).toHaveValue("");

    await act(async () => {
      settleConversation(await jsonResponse(conversationDetail));
      await Promise.resolve();
    });
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "A synthetic document-backed statement.",
    )).not.toBeInTheDocument();
  });

it("expands the collapsed Workspace sidebar from Atlas without resetting the conversation", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Example conversation/ }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Collapse conversation history" }));
    const workspaceSidebar = document.querySelector('[data-slot="workspace-context-sidebar"]');
    expect(workspaceSidebar).toHaveClass("w-14");

    fireEvent.click(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Atlas" }));
    expect(workspaceSidebar).toHaveClass("w-72");
    expect(screen.getByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
  });
});
