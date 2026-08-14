import "@testing-library/jest-dom/vitest";
import {
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
  adminSession,
  cleanupAppTest,
  incompleteReadiness,
  memberWithoutProjects,
  mockApi,
  projectAdminSession,
  projectUploaderSession,
  prepareAppTest,
  readyReadiness,
} from "../App.test-support";
import {
  confirmDestructiveAction,
  importOneDirectoryMember,
  installScopedDirectoryApi,
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: project-governance", () => {
it("project uploaders cannot render project management through direct URLs", async () => {
    window.history.pushState({}, "", "/admin/projects");
    mockApi(projectUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("tab", { name: "Access" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/access"),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Add access" }));
    let dialog = await screen.findByRole("dialog", { name: "Add access" });
    fireEvent.click(within(dialog).getByLabelText("Project member"));
    fireEvent.click(await screen.findByText("Engineer One"));
    fireEvent.click(within(dialog).getByLabelText("Role"));
    fireEvent.click(await screen.findByRole("option", { name: "Admin" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add access" }));
    expect(await screen.findByText(/Project member is active/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members",
      expect.objectContaining({
        body: expect.stringMatching(
          /"subject_id":"user-engineer-001".*"role":"admin"/,
        ),
        method: "POST",
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    dialog = await screen.findByRole("dialog");
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

it("no-scope user sees an explicit Project directory empty state", async () => {
    window.history.pushState({}, "", "/projects");
    mockApi(memberWithoutProjects, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(
      await screen.findByText("No scopes available"),
    ).toBeInTheDocument();
    expect(vi.mocked(global.fetch).mock.calls.some(([input]) =>
      new URL(String(input), "http://localhost").pathname ===
        "/api/v1/library/documents"
    )).toBe(false);
    expect(screen.queryByText(/access denied/i)).not.toBeInTheDocument();
  });

it("current Project Admin imports a Project directory batch as direct allow access", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    installScopedDirectoryApi(
      normalFetch,
      "/api/v1/admin/projects/proj-admin-live",
      "project.directory_members_imported",
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin Live Project" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Directory sources" })).not.toBeInTheDocument();
    await importOneDirectoryMember("Add access");
    expect(await screen.findByText("1 directory members imported")).toBeInTheDocument();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const importCalls = vi.mocked(global.fetch).mock.calls.filter(
      ([input, init]) =>
        String(input) ===
          "/api/v1/admin/projects/proj-admin-live/directory-connections/directory%2Fmain/users/import" &&
        init?.method === "POST",
    );
    expect(importCalls).toHaveLength(1);
    expect(JSON.parse(String(importCalls[0][1]?.body))).toMatchObject({
      external_subjects: ["directory-subject-ada"],
      role: "viewer",
    });
  });

it("retains a failed Project directory role retry and resets it after success", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    const scopePath = "/api/v1/admin/projects/proj-admin-live";
    const normalFetch = global.fetch;
    installScopedDirectoryApi(normalFetch, scopePath, "project.directory_members_imported");
    const scopedFetch = global.fetch;
    let importAttempts = 0;
    global.fetch = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (
          path === `${scopePath}/directory-connections/directory%2Fmain/users/import` &&
          init?.method === "POST"
        ) {
          importAttempts += 1;
          if (importAttempts === 1) {
            return jsonResponse(
              { message_code: "directory.import_entry_unavailable", message_params: {} },
              503,
            );
          }
        }
        return scopedFetch(input, init);
      },
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin Live Project" }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add access" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Member source"));
    fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
    expect(await within(dialog).findByText("Main AD")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByLabelText("Role"));
    fireEvent.click(await screen.findByRole("option", { name: "contributor" }));
    fireEvent.change(within(dialog).getByLabelText("Name, email, or username"), {
      target: { value: "Ada" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
    const candidate = await within(dialog).findByText("Directory Ada");
    fireEvent.click(within(candidate.closest("label")!).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));
    await waitFor(() => expect(importAttempts).toBe(1));
    expect(within(dialog).getByLabelText("Role")).toHaveTextContent("contributor");

    fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(importAttempts).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Add access" }));
    const reopened = await screen.findByRole("dialog");
    fireEvent.click(within(reopened).getByLabelText("Member source"));
    fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
    expect(await within(reopened).findByText("Main AD")).toBeInTheDocument();
    expect(within(reopened).getByLabelText("Role")).toHaveTextContent("viewer");
  });
});
