import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import i18n from "./i18n";
import {
  claimsInPresentationOrder,
  MessageSources,
  sliceCodePoints,
} from "./features/workspace/WorkspaceFeature";
import { sessionQueryClient } from "./shared/session-query-client";
import { THEME_STORAGE_KEY } from "./shared/theme";
import {
  adminSession,
  adminDetailDto,
  adminWithProjectSession,
  answeredTurn,
  conversationDetail,
  cleanupAppTest,
  incompleteReadiness,
  memberSession,
  memberWithUnauthorizedProjectSession,
  memberWithoutProjects,
  mockApi,
  operatorSession,
  projectAdminSession,
  projectUploaderSession,
  prepareAppTest,
  readyReadiness,
  runtimeTraceDetail,
  teamAdminSession,
  teamUploaderSession,
  unauthenticated,
  workspaceDetailDto,
  workspaceProjectionDto,
  runtimeEventStream,
} from "./App.test-support";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

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

const adminListScenarios = [
  {
    route: "/admin/users",
    listPath: "/api/v1/admin/users",
    heading: "Users",
    loadingTitle: "Loading users",
    emptyTitle: "No users found",
  },
  {
    route: "/admin/teams",
    listPath: "/api/v1/admin/teams",
    heading: "Teams",
    loadingTitle: "Loading teams",
    emptyTitle: "No teams yet",
  },
  {
    route: "/admin/projects",
    listPath: "/api/v1/admin/projects",
    heading: "Projects",
    loadingTitle: "Loading projects",
    emptyTitle: "No projects yet",
  },
  {
    route: "/admin/models",
    listPath: "/api/v1/admin/config/provider-connections",
    heading: "Models",
    loadingTitle: "Loading models",
    emptyTitle: "No provider connections yet",
  },
  {
    route: "/admin/agents",
    listPath: "/api/v1/admin/agent-users",
    heading: "Agent access",
    loadingTitle: "Loading agents",
    emptyTitle: "No agents yet",
  },
  {
    route: "/admin/document-library",
    listPath: "/api/v1/admin/document-library",
    heading: "Document Library",
    loadingTitle: "Loading document library",
    emptyTitle: "No documents yet",
  },
  {
    route: "/admin/plugins",
    listPath: "/api/v1/admin/processing-plugins",
    heading: "Processing Plugins",
    loadingTitle: "Loading processing plugins",
    emptyTitle: "No processing plugins",
  },
  {
    route: "/admin/audit/conversations",
    listPath: "/api/v1/admin/conversations",
    heading: "Audit",
    loadingTitle: "Loading audit data",
    emptyTitle: "No conversations yet",
  },
];

function emptyAdminListResponse(pathname: string): unknown {
  if (pathname === "/api/v1/admin/users") {
    return { users: [] };
  }
  if (pathname === "/api/v1/admin/teams") {
    return { teams: [], memberships: [] };
  }
  if (pathname === "/api/v1/admin/projects") {
    return { projects: [] };
  }
  if (pathname === "/api/v1/admin/config/model-routes") {
    return { routes: [], default_route_id: null };
  }
  if (pathname === "/api/v1/admin/config/provider-connections") {
    return { connections: [] };
  }
  if (pathname === "/api/v1/admin/document-library") {
    return { documents: [] };
  }
  if (pathname === "/api/v1/admin/processing-runs") {
    return { items: [] };
  }
  if (pathname === "/api/v1/processing/jobs") {
    return { jobs: [] };
  }
  if (pathname === "/api/v1/admin/agent-users") {
    return { agents: [] };
  }
  if (pathname === "/api/v1/admin/processing-plugins") {
    return { items: [] };
  }
  if (pathname === "/api/v1/admin/processing-profiles") {
    return { items: [] };
  }
  if (pathname === "/api/v1/admin/audit/events") {
    return { events: [] };
  }
  if (pathname === "/api/v1/admin/conversations") {
    return { conversations: [] };
  }
  return null;
}

function mockAdminListState(pathname: string, state: "pending" | "failed") {
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    if (url.pathname === "/api/v1/auth/session" && method === "GET") {
      return jsonResponse(adminWithProjectSession);
    }
    if (url.pathname === "/api/v1/ops/readiness") {
      return jsonResponse(readyReadiness);
    }
    if (url.pathname === pathname && method === "GET") {
      if (state === "pending") {
        return new Promise<Response>(() => {});
      }
      return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 500);
    }
    if (method === "GET") {
      const emptyResponse = emptyAdminListResponse(url.pathname);
      if (emptyResponse) {
        return jsonResponse(emptyResponse);
      }
    }
    return jsonResponse({ message_code: "common.rejected", message_params: {} }, 404);
  });
}

function mockPendingAdminList(pathname: string) {
  mockAdminListState(pathname, "pending");
}

function mockFailedAdminList(pathname: string) {
  mockAdminListState(pathname, "failed");
}

async function confirmDestructiveAction(confirmLabel: RegExp | string) {
  const alertDialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(alertDialog).getByRole("button", { name: confirmLabel }));
}

function selectDialogTab(dialog: HTMLElement, name: RegExp | string) {
  const tab = within(dialog).getByRole("tab", { name });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
}

async function chooseDialogOption(
  dialog: HTMLElement,
  label: RegExp | string,
  optionName: RegExp | string,
) {
  const trigger = within(dialog).getByLabelText(label);
  fireEvent.click(trigger);
  const listbox = await screen.findByRole("listbox");
  expect(listbox).toHaveClass("bg-popover");
  fireEvent.click(within(listbox).getByRole("option", { name: optionName }));
  return trigger;
}

function expectModelRuntimePolicyDraft(
  dialog: HTMLElement,
  expected: Record<string, string | number>,
) {
  for (const [label, value] of Object.entries(expected)) {
    expect(within(dialog).getByLabelText(label)).toHaveValue(value);
  }
}

async function openAccountMenu() {
  const trigger = screen.getByRole("button", { name: "Account menu" });
  expect(trigger).toHaveAttribute("title", "Account menu");
  fireEvent.keyDown(trigger, { key: "Enter", code: "Enter" });
  return {
    settings: await screen.findByRole("menuitem", { name: "Settings" }),
    signOut: await screen.findByRole("menuitem", { name: "Sign out" }),
  };
}

