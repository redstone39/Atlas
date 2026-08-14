import "@testing-library/jest-dom/vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import App from "./atlas-app.test-support";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminWithProjectSession,
  cleanupAppTest,
  memberSession,
  memberWithUnauthorizedProjectSession,
  mockApi,
  prepareAppTest,
  readyReadiness,
} from "../App.test-support";
import {
  openAccountMenu,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: workspace-knowledge", () => {
it("Project knowledge keeps Product navigation and the account footer", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Signal Integrity Alpha" }),
    ).toBeInTheDocument();
    const productNavigation = screen.getByRole("navigation", { name: "Product" });
    expect(within(productNavigation).getByRole("button", { name: "Projects" }))
      .toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atlas" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Downloads" })).not.toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/workspace/tag-scope",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(screen.getByRole("button", { name: "Account menu" })).toBeInTheDocument();
    fireEvent.click((await openAccountMenu()).signOut);
    expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/auth/session",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

it("Workspace knowledge routes survive refresh, retain conversation state, and share the content frame", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/conversations/conv-supported-001",
    );
    mockApi(memberSession, readyReadiness);
    const { unmount } = render(<App />);

    await screen.findByText(
      "A synthetic document-backed statement.",
    );
    const workspaceSidebar = document.querySelector(
      '[data-slot="workspace-context-sidebar"]',
    );
    fireEvent.click(
      within(workspaceSidebar as HTMLElement).getByRole("button", {
        name: "Teams",
      }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/teams"),
    );
    expect(await screen.findByRole("heading", { name: "Teams" }))
      .toBeInTheDocument();
    expect(
      within(workspaceSidebar as HTMLElement).getByRole("button", {
        name: "Teams",
      }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByText(
        "A synthetic document-backed statement.",
      ),
    ).toBeInTheDocument();

    unmount();
    render(<App />);
    const refreshedTeamSidebar = await waitFor(() => {
      const sidebar = document.querySelector(
        '[data-slot="workspace-context-sidebar"]',
      );
      expect(sidebar).toBeInTheDocument();
      return sidebar as HTMLElement;
    });
    expect(await screen.findByRole("heading", { name: "Teams" }))
      .toBeInTheDocument();
    expect(
      within(refreshedTeamSidebar).getByRole("button", { name: "Teams" }),
    ).toHaveAttribute("aria-current", "page");

    fireEvent.click(
      within(refreshedTeamSidebar).getByRole("button", { name: "Projects" }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/projects"),
    );
    expect(await screen.findByRole("heading", { name: "Projects" }))
      .toBeInTheDocument();
    const workspaceContentFrame = document.querySelector(
      '[data-slot="workspace-knowledge-content"]',
    );
    expect(workspaceContentFrame).toHaveClass(
      "px-3",
      "pb-4",
      "md:px-6",
      "md:py-4",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open scope" }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/workspace/projects/proj-signal-integrity-alpha/knowledge",
      ),
    );
    expect(
      await screen.findByRole("heading", { name: "Signal Integrity Alpha" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Signal Integrity Guide"))
      .toBeInTheDocument();
    expect(screen.getByText("Shared Signal Review")).toBeInTheDocument();
    expect(screen.queryByText("Project Beta Runbook")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/workspace/projects/proj-signal-integrity-alpha/notes",
      ),
    );
    expect(
      screen.getByRole("button", { name: "Notes" }),
    ).toHaveAttribute("aria-current", "page");

    unmount();
    window.history.pushState({}, "", "/projects");
    render(<App />);
    const generalContentFrame = await waitFor(() => {
      const frame = document.querySelector(
        '[data-slot="product-content-frame"]',
      );
      expect(frame).toBeInTheDocument();
      return frame;
    });
    expect(generalContentFrame).toHaveClass(
      "px-3",
      "pb-4",
      "md:px-6",
      "md:py-4",
    );
  });

it("loads an exact Workspace Team knowledge route directly", async () => {
    window.history.pushState(
      {},
      "",
      "/workspace/teams/team-platform/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Platform" }),
    ).toBeInTheDocument();
    const workspaceSidebar = document.querySelector(
      '[data-slot="workspace-context-sidebar"]',
    );
    expect(
      within(workspaceSidebar as HTMLElement).getByRole("button", {
        name: "Teams",
      }),
    ).toHaveAttribute("aria-current", "page");
  });

