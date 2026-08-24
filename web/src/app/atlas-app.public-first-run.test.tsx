import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./(public)/login/screen";
import { firstRunSetupApi } from "../features/first-run-setup/api";
import { FirstRunSetupFeature } from "../features/first-run-setup/FirstRunSetupFeature";
import { documentLibraryApi } from "../features/document-library";
import { modelRoutingApi } from "../features/model-routing";
import { opsApi } from "../features/ops";
import { projectGovernanceApi } from "../features/project-governance";
import type { SessionState } from "../features/identity-session";

const replace = vi.fn();
const push = vi.fn();
const back = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, back }),
  useSearchParams: () => searchParams,
  usePathname: () => "/setup",
}));

vi.mock("./session-provider", () => ({
  useAtlasSession: () => ({
    session: {
      authenticated: false,
      actor: null,
      available_projects: [],
      system_role: null,
      team_roles: {},
    },
    authUnavailable: false,
    beginSession: vi.fn(),
  }),
}));

const jsonResponse = (body: object) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const adminSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "actor-public-synthetic-owner",
    actor_type: "user",
    issuer: "local",
    display_name: "Public Synthetic Admin",
    groups: [],
    correlation_id: "public-synthetic-first-run",
  },
  available_projects: [
    {
      project_id: "project-public-synthetic",
      name: "Public Synthetic Project",
      role: "admin",
      membership_status: "active",
    },
  ],
  system_role: "admin",
  team_roles: {},
};

function renderSetup() {
  return render(
    <FirstRunSetupFeature
      session={adminSession}
      beginSession={vi.fn()}
      refreshSession={vi.fn().mockResolvedValue(adminSession)}
    />,
  );
}

