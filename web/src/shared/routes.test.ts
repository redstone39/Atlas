import { describe, expect, it } from "vitest";

import {
  adminAuditConversationRoute,
  adminAuditSectionRoute,
  adminProjectDetailRoute,
  adminTeamDetailRoute,
  adminUserDetailRoute,
  documentLibraryDestination,
  managementRouteFamily,
  matchAppRoute,
  workspaceConversationRoute,
} from "./routes";

describe("canonical App Router projections", () => {
  it("encodes and projects a Workspace conversation", () => {
    const route = workspaceConversationRoute("conversation one");
    expect(route).toBe("/workspace/conversations/conversation%20one");
    expect(matchAppRoute(route)).toMatchObject({
      kind: "workspace-conversation",
      conversationId: "conversation one",
    });
  });

  it("keeps directory static and user detail routes distinct", () => {
    expect(matchAppRoute("/admin/directory")).toEqual({
      kind: "static",
      route: "/admin/directory",
    });
    expect(matchAppRoute(adminUserDetailRoute("directory"))).toMatchObject({
      kind: "admin-user-detail",
      actorId: "directory",
    });
  });

  it("projects every identity and access detail shape", () => {
    expect(matchAppRoute(adminUserDetailRoute("user-1"))).toMatchObject({
      kind: "admin-user-detail",
      actorId: "user-1",
    });
    expect(matchAppRoute(adminTeamDetailRoute("team-1", "profile"))).toMatchObject({
      kind: "admin-team-detail",
      teamId: "team-1",
      section: "profile",
    });
    expect(matchAppRoute(adminTeamDetailRoute("team-1", "members"))).toMatchObject({
      kind: "admin-team-detail",
      section: "members",
    });
    expect(matchAppRoute(adminProjectDetailRoute("project-1", "profile"))).toMatchObject({
      kind: "admin-project-detail",
      projectId: "project-1",
      section: "profile",
    });
    expect(matchAppRoute(adminProjectDetailRoute("project-1", "access"))).toMatchObject({
      kind: "admin-project-detail",
      section: "access",
    });
  });

  it("encodes detail IDs and preserves management families", () => {
    expect(adminUserDetailRoute("user one")).toBe("/admin/users/user%20one");
    expect(adminTeamDetailRoute("team one", "members")).toBe(
      "/admin/teams/team%20one/members",
    );
    expect(adminProjectDetailRoute("project one", "access")).toBe(
      "/admin/projects/project%20one/access",
    );
    expect(managementRouteFamily(adminTeamDetailRoute("team one", "members"))).toBe(
      "/admin/teams",
    );
  });
});

describe("audit progressive disclosure projections", () => {
  it("projects landing, collections, transcript, and runtime routes", () => {
    expect(matchAppRoute("/admin/audit")).toEqual({
      kind: "static",
      route: "/admin/audit",
    });
    expect(matchAppRoute(adminAuditSectionRoute("conversations"))).toMatchObject({
      kind: "admin-audit-section",
      section: "conversations",
    });
    expect(matchAppRoute(adminAuditSectionRoute("events"))).toMatchObject({
      kind: "admin-audit-section",
      section: "events",
    });
    expect(
      matchAppRoute(adminAuditConversationRoute("conversation-1", "transcript")),
    ).toMatchObject({
      kind: "admin-audit-conversation",
      conversationId: "conversation-1",
      section: "transcript",
    });
    expect(
      matchAppRoute(
        adminAuditConversationRoute("conversation-1", "runtime", "turn-1"),
      ),
    ).toMatchObject({
      kind: "admin-audit-conversation",
      conversationId: "conversation-1",
      section: "runtime",
      turnId: "turn-1",
    });
  });

  it("encodes every audit resource segment and preserves its family", () => {
    expect(adminAuditSectionRoute("events")).toBe("/admin/audit/events");
    expect(adminAuditConversationRoute("conversation one", "transcript")).toBe(
      "/admin/audit/conversations/conversation%20one/transcript",
    );
    const runtimeRoute = adminAuditConversationRoute(
      "conversation one",
      "runtime",
      "turn one",
    );
    expect(runtimeRoute).toBe(
      "/admin/audit/conversations/conversation%20one/runtime/turn%20one",
    );
    expect(managementRouteFamily(runtimeRoute)).toBe("/admin/audit");
  });
});

describe("Document Library destinations", () => {
  it.each([
    ["team", "team one/primary", "team%20one%2Fprimary"],
    ["project", "project one?active=true", "project%20one%3Factive%3Dtrue"],
  ] as const)(
    "encodes a %s scope id while preserving the canonical pathname",
    (scopeType, scopeId, encodedScopeId) => {
      const destination = documentLibraryDestination(scopeType, scopeId);
      expect(destination).toBe(
        `/admin/document-library?scope_type=${scopeType}&scope_id=${encodedScopeId}`,
      );
      expect(new URL(destination, "https://atlas.example").pathname).toBe(
        "/admin/document-library",
      );
    },
  );
});
