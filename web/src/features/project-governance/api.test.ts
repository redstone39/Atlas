import { afterEach, describe, expect, it, vi } from "vitest";

import { projectGovernanceApi } from "./index";


afterEach(() => {
  vi.unstubAllGlobals();
});


function successfulFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    text: async () => "{}",
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}


describe("project governance API contract", () => {
  it("preserves Project and Project Member request generators", async () => {
    const fetchMock = successfulFetch();
    await projectGovernanceApi.createProject("project-a", "Project A");
    await projectGovernanceApi.updateProject("project-a", "Project A2", "policy-a");
    await projectGovernanceApi.addProjectMember(
      "project-a",
      "team",
      "team-a",
      "contributor",
      "deny",
    );
    await projectGovernanceApi.updateProjectMember("project-a", "grant-a", "admin", "allow");
    await projectGovernanceApi.removeProjectMember("project-a", "grant-a");

    const calls = fetchMock.mock.calls.map(([path, init]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }));
    expect(calls).toEqual([
      {
        path: "/api/v1/admin/projects",
        method: "POST",
        body: {
          project_id: "project-a",
          name: "Project A",
          policy_profile_id: "policy-default-governed",
          idempotency_key: "project-project-a",
        },
      },
      {
        path: "/api/v1/admin/projects/project-a",
        method: "PATCH",
        body: {
          name: "Project A2",
          policy_profile_id: "policy-a",
          idempotency_key: "project-update-project-a",
        },
      },
      {
        path: "/api/v1/admin/projects/project-a/members",
        method: "POST",
        body: {
          subject_type: "team",
          subject_id: "team-a",
          role: "contributor",
          effect: "deny",
          idempotency_key: "project-member-project-a-team-team-a",
        },
      },
      {
        path: "/api/v1/admin/projects/project-a/members/grant-a",
        method: "PATCH",
        body: {
          role: "admin",
          effect: "allow",
          idempotency_key: "project-member-role-grant-a",
        },
      },
      {
        path: "/api/v1/admin/projects/project-a/members/grant-a",
        method: "DELETE",
        body: null,
      },
    ]);
  });

  it("consumes the canonical grants wrapper and revoked DELETE result", async () => {
    const activeGrant = {
      grant_id: "grant-a",
      project_id: "project-a",
      subject_type: "user" as const,
      subject_id: "user-a",
      effect: "allow" as const,
      role: "viewer" as const,
      status: "active" as const,
      created_at: "2026-07-16T00:00:00Z",
      revoked_at: null,
    };
    const revokedGrant = {
      ...activeGrant,
      status: "revoked" as const,
      revoked_at: "2026-07-16T01:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        text: async () => JSON.stringify({ grants: [activeGrant] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => JSON.stringify(revokedGrant),
      });
    vi.stubGlobal("fetch", fetchMock);

    await expect(projectGovernanceApi.listProjectMembers("project-a")).resolves.toEqual({
      grants: [activeGrant],
    });
    await expect(
      projectGovernanceApi.removeProjectMember("project-a", "grant-a"),
    ).resolves.toEqual(revokedGrant);
  });
});