describe("public first-run entrypoint", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    replace.mockReset();
    push.mockReset();
    back.mockReset();
    searchParams = new URLSearchParams();
  });

  it("redirects an unclaimed deployment from login to stateless setup", async () => {
    vi.spyOn(firstRunSetupApi, "firstAdminStatus").mockResolvedValue({
      claim_available: true,
    });

    render(<LoginScreen />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/setup"));
  });

  it("claims the first administrator without a caller actor ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      authenticated: true,
      actor: {
        actor_id: "actor-public-synthetic-owner",
        actor_type: "user",
        issuer: "local",
        display_name: "Public Synthetic Admin",
        groups: [],
        correlation_id: "public-synthetic-first-run",
      },
      available_projects: [],
      system_role: "admin",
      team_roles: {},
    }));
    global.fetch = fetchMock;

    const session = await firstRunSetupApi.claimFirstAdmin({
      displayName: "Public Synthetic Admin",
      email: "admin@example.test",
      password: "public-synthetic-password",
    });

    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body).toEqual({
      display_name: "Public Synthetic Admin",
      email: "admin@example.test",
      password: "public-synthetic-password",
    });
    expect(body).not.toHaveProperty("actor_id");
    expect(session.actor?.actor_id).toBe("actor-public-synthetic-owner");
  });

  it.each([
    ["provider", "project"],
    ["project", "document"],
    ["document", "review"],
  ])("skips the optional %s step without rolling back owner state", async (step, next) => {
    searchParams = new URLSearchParams({
      step,
      project_id: "project-public-synthetic",
    });
    vi.spyOn(projectGovernanceApi, "listProjects").mockResolvedValue({
      projects: [],
    });
    const { unmount } = renderSetup();

    fireEvent.click(await screen.findByRole("button", { name: /skip/i }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        `/setup?step=${next}&project_id=project-public-synthetic`,
      ),
    );
    unmount();
    push.mockReset();
  });

  it("tests a Provider, saves the tested text default, and advances", async () => {
    searchParams = new URLSearchParams({ step: "provider" });
    const connection = {
      connection_id: "connection-public-synthetic",
      display_name: "Public Synthetic Provider",
      provider_type: "openai_compatible",
      endpoint_url: "https://provider.example.test/v1",
      api_version: null,
      enabled: true,
      status: "verified",
      credential_configured: true,
      revision: 2,
    };
    vi.spyOn(modelRoutingApi, "createProviderConnection").mockResolvedValue(
      connection as never,
    );
    vi.spyOn(modelRoutingApi, "testProviderConnection").mockResolvedValue({
      validation_status: "passed",
      connection,
    } as never);
    vi.spyOn(modelRoutingApi, "listAvailableModels").mockResolvedValue({
      discovery_status: "available",
      models: ["public-synthetic-model"],
    } as never);
    const route = {
      route_id: "route-public-synthetic",
      display_name: "public-synthetic-model",
      model_name: "public-synthetic-model",
      connection_id: connection.connection_id,
      enabled: true,
      supports_vision: false,
      status: "test_passed",
      revision: 1,
      runtime_policy: { revision: 1 },
    };
    vi.spyOn(modelRoutingApi, "configureModelRoute").mockResolvedValue(
      route as never,
    );
    vi.spyOn(modelRoutingApi, "setDefaultModelRoute").mockResolvedValue(
      route as never,
    );
    const { container } = renderSetup();
    fireEvent.change(container.querySelector("#setup-connection-name")!, {
      target: { value: connection.display_name },
    });
    fireEvent.change(container.querySelector("#setup-api-key")!, {
      target: { value: "public-synthetic-provider-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() =>
      expect(modelRoutingApi.testProviderConnection).toHaveBeenCalled(),
    );
    fireEvent.click(container.querySelector('button[form="setup-route-form"]')!);

    await waitFor(() => {
      expect(modelRoutingApi.setDefaultModelRoute).toHaveBeenCalledWith(
        route.route_id,
        "text",
        route.revision,
        expect.any(AbortSignal),
      );
      expect(push).toHaveBeenCalledWith(
        "/setup?step=project&route_id=route-public-synthetic",
      );
    });
  });

  it("advances immediately after an accepted upload", async () => {
    searchParams = new URLSearchParams({
      step: "document",
      project_id: "project-public-synthetic",
    });
    vi.spyOn(documentLibraryApi, "uploadDocumentLibraryFile").mockResolvedValue({
      document: {
        document_id: "doc-public-synthetic",
        title: "Public Synthetic Guide",
      },
    } as never);
    const { container } = renderSetup();
    const file = new File(["public synthetic guide"], "guide.txt", {
      type: "text/plain",
    });
    fireEvent.change(container.querySelector("#setup-document-file")!, {
      target: { files: [file] },
    });
    fireEvent.submit(container.querySelector("#setup-document-form")!);
    await waitFor(() =>
      expect(documentLibraryApi.uploadDocumentLibraryFile).toHaveBeenCalled(),
    );
    const uploadSignal = vi.mocked(
      documentLibraryApi.uploadDocumentLibraryFile,
    ).mock.calls[0][1];
    expect(uploadSignal?.aborted).toBe(false);

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        "/setup?step=review&project_id=project-public-synthetic",
      ),
    );
  });

  it("shows owner failures while preserving Settings re-entry and Enter Atlas", async () => {
    searchParams = new URLSearchParams({
      step: "review",
      project_id: "project-public-synthetic",
    });
    vi.spyOn(modelRoutingApi, "listProviderConnections").mockRejectedValue(
      new Error("public-synthetic provider unavailable"),
    );
    vi.spyOn(modelRoutingApi, "listModelRoutes").mockRejectedValue(
      new Error("public-synthetic routes unavailable"),
    );
    vi.spyOn(projectGovernanceApi, "listProjects").mockResolvedValue({
      projects: [],
    });
    vi.spyOn(documentLibraryApi, "listDocumentLibrary").mockResolvedValue({
      documents: [
        {
          document_id: "doc-public-synthetic-failed",
          title: "Public Synthetic Failed Document",
          lifecycle_status: "active",
          current_stage: "failed",
          failure_code: "public_synthetic_failure",
        },
      ],
    } as never);
    vi.spyOn(opsApi, "readiness").mockResolvedValue({
      ready: false,
      setup_blockers: ["public-synthetic-provider"],
    } as never);
    renderSetup();

    expect(
      await screen.findByText(/Public Synthetic Failed Document/),
    ).not.toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /open/i })[0]);
    expect(push).toHaveBeenCalledWith("/settings");
    fireEvent.click(screen.getByRole("button", { name: /enter atlas/i }));
    expect(replace).toHaveBeenCalledWith("/workspace");
  });

  it("aborts an in-flight setup mutation when the route leaves", async () => {
    searchParams = new URLSearchParams({ step: "provider" });
    let signal: AbortSignal | undefined;
    vi.spyOn(modelRoutingApi, "createProviderConnection").mockImplementation(
      (_input, _idempotencyKey, currentSignal) => {
        signal = currentSignal;
        const { promise, reject } = Promise.withResolvers<never>();
        currentSignal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
        return promise;
      },
    );
    const { container, unmount } = renderSetup();
    fireEvent.change(container.querySelector("#setup-connection-name")!, {
      target: { value: "Public Synthetic Provider" },
    });
    fireEvent.change(container.querySelector("#setup-api-key")!, {
      target: { value: "public-synthetic-provider-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(signal).toBeDefined());

    unmount();

    expect(signal?.aborted).toBe(true);
    expect(push).not.toHaveBeenCalled();
  });
});
