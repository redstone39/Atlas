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
  adminSession,
  cleanupAppTest,
  mockApi,
  projectAdminSession,
  prepareAppTest,
  readyReadiness,
  teamAdminSession,
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

describe("Atlas production web: team-administration", () => {
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
    fireEvent.click(screen.getByLabelText("Change role for Engineer One"));
    fireEvent.click(await screen.findByRole("option", { name: "admin" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/teams/team-si/members",
        expect.objectContaining({
          body: expect.stringContaining('"member_actor_id":"user-engineer-001"'),
          method: "POST",
        }),
      ),
    );
    expect(await screen.findByText(/team member role is updated/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Change role for Engineer One")).toHaveTextContent("admin");
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
    fireEvent.click(within(addMemberGroup).getByLabelText("Role"));
    fireEvent.click(await screen.findByRole("option", { name: "admin" }));
    fireEvent.click(within(addMemberGroup).getByRole("button", { name: /add selected members/i }));
    expect(await screen.findByText(/Team members are active/)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/teams/team-si/members",
      expect.objectContaining({
        body: expect.stringContaining('"role":"admin"'),
        method: "POST",
      }),
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
    expect(within(addMemberGroup).getByLabelText("Role")).toHaveTextContent("member");
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

it("System Admin imports a Team directory batch through the Team scope client", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    installScopedDirectoryApi(
      normalFetch,
      "/api/v1/admin/teams/team-si",
      "team.directory_members_imported",
    );
    const scopedFetch = global.fetch;
    let imported = false;
    global.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const path = new URL(String(input), "http://localhost").pathname;
        const method = init?.method ?? "GET";
        if (
          path.endsWith(
            "/teams/team-si/directory-connections/directory%2Fmain/users/import",
          ) &&
          method === "POST"
        ) {
          const response = await scopedFetch(input, init);
          imported = true;
          return response;
        }
        if (imported && path === "/api/v1/admin/users" && method === "GET") {
          const response = await normalFetch(input, init);
          const body = await response.json();
          return jsonResponse({
            ...body,
            users: [
              ...body.users,
              {
                actor_id: "user-directory-ada",
                actor_type: "user",
                display_name: "Directory Ada",
                email: "ada@example.test",
                system_role: "user",
                active: true,
                created_at: "2026-08-13T00:00:00Z",
                account_source: "directory",
                directory_profile: null,
              },
            ],
          });
        }
        if (imported && path === "/api/v1/admin/teams" && method === "GET") {
          const response = await normalFetch(input, init);
          const body = await response.json();
          return jsonResponse({
            ...body,
            memberships: [
              ...body.memberships,
              {
                membership_id: "tm-team-si-user-directory-ada",
                team_id: "team-si",
                member_actor_type: "user",
                member_actor_id: "user-directory-ada",
                role: "member",
                status: "active",
                created_at: "2026-08-13T00:00:00Z",
              },
            ],
          });
        }
        return scopedFetch(input, init);
      },
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    await screen.findByRole("button", { name: "Add member" });
    const userReadsBeforeImport = vi.mocked(global.fetch).mock.calls.filter(
      ([input, init]) =>
        String(input) === "/api/v1/admin/users" &&
        (init?.method === undefined || init.method === "GET"),
    ).length;
    await importOneDirectoryMember("Add member");
    expect(await screen.findByText("1 directory members imported")).toBeInTheDocument();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Directory Ada")).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/admin/users" &&
          (init?.method === undefined || init.method === "GET"),
      ).length,
    ).toBeGreaterThan(userReadsBeforeImport);
    const importCalls = vi.mocked(global.fetch).mock.calls.filter(
      ([input, init]) =>
        String(input) ===
          "/api/v1/admin/teams/team-si/directory-connections/directory%2Fmain/users/import" &&
        init?.method === "POST",
    );
    expect(importCalls).toHaveLength(1);
    expect(JSON.parse(String(importCalls[0][1]?.body))).toMatchObject({
      external_subjects: ["directory-subject-ada"],
      role: "member",
    });
  });

it("current Team Admin imports a Team directory batch without global directory access", async () => {
    window.history.pushState({}, "", "/admin/teams/team-si/members");
    mockApi(teamAdminSession, readyReadiness);
    const normalFetch = global.fetch;
    installScopedDirectoryApi(
      normalFetch,
      "/api/v1/admin/teams/team-si",
      "team.directory_members_imported",
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Signal Integrity" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Directory sources" })).not.toBeInTheDocument();
    await importOneDirectoryMember("Add member");
    expect(await screen.findByText("1 directory members imported")).toBeInTheDocument();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/directory-connections",
      expect.anything(),
    );
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith(
            "/teams/team-si/directory-connections/directory%2Fmain/users/import",
          ) && init?.method === "POST",
      ),
    ).toHaveLength(1);
  });

