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
import {
  useLayoutEffect,
  useState,
} from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import App from "./atlas-app.test-support";
import { THEME_STORAGE_KEY } from "../shared/theme";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminSession,
  adminWithProjectSession,
  cleanupAppTest,
  incompleteReadiness,
  memberSession,
  memberWithoutProjects,
  mockApi,
  projectAdminSession,
  prepareAppTest,
  readyReadiness,
  teamAdminSession,
  unauthenticated,
} from "../App.test-support";
import {
  adminListScenarios,
  importOneDirectoryMember,
  installScopedDirectoryApi,
  jsonResponse,
  mockFailedAdminList,
  mockPendingAdminList,
  openAccountMenu,
} from "./atlas-app.test-helpers";
import {
  KnowledgeLibraryFeature,
  KnowledgeScopeFeature,
} from "../features/knowledge-library";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: session-navigation", () => {
it("shows an accessible application loader while session is unresolved", async () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));

    render(<App />);

    expect(screen.getByRole("status", { name: "Loading Atlas" }))
      .toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("heading", { name: "Atlas" }))
      .not.toBeInTheDocument();
  });

it("uses the login response without repeating session or readiness requests", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    const normalFetch = global.fetch;
    let sessionReads = 0;
    let readinessReads = 0;
    let firstAdminReads = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/auth/session" && method === "GET") sessionReads += 1;
      if (url.pathname === "/api/v1/ops/readiness" && method === "GET") readinessReads += 1;
      if (url.pathname === "/api/v1/auth/first-admin" && method === "GET") {
        firstAdminReads += 1;
      }
      return normalFetch(input, init);
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Atlas" });
    fireEvent.change(screen.getByLabelText("Email or username"), {
      target: { value: "admin@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "TestLoginPassword!42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(sessionReads).toBe(1);
    expect(readinessReads).toBe(0);
    expect(firstAdminReads).toBe(1);
  });

it("/login shows unauthenticated state and can sign in through local/dev adapter", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email or username")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    const signIn = screen.getByRole("button", { name: /sign in/i });
    expect(signIn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Email or username"), {
      target: { value: "admin@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "TestLoginPassword!42" },
    });
    expect(signIn).toBeEnabled();
    fireEvent.click(signIn);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Atlas Admin")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/auth/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          identifier: "admin@example.test",
          password: "TestLoginPassword!42",
        }),
      }),
    );
  });

it("can switch the production UI between English and Traditional Chinese", async () => {
    mockApi(unauthenticated, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText("繁中"));

    expect(await screen.findByRole("button", { name: "登入" })).toBeInTheDocument();
    expect(screen.getByText("內部知識工作台")).toBeInTheDocument();
  });

it("/ routes authenticated users into the workspace", async () => {
    window.history.pushState({}, "", "/");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
  });

