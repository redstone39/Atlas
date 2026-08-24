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
  incompleteReadiness,
  mockApi,
  operatorSession,
  prepareAppTest,
  readyReadiness,
} from "../App.test-support";
import {
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: operations-plugins", () => {
it("does not request Ops readiness before entering Ops", async () => {
    window.history.pushState({}, "", "/workspace");
    mockApi(adminSession, readyReadiness);
    const normalFetch = global.fetch;
    let readinessRequests = 0;
    let firstAdminRequests = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/ops/readiness") readinessRequests += 1;
      if (url.pathname === "/api/v1/auth/first-admin") firstAdminRequests += 1;
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeInTheDocument();
    expect(readinessRequests).toBe(0);
    expect(firstAdminRequests).toBe(0);
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

it("admin users can act on setup blockers from ops", async () => {
    // acceptance-scenario:SYS-09
    window.history.pushState({}, "", "/admin/ops");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Ops" })).toBeInTheDocument();
    expect(await screen.findByText("Add documents")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open documents/i }));
    await waitFor(() => expect(window.location.pathname).toBe("/admin/document-library"));
    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: "Advanced settings" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Advanced settings" }));
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

    fireEvent.click(await screen.findByRole("button", { name: "View details" }));
    expect(await screen.findByText(/enterprise-signing-key/)).toBeInTheDocument();
    expect(screen.getByText(/Apache-2.0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => {
      const validationCall = vi.mocked(global.fetch).mock.calls.find(
        ([input]) =>
          input ===
          "/api/v1/admin/processing-plugins/com.example.table/versions/1.2.3/validate",
      );
      expect(validationCall).toBeDefined();
      expect(validationCall?.[1]?.method).toBe("POST");
      expect(new Headers(validationCall?.[1]?.headers).get("If-Match")).toBe("7");
    });
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
    fireEvent.click(await screen.findByRole("button", { name: "Advanced settings" }));
    const first = await screen.findByLabelText("Eligible com.example.table 1.0.0");
    const second = screen.getByLabelText("Eligible com.example.table 2.0.0");
    fireEvent.click(first);
    expect(first).toHaveAttribute("data-state", "checked");
    fireEvent.click(second);
    expect(first).toHaveAttribute("data-state", "unchecked");
    expect(second).toHaveAttribute("data-state", "checked");
  });
});