it.each([
    {
      route: "/admin/teams/team-si/members",
      session: adminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member" as const,
      refreshPaths: [
        "/api/v1/admin/teams",
        "/api/v1/admin/users",
        "/api/v1/admin/agents",
      ],
    },
    {
      route: "/admin/teams/team-si/members",
      session: teamAdminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member" as const,
      refreshPaths: [
        "/api/v1/admin/teams/team-si/members",
        "/api/v1/admin/teams/team-si/member-candidates",
      ],
    },
    {
      route: "/admin/projects/proj-admin-live/access",
      session: projectAdminSession,
      scopePath: "/api/v1/admin/projects/proj-admin-live",
      heading: "Admin Live Project",
      trigger: "Add access" as const,
      refreshPaths: [
        "/api/v1/admin/projects/proj-admin-live/members",
        "/api/v1/admin/projects/proj-admin-live/member-candidates",
      ],
    },
  ])(
    "does not refresh old $heading data after navigation during import authority refresh",
    async ({ route, session, scopePath, heading, trigger, refreshPaths }) => {
      window.history.pushState({}, "", route);
      mockApi(session, readyReadiness);
      const normalFetch = global.fetch;
      installScopedDirectoryApi(normalFetch, scopePath, "unused");
      const scopedFetch = global.fetch;
      let importReturned = false;
      let signalRefreshStarted!: () => void;
      const refreshStarted = new Promise<void>((resolve) => {
        signalRefreshStarted = resolve;
      });
      let settleRefresh!: (response: Response) => void;
      const delayedRefresh = new Promise<Response>((resolve) => {
        settleRefresh = resolve;
      });
      global.fetch = vi.fn(
        async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          const method = init?.method ?? "GET";
          if (path.endsWith("/users/import") && method === "POST") {
            const response = await scopedFetch(input, init);
            importReturned = true;
            return response;
          }
          if (path === "/api/v1/auth/session" && method === "GET" && importReturned) {
            signalRefreshStarted();
            return delayedRefresh;
          }
          return scopedFetch(input, init);
        },
      );
      render(<App />);

      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
      await importOneDirectoryMember(trigger);
      await refreshStarted;
      const refreshReadsBeforeNavigation = vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          refreshPaths.includes(new URL(String(input), "http://localhost").pathname) &&
          (init?.method === undefined || init.method === "GET"),
      ).length;

      await act(async () => {
        window.history.pushState({}, "", "/settings");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await act(async () => {
        settleRefresh(jsonResponse(session));
        await delayedRefresh;
      });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(
        vi.mocked(global.fetch).mock.calls.filter(
          ([input, init]) =>
            refreshPaths.includes(new URL(String(input), "http://localhost").pathname) &&
            (init?.method === undefined || init.method === "GET"),
        ).length,
      ).toBe(refreshReadsBeforeNavigation);
    },
  );