it("uses one radius token and neutral light theme tokens", () => {
    const styles = readFileSync("src/styles.css", "utf8");

    expect(styles).toContain("--radius: 0.5rem;");
    expect(styles).toContain("--radius-md: var(--radius);");
    expect(styles).toContain("--radius-lg: var(--radius);");
    expect(styles).toContain("--radius-xl: var(--radius);");
    expect(styles).toContain("--radius-2xl: var(--radius);");
    expect(styles).toContain("cursor: pointer;");
    expect(styles).toContain("cursor: text;");
    expect(styles).not.toContain('[data-slot$="-item"]');
    expect(styles).toContain("--background: 0 0% 98%;");
    expect(styles).not.toContain("--background: 42 38% 96%;");
    const lightThemeTokens = {
      "--background": "0 0% 98%",
      "--foreground": "0 0% 9%",
      "--card": "0 0% 100%",
      "--card-foreground": "0 0% 9%",
      "--popover": "0 0% 100%",
      "--popover-foreground": "0 0% 9%",
      "--primary": "0 0% 9%",
      "--primary-foreground": "0 0% 98%",
      "--secondary": "0 0% 96.1%",
      "--secondary-foreground": "0 0% 9%",
      "--muted": "0 0% 96.1%",
      "--muted-foreground": "0 0% 45.1%",
      "--accent": "0 0% 93.5%",
      "--accent-foreground": "0 0% 9%",
      "--border": "0 0% 89.8%",
      "--input": "0 0% 89.8%",
      "--sidebar": "0 0% 96.5%",
      "--sidebar-foreground": "0 0% 18%",
      "--sidebar-primary": "0 0% 9%",
      "--sidebar-primary-foreground": "0 0% 98%",
      "--sidebar-accent": "0 0% 92.5%",
      "--sidebar-accent-foreground": "0 0% 9%",
      "--sidebar-border": "0 0% 88%",
      "--ring": "168 44% 32%",
      "--sidebar-ring": "168 44% 32%",
      "--destructive": "0 73% 41%",
      "--destructive-foreground": "0 0% 100%",
      "--evidence": "173 70% 27%",
      "--warning": "33 88% 34%",
      "--info": "203 80% 36%",
    } as const;
    for (const [token, value] of Object.entries(lightThemeTokens)) {
      expect(styles).toContain(`${token}: ${value};`);
    }
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

    expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
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
      "Skill slots",
      "Agents",
      "Processing Plugins",
      "Audit",
      "System Status",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(await screen.findByRole("heading", { name: "Notes checkpoints" }))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Dirty checkpoint interval (seconds)"))
      .toHaveValue(30);
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

it.each(["revoked", "missing"] as const)(
  "non-active %s Project admin cannot enter scoped management or operational routes",
  async (membershipStatus) => {
    window.history.pushState({}, "", "/settings");
    const nonActiveProjectAdminSession = {
      ...projectAdminSession,
      available_projects: projectAdminSession.available_projects.map((project) => ({
        ...project,
        membership_status: membershipStatus,
      })),
    };
    mockApi(nonActiveProjectAdminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Management" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Document Library" }))
      .not.toBeInTheDocument();

    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByRole("heading", { name: "Admin access required" }))
      .toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        new URL(String(input), "http://localhost").pathname.startsWith(
          "/api/v1/admin/projects",
        ),
      ),
    ).toBe(false);
  },
);

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
    expect(screen.queryByRole("heading", { name: "Notes checkpoints" }))
      .not.toBeInTheDocument();
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

it("default shell hides admin navigation until settings", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Teams" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Projects" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Document Library" }))
      .not.toBeInTheDocument();
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

it("workspace primary surface does not show phase or backend internals", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const { container } = render(<App />);

    expect((await screen.findAllByRole("heading", { name: "Workspace" })).length).toBeGreaterThan(0);
    await waitFor(() => expect(container).not.toHaveTextContent(/phase|fixture|qdrant|postgres|route registry/i));
    expect(container).not.toHaveTextContent(/qry-p0|epack-p0|audit-p0|proj-signal/i);
  });

it("invalidates pending download actions during route replacement commit", async () => {
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let documentAttempts = 0;
    let resolveHead: (response: Response) => void = () => {};
    const delayedHead = new Promise<Response>((resolve) => {
      resolveHead = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        documentAttempts += 1;
      }
      if (
        url.pathname ===
          "/api/v1/library/documents/doc-member-guide/content" &&
        init?.method === "HEAD"
      ) {
        return delayedHead;
      }
      return normalFetch(input, init);
    });
    function ReplacementRoute() {
      useLayoutEffect(() => {
        resolveHead(new Response(null, { status: 500 }));
      }, []);
      return <h1>Replacement route</h1>;
    }

    const view = render(
      <KnowledgeLibraryFeature
        scope={{
          tag_type: "project",
          tag_id: "proj-signal-integrity-alpha",
          label: "Signal Integrity Alpha",
        }}
      />,
    );
    const guideRow = await screen.findByRole("row", {
      name: /Signal Integrity Guide/,
    });
    expect(documentAttempts).toBe(1);
    fireEvent.click(within(guideRow).getByRole("button", { name: "Download" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/library/documents/doc-member-guide/content",
        { credentials: "include", method: "HEAD" },
      )
    );

    view.rerender(<ReplacementRoute />);
    await act(async () => {
      await delayedHead;
      await Promise.resolve();
    });

    expect(documentAttempts).toBe(1);
    expect(screen.queryByText(
      "The request could not be completed. Please try again.",
    )).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Replacement route" }))
      .toBeInTheDocument();
  });

