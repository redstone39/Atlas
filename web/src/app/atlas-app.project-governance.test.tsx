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
import i18n, { LANGUAGE_STORAGE_KEY } from "../i18n";
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
    const clipboardWriteText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWriteText },
    });

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
    expect(screen.getByRole("button", { name: /edit profile/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    const profileDialog = await screen.findByRole("dialog");
    expect(within(profileDialog).queryByLabelText("Status")).not.toBeInTheDocument();
    expect(within(profileDialog).queryByLabelText("Access profile")).not.toBeInTheDocument();
    fireEvent.change(within(profileDialog).getByLabelText("Project name"), {
      target: { value: "Admin Live Project Renamed" },
    });
    fireEvent.click(within(profileDialog).getByRole("button", { name: /save project/i }));
    expect(
      await screen.findByRole("heading", { name: "Admin Live Project Renamed" }),
    ).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          name: "Admin Live Project Renamed",
          idempotency_key: "project-update-proj-admin-live",
        }),
      }),
    );
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
    fireEvent.click(screen.getByRole("link", { name: "Admin Live Project Renamed" }));
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
    const memberCreate = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/projects/proj-admin-live/members"
        && init?.method === "POST",
    );
    expect(JSON.parse(String(memberCreate?.[1]?.body)))
      .not.toHaveProperty("grant_id");

    fireEvent.click(screen.getByRole("button", { name: "Invite new user" }));
    const inviteDialog = await screen.findByRole("dialog", { name: "Invite new user" });
    fireEvent.change(within(inviteDialog).getByLabelText("Invite name"), {
      target: { value: "Project Teammate" },
    });
    fireEvent.change(within(inviteDialog).getByLabelText("Invite email"), {
      target: { value: "project-teammate@example.test" },
    });
    expect(within(inviteDialog).getByLabelText("Invite email")).toHaveAttribute("type", "email");
    fireEvent.click(within(inviteDialog).getByRole("button", { name: "Invite new user" }));
    expect(await within(inviteDialog).findByLabelText("Invite acceptance link")).toHaveValue(
      "/accept-invite?token=atlas_invite_visible_once",
    );
    fireEvent.click(within(inviteDialog).getByRole("button", { name: "Copy invite link" }));
    expect(clipboardWriteText).toHaveBeenCalledWith(
      "/accept-invite?token=atlas_invite_visible_once",
    );
    expect(await screen.findByText("Invite link copied.")).toBeInTheDocument();
    fireEvent.click(within(inviteDialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Invite new user" }));
    expect(
      within(await screen.findByRole("dialog", { name: "Invite new user" }))
        .queryByLabelText("Invite acceptance link"),
    ).not.toBeInTheDocument();
  });

it("retains the scoped Project rename draft when owner authorization rejects it", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/profile");
    mockApi(projectAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/projects/proj-admin-live" &&
        (init?.method ?? "GET") === "PATCH"
      ) {
        return jsonResponse(
          { message_code: "project.manage_access_is_required", message_params: {} },
          403,
        );
      }
      return normalFetch(input, init);
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /edit profile/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Project name"), {
      target: { value: "Uncommitted Scoped Project" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /save project/i }));

    expect(await within(dialog).findByText("Action failed")).toBeInTheDocument();
    expect(within(dialog).getByText("Admin Live Project")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Project name"))
      .toHaveValue("Uncommitted Scoped Project");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          name: "Uncommitted Scoped Project",
          idempotency_key: "project-update-proj-admin-live",
        }),
      }),
    );
  });

it("retains the canonical Project and edit draft when lifecycle update fails", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/profile");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/projects/proj-admin-live" &&
        (init?.method ?? "GET") === "PATCH"
      ) {
        return jsonResponse(
          { message_code: "project.concurrent_update", message_params: {} },
          409,
        );
      }
      return normalFetch(input, init);
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /edit profile/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Project name"), {
      target: { value: "Uncommitted Project Name" },
    });
    fireEvent.click(within(dialog).getByLabelText("Status"));
    fireEvent.click(await screen.findByRole("option", { name: "Retired" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /save project/i }));

    expect(await within(dialog).findByText("Action failed")).toBeInTheDocument();
    expect(within(dialog).getByText("Admin Live Project")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Project name")).toHaveValue(
      "Uncommitted Project Name",
    );
    expect(within(dialog).getByLabelText("Status")).toHaveTextContent("Retired");
  });