it("loads Product Notes history and a deep-linked historical preview", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-admin-live/notes/note-shared-001/history",
    );
    mockApi(adminWithProjectSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Architecture decisions history" }))
      .toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Advanced activity log" })).toBeInTheDocument();
    const activityToggle = screen.getByRole("button", { name: "Show activity" });
    expect(activityToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("user-admin-001", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("Formatting mark change")).not.toBeInTheDocument();
    fireEvent.click(activityToggle);
    expect(screen.getByRole("button", { name: "Hide activity" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("user-admin-001", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Formatting mark change")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Version 1/ }));
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/projects/proj-admin-live/notes/note-shared-001/history/savepoint-1",
      ),
    );
    expect(await screen.findByRole("heading", { name: "Historical version 1" }))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Historical body")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore this body" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "The title, category, trash state, and all existing history stay unchanged.",
    );
  });

it("ignores a pending scoped download failure after leaving its route", async () => {
    window.history.pushState(
      {},
      "",
      "/projects/proj-signal-integrity-alpha/knowledge",
    );
    mockApi(memberSession, readyReadiness);
    const normalFetch = global.fetch;
    let documentAttempts = 0;
    let headAttempts = 0;
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
        headAttempts += 1;
        return delayedHead;
      }
      return normalFetch(input, init);
    });

    render(<App />);
    const guideRow = await screen.findByRole("row", {
      name: /Signal Integrity Guide/,
    });
    expect(documentAttempts).toBe(1);
    fireEvent.click(within(guideRow).getByRole("button", { name: "Download" }));
    await waitFor(() => expect(headAttempts).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: "Projects" }));
    await waitFor(() => expect(window.location.pathname).toBe("/projects"));
    resolveHead(new Response(null, { status: 500 }));
    await act(async () => {
      await delayedHead;
      await Promise.resolve();
    });

    expect(documentAttempts).toBe(1);
    expect(screen.queryByText(
      "The request could not be completed. Please try again.",
    )).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Projects" }))
      .toBeInTheDocument();
  });

it("Teams uses the same exact scope filter and keeps view-only documents read-only", async () => {
    window.history.pushState({}, "", "/teams");
    mockApi(memberSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Teams" })).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "Open scope" }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe("/teams/team-platform/knowledge"),
    );
    expect(await screen.findByRole("heading", { name: "Platform" }))
      .toBeInTheDocument();
    expect(await screen.findByText("Protected Fabrication Note")).toBeInTheDocument();
    expect(screen.getByText("Shared Signal Review")).toBeInTheDocument();
    expect(screen.queryByText("Team Beta Checklist")).not.toBeInTheDocument();
    expect(screen.queryByText("Signal Integrity Guide")).not.toBeInTheDocument();
    const protectedRow = screen.getByRole("row", {
      name: /Protected Fabrication Note/,
    });
    expect(within(protectedRow).getByText("View only")).toBeInTheDocument();
    expect(within(protectedRow).queryByRole("button", { name: "Download" }))
      .not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/library/documents/doc-view-only/content",
      expect.anything(),
    );
  });

it("Project knowledge restores the selected scope across URL history and reload", async () => {
    window.history.pushState({}, "", "/projects");
    mockApi(memberSession, readyReadiness);
    const firstRender = render(<App />);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "Open scope" }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/projects/proj-signal-integrity-alpha/knowledge",
      ),
    );
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();

    act(() => window.history.back());
    await waitFor(() => expect(window.location.pathname).toBe("/projects"));
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/projects/proj-signal-integrity-alpha/knowledge",
      ),
    );
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();

    firstRender.unmount();
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Signal Integrity Alpha" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Signal Integrity Guide")).toBeInTheDocument();
  });

it.each([
    ["unknown", "/projects/unknown-project/knowledge"],
    ["revoked", "/projects/proj-revoked-lab/knowledge"],
    ["type-mismatched", "/projects/team-platform/knowledge"],
  ])("%s Project detail fails closed before documents load", async (_case, route) => {
    window.history.pushState({}, "", route);
    mockApi(memberWithUnauthorizedProjectSession, readyReadiness);
    render(<App />);

    expect(await screen.findByText("This item is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to directory" }))
      .toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.some(([input]) =>
      new URL(String(input), "http://localhost").pathname ===
        "/api/v1/library/documents"
    )).toBe(false);
    expect(screen.queryByText("Signal Integrity Guide")).not.toBeInTheDocument();
  });
});