it("Workspace selection creates one canonical browser history entry", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(memberSession, readyReadiness);
    const pushState = vi.spyOn(window.history, "pushState");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    fireEvent.click(screen.getByRole("button", { name: /Example conversation/ }));
    expect(await screen.findByText(
      "A synthetic document-backed statement.",
    )).toBeInTheDocument();
    expect(pushState).toHaveBeenCalledTimes(1);
    expect(pushState).toHaveBeenCalledWith(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
  });

it("scope directory keeps loading separate from its empty state", async () => {
    window.history.pushState({}, "", "/projects");
    mockApi(memberWithoutProjects, readyReadiness);
    const normalFetch = global.fetch;
    let resolveScope: (response: Response) => void = () => {};
    const delayedScope = new Promise<Response>((resolve) => {
      resolveScope = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return delayedScope;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByText("Loading available scopes"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No scopes available"))
      .not.toBeInTheDocument();
    resolveScope(await jsonResponse({ tags: [] }));
    expect(await screen.findByText("No scopes available"))
      .toBeInTheDocument();
  });

it("scope directory shows a load error and recovers through Retry", async () => {
    window.history.pushState({}, "", "/projects");
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let attempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        attempts += 1;
        if (attempts === 1) {
          return jsonResponse(
            {
              message_code: "artifact.storage_is_temporarily_unavailable",
              message_params: {},
            },
            503,
          );
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByText("Unable to load available scopes"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Signal Integrity Alpha")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

it("scoped documents keep loading separate from their empty state", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveDocuments: (response: Response) => void = () => {};
    const delayedDocuments = new Promise<Response>((resolve) => {
      resolveDocuments = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return delayedDocuments;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Loading knowledge documents"))
      .toBeInTheDocument();
    expect(screen.queryByText("No documents available in this scope"))
      .not.toBeInTheDocument();
    resolveDocuments(await jsonResponse({ documents: [] }));
    expect(await screen.findByText("No documents available in this scope"))
      .toBeInTheDocument();
  });

it("scoped documents show a load error and recover through Retry", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let attempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        attempts += 1;
        if (attempts === 1) {
          return jsonResponse(
            {
              message_code: "artifact.storage_is_temporarily_unavailable",
            },
            503,
          );
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Unable to load knowledge documents"))
      .toBeInTheDocument();
    expect(screen.queryByText("Signal Integrity Guide")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

it("revalidates a literal directory scope ID after directory selection", async () => {
    let scopeAttempts = 0;
    let documentAttempts = 0;
    let resolveRevokedScope: (response: Response) => void = () => {};
    const delayedRevokedScope = new Promise<Response>((resolve) => {
      resolveRevokedScope = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        scopeAttempts += 1;
        if (scopeAttempts === 1) {
          return jsonResponse({
            tags: [
              {
                tag_type: "project",
                tag_id: "directory",
                label: "Directory Project",
              },
            ],
          });
        }
        return delayedRevokedScope;
      }
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        documentAttempts += 1;
      }
      return jsonResponse({ documents: [] });
    });

    function ScopeTransitionHarness() {
      const [scopeId, setScopeId] = useState<string | null>(null);
      return (
        <KnowledgeScopeFeature
          scopeType="project"
          scopeId={scopeId}
          onNavigate={(route) => {
            if (route === "/projects/directory/knowledge") {
              setScopeId("directory");
            }
          }}
        />
      );
    }

    render(<ScopeTransitionHarness />);
    const directoryRow = await screen.findByRole("button", {
      name: "Open Directory Project",
    });
    fireEvent.click(
      within(directoryRow).getByRole("button", {
        name: "Open scope",
      }),
    );
    expect(
      await screen.findByText("Loading available scopes"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Directory Project")).not.toBeInTheDocument();
    expect(scopeAttempts).toBe(2);
    expect(documentAttempts).toBe(0);

    resolveRevokedScope(
      await jsonResponse({
        tags: [
          {
            tag_type: "project",
            tag_id: "proj-signal-integrity-alpha",
            label: "Signal Integrity Alpha",
          },
        ],
      }),
    );
    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(documentAttempts).toBe(0);
  });

it("ignores a late same-family document response after the route changes", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let documentAttempts = 0;
    let resolveBetaDocuments: (response: Response) => void = () => {};
    const delayedBetaDocuments = new Promise<Response>((resolve) => {
      resolveBetaDocuments = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({
          tags: [
            {
              tag_type: "project",
              tag_id: "proj-signal-integrity-alpha",
              label: "Signal Integrity Alpha",
            },
            {
              tag_type: "project",
              tag_id: "project-beta",
              label: "Project Beta",
            },
          ],
        });
      }
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        documentAttempts += 1;
        if (documentAttempts === 2) return delayedBetaDocuments;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();

    act(() => {
      window.history.pushState({}, "", "/projects/project-beta/knowledge");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("heading", { name: "Project Beta" }))
      .toBeInTheDocument();
    expect(await screen.findByText("Loading knowledge documents"))
      .toBeInTheDocument();

    act(() => {
      window.history.pushState(
        {},
        "",
        "/projects/proj-signal-integrity-alpha/knowledge",
      );
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();

    resolveBetaDocuments(
      await jsonResponse({
        documents: [
          {
            document_id: "doc-late-beta",
            title: "Late Project Beta Document",
            document_format: "pdf",
            description: null,
            authorized_scopes: [
              {
                scope_type: "project",
                scope_id: "project-beta",
                scope_label: "Project Beta",
              },
            ],
            source_filename: "late-project-beta.pdf",
            source_byte_size: 1,
            uploaded_at: null,
            download_available: true,
          },
        ],
      }),
    );
    await act(async () => {});
    expect(screen.queryByText("Late Project Beta Document")).not.toBeInTheDocument();
    expect(screen.getByText("Signal Integrity Guide")).toBeInTheDocument();
  });

it("ignores a late document response after navigating to another scope family", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let resolveDocuments: (response: Response) => void = () => {};
    const delayedDocuments = new Promise<Response>((resolve) => {
      resolveDocuments = resolve;
    });
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/library/documents" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return delayedDocuments;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    expect(await screen.findByText("Loading knowledge documents"))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Teams" }));
    await waitFor(() => expect(window.location.pathname).toBe("/teams"));
    expect(await screen.findByRole("heading", { name: "Teams" })).toBeInTheDocument();

    resolveDocuments(
      await jsonResponse({
        documents: [
          {
            document_id: "doc-late-project",
            title: "Late Project Document",
            description: null,
            authorized_scopes: [
              {
                scope_type: "project",
                scope_id: "proj-signal-integrity-alpha",
                scope_label: "Signal Integrity Alpha",
              },
            ],
            source_filename: "late.pdf",
            source_byte_size: 1,
            uploaded_at: null,
            download_available: true,
          },
        ],
      }),
    );
    await act(async () => {});
    expect(screen.queryByText("Late Project Document")).not.toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: "Add member" }));
    expect(await screen.findByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });

it("contextual document management opens the selected Project", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/projects/proj-admin-live/profile",
    );
    mockApi(projectAdminSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Admin Live Project" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage documents" }));

    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe(
        "/admin/document-library?scope_type=project&scope_id=proj-admin-live",
      ),
    );
    expect(await screen.findByLabelText("Target")).toHaveTextContent(
      "Project: Admin Live Project",
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Upload to")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Project: Admin Live Project"),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    ).not.toBeInTheDocument();
  });

it("contextual document management opens the selected Team", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Signal Integrity" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage documents" }));

    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe(
        "/admin/document-library?scope_type=team&scope_id=team-si",
      ),
    );
    expect(await screen.findByLabelText("Target")).toHaveTextContent(
      "Team: Signal Integrity",
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Upload to")).toBeInTheDocument();
    expect(within(dialog).getByText("Team: Signal Integrity")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    ).not.toBeInTheDocument();
  });