it.each([
    {
      route: "/admin/teams/team-si/members",
      session: adminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member" as const,
    },
    {
      route: "/admin/teams/team-si/members",
      session: teamAdminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member" as const,
    },
    {
      route: "/admin/projects/proj-admin-live/access",
      session: projectAdminSession,
      scopePath: "/api/v1/admin/projects/proj-admin-live",
      heading: "Admin Live Project",
      trigger: "Add access" as const,
    },
  ].flatMap((persona) => [
    { ...persona, invalidation: "mode" as const },
    { ...persona, invalidation: "search" as const },
  ]))(
    "keeps $heading member management actionable after $invalidation invalidation",
    async ({ route, session, scopePath, heading, trigger, invalidation }) => {
      window.history.pushState({}, "", route);
      mockApi(session, readyReadiness);
      const normalFetch = global.fetch;
      installScopedDirectoryApi(normalFetch, scopePath, "unused");
      const scopedFetch = global.fetch;
      let settleImport!: (response: Response) => void;
      const delayedImport = new Promise<Response>((resolve) => {
        settleImport = resolve;
      });
      global.fetch = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          if (path.endsWith("/users/import") && init?.method === "POST") {
            return delayedImport;
          }
          return scopedFetch(input, init);
        },
      );
      render(<App />);

      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
      await importOneDirectoryMember(trigger);
      const currentDialog = await screen.findByRole("dialog");
      if (invalidation === "mode") {
        fireEvent.click(within(currentDialog).getByLabelText("Member source"));
        fireEvent.click(await screen.findByRole("option", { name: "Atlas users" }));
        fireEvent.click(within(currentDialog).getByLabelText("Member source"));
        fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
        expect(await within(currentDialog).findByText("Main AD")).toBeInTheDocument();
      }
      fireEvent.change(within(currentDialog).getByLabelText("Name, email, or username"), {
        target: { value: "Ada" },
      });
      fireEvent.click(within(currentDialog).getByRole("button", { name: "Search" }));
      const candidate = await within(currentDialog).findByText("Directory Ada");
      fireEvent.click(within(candidate.closest("label")!).getByRole("checkbox"));

      await act(async () => {
        settleImport(
          new Response(
            JSON.stringify({
              message_code: "unused",
              message_params: { count: 1 },
              actor_ids: ["user-directory-ada"],
              applied_count: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
        await delayedImport;
      });
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(within(currentDialog).getByRole("button", { name: "Import selected" }))
        .toBeEnabled();
    },
  );

it.each([
    {
      route: "/admin/teams/team-si/members",
      session: adminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member",
    },
    {
      route: "/admin/teams/team-si/members",
      session: teamAdminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member",
    },
    {
      route: "/admin/projects/proj-admin-live/access",
      session: projectAdminSession,
      scopePath: "/api/v1/admin/projects/proj-admin-live",
      heading: "Admin Live Project",
      trigger: "Add access",
    },
  ])(
    "recovers $heading directory sources and distinguishes a successful empty list",
    async ({ route, session, scopePath, heading, trigger }) => {
      window.history.pushState({}, "", route);
      mockApi(session, readyReadiness);
      const normalFetch = global.fetch;
      let sourceLoads = 0;
      global.fetch = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          if (path === `${scopePath}/directory-connections` && (init?.method ?? "GET") === "GET") {
            sourceLoads += 1;
            if (sourceLoads === 1) return Promise.reject(new Error("temporary source failure"));
            return jsonResponse({
              connections: sourceLoads === 2
                ? [{ connection_id: "directory/main", display_name: "Main AD" }]
                : [],
            });
          }
          return normalFetch(input, init);
        },
      );
      render(<App />);

      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: trigger }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByLabelText("Member source"));
      fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
      expect(await within(dialog).findByText("Could not load this list")).toBeInTheDocument();
      expect(within(dialog).queryByText("No enabled directory sources are available. Contact a System Admin to enable one."))
        .not.toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }));
      expect(await within(dialog).findByText("Main AD")).toBeInTheDocument();

      fireEvent.click(within(dialog).getByLabelText("Member source"));
      fireEvent.click(await screen.findByRole("option", { name: "Atlas users" }));
      fireEvent.click(within(dialog).getByLabelText("Member source"));
      fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
      expect(await within(dialog).findByText(
        "No enabled directory sources are available. Contact a System Admin to enable one.",
      )).toBeInTheDocument();
      expect(sourceLoads).toBe(3);
    },
  );

