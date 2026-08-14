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
  adminWithProjectSession,
  cleanupAppTest,
  incompleteReadiness,
  mockApi,
  prepareAppTest,
  readyReadiness,
  teamAdminSession,
  unauthenticated,
} from "../App.test-support";
import {
  chooseDialogOption,
  confirmDestructiveAction,
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: identity-directory", () => {
it("/admin/directory creates, tests, searches, imports, and exposes read-only directory profiles", async () => {
    window.history.pushState({}, "", "/admin/directory");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/directory-connections" &&
        init?.method === "POST"
      ) {
        const body = JSON.parse(String(init.body));
        return normalFetch(input, {
          ...init,
          body: JSON.stringify({
            ...body,
            custom_ca_pem: "persisted-ca-test-seed",
          }),
        });
      }
      return normalFetch(input, init);
    });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Directory sources" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add directory" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("combobox", { name: "Directory type" })).toHaveTextContent(
      "Active Directory",
    );
    await chooseDialogOption(dialog, "TLS mode", "Unencrypted LDAP (ldap://)");
    fireEvent.change(within(dialog).getByLabelText("Port"), {
      target: { value: "389" },
    });
    expect(
      within(dialog).getByText(
        "Unencrypted LDAP sends the bind password, user login passwords, and directory data in plaintext. Use only on a controlled, trusted network.",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Custom CA certificate (PEM)")).toBeDisabled();
    expect(
      within(dialog).getByText(
        "Unencrypted LDAP does not use a CA. Any configured CA is preserved.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Display name"), {
      target: { value: "Main AD" },
    });
    fireEvent.change(within(dialog).getByLabelText("Host"), {
      target: { value: "ad.example.test" },
    });
    fireEvent.change(within(dialog).getByLabelText("Bind DN"), {
      target: { value: "cn=atlas,ou=services,dc=example,dc=test" },
    });
    fireEvent.change(within(dialog).getByLabelText("Bind password"), {
      target: { value: "bind-secret-canary" },
    });
    fireEvent.change(within(dialog).getByLabelText("User base DN"), {
      target: { value: "ou=people,dc=example,dc=test" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save directory" }));

    const connectionCard = (await screen.findAllByText("Main AD"))
      .map((element) => element.closest<HTMLElement>('[data-slot="card"]'))
      .find((element): element is HTMLElement => element !== null)!;
    expect(connectionCard).not.toHaveTextContent("bind-secret-canary");
    expect(connectionCard).not.toHaveTextContent("ca-secret-canary");
    fireEvent.change(screen.getByLabelText("Name, email, or username"), {
      target: { value: "a" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Ada Lovelace" }));
    fireEvent.click(within(connectionCard).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Directory connection test passed/i)).toBeInTheDocument();
    fireEvent.click(within(connectionCard).getByRole("button", { name: "Edit directory" }));
    const editDialog = await screen.findByRole("dialog");
    expect(
      within(editDialog).getByRole("combobox", { name: "Directory type" }),
    ).toBeEnabled();
    expect(within(editDialog).getByRole("combobox", { name: "TLS mode" })).toHaveTextContent(
      "Unencrypted LDAP (ldap://)",
    );
    await chooseDialogOption(editDialog, "TLS mode", "LDAPS");
    expect(within(editDialog).getByLabelText("Clear the saved custom CA and use the system trust store")).toBeInTheDocument();
    fireEvent.click(within(editDialog).getByLabelText("Clear the saved custom CA and use the system trust store"));
    expect(within(editDialog).getByLabelText("Clear the saved custom CA and use the system trust store")).toBeChecked();
    await chooseDialogOption(editDialog, "TLS mode", "Unencrypted LDAP (ldap://)");
    expect(within(editDialog).queryByLabelText("Clear the saved custom CA and use the system trust store")).not.toBeInTheDocument();
    await chooseDialogOption(editDialog, "TLS mode", "LDAPS");
    fireEvent.change(within(editDialog).getByLabelText("Custom CA certificate (PEM)"), {
      target: { value: "replacement-ca-must-not-submit" },
    });
    await chooseDialogOption(editDialog, "TLS mode", "Unencrypted LDAP (ldap://)");
    expect(within(editDialog).getByLabelText("Custom CA certificate (PEM)")).toHaveValue("");
    expect(within(editDialog).getByLabelText("Custom CA certificate (PEM)")).toBeDisabled();
    expect(within(editDialog).queryByLabelText("Clear configured custom CA")).not.toBeInTheDocument();
    expect(within(editDialog).queryByLabelText("Clear the saved custom CA and use the system trust store")).not.toBeInTheDocument();
    await chooseDialogOption(editDialog, "Directory type", "LDAP");
    expect(within(editDialog).getByRole("combobox", { name: "TLS mode" })).toHaveTextContent(
      "Unencrypted LDAP (ldap://)",
    );
    expect(within(editDialog).getByLabelText("Login attribute")).toHaveValue("uid");
    expect(within(editDialog).getByLabelText("Stable ID attribute")).toHaveValue("entryUUID");
    await chooseDialogOption(editDialog, "Directory type", "Active Directory");
    expect(within(editDialog).getByRole("combobox", { name: "TLS mode" })).toHaveTextContent(
      "Unencrypted LDAP (ldap://)",
    );
    expect(within(editDialog).getByLabelText("Port")).toHaveValue(389);
    expect(within(editDialog).getByLabelText("Login attribute")).toHaveValue("userPrincipalName");
    expect(within(editDialog).getByLabelText("Stable ID attribute")).toHaveValue("objectGUID");
    fireEvent.click(within(editDialog).getByRole("button", { name: "Save directory" }));
    await waitFor(() => {
      const updateCall = vi.mocked(global.fetch).mock.calls.find(
        ([input, init]) =>
          String(input).startsWith("/api/v1/admin/directory-connections/") &&
          init?.method === "PATCH",
      );
      expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({
        provider_type: "active_directory",
        port: 389,
        tls_mode: "plain",
        login_attribute: "userPrincipalName",
        stable_id_attribute: "objectGUID",
      });
      const body = JSON.parse(String(updateCall?.[1]?.body));
      expect(body).not.toHaveProperty("custom_ca_pem");
      expect(body).not.toHaveProperty("clear_custom_ca");
      expect(within(editDialog).queryByLabelText("Clear the saved custom CA and use the system trust store")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Name, email, or username"), {
      target: { value: "a" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Ada Lovelace" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Grace Hopper" }));
    fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
    expect(await screen.findByText(/Directory users imported/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Users" }));
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search users"), {
      target: { value: "compiler" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).includes("/api/v1/admin/users?q=compiler"),
      ),
    ).toBe(true);

    fireEvent.click(screen.getByText("Grace Hopper"));
    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit profile" })).not.toBeInTheDocument();
    expect(screen.getByText("grace")).toBeInTheDocument();
    expect(screen.getByText("Compiler")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
    expect(await screen.findByText("Directory profile refreshed.")).toBeInTheDocument();

    const createCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/directory-connections" &&
        init?.method === "POST",
    );
    const createBody = JSON.parse(String(createCall?.[1]?.body));
    expect(createBody).toMatchObject({
      provider_type: "active_directory",
      port: 389,
      tls_mode: "plain",
      bind_password: "bind-secret-canary",
      stable_id_attribute: "objectGUID",
    });
    expect(createBody).not.toHaveProperty("custom_ca_pem");
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
});
