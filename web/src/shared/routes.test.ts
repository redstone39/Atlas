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
  normalizeRoute,
  workspaceConversationRoute,
} from "./routes";

describe("Workspace conversation routes", () => {
  it("encodes and parses a canonical conversation route", () => {
    const route = workspaceConversationRoute("conversation one");
    expect(route).toBe("/workspace/conversations/conversation%20one");
    expect(matchAppRoute(normalizeRoute(route)!)).toMatchObject({
      kind: "workspace-conversation",
      conversationId: "conversation one",
    });
  });

  it("fails closed for missing, extra, and malformed conversation ids", () => {
    expect(normalizeRoute("/workspace/conversations")).toBeNull();
    expect(normalizeRoute("/workspace/conversations/conversation-1/extra")).toBeNull();
    expect(normalizeRoute("/workspace/conversations/%E0%A4%A")).toBeNull();
  });
});

describe("directory administration route", () => {
  it("recognizes the dedicated static route without colliding with user detail", () => {
    expect(matchAppRoute(normalizeRoute("/admin/directory")!)).toEqual({
      kind: "static",
      route: "/admin/directory",
    });
    expect(normalizeRoute("/admin/users/directory")).toBe(
      "/admin/users/directory",
    );
    expect(matchAppRoute(normalizeRoute("/admin/users/directory")!)).toMatchObject({
      kind: "admin-user-detail",
      actorId: "directory",
    });
  });
});

describe("identity and access detail routes", () => {
  it("parses the five canonical detail route shapes", () => {
    expect(matchAppRoute(normalizeRoute("/admin/users/user-1")!)).toMatchObject({
      kind: "admin-user-detail",
      actorId: "user-1",
    });
    expect(matchAppRoute(normalizeRoute("/admin/teams/team-1/profile")!)).toMatchObject({
      kind: "admin-team-detail",
      teamId: "team-1",
      section: "profile",
    });
    expect(matchAppRoute(normalizeRoute("/admin/teams/team-1/members")!)).toMatchObject({
      kind: "admin-team-detail",
      section: "members",
    });
    expect(matchAppRoute(normalizeRoute("/admin/projects/project-1/profile")!)).toMatchObject({
      kind: "admin-project-detail",
      projectId: "project-1",
      section: "profile",
    });
    expect(matchAppRoute(normalizeRoute("/admin/projects/project-1/access")!)).toMatchObject({
      kind: "admin-project-detail",
      section: "access",
    });
  });

  it("encodes resource ids and preserves the management route family", () => {
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

  it("fails closed for malformed, bare, and unknown subsection paths", () => {
    expect(normalizeRoute("/admin/users/%E0%A4%A")).toBeNull();
    expect(normalizeRoute("/admin/teams/team-1")).toBeNull();
    expect(normalizeRoute("/admin/teams/team-1/unknown")).toBeNull();
    expect(normalizeRoute("/admin/projects/project-1/members")).toBeNull();
  });
});

describe("audit progressive disclosure routes", () => {
  it("parses the audit landing, collections, transcript, and runtime routes", () => {
    expect(matchAppRoute(normalizeRoute("/admin/audit")!)).toEqual({
      kind: "static",
      route: "/admin/audit",
    });
    expect(matchAppRoute(normalizeRoute("/admin/audit/conversations")!)).toMatchObject({
      kind: "admin-audit-section",
      section: "conversations",
    });
    expect(matchAppRoute(normalizeRoute("/admin/audit/events")!)).toMatchObject({
      kind: "admin-audit-section",
      section: "events",
    });
    expect(
      matchAppRoute(
        normalizeRoute("/admin/audit/conversations/conversation-1/transcript")!,
      ),
    ).toMatchObject({
      kind: "admin-audit-conversation",
      conversationId: "conversation-1",
      section: "transcript",
    });
    expect(
      matchAppRoute(
        normalizeRoute(
          "/admin/audit/conversations/conversation-1/runtime/turn-1",
        )!,
      ),
    ).toMatchObject({
      kind: "admin-audit-conversation",
      conversationId: "conversation-1",
      section: "runtime",
      turnId: "turn-1",
    });
  });

  it("encodes each resource id segment and preserves the audit route family", () => {
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

  it("fails closed for malformed ids, missing ids, and unknown subsections", () => {
    expect(normalizeRoute("/admin/audit/conversations/%E0%A4%A/transcript")).toBeNull();
    expect(normalizeRoute("/admin/audit/conversations/conversation-1")).toBeNull();
    expect(
      normalizeRoute("/admin/audit/conversations/conversation-1/unknown"),
    ).toBeNull();
    expect(
      normalizeRoute("/admin/audit/conversations/conversation-1/runtime"),
    ).toBeNull();
    expect(
      normalizeRoute("/admin/audit/conversations/conversation-1/runtime/%E0%A4%A"),
    ).toBeNull();
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
      expect(
        normalizeRoute(new URL(destination, "https://atlas.example").pathname),
      ).toBe("/admin/document-library");
    },
  );
});