it.each([
    { session: adminSession, persona: "System Admin" },
    { session: teamAdminSession, persona: "Team Admin" },
  ])(
    "$persona can scroll the add-member dialog and select every department result",
    async ({ session }) => {
      window.history.pushState({}, "", "/admin/teams/team-si/members");
      mockApi(session, readyReadiness);
      const normalFetch = global.fetch;
      installScopedDirectoryApi(
        normalFetch,
        "/api/v1/admin/teams/team-si",
        "team.directory_members_imported",
      );
      const directoryFetch = global.fetch;
      global.fetch = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          if (
            path ===
              "/api/v1/admin/teams/team-si/directory-connections/directory%2Fmain/users/search" &&
            init?.method === "POST"
          ) {
            return jsonResponse({
              users: [
                {
                  external_subject: "directory-subject-ada",
                  username: "ada",
                  display_name: "Directory Ada",
                  email: "ada@example.test",
                },
                {
                  external_subject: "directory-subject-grace",
                  username: "grace",
                  display_name: "Directory Grace",
                  email: "grace@example.test",
                },
              ],
              limit_reached: false,
            });
          }
          return directoryFetch(input, init);
        },
      );

      render(<App />);
      expect(await screen.findByRole("heading", { name: "Signal Integrity" }))
        .toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Add member" }));
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveClass("max-h-[calc(100dvh-2rem)]", "overflow-y-auto");
      fireEvent.click(within(dialog).getByLabelText("Member source"));
      fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
      expect(await within(dialog).findByText("Main AD")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByLabelText("Search by"));
      fireEvent.click(await screen.findByRole("option", { name: "Department" }));
      fireEvent.change(within(dialog).getByLabelText("Department"), {
        target: { value: "Engineering" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
      expect(await within(dialog).findByText("Directory Ada")).toBeInTheDocument();
      expect(within(dialog).getByText("Directory Grace")).toBeInTheDocument();

      const selectAll = within(dialog).getByLabelText("Select all results");
      fireEvent.click(selectAll);
      expect(within(dialog).getByText("2 selected")).toBeInTheDocument();
      expect(
        within(within(dialog).getByText("Directory Ada").closest("label")!)
          .getByRole("checkbox"),
      ).toBeChecked();
      expect(
        within(within(dialog).getByText("Directory Grace").closest("label")!)
          .getByRole("checkbox"),
      ).toBeChecked();

      fireEvent.click(selectAll);
      expect(within(dialog).getByText("0 selected")).toBeInTheDocument();
    },
  );

it.each([
    {
      route: "/admin/teams/team-si/members",
      session: adminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member",
    },
    {
      route: "/admin/teams/team-si/members",
      session: teamAdminSession,
      scopePath: "/api/v1/admin/teams/team-si",
      heading: "Signal Integrity",
      trigger: "Add member",
    },
    {
      route: "/admin/projects/proj-admin-live/access",
      session: projectAdminSession,
      scopePath: "/api/v1/admin/projects/proj-admin-live",
      heading: "Admin Live Project",
      trigger: "Add access",
    },
  ])(
    "rejects a stale $heading member-search response after switching to department mode",
    async ({ route, session, scopePath, heading, trigger }) => {
      window.history.pushState({}, "", route);
      mockApi(session, readyReadiness);
      const normalFetch = global.fetch;
      installScopedDirectoryApi(normalFetch, scopePath, "unused");
      const scopedFetch = global.fetch;
      let settleSearch!: (response: Response) => void;
      const delayedSearch = new Promise<Response>((resolve) => {
        settleSearch = resolve;
      });
      global.fetch = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          if (
            path ===
              `${scopePath}/directory-connections/directory%2Fmain/users/search` &&
            init?.method === "POST"
          ) {
            return delayedSearch;
          }
          return scopedFetch(input, init);
        },
      );
      render(<App />);

      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: trigger }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByLabelText("Member source"));
      fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
      expect(await within(dialog).findByText("Main AD")).toBeInTheDocument();
      fireEvent.change(within(dialog).getByLabelText("Name, email, or username"), {
        target: { value: "Ada" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
      fireEvent.click(within(dialog).getByLabelText("Search by"));
      fireEvent.click(await screen.findByRole("option", { name: "Department" }));
      await act(async () => {
        settleSearch(
          new Response(
            JSON.stringify({
              users: [{
                external_subject: "stale-subject",
                username: "stale",
                display_name: "Stale Directory User",
                email: "stale@example.test",
              }],
              limit_reached: false,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
        await delayedSearch;
      });
      expect(within(dialog).queryByText("Stale Directory User")).not.toBeInTheDocument();
      expect(within(dialog).getByLabelText("Department")).toBeInTheDocument();
      expect(within(dialog).getByText("0 selected")).toBeInTheDocument();
    },
  );

it.each([
    [409, "directory.import_conflict"],
    [503, "directory.import_entry_unavailable"],
  ])(
    "keeps the scoped Team directory draft retryable after HTTP %i",
    async (status, messageCode) => {
      window.history.pushState({}, "", "/admin/teams/team-si/members");
      mockApi(teamAdminSession, readyReadiness);
      const normalFetch = global.fetch;
      installScopedDirectoryApi(
        normalFetch,
        "/api/v1/admin/teams/team-si",
        "team.directory_members_imported",
      );
      const scopedFetch = global.fetch;
      let importAttempts = 0;
      global.fetch = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
          const path = new URL(String(input), "http://localhost").pathname;
          if (
            path ===
              "/api/v1/admin/teams/team-si/directory-connections/directory%2Fmain/users/import" &&
            init?.method === "POST"
          ) {
            importAttempts += 1;
            if (importAttempts === 1) {
              return jsonResponse({ message_code: messageCode }, status);
            }
          }
          return scopedFetch(input, init);
        },
      );
      render(<App />);

      expect(await screen.findByRole("heading", { name: "Signal Integrity" }))
        .toBeInTheDocument();
      await importOneDirectoryMember("Add member");
      const dialog = await screen.findByRole("dialog");
      await waitFor(() =>
        expect(within(dialog).getByRole("button", { name: "Import selected" }))
          .toBeEnabled(),
      );
      expect(within(dialog).getByText("Directory Ada")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      const importCalls = vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith(
            "/teams/team-si/directory-connections/directory%2Fmain/users/import",
          ) && init?.method === "POST",
      );
      expect(importCalls).toHaveLength(2);
      expect(JSON.parse(String(importCalls[0][1]?.body)).idempotency_key).toBe(
        JSON.parse(String(importCalls[1][1]?.body)).idempotency_key,
      );
    },
  );
});
