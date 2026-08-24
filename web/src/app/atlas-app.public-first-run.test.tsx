import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./(public)/login/screen";
import { firstRunSetupApi } from "../features/first-run-setup/api";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn() }),
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

describe("public first-run entrypoint", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    replace.mockReset();
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
});