describe("Atlas production web", () => {
  it("shows an accessible application loader while session is unresolved", async () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));

    render(<App />);

    expect(screen.getByRole("status", { name: "Loading Atlas Production" }))
      .toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("heading", { name: "Atlas Production" }))
      .not.toBeInTheDocument();
  });

  it("does not request Ops readiness before entering Ops", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let readinessRequests = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/ops/readiness") readinessRequests += 1;
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(readinessRequests).toBe(0);
  });

  it("uses the login response without repeating session or readiness requests", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    const normalFetch = global.fetch;
    let sessionReads = 0;
    let readinessReads = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/auth/session" && method === "GET") sessionReads += 1;
      if (url.pathname === "/api/v1/ops/readiness" && method === "GET") readinessReads += 1;
      return normalFetch(input, init);
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Atlas Production" });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "admin@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "TestLoginPassword!42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(sessionReads).toBe(1);
    expect(readinessReads).toBe(0);
  });

  it("/login shows unauthenticated state and can sign in through local/dev adapter", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Atlas Production" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    const signIn = screen.getByRole("button", { name: /sign in/i });
    expect(signIn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "admin@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "TestLoginPassword!42" },
    });
    expect(signIn).toBeEnabled();
    fireEvent.click(signIn);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Atlas Admin")).toBeInTheDocument();
  });

  it("can switch the production UI between English and Traditional Chinese", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText("繁中"));

    expect(await screen.findByRole("button", { name: "登入" })).toBeInTheDocument();
    expect(screen.getByText("本機開發身分")).toBeInTheDocument();
  });

  it("/ routes authenticated users into the workspace", async () => {
    window.history.pushState({}, "", "/");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
  });

  it("/workspace presents pure chat and keeps management out of the default shell", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.queryByText(
      "Atlas selects and retrieves relevant sources in multiple steps from the documents you can currently access.",
    )).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Knowledge scope" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New conversation" })).toBeInTheDocument();
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
    const composer = screen.getByLabelText("Message");
    expect(composer).toHaveAttribute(
      "placeholder",
      "For example: summarize a document or compare information across related sources.",
    );
    await waitFor(() => expect(composer).toHaveFocus());
    expect(composer.parentElement).toHaveClass("items-center");
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
    expect(sidebarHeader).toHaveClass("border-b");
    const newConversation = screen.getByRole("button", { name: "New conversation" });
    expect(newConversation.nextElementSibling).toHaveAccessibleName("Knowledge Library");
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
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Knowledge Library" }))
      .toBeInTheDocument();
    expect(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Account menu" }))
      .toHaveAttribute("data-presentation", "compact");
    fireEvent.click(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Atlas" }));
    expect(workspaceSidebar).toHaveClass("w-72");

    fireEvent.click(screen.getByRole("button", { name: "Open conversation history" }));
    const mobileHistory = await screen.findByRole("dialog", { name: "Conversations" });
    expect(within(mobileHistory).getByRole("button", { name: "New conversation" })).toBeInTheDocument();
    expect(within(mobileHistory).getByRole("button", { name: "Knowledge Library" })).toBeInTheDocument();
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
    const answeredHistory = await screen.findByRole("button", { name: /PCIe lane target/ });
    expect(answeredHistory).toBeInTheDocument();
    expect(within(answeredHistory).getByText("answered")).toBeInTheDocument();
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

    const workspaceSource = readFileSync("src/features/workspace/WorkspaceFeature.tsx", "utf8");
    const messageScrollerSource = readFileSync("src/components/ui/message-scroller.tsx", "utf8");
    expect(workspaceSource).toContain("<MessageScrollerProvider autoScroll>");
    expect(workspaceSource).not.toMatch(/<MessageScrollerItem[^>]*scrollAnchor/);
    expect(messageScrollerSource).not.toContain("contain-intrinsic-size:auto_10rem");
    expect(messageScrollerSource).not.toContain("content-visibility:auto");
  });

  it("uses one radius token and warm semantic theme tokens", () => {
    const styles = readFileSync("src/styles.css", "utf8");

    expect(styles).toContain("--radius: 0.75rem;");
    expect(styles).toContain("--radius-md: var(--radius);");
    expect(styles).toContain("--radius-lg: var(--radius);");
    expect(styles).toContain("--radius-xl: var(--radius);");
    expect(styles).toContain("cursor: pointer;");
    expect(styles).toContain("cursor: text;");
    expect(styles).toContain("--background: 42 38% 96%;");
    expect(styles).not.toContain("--background: 210 40% 98%;");
    for (const token of [
      "--background",
      "--foreground",
      "--card",
      "--popover",
      "--primary",
      "--secondary",
      "--muted",
      "--accent",
      "--border",
      "--input",
      "--ring",
      "--sidebar",
      "--evidence",
      "--warning",
      "--info",
    ]) {
      expect(styles.match(new RegExp(`${token}:`, "g"))?.length).toBeGreaterThanOrEqual(2);
    }
  });

  it("/ routes unauthenticated users to login", async () => {
    window.history.pushState({}, "", "/");
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Atlas Production" })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
  });

  it("/workspace shows disabled, answered, refused, failed-closed, and copy states", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    const ask = await screen.findByRole("button", { name: /^send$/i });
    expect(ask).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the controlled impedance target for the PCIe reference lane?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(
      await screen.findByText(
        "The PCIe reference lane controlled impedance target is 85 ohms differential.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What changed in the routing policy?" },
    });
    expect(
      screen.getByRole("region", {
        name: "Answer verification and cited documents",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Verification passed")).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Knowledge scope" })).not.toBeInTheDocument();
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
  });

  it("/workspace creates conversations without a caller-selected knowledge scope", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the controlled impedance target for the PCIe reference lane?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(
      await screen.findByText(
        "The PCIe reference lane controlled impedance target is 85 ohms differential.",
      ),
    ).toBeInTheDocument();
    const createConversationCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/workspace/conversations" && init?.method === "POST",
    );
    expect(createConversationCall).toBeDefined();
    const body = JSON.parse(String(createConversationCall![1]!.body));
    expect(Object.keys(body)).toEqual(["title", "response_language"]);
    expect(body.title).toBe("What is the controlled impedance target for the PCIe reference l");
    expect(body.response_language).toBe("en");
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /PCIe lane target/ }))
      .toHaveAttribute("data-variant", "secondary");
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
        "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).not.toBeInTheDocument();

    act(() => window.history.back());
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/workspace/conversations/conv-supported-001",
      ),
    );
    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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

  it("reconnects a nonterminal conversation route and replaces its turn", async () => {
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
        return Promise.resolve(
          runtimeEventStream(answeredTurn.execution_id, "terminal_completed"),
        );
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

    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    expect(detailReads).toBe(2);
    expect(screen.getAllByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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
    expect((await screen.findAllByText("PCIe lane target")).length).toBeGreaterThan(0);
    expect(screen.getByText("Chat with Atlas")).toBeInTheDocument();
    expect(
      screen.queryByText("What is the controlled impedance target for the PCIe reference lane?"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Knowledge scope" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /PCIe lane target/i }));
    expect(
      await screen.findByText("What is the controlled impedance target for the PCIe reference lane?"),
    ).toBeInTheDocument();
    expect(container.querySelector('time[datetime="2026-07-09T00:00:01+00:00"]')).not.toBeNull();

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "What is the controlled impedance target for the PCIe reference lane?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("You")).toBeInTheDocument();
    expect(await screen.findAllByText("Atlas")).not.toHaveLength(0);
    expect(
      await screen.findAllByText(
        "The PCIe reference lane controlled impedance target is 85 ohms differential.",
      ),
    ).not.toHaveLength(0);
    expect(
      (await screen.findAllByRole("region", {
        name: "Answer verification and cited documents",
      })).length,
    ).toBeGreaterThan(0);
    expect((await screen.findAllByText("Verification passed")).length).toBeGreaterThan(0);
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

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/i }));

    expect(await screen.findByText("Verification not passed")).toBeInTheDocument();
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

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/i }));

    expect(
      await screen.findByRole("heading", { name: "Deployment result", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Verification passed")).toBeInTheDocument();
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

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/i }));

    expect(
      await screen.findByRole("region", {
        name: "Answer verification and cited documents",
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
    expect(
      screen.getByText(
        "The visual watermark is for identification only. It does not prevent downloads or guarantee traceability.",
      ),
    ).toBeInTheDocument();

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

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/i }));
    const evidenceStatus = (await screen.findByText("Verification not passed"))
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

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/i }));
    expect(await screen.findByText(
      "Verification not passed",
    )).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Run degraded validation" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => expect(screen.getAllByText(
      "Verification not passed",
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
        "The PCIe reference lane controlled impedance target is 85 ohms differential.",
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

  it("/settings exposes full management entry points for admins", async () => {
    window.history.pushState({}, "", "/settings");
    mockApi(adminWithProjectSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    const settingsSurface = container.querySelector("main section");
    expect(settingsSurface).toHaveClass("w-full");
    expect(settingsSurface).not.toHaveClass("mx-auto");
    expect(settingsSurface).not.toHaveClass("max-w-6xl");
    const mobileNavigationTrigger = screen.getByRole("button", {
      name: "Open navigation",
    });
    expect(mobileNavigationTrigger).toHaveClass("md:hidden");
    const management = screen.getByRole("navigation", { name: "Management" });
    const footer = management.parentElement?.querySelector(
      '[data-slot="contextual-sidebar-footer"]',
    );
    const scrollRegion = management.querySelector('[data-slot="management-nav-scroll"]');
    expect(scrollRegion).toHaveClass("flex-1", "overflow-y-auto");
    expect(footer).toHaveClass("shrink-0");
    expect(within(footer as HTMLElement).getByRole("button", { name: "Account menu" })).toHaveClass(
      "w-full",
    );
    const sidebarHeader = management.parentElement?.querySelector(
      '[data-slot="contextual-sidebar-header"]',
    );
    expect(within(sidebarHeader as HTMLElement).getByRole("button", { name: "Atlas" }))
      .toBeInTheDocument();
    expect(within(sidebarHeader as HTMLElement).queryByRole("button", { name: "Downloads" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Workspace" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Management" })).toBeInTheDocument();
    for (const label of [
      "Users",
      "Teams",
      "Projects",
      "Document Library",
      "Models",
      "Agents",
      "Processing Plugins",
      "Audit",
      "System Status",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(mobileNavigationTrigger);
    const mobileNavigation = await screen.findByRole("dialog", { name: "Management" });
    expect(within(mobileNavigation).getByRole("navigation", { name: "Management" })).toBeInTheDocument();
    expect(within(mobileNavigation).getByRole("button", { name: "Account menu" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Account menu" })).toHaveLength(1);
    fireEvent.keyDown(mobileNavigation, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Users" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/users"));
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
  });

  it("/settings persists light and dark appearance only in this browser", async () => {
    window.history.pushState({}, "", "/settings");
    mockApi(memberSession, readyReadiness);
    const firstRender = render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    fireEvent.click(screen.getByLabelText("Use dark mode"));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(vi.mocked(global.fetch).mock.calls.some(([input]) =>
      String(input).includes("preference") || String(input).includes("theme")
    )).toBe(false);

    firstRender.unmount();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));

    fireEvent.click(screen.getByLabelText("Use light mode"));
    await waitFor(() => expect(document.documentElement).toHaveClass("light"));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
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

  it("member users cannot render admin controls through direct URLs", async () => {
    // acceptance-scenario:T-MEM-03
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    const productNavigation = screen.getByRole("navigation", { name: "Product" });
    expect(within(productNavigation).getByRole("button", { name: "Workspace" })).toBeInTheDocument();
    expect(within(productNavigation).queryByRole("button", { name: "Knowledge Library" }))
      .not.toBeInTheDocument();
    expect(within(productNavigation).getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Account menu" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atlas" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create invite/i })).not.toBeInTheDocument();
  });

  it("project uploaders cannot render project management through direct URLs", async () => {
    window.history.pushState({}, "", "/admin/projects");
    mockApi(projectUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();
  });

  it("member settings do not expose management entry points", async () => {
    window.history.pushState({}, "", "/settings");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getAllByText("Engineer One").length).toBeGreaterThan(0);
    const productNavigation = screen.getByRole("navigation", { name: "Product" });
    expect(within(productNavigation).getByRole("button", { name: "Settings" }))
      .toHaveAttribute("aria-current", "page");
    expect(container.querySelector('[data-slot="contextual-sidebar-footer"]')).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    expect((await openAccountMenu()).signOut).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Management" })).not.toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("System Status")).not.toBeInTheDocument();
  });

  it("Knowledge Library keeps the shared Workspace sidebar and account footer", async () => {
    window.history.pushState({}, "", "/library");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Product" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Knowledge Library" }))
      .toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Atlas" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Downloads" })).not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/workspace/tag-scope",
      expect.any(Object),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open conversation history" }));
    const mobileWorkspaceNavigation = await screen.findByRole("dialog", { name: "Conversations" });
    expect(within(mobileWorkspaceNavigation).getByRole("button", { name: "Knowledge Library" }))
      .toHaveAttribute("aria-current", "page");
    expect(within(mobileWorkspaceNavigation).getByRole("button", { name: "Account menu" }))
      .toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Account menu" })).toHaveLength(1);
    fireEvent.click((await openAccountMenu()).signOut);
    expect(await screen.findByRole("heading", { name: "Atlas Production" })).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/auth/session",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("team admin settings group only scoped identity and knowledge tools", async () => {
    window.history.pushState({}, "", "/settings");
    mockApi(teamAdminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Identity & Access")).toBeInTheDocument();
    expect(screen.getByText("Knowledge Content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Teams" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Document Library" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Permissions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Models" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Audit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "System Status" })).not.toBeInTheDocument();
  });

  it("operator settings expose only System Status and direct ops remains protected", async () => {
    // acceptance-scenario:OP-01
    window.history.pushState({}, "", "/settings");
    mockApi(operatorSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Management" })).toBeInTheDocument();
    expect(screen.getByText("System Operations")).toBeInTheDocument();
    expect(screen.getByText("System Status")).toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Teams")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "System Status" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/ops"));
    expect(await screen.findByRole("heading", { name: "Ops" })).toBeInTheDocument();
  });

  it("operator users can inspect ops but cannot render admin setup controls", async () => {
    window.history.pushState({}, "", "/admin/ops");
    mockApi(operatorSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Ops" })).toBeInTheDocument();
    const management = screen.getByRole("navigation", { name: "Management" });
    expect(within(management).getByRole("button", { name: "System Status" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(management).queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Admin setup" })).not.toBeInTheDocument();
    expect(await screen.findByText("Finish workspace setup")).toBeInTheDocument();
    expect(screen.getAllByText("Admin action").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /open projects/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open models/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open permissions/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open documents/i })).not.toBeInTheDocument();
  });

  it("Ops reports a settled readiness failure and recovers through Retry", async () => {
    window.history.pushState({}, "", "/admin/ops");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let readinessAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/ops/readiness" && (init?.method ?? "GET") === "GET") {
        readinessAttempts += 1;
        if (readinessAttempts === 2) {
          return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Ops" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("The latest service and setup status could not be loaded."))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Workspace is ready."))
      .toBeInTheDocument();
    expect(readinessAttempts).toBe(3);
  });

  it("project admins manage only their own project members from Projects", async () => {
    // acceptance-scenario:P-AD-05 acceptance-scenario:SYS-03
    window.history.pushState({}, "", "/settings");
    mockApi(projectAdminSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Identity & Access")).toBeInTheDocument();
    expect(screen.getByText("Knowledge Content")).toBeInTheDocument();
    expect(screen.queryByText("AI & Automation")).not.toBeInTheDocument();
    expect(screen.queryByText("System Operations")).not.toBeInTheDocument();
    expect(screen.getByText("Document Library")).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Teams")).not.toBeInTheDocument();
    expect(screen.queryByText("Permissions")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Projects" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/projects"));
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByText("Admin Live Project")).toBeInTheDocument();
    expect(screen.queryByText("Signal Integrity Alpha")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Admin Live Project$/ }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    expect(await screen.findByText("Project profile is managed by system admins")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save project/i })).not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members",
      expect.any(Object),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/member-candidates",
      expect.any(Object),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Access" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/access"),
    );
    expect((await screen.findAllByText("Project Admin")).length).toBeGreaterThan(0);
    expect(container).not.toHaveTextContent(/grant-project-member|user-project-admin-001/);
    fireEvent.click(screen.getByRole("link", { name: "Admin Live Project" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Access" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/access"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Add access" }));
    const dialog = await screen.findByRole("dialog", { name: "Add access" });
    fireEvent.click(within(dialog).getByLabelText("Project member"));
    fireEvent.click(await screen.findByText("Engineer One"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add access" }));
    expect(await screen.findByText(/Project member is active/)).toBeInTheDocument();
    expect((await screen.findAllByText("Engineer One")).length).toBeGreaterThan(0);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members",
      expect.objectContaining({
        body: expect.stringMatching(
          /"subject_type":"user".*"subject_id":"user-engineer-001".*"role":"viewer"/,
        ),
        method: "POST",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Invite new user" }));
    expect(await screen.findByRole("dialog", { name: "Invite new user" })).toBeInTheDocument();
  });

  it("replaces Project detail with the directory after the current admin revokes own access", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let revokedOwnAccess = false;
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/auth/session" && method === "GET" && revokedOwnAccess) {
        return jsonResponse({ ...projectAdminSession, available_projects: [] });
      }
      const response = await normalFetch(input, init);
      if (
        url.pathname ===
          "/api/v1/admin/projects/proj-admin-live/members/grant-project-member-proj-admin-live-user-user-project-admin-001" &&
        method === "DELETE"
      ) {
        revokedOwnAccess = true;
      }
      return response;
    });

    render(<App />);
    const removeOwnAccess = await screen.findByRole("button", {
      name: /remove Project Admin/i,
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add access" })).toBeEnabled(),
    );
    const candidateReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) ===
        "/api/v1/admin/projects/proj-admin-live/member-candidates",
    ).length;

    fireEvent.click(removeOwnAccess);
    await confirmDestructiveAction(/remove/i);

    await waitFor(() => expect(window.location.pathname).toBe("/admin/projects"));
    expect(screen.queryByText("Access")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove Project Admin/i }),
    ).not.toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) ===
        "/api/v1/admin/projects/proj-admin-live/member-candidates",
    )).toHaveLength(candidateReadsBefore);
  });

  it("fails closed after Project self-revocation when the authority refresh fails", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let revokedOwnAccess = false;
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (
        revokedOwnAccess &&
        url.pathname === "/api/v1/auth/session" &&
        method === "GET"
      ) {
        return jsonResponse({ message_code: "identity.session_unavailable" }, 503);
      }
      const response = await normalFetch(input, init);
      if (
        url.pathname ===
          "/api/v1/admin/projects/proj-admin-live/members/grant-project-member-proj-admin-live-user-user-project-admin-001" &&
        method === "DELETE"
      ) {
        revokedOwnAccess = true;
      }
      return response;
    });

    render(<App />);
    const removeOwnAccess = await screen.findByRole("button", {
      name: /remove Project Admin/i,
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add access" })).toBeEnabled(),
    );
    const protectedReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/projects/proj-admin-live/members" ||
        String(input) === "/api/v1/admin/projects/proj-admin-live/member-candidates",
    ).length;

    fireEvent.click(removeOwnAccess);
    await confirmDestructiveAction(/remove/i);

    await waitFor(() => expect(window.location.pathname).toBe("/admin/projects"));
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Admin Live Project")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Access" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove Project Admin/i }),
    ).not.toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/projects/proj-admin-live/members" ||
        String(input) === "/api/v1/admin/projects/proj-admin-live/member-candidates",
    )).toHaveLength(protectedReadsBefore);
  });

  it("invalidates Project collection when authority refresh fails after breadcrumb navigation", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let revokedOwnAccess = false;
    let resolveFailedRefresh: (response: Response | PromiseLike<Response>) => void = () => {};
    const failedRefresh = new Promise<Response>((resolve) => {
      resolveFailedRefresh = resolve;
    });
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (
        revokedOwnAccess &&
        url.pathname === "/api/v1/auth/session" &&
        method === "GET"
      ) {
        return failedRefresh;
      }
      const response = await normalFetch(input, init);
      if (
        url.pathname ===
          "/api/v1/admin/projects/proj-admin-live/members/grant-project-member-proj-admin-live-user-user-project-admin-001" &&
        method === "DELETE"
      ) {
        revokedOwnAccess = true;
      }
      return response;
    });

    render(<App />);
    const removeOwnAccess = await screen.findByRole("button", {
      name: /remove Project Admin/i,
    });
    const sessionReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) => String(input) === "/api/v1/auth/session",
    ).length;
    fireEvent.click(removeOwnAccess);
    await confirmDestructiveAction(/remove/i);
    await waitFor(() =>
      expect(vi.mocked(global.fetch).mock.calls.filter(
        ([input]) => String(input) === "/api/v1/auth/session",
      ).length).toBeGreaterThan(sessionReadsBefore),
    );

    fireEvent.click(screen.getByRole("link", { name: "Projects" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/projects"));
    expect(screen.getByText("Admin Live Project")).toBeInTheDocument();

    resolveFailedRefresh(
      jsonResponse({ message_code: "identity.session_unavailable" }, 503),
    );

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Admin Live Project")).not.toBeInTheDocument();
  });

  it("admin users can act on setup blockers from ops", async () => {
    // acceptance-scenario:SYS-09
    window.history.pushState({}, "", "/admin/ops");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Ops" })).toBeInTheDocument();
    expect(await screen.findByText("Prepare project evidence")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open documents/i }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/document-library"));
    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
  });

  it("default shell hides admin navigation until settings", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Teams" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Permissions" })).not.toBeInTheDocument();
    fireEvent.click((await openAccountMenu()).settings);
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByText("Teams")).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("Document Library")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Atlas" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
  });

  for (const scenario of adminListScenarios) {
    it(`${scenario.route} keeps loading state separate from empty state`, async () => {
      window.history.pushState({}, "", scenario.route);
      mockPendingAdminList(scenario.listPath);
      render(<App />);

      expect(await screen.findByRole("heading", { name: scenario.heading })).toBeInTheDocument();
      expect(screen.getByText(scenario.loadingTitle)).toBeInTheDocument();
      expect(screen.getByRole("status", { name: scenario.loadingTitle }))
        .toHaveAttribute("aria-busy", "true");
      expect(screen.queryByText(scenario.emptyTitle)).not.toBeInTheDocument();
    });

    it(`${scenario.route} shows load failure instead of empty state`, async () => {
      window.history.pushState({}, "", scenario.route);
      mockFailedAdminList(scenario.listPath);
      render(<App />);

      expect(await screen.findByRole("heading", { name: scenario.heading })).toBeInTheDocument();
      expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
      expect(screen.getByText("The requested data is unavailable.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
      expect(screen.queryByText(scenario.emptyTitle)).not.toBeInTheDocument();
    });
  }

  it("/admin/document-library waits for its scope data before showing settled controls", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/teams" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return new Promise<Response>(() => {});
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading document library" }))
      .toBeInTheDocument();
    expect(screen.queryByLabelText("Target")).not.toBeInTheDocument();
  });

  it("/admin/document-library reports scope-source failure and retries its fallback", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    let scopeAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/teams" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
      }
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        scopeAttempts += 1;
        if (scopeAttempts === 1) {
          return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    expect(screen.queryByLabelText("Target")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    const target = await screen.findByLabelText("Target");
    fireEvent.click(target);
    expect(await screen.findByRole("option", { name: /Team: Platform/ })).toBeInTheDocument();
    expect(scopeAttempts).toBe(2);
  });

  it("/admin/projects owns the canonical Project Members controls", async () => {
    // acceptance-scenario:SYS-04
    window.history.pushState({}, "", "/admin/projects");
    mockApi(adminSession, incompleteReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect((await screen.findAllByText("Admin Live Project")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("Default governed access")).length).toBeGreaterThan(0);
    expect(container.querySelector('[data-slot="project-directory-layout"]'))
      .toHaveClass("w-full");
    expect(container.querySelector('[data-slot="project-directory-layout"]'))
      .not.toHaveClass("xl:grid-cols-[minmax(0,1fr)_420px]");
    expect(container).not.toHaveTextContent("proj-admin-live");
    expect(container).not.toHaveTextContent("policy-default-governed");
    expect(screen.queryByRole("button", { name: /create invite/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /grant membership/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Project name")).not.toBeInTheDocument();
    const projectRow = screen.getByRole("button", { name: /^Admin Live Project$/ });
    expect(projectRow).toHaveAttribute("tabindex", "0");
    projectRow.focus();
    expect(projectRow).toHaveFocus();
    fireEvent.keyDown(projectRow, { key: "Enter" });
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    expect(screen.getByRole("tab", { name: "Access" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Project ID")).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Select project")).not.toBeInTheDocument();
    expect(within(dialog).getAllByText("Admin Live Project").length).toBe(1);
    expect(within(dialog).getAllByText("Default governed access").length).toBeGreaterThan(0);
    fireEvent.change(within(dialog).getByLabelText("Project name"), {
      target: { value: "Admin Live Project Edited" },
    });
    expect(within(dialog).queryByRole("tab", { name: /documents/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Open Document Library" }))
      .not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /save project/i }));
    expect(await screen.findByText(/project is updated/i)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live",
      expect.objectContaining({
        body: expect.stringMatching(
          /"name":"Admin Live Project Edited".*"policy_profile_id":"policy-default-governed"/,
        ),
        method: "PATCH",
      }),
    );
    fireEvent.click(screen.getByRole("link", { name: "Projects" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/projects"));
    fireEvent.click(screen.getByRole("button", { name: /create project/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Project name"), {
      target: { value: "Discarded Project" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create project/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Project name")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Project name"), {
      target: { value: "Admin Live Project" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /create project/i }));
    expect(await screen.findByText(/Project is ready/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create project/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Project name")).toHaveValue("");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(screen.queryByRole("button", { name: /configure route/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /test route/i })).not.toBeInTheDocument();
  });

  it("/admin/models manages provider connections, encrypted-key entry, and models", async () => {
    // acceptance-scenario:SYS-05
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes",
      expect.any(Object),
    );
    expect(await screen.findByText("OpenAI production")).toBeInTheDocument();
    expect(screen.getByText("Migrated provider")).toBeInTheDocument();
    expect(screen.getByText("Manual provider")).toBeInTheDocument();
    expect(screen.getAllByText("API key required").length).toBeGreaterThan(0);
    const connectionsTab = screen.getByRole("tab", { name: "Provider connections" });
    const modelsTab = screen.getByRole("tab", { name: "Models" });
    const answerBehaviorTab = screen.getByRole("tab", { name: "Answer behavior" });
    expect(connectionsTab).toHaveAttribute("aria-selected", "true");
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/answer-behavior",
      expect.any(Object),
    );
    expect(screen.queryByText("Primary provider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload document/i })).not.toBeInTheDocument();

    fireEvent.mouseDown(answerBehaviorTab, { button: 0 });
    fireEvent.click(answerBehaviorTab);
    const guidance = await screen.findByLabelText("Custom guidance");
    expect(screen.getByText("Current revision 0")).toBeInTheDocument();
    expect(screen.getByText("0 / 2,000 characters")).toBeInTheDocument();
    fireEvent.change(guidance, { target: { value: "😀".repeat(2001) } });
    expect(screen.getByText("2001 / 2,000 characters")).toBeInTheDocument();
    expect(
      screen.getByText("Custom guidance cannot exceed 2,000 characters."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save guidance" })).toBeDisabled();
    fireEvent.change(guidance, {
      target: { value: "Prefer concise comparison tables." },
    });
    expect(screen.getByText("33 / 2,000 characters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save guidance" }));
    expect(await screen.findByText("Current revision 1")).toBeInTheDocument();
    const answerBehaviorPut = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/answer-behavior" &&
        init?.method === "PUT",
    );
    expect(JSON.parse(String(answerBehaviorPut![1]!.body))).toEqual(
      expect.objectContaining({
        custom_guidance: "Prefer concise comparison tables.",
        expected_revision: 0,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear guidance" }));
    expect(await screen.findByText("Current revision 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Custom guidance")).toHaveValue("");
    fireEvent.mouseDown(connectionsTab, { button: 0 });
    fireEvent.click(connectionsTab);

    fireEvent.click(screen.getByRole("button", { name: /set api key/i }));
    let dialog = await screen.findByRole("dialog");
    const keyInput = within(dialog).getByLabelText("API key");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(keyInput).toHaveValue("");
    fireEvent.change(keyInput, { target: { value: "rotated-secret-canary" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /save connection/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const connectionPatch = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) ===
          "/api/v1/admin/config/provider-connections/connection-migrated-required" &&
        init?.method === "PATCH",
    );
    expect(connectionPatch).toBeDefined();
    expect(JSON.parse(String(connectionPatch![1]!.body))).toEqual(
      expect.objectContaining({ api_key: "rotated-secret-canary", expected_revision: 1 }),
    );

    fireEvent.mouseDown(modelsTab, { button: 0 });
    fireEvent.click(modelsTab);
    expect(await screen.findByText("Primary provider")).toBeInTheDocument();
    expect(screen.getByText("Secondary provider")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add connection/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set api key/i })).not.toBeInTheDocument();
    expect(modelsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Models" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.getByText("Primary provider")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(
      "Copied from the current tested default route: Primary provider. Review it for this model before saving.",
    )).toBeInTheDocument();
    expectModelRuntimePolicyDraft(dialog, {
      "Tokenizer profile": "cl100k_base",
      "Maximum tool executions": 3,
      "Maximum provider invocations": 14,
      "Maximum catalog pages": 5,
      "Maximum search rounds": 6,
      "Maximum unique evidence items": 40,
      "Maximum retrieval repairs": 3,
      "Maximum schema retries per turn": 3,
      "Provider invocation timeout": 30,
      "Tool execution timeout": 20,
      "Turn timeout": 90,
      "Context window tokens": 16_000,
      "Maximum input tokens per invocation": 8_000,
      "Maximum output tokens per invocation": 2_000,
      "Maximum tool-result tokens per execution": 4_000,
      "Maximum total tokens per conversation": 20_000,
    });
    await chooseDialogOption(dialog, "Connection", "Manual provider");
    expect(await within(dialog).findByText("Discovery unavailable")).toBeInTheDocument();
    const modelNameInput = within(dialog).getByLabelText("Model or deployment name");
    expect(modelNameInput).toHaveAttribute("role", "combobox");
    expect(modelNameInput).not.toHaveAttribute("list");
    expect(
      within(dialog).getByRole("button", { name: "Show model suggestions" }),
    ).toBeInTheDocument();
    expect(document.querySelector("select, datalist")).toBeNull();
    fireEvent.focus(modelNameInput);
    fireEvent.input(modelNameInput, {
      target: { value: "azure-manual-deployment" },
      inputType: "insertText",
    });
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    fireEvent.pointerDown(within(dialog).getByLabelText("Route name"));
    fireEvent.focus(within(dialog).getByLabelText("Route name"));
    await waitFor(() =>
      expect(modelNameInput).toHaveValue("azure-manual-deployment"),
    );
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Azure manual deployment route" },
    });
    fireEvent.change(within(dialog).getByLabelText("Maximum output tokens per invocation"), {
      target: { value: "1000" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /save model/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const manualModelCreate = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/config/model-routes" &&
        init?.method === "POST" &&
        String(init.body).includes("azure-manual-deployment"),
    );
    expect(manualModelCreate).toBeDefined();
    expect(JSON.parse(String(manualModelCreate![1]!.body))).toEqual(
      expect.objectContaining({
        display_name: "Azure manual deployment route",
        model_name: "azure-manual-deployment",
        connection_id: "connection-manual-entry",
        runtime_policy: expect.objectContaining({
          tokenizer_profile: "cl100k_base",
          max_output_tokens_per_invocation: 1_000,
        }),
      }),
    );

    let secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(within(secondaryRow).getByRole("button", { name: /set default/i })).toBeDisabled();
    fireEvent.click(within(secondaryRow).getByRole("button", { name: /test route/i }));
    expect((await screen.findAllByText(/passed the controlled test/i)).length).toBeGreaterThan(0);
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(within(secondaryRow).getByText("Test passed")).toBeInTheDocument();
    const setDefault = within(secondaryRow).getByRole("button", { name: /set default/i });
    expect(setDefault).toBeEnabled();
    fireEvent.click(setDefault);
    expect(await screen.findByText(/Default model route is updated/i)).toBeInTheDocument();
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(within(secondaryRow).getByText("Default")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes/route-secondary-provider/default",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Discarded model" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Route name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Model or deployment name")).toHaveValue("");
    expect(within(dialog).getByText(
      "Copied from the current tested default route: Secondary provider. Review it for this model before saving.",
    )).toBeInTheDocument();
    expectModelRuntimePolicyDraft(dialog, {
      "Tokenizer profile": "o200k_base",
      "Maximum tool executions": 2,
      "Maximum provider invocations": 14,
      "Maximum catalog pages": 5,
      "Maximum search rounds": 6,
      "Maximum unique evidence items": 40,
      "Maximum retrieval repairs": 3,
      "Maximum schema retries per turn": 3,
      "Provider invocation timeout": 45,
      "Tool execution timeout": 30,
      "Turn timeout": 120,
      "Context window tokens": 32_000,
      "Maximum input tokens per invocation": 24_000,
      "Maximum output tokens per invocation": 4_000,
      "Maximum tool-result tokens per execution": 8_000,
      "Maximum total tokens per conversation": 48_000,
    });
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Production answer provider" },
    });
    const suggestedModelInput = within(dialog).getByLabelText("Model or deployment name");
    fireEvent.input(suggestedModelInput, {
      target: { value: "gpt-4.1" },
      inputType: "insertText",
    });
    const modelSuggestions = await screen.findByRole("listbox");
    fireEvent.click(
      await within(modelSuggestions).findByRole("option", { name: "gpt-4.1-mini" }),
    );
    expect(suggestedModelInput).toHaveValue("gpt-4.1-mini");
    fireEvent.click(within(dialog).getByRole("button", { name: /save model/i }));
    expect((await screen.findAllByText(/Model route is configured/i)).length).toBeGreaterThan(0);
    const modelCreate = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/config/model-routes" &&
        init?.method === "POST" &&
        String(init.body).includes("Production answer provider"),
    );
    expect(modelCreate).toBeDefined();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes",
      expect.objectContaining({
        body: expect.stringMatching(
          /"display_name":"Production answer provider".*"model_name":"gpt-4.1-mini".*"connection_id":"connection-openai-primary"/,
        ),
        method: "POST",
      }),
    );
    expect(String(modelCreate![1]!.body)).not.toContain("secret_ref");
    expect(String(modelCreate![1]!.body)).not.toContain("endpoint_url");
    expect(JSON.parse(String(modelCreate![1]!.body)).runtime_policy).toEqual(
      expect.objectContaining({
        tokenizer_profile: "o200k_base",
        context_window_tokens: 32_000,
        max_total_tokens_per_conversation: 48_000,
      }),
    );
  });

  it("/admin/models keeps the original minimal prefills without a tested default route", async () => {
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness, { modelRoutes: [] });
    render(<App />);

    const modelsTab = await screen.findByRole("tab", { name: "Models" });
    fireEvent.mouseDown(modelsTab, { button: 0 });
    fireEvent.click(modelsTab);
    expect(await screen.findByText("No models yet")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /add model/i }));
    const dialog = await screen.findByRole("dialog");

    expect(
      within(dialog).queryByText(/Copied from the current tested default route:/),
    ).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Tokenizer profile")).toHaveValue("cl100k_base");
    expect(within(dialog).getByLabelText("Maximum tool executions")).toHaveValue(12);
    expect(within(dialog).getByLabelText("Maximum provider invocations")).toHaveValue(14);
    expect(within(dialog).getByLabelText("Maximum catalog pages")).toHaveValue(5);
    expect(within(dialog).getByLabelText("Maximum search rounds")).toHaveValue(6);
    expect(within(dialog).getByLabelText("Maximum unique evidence items")).toHaveValue(40);
    expect(within(dialog).getByLabelText("Maximum retrieval repairs")).toHaveValue(3);
    expect(within(dialog).getByLabelText("Maximum schema retries per turn")).toHaveValue(3);
    expect(within(dialog).getByLabelText("Provider invocation timeout")).toHaveValue(60);
    expect(within(dialog).getByLabelText("Tool execution timeout")).toHaveValue(45);
    expect(within(dialog).getByLabelText("Turn timeout")).toHaveValue(240);
    expect(within(dialog).getByLabelText("Context window tokens")).toHaveValue(400_000);
    expect(within(dialog).getByLabelText("Maximum input tokens per invocation")).toHaveValue(272_000);
    expect(within(dialog).getByLabelText("Maximum output tokens per invocation")).toHaveValue(16_000);
    expect(
      within(dialog).getByLabelText("Maximum tool-result tokens per execution"),
    ).toHaveValue(64_000);
    expect(
      within(dialog).getByLabelText("Maximum total tokens per conversation"),
    ).toHaveValue(1_000_000);
    expect(
      within(dialog).getByRole("button", { name: /save model/i }),
    ).toBeDisabled();
  });

  it("/admin/plugins exposes controlled Plugins, Profiles, and Runs tabs", async () => {
    window.history.pushState({}, "", "/admin/plugins");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    const requestCounts = { plugins: 0, profiles: 0, runs: 0 };
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if ((init?.method ?? "GET") === "GET") {
        if (path === "/api/v1/admin/processing-plugins") requestCounts.plugins += 1;
        if (path === "/api/v1/admin/processing-profiles") {
          requestCounts.profiles += 1;
          if (requestCounts.profiles === 2) {
            return jsonResponse({ message_code: "plugins.unavailable" }, 503);
          }
        }
        if (path === "/api/v1/admin/processing-runs") requestCounts.runs += 1;
      }
      return fallbackFetch(input, init);
    });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Processing Plugins" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Plugins" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Profiles" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByLabelText("Plugin package")).toHaveAttribute("accept", ".atlas-plugin");
    expect(requestCounts).toEqual({ plugins: 1, profiles: 0, runs: 0 });
    const profilesTab = await screen.findByRole("tab", { name: "Profiles" });
    fireEvent.mouseDown(profilesTab, { button: 0 });
    fireEvent.click(profilesTab);
    expect(await screen.findByLabelText("Maximum regions per plan")).toHaveValue(100);
    expect(requestCounts).toEqual({ plugins: 1, profiles: 1, runs: 0 });
    expect(screen.getByLabelText("Maximum modules per region")).toHaveValue(4);
    expect(screen.getByLabelText("Maximum total plugin invocations")).toHaveValue(500);
    fireEvent.change(screen.getByLabelText("Maximum regions per plan"), {
      target: { value: "25" },
    });
    expect(screen.getByLabelText("Maximum regions per plan")).toHaveValue(25);
    const runsTab = screen.getByRole("tab", { name: "Runs" });
    fireEvent.mouseDown(runsTab, { button: 0 });
    fireEvent.click(runsTab);
    await waitFor(() => expect(requestCounts.runs).toBe(1));
    fireEvent.mouseDown(profilesTab, { button: 0 });
    fireEvent.click(profilesTab);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("button", { name: /^retry$/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Maximum regions per plan")).toHaveValue(25);
  });

  it("/admin/plugins keeps Profiles and Runs request state independent", async () => {
    window.history.pushState({}, "", "/admin/plugins");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let settleProfiles: (response: Response) => void = () => undefined;
    const profilesRequest = new Promise<Response>((resolve) => {
      settleProfiles = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/admin/processing-profiles" && (init?.method ?? "GET") === "GET") {
        return profilesRequest;
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Processing Plugins" })).toBeInTheDocument();
    const profilesTab = await screen.findByRole("tab", { name: "Profiles" });
    fireEvent.mouseDown(profilesTab, { button: 0 });
    fireEvent.click(profilesTab);
    const runsTab = screen.getByRole("tab", { name: "Runs" });
    fireEvent.mouseDown(runsTab, { button: 0 });
    fireEvent.click(runsTab);
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/processing-runs",
        expect.any(Object),
      ),
    );
    await act(async () => {
      settleProfiles(await jsonResponse({ message_code: "plugins.unavailable" }, 503));
      await Promise.resolve();
    });
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument();

    fireEvent.mouseDown(profilesTab, { button: 0 });
    fireEvent.click(profilesTab);
    expect(await screen.findByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });

  it("Project member candidates can fail without erasing current members", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (
        path === "/api/v1/admin/projects/proj-admin-live/member-candidates" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({ message_code: "project.candidates_unavailable" }, 503);
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Admin Live Project" })).toBeInTheDocument();
    expect((await screen.findAllByText("Project Admin")).length).toBeGreaterThan(0);
    expect(screen.queryByText("user-project-admin-001")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });

  it("/admin/plugins shows safe package provenance and sends revision-protected validation", async () => {
    window.history.pushState({}, "", "/admin/plugins");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/processing-plugins" && (init?.method ?? "GET") === "GET") {
        return Promise.resolve(jsonResponse({ items: [{
          plugin_id: "com.example.table",
          plugin_version: "1.2.3",
          package_digest: "sha256:0123456789abcdef",
          runtime_profile: "atlas-python-v1",
          plugin_kind: "region_processor",
          status: "uploaded",
          trust_provenance: "structurally_signed_pending_validation",
          revision: 7,
          diagnostic_code: "validation_required",
          canary_passed_at: null,
          active: false,
          descriptor: {
            signature_key_id: "enterprise-signing-key",
            license_expression: "Apache-2.0",
            sdk_api_version: 1,
            sbom_present: true,
            sbom_spdx_version: "SPDX-2.3",
            checksums_verified: true,
          },
        }] }));
      }
      if (url.pathname.endsWith("/com.example.table/versions/1.2.3/validate") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ status: "verified" }));
      }
      return fallbackFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByText(/enterprise-signing-key/)).toBeInTheDocument();
    expect(screen.getByText(/Apache-2.0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/processing-plugins/com.example.table/versions/1.2.3/validate",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "If-Match": "7" }),
      }),
    ));
  });

  it("/admin/plugins keeps processor version identity exact", async () => {
    window.history.pushState({}, "", "/admin/plugins");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/processing-plugins" && (init?.method ?? "GET") === "GET") {
        const common = {
          runtime_profile: "atlas-python-v1",
          status: "verified",
          trust_provenance: "platform_builtin",
          revision: 1,
          diagnostic_code: null,
          canary_passed_at: "2026-07-12T00:00:00Z",
          active: false,
        };
        return Promise.resolve(jsonResponse({ items: [
          { ...common, plugin_id: "atlas-pypdf", plugin_version: "1.0.0", package_digest: "sha256:base", plugin_kind: "base_parser" },
          { ...common, plugin_id: "com.example.table", plugin_version: "1.0.0", package_digest: "sha256:v1", plugin_kind: "region_processor" },
          { ...common, plugin_id: "com.example.table", plugin_version: "2.0.0", package_digest: "sha256:v2", plugin_kind: "region_processor" },
        ] }));
      }
      return fallbackFetch(input, init);
    });
    render(<App />);

    const profilesTab = await screen.findByRole("tab", { name: "Profiles" });
    fireEvent.mouseDown(profilesTab, { button: 0 });
    fireEvent.click(profilesTab);
    const first = await screen.findByLabelText("Eligible com.example.table 1.0.0");
    const second = screen.getByLabelText("Eligible com.example.table 2.0.0");
    fireEvent.click(first);
    expect(first).toHaveAttribute("data-state", "checked");
    fireEvent.click(second);
    expect(first).toHaveAttribute("data-state", "unchecked");
    expect(second).toHaveAttribute("data-state", "checked");
  });

  it("/admin/models shows the canonical credential encryption error in zh-TW", async () => {
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/config/provider-connections" &&
        init?.method === "POST"
      ) {
        return jsonResponse(
          {
            error_code: "credential_master_key_unavailable",
            message_code: "provider.credential_encryption_is_unavailable", message_params: {},
            correlation_id: "corr-p0-local-dev",
            audit_event_ref: null,
          },
          503,
        );
      }
      return fallbackFetch(input, init);
    });
    await i18n.changeLanguage("zh-TW");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "模型" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "新增連線" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("連線名稱"), {
      target: { value: "無法加密的連線" },
    });
    fireEvent.change(within(dialog).getByLabelText("API 金鑰"), {
      target: { value: "secret-canary" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "儲存連線" }));

    expect(
      await screen.findByText("供應商憑證加密服務目前無法使用。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Request failed.")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("/admin/users owns invite and user lifecycle controls", async () => {
    // acceptance-scenario:SYS-01
    window.history.pushState({}, "", "/admin/users");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/projects", expect.any(Object));
    expect(screen.queryByRole("button", { name: /deactivate atlas admin/i })).not.toBeInTheDocument();
    expect(await screen.findByText("Engineer One")).toBeInTheDocument();
    expect(await screen.findByText("Invited Engineer")).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    const engineerRow = screen.getByRole("button", { name: /^Engineer One$/ });
    expect(engineerRow).toHaveAttribute("tabindex", "0");
    engineerRow.focus();
    expect(engineerRow).toHaveFocus();
    fireEvent.keyDown(engineerRow, { key: " " });
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/users/user-engineer-001"),
    );
    expect(await screen.findByRole("heading", { name: "Engineer One" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select user")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Engineer One")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Discarded User Draft" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Engineer One");
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Engineer One Edited" },
    });
    expect(within(dialog).queryByLabelText("System role")).not.toBeInTheDocument();
    expect(within(dialog).getByText("user")).toBeInTheDocument();
    expect(within(dialog).getByText(/System role changes are handled later/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /save user/i }));
    expect(await screen.findByText(/User profile is updated/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set permission engineer one/i }))
      .not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/projects", expect.any(Object));
    fireEvent.click(screen.getByRole("link", { name: "Users" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/users"));
    fireEvent.click(screen.getByRole("button", { name: /create invite/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Member name"), {
      target: { value: "Discarded Invite" },
    });
    fireEvent.change(within(dialog).getByLabelText("Member email"), {
      target: { value: "discarded@example.test" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create invite/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Member name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Member email")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Member name"), {
      target: { value: "Engineer One" },
    });
    fireEvent.change(within(dialog).getByLabelText("Member email"), {
      target: { value: "engineer@example.test" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /create invite/i }));
    expect(await screen.findByText(/Invite is ready/)).toBeInTheDocument();
    expect(await screen.findByDisplayValue(/atlas_invite_visible_once/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create invite/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Member name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Member email")).toHaveValue("");
    expect(within(dialog).queryByText(/Invite is ready/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("button", { name: /revoke invite invited engineer/i }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "Invited Engineer will be updated immediately. Continue?",
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/user-invites/inv-pending/revoke",
      expect.objectContaining({ method: "POST" }),
    );
    await confirmDestructiveAction(/revoke invite/i);
    expect(await screen.findByText(/Invite has been revoked/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/user-invites/inv-pending/revoke",
      expect.objectContaining({ method: "POST" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /deactivate engineer one/i }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "Engineer One will be updated immediately. Continue?",
    );
    await confirmDestructiveAction(/deactivate/i);
    expect(await screen.findByText(/User access has been removed/)).toBeInTheDocument();
  });

  it("does not expose service accounts through human User detail routes", async () => {
    window.history.pushState({}, "", "/admin/users/agent-layout-review-001");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to directory" })).toBeInTheDocument();
    expect(screen.queryByText("Layout Review Agent")).not.toBeInTheDocument();
  });

  it("clears an authorized User detail before showing an unavailable route target", async () => {
    window.history.pushState({}, "", "/admin/users/user-engineer-001");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Engineer One" })).toBeInTheDocument();
    window.history.pushState({}, "", "/admin/users/missing-user");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Engineer One" })).not.toBeInTheDocument();
    expect(screen.queryByText("engineer@example.test")).not.toBeInTheDocument();
  });

  it("clears Project detail and relationship projections for an unavailable route target", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect((await screen.findAllByText("Project Admin")).length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add access" })).toBeEnabled(),
    );
    const protectedReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/projects/proj-admin-live/members" ||
        String(input) === "/api/v1/admin/projects/proj-admin-live/member-candidates",
    ).length;
    window.history.pushState({}, "", "/admin/projects/missing-project/access");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Project Admin")).not.toBeInTheDocument();
    expect(screen.queryByText("Admin Live Project")).not.toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/projects/proj-admin-live/members" ||
        String(input) === "/api/v1/admin/projects/proj-admin-live/member-candidates",
    )).toHaveLength(protectedReadsBefore);
  });

  it("/admin/teams can create nested teams and add a member", async () => {
    window.history.pushState({}, "", "/admin/teams");
    mockApi(adminSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Teams" })).toBeInTheDocument();
    expect((await screen.findAllByText("Signal Integrity")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Invited Engineer")).not.toBeInTheDocument();
    expect(screen.queryByText("Engineer One")).not.toBeInTheDocument();
    expect(screen.queryByText("Human user")).not.toBeInTheDocument();
    const signalTeamRow = screen.getAllByText("Signal Integrity")[0].closest("tr");
    expect(signalTeamRow).toHaveClass("hover:bg-muted/50");
    expect(signalTeamRow).toHaveClass("focus-visible:ring-2");
    expect(signalTeamRow).toHaveAttribute("role", "button");
    expect(signalTeamRow).toHaveAttribute("tabindex", "0");
    expect(signalTeamRow).toHaveAttribute("aria-label", "Signal Integrity");
    signalTeamRow!.focus();
    expect(signalTeamRow).toHaveFocus();
    fireEvent.keyDown(signalTeamRow!, { key: "Enter" });
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/profile"),
    );
    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/users", expect.any(Object));
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/agent-users", expect.any(Object));
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/projects", expect.any(Object));
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("user-engineer-001");
    expect(container).not.toHaveTextContent("user:user-engineer-001");
    expect(screen.queryByLabelText("Team name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Teams" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/teams"));
    fireEvent.click(screen.getAllByText("Platform")[0]);
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-platform/profile"),
    );
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Parent team"));
    expect(screen.getByRole("option", { name: /No parent/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Signal Integrity/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: /No parent/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("link", { name: "Teams" }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/teams"));
    fireEvent.click(screen.getByRole("button", { name: /create team/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Parent team"));
    fireEvent.click(screen.getAllByText("Signal Integrity").at(-1)!);
    expect(within(dialog).getByLabelText("Parent team")).toHaveTextContent("Signal Integrity");
    fireEvent.change(within(dialog).getByLabelText("Team name"), {
      target: { value: "Draft Team" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("button", { name: /create team/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Team name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Parent team")).toHaveTextContent("No parent");
    fireEvent.change(within(dialog).getByLabelText("Team name"), {
      target: { value: "Admin Live Team" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /create team/i }));
    expect(await screen.findByText(/Team is ready/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/teams",
      expect.objectContaining({
        body: expect.stringContaining('"parent_team_id":null'),
        method: "POST",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /create team/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Team name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Parent team")).toHaveTextContent("No parent");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getAllByText("Signal Integrity")[0]);
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/profile"),
    );
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/users", expect.any(Object));
    fireEvent.click(screen.getByRole("tab", { name: /members/i }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/members"),
    );
    expect(await screen.findByText("Engineer One")).toBeInTheDocument();
    let existingMemberTable = screen.getByRole("table");
    expect(within(existingMemberTable).queryByText("Invited Engineer")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /documents/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Document Library" }))
      .not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    let addMemberGroup = await screen.findByRole("dialog", { name: "Add member" });
    expect(within(addMemberGroup).queryByText("Engineer One")).not.toBeInTheDocument();
    let memberSearch = within(addMemberGroup).getByLabelText("Search members");
    fireEvent.change(memberSearch, { target: { value: "Engineer" } });
    expect(within(addMemberGroup).queryByText("Engineer One")).not.toBeInTheDocument();
    expect(
      within(addMemberGroup).getByRole("button", { name: /add selected members/i }),
    ).toBeDisabled();
    fireEvent.change(memberSearch, { target: { value: "no matching member" } });
    expect(within(addMemberGroup).getByText("No matching members")).toBeInTheDocument();
    expect(
      within(addMemberGroup).getByText("Try another name or clear the search."),
    ).toBeInTheDocument();
    fireEvent.change(memberSearch, { target: { value: "Invited" } });
    expect(within(addMemberGroup).getByText("Invited Engineer")).toBeInTheDocument();
    const invitedMemberRow = within(addMemberGroup).getByText("Invited Engineer").closest("label");
    expect(invitedMemberRow).toHaveClass("focus-within:ring-2");
    const invitedMemberCheckbox = within(invitedMemberRow!).getByRole("checkbox");
    invitedMemberCheckbox.focus();
    expect(invitedMemberCheckbox).toHaveFocus();
    fireEvent.click(invitedMemberCheckbox);
    fireEvent.click(within(addMemberGroup).getByRole("button", { name: /add selected members/i }));
    expect(await screen.findByText(/Team members are active/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-si/members",
      expect.objectContaining({ method: "POST" }),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-platform/members",
      expect.objectContaining({ method: "POST" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /remove engineer one/i }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "Engineer One will be updated immediately. Continue?",
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-si/members/team-member-engineer",
      expect.objectContaining({ method: "DELETE" }),
    );
    await confirmDestructiveAction(/remove/i);
    expect(await screen.findByText(/Team member has been removed/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-si/members/team-member-engineer",
      expect.objectContaining({ method: "DELETE" }),
    );
    const currentMembersGroup = screen
      .getAllByText("Members")
      .map((element) => element.closest('[data-slot="card"]'))
      .find((element): element is HTMLElement => element instanceof HTMLElement)!;
    await waitFor(() => {
      expect(within(currentMembersGroup).queryByText("Engineer One")).not.toBeInTheDocument();
    });
    expect(within(currentMembersGroup).getByText("Invited Engineer")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    addMemberGroup = await screen.findByRole("dialog", { name: "Add member" });
    memberSearch = within(addMemberGroup).getByLabelText("Search members");
    fireEvent.change(memberSearch, { target: { value: "Engineer" } });
    expect(within(addMemberGroup).getByText("Engineer One")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set permission signal integrity/i }))
      .not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/projects", expect.any(Object));
  });

  it("restores the same Team and section through browser back and forward", async () => {
    window.history.pushState({}, "", "/admin/teams");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Signal Integrity" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/profile"),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Members" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/members"),
    );

    act(() => window.history.back());
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/profile"),
    );
    expect(screen.getByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/teams/team-si/members"),
    );
    expect(await screen.findByText("Engineer One")).toBeInTheDocument();
  });

  it("uses mobile cards for System Team member relationships", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText("Engineer One")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove Engineer One/i })).toBeInTheDocument();
  });

  it("uses mobile cards for scoped Team member relationships", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    render(<App />);

    expect((await screen.findAllByText("Team Admin")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(await screen.findByText("Required admin")).toBeInTheDocument();
  });

  it("uses mobile cards for Project access relationships", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    render(<App />);

    expect((await screen.findAllByText("Project Admin")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Role")).toBeInTheDocument();
    expect(await screen.findByLabelText("Decision")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove Project Admin/i })).toBeInTheDocument();
  });

  it("/accept-invite lets invited users set their own password", async () => {
    window.history.pushState({}, "", "/accept-invite?token=atlas_invite_visible_once");
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Accept invite" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "AtlasLocalEngineer!01" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "AtlasLocalEngineer!01" },
    });
    fireEvent.click(screen.getByRole("button", { name: /accept invite/i }));

    expect(await screen.findByText(/Your account is active/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /go to sign in/i }));
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
  });

  it("/accept-invite explains password validation before submit", async () => {
    window.history.pushState({}, "", "/accept-invite?token=atlas_invite_visible_once");
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Accept invite" })).toBeInTheDocument();
    const acceptButton = screen.getByRole("button", { name: /accept invite/i });
    expect(acceptButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "short" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "short" },
    });
    expect(screen.getByText("Use at least 12 characters to continue.")).toBeInTheDocument();
    expect(acceptButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "valid-password-123" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "different-password-123" },
    });
    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument();
    expect(acceptButton).toBeDisabled();
  });

  it("/admin/agents manages agents, tokens, and canonical Project Member access", async () => {
    // acceptance-scenario:SYS-06
    window.history.pushState({}, "", "/admin/agents");
    mockApi(adminWithProjectSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findAllByRole("heading", { name: "Agent access" })).toHaveLength(1);
    expect((await screen.findAllByText("Layout Review Agent")).length).toBeGreaterThan(0);
    expect(screen.getByText("Service account")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("agent-layout-review-001");
    expect(container).not.toHaveTextContent("proj-signal-integrity-alpha");
    expect(screen.queryByText("atlas_agent_visible_once")).not.toBeInTheDocument();
    expect(screen.queryByText("Choose an agent")).not.toBeInTheDocument();
    expect(screen.queryByText("Selected agent")).not.toBeInTheDocument();
    const agentCard = screen.getByRole("button", { name: /^Layout Review Agent$/ });
    expect(agentCard).toHaveAttribute("tabindex", "0");
    agentCard.focus();
    expect(agentCard).toHaveFocus();
    fireEvent.keyDown(agentCard, { key: " " });
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select agent")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Layout Review Agent")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Agent name"), {
      target: { value: "Discarded Agent" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Agent name")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Agent name"), {
      target: { value: "Admin Live Agent" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /create agent/i }));
    expect(await screen.findByText(/Agent user is ready/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Agent name")).toHaveValue("");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    fireEvent.click(screen.getByRole("button", { name: /edit layout review agent/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select agent")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Layout Review Agent")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Agent name"), {
      target: { value: "Discarded Agent Draft" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /edit layout review agent/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select agent")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Agent name")).toHaveValue("Layout Review Agent");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    fireEvent.click(screen.getByRole("button", { name: /issue token layout review agent/i }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select agent")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Layout Review Agent")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /issue token/i }));
    dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByDisplayValue("atlas_agent_visible_once")).toBeInTheDocument();
    expect(within(dialog).getAllByText(/copy it now/i).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/abc123def456/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() =>
      expect(screen.queryByText("atlas_agent_visible_once")).not.toBeInTheDocument(),
    );
    expect(container).not.toHaveTextContent("abc123def456");
    expect(screen.queryByText("Fingerprint")).not.toBeInTheDocument();

    expect((await screen.findAllByText(/Signal Integrity Alpha/)).length).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole("button", { name: /grant project access layout review agent/i }),
    );
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText("Select agent")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Layout Review Agent")).toBeInTheDocument();
    let project = within(dialog).getByLabelText("Project");
    fireEvent.click(project);
    fireEvent.click(await screen.findByRole("option", { name: /Signal Integrity Alpha/ }));
    await waitFor(() => expect(project).toHaveTextContent("Signal Integrity Alpha"));
    await chooseDialogOption(dialog, "Role", "admin");
    await chooseDialogOption(dialog, "Decision", "Deny");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(
      screen.getByRole("button", { name: /grant project access layout review agent/i }),
    );
    dialog = await screen.findByRole("dialog");
    project = within(dialog).getByLabelText("Project");
    expect(project).toHaveTextContent("Admin Live Project");
    expect(within(dialog).getByLabelText("Role")).toHaveTextContent("viewer");
    expect(within(dialog).getByLabelText("Decision")).toHaveTextContent("Allow");
    fireEvent.click(within(dialog).getByLabelText("Role"));
    expect(screen.queryByRole("option", { name: "owner" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("option", { name: "contributor" }));
    await chooseDialogOption(dialog, "Decision", "Deny");
    fireEvent.click(within(dialog).getByRole("button", { name: /grant project access/i }));
    expect((await screen.findAllByText(/Project member is active/)).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members",
      expect.objectContaining({
        body: expect.stringMatching(
          /"subject_type":"service_account".*"subject_id":"agent-layout-review-001".*"role":"contributor".*"effect":"deny"/,
        ),
        method: "POST",
      }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /revoke access to Admin Live Project/i }),
    );
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "Layout Review Agent will be updated immediately. Continue?",
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members/grant-project-member-proj-admin-live-service_account-agent-layout-review-001",
      expect.objectContaining({ method: "DELETE" }),
    );
    await confirmDestructiveAction(/revoke access to Admin Live Project/i);
    expect((await screen.findAllByText(/Project member access is revoked/)).length)
      .toBeGreaterThan(0);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members/grant-project-member-proj-admin-live-service_account-agent-layout-review-001",
      expect.objectContaining({ method: "DELETE" }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: /revoke token 1 for layout review agent/i }),
    );
    const revokeTokenDialog = await screen.findByRole("alertdialog");
    expect(revokeTokenDialog).toHaveTextContent(
      "Layout Review Agent / Token 1 will be updated immediately. Continue?",
    );
    expect(revokeTokenDialog).not.toHaveTextContent("abc123def456");
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/agent-tokens/agtok-layout-review",
      expect.objectContaining({ method: "DELETE" }),
    );
    await confirmDestructiveAction(/^revoke token$/i);
    expect((await screen.findAllByText(/Agent token has been revoked/)).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /edit layout review agent/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /deactivate/i }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "Layout Review Agent will be updated immediately. Continue?",
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/agent-users/agent-layout-review-001",
      expect.objectContaining({ method: "PATCH" }),
    );
    await confirmDestructiveAction(/deactivate/i);
    expect((await screen.findAllByText(/Agent user is updated/)).length).toBeGreaterThan(0);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/agent-users/agent-layout-review-001",
      expect.objectContaining({
        body: expect.stringContaining('"active":false'),
        method: "PATCH",
      }),
    );
  });

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
    expect(screen.getByRole("columnheader", { name: "Time" })).toBeInTheDocument();
    const eventTime = container.querySelector('time[datetime="2026-07-08T00:00:00+00:00"]');
    expect(eventTime).toBeInTheDocument();
    expect(eventTime).not.toHaveTextContent(/^\s*$/);
    expect(screen.queryByText("PCIe lane target")).not.toBeInTheDocument();
    expect(await screen.findByText("abc123def456")).toBeInTheDocument();
    expect(screen.queryByText("atlas_agent_visible_once")).not.toBeInTheDocument();
  });

  it("/admin/audit opens directly on conversation history without a landing layer", async () => {
    window.history.pushState({}, "", "/admin/audit");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("button", { name: /PCIe lane target/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open operation history" })).toBeInTheDocument();
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
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/admin/audit");
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
      "What is the controlled impedance target for the PCIe reference lane?",
    );
    fireEvent.click(screen.getByRole("link", { name: "Conversation history" }));
    expect(await screen.findByRole("button", { name: /PCIe lane target/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/admin/audit/conversations");
  });

  it("restores the audit collection and transcript through browser history", async () => {
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: /PCIe lane target/ }),
    );
    expect(
      await screen.findByText(
        "What is the controlled impedance target for the PCIe reference lane?",
      ),
    ).toBeInTheDocument();

    act(() => window.history.back());
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/audit/conversations"),
    );
    expect(await screen.findByRole("button", { name: /PCIe lane target/ })).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/admin/audit/conversations/conv-supported-001/transcript",
      ),
    );
    expect(
      await screen.findByText(
        "What is the controlled impedance target for the PCIe reference lane?",
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
    expect(await screen.findByRole("button", { name: /PCIe lane target/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/admin/audit/conversations");
  });

  it("uses mobile cards for audit conversation collections", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/audit/conversations");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("button", {
        name: "Open conversation PCIe lane target",
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

    expect(await screen.findByRole("button", { name: /PCIe lane target/ })).toBeInTheDocument();
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
      "What is the controlled impedance target for the PCIe reference lane?",
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
    expect(await screen.findByRole("button", { name: /PCIe lane target/ })).toBeInTheDocument();

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

  it("/admin/audit shows global conversation history with a bounded runtime trace", async () => {
    // acceptance-scenario:SYS-08
    window.history.pushState(
      {},
      "",
      "/admin/audit/conversations/conv-supported-001/transcript",
    );
    mockApi(adminSession, readyReadiness);
    const { container } = render(<App />);

    expect((await screen.findAllByText("PCIe lane target")).length).toBeGreaterThan(0);
    expect(container.querySelector('[data-slot="audit-conversation-layout"]')).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    const transcript = container.querySelector('[data-slot="audit-transcript"]');
    expect(transcript).toHaveClass("grid", "gap-4");
    expect(transcript?.querySelector('[data-slot="message-scroller"]')).not.toBeInTheDocument();
    const userMessage = screen.getByText(
      "What is the controlled impedance target for the PCIe reference lane?",
    );
    expect(userMessage.closest('[data-slot="message"]')).toHaveAttribute("data-align", "end");
    expect(
      screen
        .getByText(
          "The PCIe reference lane controlled impedance target is 85 ohms differential.",
        )
        .closest('[data-slot="message"]'),
    ).toHaveAttribute("data-align", "start");
    fireEvent.click(await screen.findByRole("button", { name: "View runtime trace" }));

    expect(
      await screen.findByRole("button", { name: "Back to conversation" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(await screen.findByText("Runtime budget")).toBeInTheDocument();
    expect(screen.getByText("Answer guidance revision").parentElement).toHaveTextContent("3");
    expect(screen.getByText("Answer guidance digest")).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("Document content discovery path")).toBeInTheDocument();
    expect(screen.getByText("retention policy")).toBeInTheDocument();
    const discoveryDocument = screen.getByText("Retention Policy.pdf");
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
    expect(within(discoveryPreviewDialog).getByText("Retention Policy.pdf · p. 4"))
      .toBeInTheDocument();
    expect(screen.getByText("kh_document_revoked")).toBeInTheDocument();
    expect(screen.getByText("access_required")).toBeInTheDocument();
    expect(await screen.findByText("Durable runtime events")).toBeInTheDocument();
    expect(await screen.findByText("exec-answer-001")).toBeInTheDocument();
    expect(await screen.findByText("execution_allocated")).toBeInTheDocument();
    expect((await screen.findAllByText("terminal_completed")).length).toBeGreaterThan(0);
    expect(await screen.findByText("result-answer-001")).toBeInTheDocument();
    expect(screen.queryByText("Redacted payloads")).not.toBeInTheDocument();
    expect(screen.queryByText(/provider.example/)).not.toBeInTheDocument();
    expect(screen.queryByText(/redacted prompt preview/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export" })).not.toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).includes("/export"),
      ),
    ).toBe(false);
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

    expect(
      await screen.findByRole("region", {
        name: "Model-declared evidence",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("kh_visual_admin_claim")).toBeInTheDocument();
    expect(screen.getByText("visual-evidence-admin-001")).toBeInTheDocument();
    expect(screen.getByText("Verification passed")).toBeInTheDocument();
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
        "What is the controlled impedance target for the PCIe reference lane?",
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
        "What is the controlled impedance target for the PCIe reference lane?",
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
        "What is the controlled impedance target for the PCIe reference lane?",
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
      "What is the controlled impedance target for the PCIe reference lane?",
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
        "What is the controlled impedance target for the PCIe reference lane?",
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

  it("workspace primary surface does not show phase or backend internals", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    expect((await screen.findAllByRole("heading", { name: "Workspace" })).length).toBeGreaterThan(0);
    await waitFor(() => expect(container).not.toHaveTextContent(/phase|fixture|qdrant|postgres|route registry/i));
    expect(container).not.toHaveTextContent(/qry-p0|epack-p0|audit-p0|proj-signal/i);
  });

  it("Workspace sidebar opens a safe Knowledge Library with direct original content", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    await screen.findByRole("heading", { name: "Workspace" });
    const accountMenu = await openAccountMenu();
    expect(accountMenu.settings).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Knowledge Library" })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("menu", { name: "Account menu" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu", { name: "Account menu" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Knowledge Library" }));

    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New conversation" }))
      .toHaveAttribute("data-variant", "ghost");
    expect(screen.getByRole("button", { name: "Knowledge Library" }))
      .toHaveAttribute("data-variant", "secondary");
    expect(screen.getByRole("button", { name: "Knowledge Library" }))
      .toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Signal Integrity Guide")).toBeInTheDocument();
    expect(screen.getByText("DOCX")).toBeInTheDocument();
    expect(screen.getByText("Protected Fabrication Note")).toBeInTheDocument();
    expect(screen.getByText("Signal Integrity Alpha")).toBeInTheDocument();
    expect(screen.getByText("View only")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1);
    expect(container).not.toHaveTextContent(/doc-member-guide|doc-view-only|sha256|blob_id|checksum/i);

    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/library/documents/doc-member-guide/content",
        { credentials: "include", method: "HEAD" },
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
      .closest("button");
    const completedButton = screen.getByText("Completed conversation").closest("button");
    expect(processingButton?.querySelector(
      '[data-slot="conversation-processing-indicator"]',
    )).toHaveClass("animate-spin");
    expect(completedButton?.querySelector(
      '[data-slot="conversation-processing-indicator"]',
    )).not.toBeInTheDocument();
  });

  it("Knowledge Library sidebar returns to a new or selected Workspace conversation", async () => {
    window.history.pushState({}, "", "/library");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/ }));
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/workspace/conversations/conv-supported-001",
      ),
    );
    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Knowledge Library" }));
    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Atlas" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    expect(await screen.findByLabelText("Message")).toHaveValue("");
    expect(screen.queryByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/ }));
    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Knowledge Library" }));
    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace"));
    const composer = await screen.findByLabelText("Message");
    expect(composer).toHaveValue("");
    await waitFor(() => expect(composer).toHaveFocus());
    expect(screen.queryByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).not.toBeInTheDocument();
  });

  it("Workspace selection creates one canonical browser history entry", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const pushState = vi.spyOn(window.history, "pushState");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    fireEvent.click(screen.getByRole("button", { name: /PCIe lane target/ }));
    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    expect(pushState).toHaveBeenCalledTimes(1);
    expect(pushState).toHaveBeenCalledWith(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
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
    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/ }));
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
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).not.toBeInTheDocument();
  });

  it("expands the collapsed Workspace sidebar from Atlas without resetting the conversation", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/ }));
    expect(await screen.findByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Collapse conversation history" }));
    const workspaceSidebar = document.querySelector('[data-slot="workspace-context-sidebar"]');
    expect(workspaceSidebar).toHaveClass("w-14");

    fireEvent.click(within(workspaceSidebar as HTMLElement).getByRole("button", { name: "Atlas" }));
    expect(workspaceSidebar).toHaveClass("w-72");
    expect(screen.getByText(
      "The PCIe reference lane controlled impedance target is 85 ohms differential.",
    )).toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
  });

  it("keeps a failed canonical conversation URL and offers retry", async () => {
    window.history.pushState({}, "", "/library");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/conversations/conv-supported-001" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /PCIe lane target/ }));
    expect(await screen.findByText("Conversation history could not be loaded"))
      .toBeInTheDocument();
    expect(window.location.pathname).toBe(
      "/workspace/conversations/conv-supported-001",
    );
    expect(screen.getByRole("button", { name: "Retry conversation history" }))
      .toBeInTheDocument();
  });

  it("no-scope user sees an explicit safe Knowledge Library empty state", async () => {
    // acceptance-scenario:UI-02
    window.history.pushState({}, "", "/library");
    mockApi(memberWithoutProjects, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Knowledge Library" })).toBeInTheDocument();
    expect(await screen.findByText("No documents available")).toBeInTheDocument();
    expect(screen.getByText(/account is active/i)).toBeInTheDocument();
    expect(screen.queryByText(/access denied/i)).not.toBeInTheDocument();
  });

  it("Knowledge Library keeps loading separate from its empty state", async () => {
    // acceptance-scenario:UI-01
    window.history.pushState({}, "", "/library");
    mockApi(memberWithoutProjects, readyReadiness);
    const normalFetch = global.fetch;
    let resolveLibrary: (response: Response) => void = () => {};
    const delayedLibrary = new Promise<Response>((resolve) => {
      resolveLibrary = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/library/documents" && (init?.method ?? "GET") === "GET") {
        return delayedLibrary;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Loading knowledge library")).toBeInTheDocument();
    expect(screen.queryByText("No documents available")).not.toBeInTheDocument();
    resolveLibrary(await jsonResponse({ documents: [] }));
    expect(await screen.findByText("No documents available")).toBeInTheDocument();
  });

  it("Knowledge Library shows a load error and recovers through Retry", async () => {
    // acceptance-scenario:UI-03 acceptance-scenario:UI-04
    window.history.pushState({}, "", "/library");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let attempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/library/documents" && (init?.method ?? "GET") === "GET") {
        attempts += 1;
        if (attempts === 1) return jsonResponse({ message_code: "artifact.storage_is_temporarily_unavailable", message_params: {} }, 503);
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    expect(screen.queryByText("Signal Integrity Guide")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("scoped Team management keeps initial loading separate from member data", async () => {
    // acceptance-scenario:UI-01
    window.history.pushState({}, "", "/admin/teams");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveTeams: (response: Response) => void = () => {};
    const delayedTeams = new Promise<Response>((resolve) => {
      resolveTeams = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/teams" && (init?.method ?? "GET") === "GET") {
        return delayedTeams;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Loading managed Teams")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Team management" })).not.toBeInTheDocument();
    resolveTeams(await jsonResponse({
      teams: [{
        team_id: "team-si",
        name: "Signal Integrity",
        parent_team_id: null,
        status: "active",
        created_at: "2026-07-08T00:00:00Z",
        inherit_parent_documents: true,
      }],
      memberships: [],
    }));
    expect(await screen.findByRole("heading", { name: "Team management" })).toBeInTheDocument();
  });

  it("scoped Team management shows an empty member state", async () => {
    // acceptance-scenario:UI-02
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/teams/team-si/members" && method === "GET") {
        return jsonResponse({ members: [] });
      }
      if (url.pathname === "/api/v1/admin/teams/team-si/member-candidates" && method === "GET") {
        return jsonResponse({ users: [] });
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("No team members")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    expect(await screen.findByText("No active human users are available to add.")).toBeInTheDocument();
  });

  it("scoped Team candidate failure preserves current members and local Retry", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/teams/team-si/member-candidates" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({ message_code: "team.candidates_unavailable" }, 503);
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect((await screen.findAllByText("Team Admin")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    expect(await screen.findByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });

  it("scoped Team management shows a load error and recovers through Retry", async () => {
    // acceptance-scenario:UI-03 acceptance-scenario:UI-04
    window.history.pushState({}, "", "/admin/teams");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let attempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/teams" && (init?.method ?? "GET") === "GET") {
        attempts += 1;
        if (attempts === 1) return jsonResponse({ message_code: "artifact.storage_is_temporarily_unavailable", message_params: {} }, 503);
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Team management" })).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("canonical Project admin keeps Project and Document Library access", async () => {
    // acceptance-scenario:P-OWNER-01 acceptance-scenario:P-OWNER-02
    const projectScopedAdminSession = {
      ...projectAdminSession,
      available_projects: projectAdminSession.available_projects.map((project) => ({
        ...project,
        role: "admin" as const,
      })),
    };
    window.history.pushState({}, "", "/admin/projects");
    mockApi(projectScopedAdminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect((await screen.findAllByText("Admin Live Project")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Document Library" }));
    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(screen.getByLabelText("Target")).toHaveTextContent("Admin Live Project");
    expect(await screen.findByRole("button", { name: "Manage" })).toBeInTheDocument();
  });

  it("scoped Team admin manages only human membership and scoped invites", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    expect((await screen.findAllByText("Team Admin")).length).toBeGreaterThan(0);
    expect(screen.getByText("Layout Review Agent")).toBeInTheDocument();
    expect(screen.getByText("Read only")).toBeInTheDocument();
    expect(screen.getByText("Required admin")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create team/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/parent team|inherit parent|permission grant/i)).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent(/user-team-admin-001|agent-layout-review-001|team-si/);

    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(await screen.findByText(/Team member is active/)).toBeInTheDocument();
    expect(await screen.findByText("Team Candidate")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-si/members",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"member_actor_type":"user"'),
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Create invite" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "New Teammate" } });
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "new@example.test" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create invite" }));
    expect(await within(dialog).findByLabelText("Invite acceptance link")).toHaveValue(
      "/accept-invite?token=atlas_invite_visible_once",
    );
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/user-invites",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"scope_type":"team"'),
      }),
    );
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/users", expect.any(Object));
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/agent-users", expect.any(Object));
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/projects", expect.any(Object));
  });

  it("keeps scoped Team invite input in the focused dialog after a mutation failure", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/user-invites" &&
        (init?.method ?? "GET") === "POST"
      ) {
        return jsonResponse({ message_code: "admin.action_failed" }, 503);
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create invite" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Retry Teammate" },
    });
    fireEvent.change(within(dialog).getByLabelText("Email"), {
      target: { value: "retry@example.test" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create invite" }));

    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Retry Teammate");
    expect(within(dialog).getByLabelText("Email")).toHaveValue("retry@example.test");
  });

  it("clears scoped Team detail when the authorized directory no longer contains the route target", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(
      {
        ...teamAdminSession,
        team_roles: { ...teamAdminSession.team_roles, "team-missing": "admin" },
      },
      readyReadiness,
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    expect(screen.getByText("Layout Review Agent")).toBeInTheDocument();

    window.history.pushState({}, "", "/admin/teams/team-missing/members");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Signal Integrity" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Layout Review Agent")).not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-missing/members",
      expect.any(Object),
    );
  });

  it("fails closed after scoped Team self-removal when the authority refresh fails", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    let removedOwnMembership = false;
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (
        removedOwnMembership &&
        url.pathname === "/api/v1/auth/session" &&
        method === "GET"
      ) {
        return jsonResponse({ message_code: "identity.session_unavailable" }, 503);
      }
      const response = await normalFetch(input, init);
      if (
        url.pathname ===
          "/api/v1/admin/teams/team-si/members/tm-team-si-user-team-admin-001" &&
        method === "DELETE"
      ) {
        removedOwnMembership = true;
      }
      return response;
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Role"));
    fireEvent.click(await screen.findByRole("option", { name: "admin" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    const removeOwnMembership = await screen.findByRole("button", {
      name: "Remove Team Admin",
    });
    const protectedReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/teams/team-si/members" ||
        String(input) === "/api/v1/admin/teams/team-si/member-candidates",
    ).length;

    fireEvent.click(removeOwnMembership);
    await confirmDestructiveAction(/remove/i);

    await waitFor(() => expect(window.location.pathname).toBe("/admin/teams"));
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Signal Integrity")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Signal Integrity" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Layout Review Agent")).not.toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.filter(
      ([input]) =>
        String(input) === "/api/v1/admin/teams/team-si/members" ||
        String(input) === "/api/v1/admin/teams/team-si/member-candidates",
    )).toHaveLength(protectedReadsBefore);
  });

  it("scoped Team admin ignores a stale member response after switching Teams", async () => {
    window.history.pushState({}, "", "/admin/teams/team-platform/members");
    mockApi(
      {
        ...teamAdminSession,
        team_roles: { "team-platform": "admin", "team-si": "admin" },
      },
      readyReadiness,
    );
    const normalFetch = global.fetch;
    let resolveOldMembers: (response: Response) => void = () => {};
    const oldMembers = new Promise<Response>((resolve) => {
      resolveOldMembers = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/teams/team-platform/members" && method === "GET") {
        return oldMembers;
      }
      if (
        url.pathname === "/api/v1/admin/teams/team-platform/member-candidates" &&
        method === "GET"
      ) {
        return jsonResponse({ users: [] });
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Platform" })).toBeInTheDocument();
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    expect((await screen.findAllByText("Team Admin")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/teams/team-si/members",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-platform/members",
      expect.objectContaining({ method: "POST" }),
    );

    resolveOldMembers(
      await jsonResponse({
        members: [
          {
            membership_id: "tm-team-platform-stale",
            team_id: "team-platform",
            subject_type: "user",
            subject_id: "user-stale-001",
            display_name: "Stale Platform Member",
            display_detail: "stale@example.test",
            role: "member",
            status: "active",
            created_at: "2026-07-09T00:00:00Z",
          },
        ],
      }),
    );
    await waitFor(() => expect(screen.queryByText("Stale Platform Member")).not.toBeInTheDocument());
    expect(window.location.pathname).toBe("/admin/teams/team-si/members");
  });

  it("Team uploader cannot open Team management directly", async () => {
    window.history.pushState({}, "", "/admin/teams");
    mockApi(teamUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Teams" })).not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/teams", expect.any(Object));
  });

  it("Document Library feature preserves upload update download lifecycle and event interactions", async () => {
    // acceptance-scenario:SYS-07
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(await screen.findByText("Uploader-owned Team note")).toBeInTheDocument();
    const updatingRow = screen.getByText("Uploader-owned Team note").closest("tr");
    const processingRow = screen.getByText("Uploader-owned Project note").closest("tr");
    expect(updatingRow).not.toBeNull();
    expect(processingRow).not.toBeNull();
    expect(within(updatingRow!).getByText("Updating")).toBeInTheDocument();
    expect(within(updatingRow!).getByText("Active")).toBeInTheDocument();
    expect(within(updatingRow!).queryByText("Parsing")).not.toBeInTheDocument();
    expect(within(processingRow!).getByText("Processing")).toBeInTheDocument();
    let disabledRow = screen.getByText("Disabled Team note").closest("tr");
    expect(disabledRow).not.toBeNull();
    expect(within(disabledRow!).getByText("Searchable")).toBeInTheDocument();
    expect(within(disabledRow!).getByText("Disabled")).toBeInTheDocument();
    expect(within(disabledRow!).queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Target"));
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByRole("option", { name: "Team: Signal Integrity" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    let dialog = await screen.findByRole("dialog");
    const uploadFile = new File(["%PDF-1.4"], "feature-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("Document file"), {
      target: { files: [uploadFile] },
    });
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Feature-owned upload" },
    });
    fireEvent.click(within(dialog).getByText("Project: Admin Live Project"));
    fireEvent.click(within(dialog).getByText("Allow member download"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload document" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
      ),
    );
    const uploadCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/document-library" && init?.method === "POST",
    );
    expect(uploadCall).toBeDefined();
    const uploadForm = uploadCall![1]!.body as FormData;
    expect(uploadForm.get("scope_type")).toBe("team");
    expect(uploadForm.get("scope_id")).toBe("team-si");
    expect(JSON.parse(String(uploadForm.get("tag_refs")))).toEqual([
      { tag_type: "team", tag_id: "team-si" },
      { tag_type: "project", tag_id: "proj-admin-live" },
    ]);
    expect(uploadForm.get("description")).toBe("Feature-owned upload");
    expect(uploadForm.get("allow_member_download")).toBe("true");
    expect(uploadForm.get("file")).toBe(uploadFile);

    fireEvent.click((await screen.findAllByRole("button", { name: "Download" }))[0]);
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/library/documents/doc-team-uploader-owned/content",
        { credentials: "include", method: "HEAD" },
      ),
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Manage" })[0]);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Office preview unavailable/)).toBeInTheDocument();
    expect(within(dialog).getByText(/default-office r1/)).toBeInTheDocument();
    expect(within(dialog).getByText("3 / 10 pages")).toBeInTheDocument();
    expect(within(dialog).getByText("0:45")).toBeInTheDocument();
    expect(await within(dialog).findByText("Document is uploaded.")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/document-library/doc-team-uploader-owned/events",
      expect.any(Object),
    );
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Updated by feature" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save description" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-uploader-owned",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    const descriptionPatch = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/document-library/doc-team-uploader-owned" &&
        init?.method === "PATCH" &&
        JSON.parse(String(init.body)).description === "Updated by feature",
    );
    expect(descriptionPatch).toBeDefined();

    fireEvent.click(within(dialog).getByText("Allow member download"));
    await waitFor(() =>
      expect(
        vi.mocked(global.fetch).mock.calls.some(
          ([input, init]) =>
            String(input) === "/api/v1/admin/document-library/doc-team-uploader-owned" &&
            init?.method === "PATCH" &&
            JSON.parse(String(init.body)).allow_member_download === true,
        ),
      ).toBe(true),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: /^stop$/i }));
    const stopDialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(stopDialog).getByRole("button", { name: /^stop$/i }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/processing/jobs/job-team-uploader-owned/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const retryProcessing = await within(dialog).findByRole("button", { name: /^retry$/i });
    fireEvent.click(retryProcessing);
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/processing/jobs/job-team-uploader-owned/retry",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/document-library/doc-team-uploader-owned/refresh-searchable-content",
      expect.any(Object),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-uploader-owned/disable",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    disabledRow = screen.getByText("Disabled Team note").closest("tr");
    expect(disabledRow).not.toBeNull();
    fireEvent.click(within(disabledRow!).getByRole("button", { name: "Manage" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-disabled/restore",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("Team uploader Document Library hides non-executable policy and lifecycle controls", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(teamUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(await screen.findByText("Uploader-owned Team note")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Uploader-owned Project note")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Target")).toHaveTextContent("Signal Integrity");
    expect(screen.queryByText("Platform")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Save description" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Update searchable content" })).toBeInTheDocument();
    expect(within(dialog).queryByText("Allow member download")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
  });
});