it("contextual document management preserves the selected Team for System Admin", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/profile");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    let delayDocumentScopes = false;
    let resolveDocumentScopes: (response: Response) => void = () => {};
    const delayedDocumentScopes = new Promise<Response>((resolve) => {
      resolveDocumentScopes = resolve;
    });
    global.fetch = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = new URL(String(input), "http://localhost");
        if (
          delayDocumentScopes &&
          url.pathname === "/api/v1/admin/teams" &&
          (init?.method ?? "GET") === "GET"
        ) {
          return delayedDocumentScopes;
        }
        return normalFetch(input, init);
      },
    );
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Signal Integrity" }),
    ).toBeInTheDocument();
    delayDocumentScopes = true;
    fireEvent.click(screen.getByRole("button", { name: "Manage documents" }));

    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe(
        "/admin/document-library?scope_type=team&scope_id=team-si",
      ),
    );
    expect(
      await screen.findByText("Loading document library"),
    ).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.filter(([input, init]) => {
        const url = new URL(String(input), "http://localhost");
        return (
          url.pathname === "/api/v1/admin/document-library" &&
          (init?.method ?? "GET") === "GET"
        );
      }),
    ).toHaveLength(0);

    resolveDocumentScopes(
      await jsonResponse({
        teams: [
          {
            team_id: "team-platform",
            name: "Platform",
            parent_team_id: null,
            status: "active",
            created_at: "2026-07-08T00:00:00Z",
            inherit_parent_documents: true,
          },
          {
            team_id: "team-si",
            name: "Signal Integrity",
            parent_team_id: "team-platform",
            status: "active",
            created_at: "2026-07-08T00:00:00Z",
            inherit_parent_documents: true,
          },
        ],
        memberships: [],
      }),
    );

    expect(await screen.findByLabelText("Target")).toHaveTextContent(
      "Team: Signal Integrity",
    );
    await waitFor(() => {
      const listUrls = vi.mocked(global.fetch).mock.calls
        .filter(([, init]) => (init?.method ?? "GET") === "GET")
        .map(([input]) => new URL(String(input), "http://localhost"))
        .filter((url) => url.pathname === "/api/v1/admin/document-library");
      expect(listUrls).toHaveLength(1);
      expect(listUrls[0]?.searchParams.get("scope_type")).toBe("team");
      expect(listUrls[0]?.searchParams.get("scope_id")).toBe("team-si");
    });

    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Team: Signal Integrity")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    );
    expect(
      await within(dialog).findByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    ).toBeInTheDocument();
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
    expect(await screen.findByLabelText("Target")).toHaveTextContent("Admin Live Project");
    expect(await screen.findByRole("button", { name: "Manage" })).toBeInTheDocument();
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
    expect(await screen.findByText("Layout Review Agent")).toBeInTheDocument();

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

