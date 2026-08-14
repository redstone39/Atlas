import { describe, expect, it } from "vitest";

import {
  adminAuditConversationRoute,
  adminAuditSectionRoute,
  adminProjectDetailRoute,
  adminTeamDetailRoute,
  adminUserDetailRoute,
  documentLibraryDestination,
  managementRouteFamily,
  productRouteFamily,
  projectKnowledgeRoute,
  teamKnowledgeRoute,
  scopeNotesRoute,
  matchAppRoute,
  workspaceConversationRoute,
  workspaceProjectKnowledgeRoute,
  workspaceTeamKnowledgeRoute,
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

  it("encodes and projects Workspace Project and Team knowledge routes", () => {
    expect(matchAppRoute("/workspace/projects")).toMatchObject({
      kind: "workspace-projects",
      projectId: null,
    });
    expect(matchAppRoute("/workspace/teams")).toMatchObject({
      kind: "workspace-teams",
      teamId: null,
    });
    const projectRoute = workspaceProjectKnowledgeRoute("project one");
    const teamRoute = workspaceTeamKnowledgeRoute("team one");
    expect(projectRoute).toBe(
      "/workspace/projects/project%20one/knowledge",
    );
    expect(teamRoute).toBe("/workspace/teams/team%20one/knowledge");
    expect(matchAppRoute(projectRoute)).toMatchObject({
      kind: "workspace-projects",
      projectId: "project one",
    });
    expect(matchAppRoute(teamRoute)).toMatchObject({
      kind: "workspace-teams",
      teamId: "team one",
    });
    expect(productRouteFamily(projectRoute)).toBe("/workspace");
    expect(productRouteFamily(teamRoute)).toBe("/workspace");
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

describe("Project and Team knowledge routes", () => {
  it.each([
    ["project", "", "/projects//knowledge"],
    ["project", "/", "/projects/%2F/knowledge"],
    ["project", "?", "/projects/%3F/knowledge"],
    ["team", "", "/teams//knowledge"],
    ["team", "/", "/teams/%2F/knowledge"],
    ["team", "?", "/teams/%3F/knowledge"],
  ] as const)("encodes a %s knowledge resource segment", (scopeType, scopeId, expected) => {
    const route =
      scopeType === "project"
        ? projectKnowledgeRoute(scopeId)
        : teamKnowledgeRoute(scopeId);
    expect(route).toBe(expected);
  });

  it("matches Project and Team details and projects their product families", () => {
    const projectRoute = projectKnowledgeRoute("project one");
    const teamRoute = teamKnowledgeRoute("team one");
    expect(matchAppRoute(projectRoute)).toMatchObject({
      kind: "project-knowledge",
      projectId: "project one",
    });
    expect(matchAppRoute(teamRoute)).toMatchObject({
      kind: "team-knowledge",
      teamId: "team one",
    });
    expect(productRouteFamily(projectRoute)).toBe("/projects");
    expect(productRouteFamily(teamRoute)).toBe("/teams");
    expect(productRouteFamily("/projects")).toBe("/projects");
    expect(productRouteFamily("/teams")).toBe("/teams");
    expect(productRouteFamily("/workspace")).toBe("/workspace");
    expect(productRouteFamily(workspaceConversationRoute("conversation one"))).toBe(
      "/workspace",
    );
    expect(productRouteFamily("/admin/projects")).toBeNull();
  });

  it("preserves every existing management family", () => {
    expect(managementRouteFamily(adminUserDetailRoute("user-1"))).toBe("/admin/users");
    expect(managementRouteFamily(adminTeamDetailRoute("team-1", "profile"))).toBe(
      "/admin/teams",
    );
    expect(managementRouteFamily(adminProjectDetailRoute("project-1", "access"))).toBe(
      "/admin/projects",
    );
    expect(managementRouteFamily(adminAuditSectionRoute("events"))).toBe("/admin/audit");
    expect(managementRouteFamily(projectKnowledgeRoute("project-1"))).toBeNull();
    expect(managementRouteFamily(teamKnowledgeRoute("team-1"))).toBeNull();
  });
});

describe("Project and Team Notes routes", () => {
  it("keeps trash and history precedence ahead of note IDs", () => {
    const list = scopeNotesRoute("project", "project one", { kind: "list" });
    const trash = scopeNotesRoute("project", "project one", { kind: "trash" });
    const editor = scopeNotesRoute("team", "team one", {
      kind: "editor",
      noteId: "note one",
    });
    const history = scopeNotesRoute("team", "team one", {
      kind: "history",
      noteId: "note one",
    });
    const preview = scopeNotesRoute("team", "team one", {
      kind: "preview",
      noteId: "note one",
      savepointId: "version one",
    });

    expect(matchAppRoute(list)).toMatchObject({
      kind: "scope-notes",
      view: "list",
      scopeId: "project one",
    });
    expect(matchAppRoute(trash)).toMatchObject({
      kind: "scope-notes",
      view: "trash",
      noteId: undefined,
    });
    expect(matchAppRoute(editor)).toMatchObject({
      kind: "scope-notes",
      view: "editor",
      noteId: "note one",
    });
    expect(matchAppRoute(history)).toMatchObject({
      kind: "scope-notes",
      view: "history",
      noteId: "note one",
    });
    expect(matchAppRoute(preview)).toMatchObject({
      kind: "scope-notes",
      view: "preview",
      savepointId: "version one",
    });
  });

  it("projects Workspace mirrors into the retained Workspace shell", () => {
    const route = scopeNotesRoute("project", "project-1", {
      kind: "preview",
      noteId: "note-1",
      savepointId: "savepoint-1",
    }, true);

    expect(matchAppRoute(route)).toMatchObject({
      kind: "scope-notes",
      workspace: true,
      scopeType: "project",
      view: "preview",
    });
    expect(productRouteFamily(route)).toBe("/workspace");
    expect(productRouteFamily(scopeNotesRoute("team", "team-1", { kind: "list" })))
      .toBe("/teams");
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