it("redirects a retired Project access URL without reading protected relationships", async () => {
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/projects" && method === "GET") {
        return jsonResponse({
          projects: [{
            project_id: "proj-admin-live",
            name: "Admin Live Project",
            policy_profile_id: "policy-default-governed",
            status: "retired",
          }],
        });
      }
      return normalFetch(input, init);
    });

    render(<App />);
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    expect(await screen.findByText("Retired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add access" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite new user" })).not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/members",
      expect.any(Object),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live/member-candidates",
      expect.any(Object),
    );
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
    const projectDirectory = container.querySelector(
      '[data-slot="project-directory-layout"]',
    )!;
    expect(projectDirectory).toHaveClass("w-full");
    expect(projectDirectory).not.toHaveClass("xl:grid-cols-[minmax(0,1fr)_420px]");
    expect(projectDirectory.querySelector('[data-slot="card"]')).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("proj-admin-live");
    expect(container).not.toHaveTextContent("policy-default-governed");
    expect(screen.queryByRole("button", { name: /create invite/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /grant membership/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Project name")).not.toBeInTheDocument();
    const projectNameTrigger = screen.getByRole("button", { name: /^Admin Live Project$/ });
    const projectRow = projectNameTrigger.closest("tr");
    expect(projectRow).not.toHaveAttribute("role");
    expect(projectRow).not.toHaveAttribute("tabindex");
    projectNameTrigger.focus();
    expect(projectNameTrigger).toHaveFocus();
    fireEvent.click(projectNameTrigger);
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
    );
    expect(screen.getByRole("tab", { name: "Access" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    let lifecycleDialog = await screen.findByRole("dialog");
    fireEvent.click(within(lifecycleDialog).getByLabelText("Status"));
    fireEvent.click(await screen.findByRole("option", { name: "Retired" }));
    fireEvent.click(within(lifecycleDialog).getByRole("button", { name: /save project/i }));
    await waitFor(() =>
      expect(screen.queryByRole("tab", { name: "Access" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /manage target documents/i }))
      .not.toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          status: "retired",
          idempotency_key: "project-update-proj-admin-live",
        }),
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /edit profile/i }));
    lifecycleDialog = await screen.findByRole("dialog");
    fireEvent.click(within(lifecycleDialog).getByLabelText("Status"));
    fireEvent.click(await screen.findByRole("option", { name: "Active" }));
    fireEvent.click(within(lifecycleDialog).getByRole("button", { name: /save project/i }));
    expect(await screen.findByRole("tab", { name: "Access" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Access" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/access"),
    );
    const accessCard = (await screen.findByRole("heading", {
      name: "Current members",
      level: 2,
    })).closest('[data-slot="admin-section"]')!;
    expect(within(accessCard).getByRole("button", { name: "Add access" }))
      .toBeInTheDocument();
    expect(within(accessCard).getByRole("button", { name: "Invite new user" }))
      .toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Add access" }));
    let dialog = await screen.findByRole("dialog", { name: "Add access" });
    fireEvent.click(within(dialog).getByLabelText("Project member"));
    fireEvent.click(await screen.findByText("Engineer One"));
    fireEvent.click(within(dialog).getByLabelText("Role"));
    fireEvent.click(await screen.findByRole("option", { name: "Administrator" }));
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
    expect((await screen.findAllByText(/project is updated/i)).length).toBeGreaterThan(0);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/projects/proj-admin-live",
      expect.objectContaining({
        body: JSON.stringify({
          name: "Admin Live Project Edited",
          idempotency_key: "project-update-proj-admin-live",
        }),
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

it("uses a flat mobile list for Project access relationships", async () => {
    window.innerWidth = 500;
    window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
    mockApi(projectAdminSession, readyReadiness);
    render(<App />);

    expect((await screen.findAllByText("Project Admin")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Role")).toBeInTheDocument();
    expect(await screen.findByLabelText("Decision")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add access" })).toHaveClass("min-h-11");
    const removeButton = screen.getByRole("button", { name: /remove Project Admin/i });
    expect(removeButton).toBeInTheDocument();
    const memberList = removeButton.closest('[data-slot="item-group"]')!;
    expect(memberList).toHaveAttribute("role", "list");
    expect(within(memberList).getAllByRole("listitem")).not.toHaveLength(0);
    expect(memberList.querySelector('[data-slot="card"]')).not.toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("option", { name: "Contributor" }));
    fireEvent.change(within(dialog).getByLabelText("Name, email, or username"), {
      target: { value: "Ada" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
    const candidate = await within(dialog).findByText("Directory Ada");
    fireEvent.click(within(candidate.closest("label")!).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));
    await waitFor(() => expect(importAttempts).toBe(1));
    expect(within(dialog).getByLabelText("Role")).toHaveTextContent("Contributor");

    fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(importAttempts).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Add access" }));
    const reopened = await screen.findByRole("dialog");
    fireEvent.click(within(reopened).getByLabelText("Member source"));
    fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
    expect(await within(reopened).findByText("Main AD")).toBeInTheDocument();
    expect(within(reopened).getByLabelText("Role")).toHaveTextContent("Viewer");
  });
it("uses a flat mobile list with one 44px Project name trigger", async () => {
  window.innerWidth = 500;
  window.history.pushState({}, "", "/admin/projects");
  mockApi(projectAdminSession, readyReadiness);
  render(<App />);

  const trigger = await screen.findByRole("button", { name: "Admin Live Project" });
  const projectList = trigger.closest('[data-slot="item-group"]')!;
  expect(projectList).toHaveAttribute("role", "list");
  expect(within(projectList).getAllByRole("listitem")).not.toHaveLength(0);
  expect(projectList.querySelector('[data-slot="card"]')).not.toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(trigger).toHaveClass("min-h-11");
  expect(trigger.parentElement?.closest('[role="button"]')).toBeNull();
  fireEvent.click(trigger);
  await waitFor(() =>
    expect(window.location.pathname).toBe("/admin/projects/proj-admin-live/profile"),
  );
});

it("disables a pending Project invite and replaces its action icon with a Spinner", async () => {
  window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
  mockApi(projectAdminSession, readyReadiness);
  const normalFetch = global.fetch;
  let settleInvite!: (response: Response) => void;
  const delayedInvite = new Promise<Response>((resolve) => {
    settleInvite = resolve;
  });
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/api/v1/admin/user-invites" && init?.method === "POST") {
      return delayedInvite;
    }
    return normalFetch(input, init);
  });
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Invite new user" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Invite name"), {
    target: { value: "Pending Project User" },
  });
  fireEvent.change(within(dialog).getByLabelText("Invite email"), {
    target: { value: "pending-project@example.test" },
  });
  const submit = within(dialog).getByRole("button", { name: "Invite new user" });
  fireEvent.click(submit);

  expect(submit).toBeDisabled();
  expect(within(submit).getByRole("status", { name: "Loading" })).toBeInTheDocument();

  await act(async () => {
    settleInvite(await jsonResponse({
      message_code: "identity.invite_is_ready",
      message_params: {},
      local_pilot_acceptance: {
        acceptance_url: "/accept-invite?token=pending_project_invite",
      },
    }));
    await delayedInvite;
  });
  expect(await within(dialog).findByLabelText("Invite acceptance link")).toHaveValue(
    "/accept-invite?token=pending_project_invite",
  );
});

it("renders Project roles and effects as Traditional Chinese labels", async () => {
  await i18n.changeLanguage("zh-TW");
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-TW");
  window.history.pushState({}, "", "/admin/projects/proj-admin-live/access");
  mockApi(projectAdminSession, readyReadiness);
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Admin Live Project" }))
    .toBeInTheDocument();
  fireEvent.click(await screen.findByLabelText("Project Admin 的角色"));
  expect(await screen.findByRole("option", { name: "檢視者" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "貢獻者" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "管理員" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "viewer" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "contributor" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "admin" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("決策 Project Admin"));
  expect(await screen.findByRole("option", { name: "允許" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "拒絕" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "allow" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "deny" })).not.toBeInTheDocument();
});

});