it("invalidates a late scoped directory import response after route departure", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    installScopedDirectoryApi(
      normalFetch,
      "/api/v1/admin/teams/team-si",
      "team.directory_members_imported",
    );
    const scopedFetch = global.fetch;
    let settleImport!: (response: Response) => void;
    const delayedImport = new Promise<Response>((resolve) => {
      settleImport = resolve;
    });
    global.fetch = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (
          path ===
            "/api/v1/admin/teams/team-si/directory-connections/directory%2Fmain/users/import" &&
          init?.method === "POST"
        ) {
          return delayedImport;
        }
        return scopedFetch(input, init);
      },
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" }))
      .toBeInTheDocument();
    await importOneDirectoryMember("Add member");
    const memberReadsBefore = vi.mocked(global.fetch).mock.calls.filter(
      ([input, init]) =>
        String(input) === "/api/v1/admin/teams/team-si/members" &&
        (init?.method ?? "GET") === "GET",
    ).length;
    act(() => {
      window.history.pushState({}, "", "/admin/teams/team-platform/members");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    settleImport(
      new Response(
        JSON.stringify({
          message_code: "team.directory_members_imported",
          message_params: { count: 1 },
          actor_ids: ["user-directory-ada"],
          applied_count: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/admin/teams/team-si/members" &&
          (init?.method ?? "GET") === "GET",
      ),
    ).toHaveLength(memberReadsBefore);
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

});
