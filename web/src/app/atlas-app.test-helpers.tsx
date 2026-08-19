import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { expect, vi } from "vitest";

import {
  adminWithProjectSession,
  readyReadiness,
} from "../App.test-support";

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function installScopedDirectoryApi(
  normalFetch: typeof global.fetch,
  scopePath: string,
  messageCode: string,
) {
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = new URL(String(input), "http://localhost").pathname;
    const method = init?.method ?? "GET";
    if (path === `${scopePath}/directory-connections` && method === "GET") {
      return jsonResponse({
        connections: [{ connection_id: "directory/main", display_name: "Main AD" }],
      });
    }
    if (
      path === `${scopePath}/directory-connections/directory%2Fmain/users/search` &&
      method === "POST"
    ) {
      return jsonResponse({
        users: [
          {
            external_subject: "directory-subject-ada",
            username: "ada",
            display_name: "Directory Ada",
            email: "ada@example.test",
          },
        ],
        limit_reached: false,
      });
    }
    if (
      path === `${scopePath}/directory-connections/directory%2Fmain/users/import` &&
      method === "POST"
    ) {
      return jsonResponse({
        message_code: messageCode,
        message_params: { count: 1 },
        actor_ids: ["user-directory-ada"],
        applied_count: 1,
      });
    }
    return normalFetch(input, init);
  });
}

async function importOneDirectoryMember(
  triggerName: "Add member" | "Add access",
) {
  fireEvent.click(await screen.findByRole("button", { name: triggerName }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByLabelText("Member source"));
  fireEvent.click(await screen.findByRole("option", { name: "Directory sources" }));
  expect(await within(dialog).findByText("Main AD")).toBeInTheDocument();
  expect(within(dialog).getByText("0 selected")).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Name, email, or username"), {
    target: { value: "Ada" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
  const candidate = await within(dialog).findByText("Directory Ada");
  const checkbox = within(candidate.closest("label")!).getByRole("checkbox");
  fireEvent.click(checkbox);
  expect(within(dialog).getByText("1 selected")).toBeInTheDocument();
  fireEvent.click(checkbox);
  expect(within(dialog).getByText("0 selected")).toBeInTheDocument();
  fireEvent.click(checkbox);
  expect(within(dialog).getByText("1 selected")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Import selected" }));
}

const adminListScenarios = [
  {
    route: "/admin/users",
    listPath: "/api/v1/admin/users",
    heading: "Users",
    loadingTitle: "Loading users",
    emptyTitle: "No users found",
  },
  {
    route: "/admin/directory",
    listPath: "/api/v1/admin/directory-connections",
    heading: "Directory sources",
    loadingTitle: "Loading directory sources",
    emptyTitle: "No directory sources",
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
    return {
      routes: [],
      text_default_route_id: null,
      vision_default_route_id: null,
    };
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
  if (pathname === "/api/v1/admin/prompt-skills") {
    return { items: [] };
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

export {
  adminListScenarios,
  chooseDialogOption,
  confirmDestructiveAction,
  expectModelRuntimePolicyDraft,
  importOneDirectoryMember,
  installScopedDirectoryApi,
  jsonResponse,
  mockFailedAdminList,
  mockPendingAdminList,
  openAccountMenu,
  selectDialogTab,
};
