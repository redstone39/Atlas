/** Stable application route contract shared by composition and pages. */
export type StaticAppRoute =
  | "/login"
  | "/accept-invite"
  | "/setup"
  | "/workspace"
  | "/workspace/projects"
  | "/workspace/teams"
  | "/projects"
  | "/teams"
  | "/settings"
  | "/admin/document-library"
  | "/admin/directory"
  | "/admin/users"
  | "/admin/teams"
  | "/admin/projects"
  | "/admin/models"
  | "/admin/prompt-skills"
  | "/admin/plugins"
  | "/admin/agents"
  | "/admin/audit"
  | "/admin/ops";

export type NotesScopeRoute =
  | `/workspace/${"projects" | "teams"}/${string}/notes`
  | `/workspace/${"projects" | "teams"}/${string}/notes/trash`
  | `/workspace/${"projects" | "teams"}/${string}/notes/${string}`
  | `/workspace/${"projects" | "teams"}/${string}/notes/${string}/history`
  | `/workspace/${"projects" | "teams"}/${string}/notes/${string}/history/${string}`
  | `/${"projects" | "teams"}/${string}/notes`
  | `/${"projects" | "teams"}/${string}/notes/trash`
  | `/${"projects" | "teams"}/${string}/notes/${string}`
  | `/${"projects" | "teams"}/${string}/notes/${string}/history`
  | `/${"projects" | "teams"}/${string}/notes/${string}/history/${string}`;

export type AppRoute =
  | StaticAppRoute
  | NotesScopeRoute
  | `/workspace/conversations/${string}`
  | `/workspace/projects/${string}/knowledge`
  | `/workspace/teams/${string}/knowledge`
  | `/projects/${string}/knowledge`
  | `/teams/${string}/knowledge`
  | "/admin/audit/conversations"
  | "/admin/audit/events"
  | `/admin/audit/conversations/${string}/transcript`
  | `/admin/audit/conversations/${string}/runtime/${string}`
  | "/admin/audit/agent-research"
  | `/admin/audit/agent-research/${string}`
  | `/admin/audit/agent-research/${string}/runtime`
  | `/admin/users/${string}`
  | `/admin/teams/${string}/profile`
  | `/admin/teams/${string}/members`
  | `/admin/projects/${string}/profile`
  | `/admin/projects/${string}/access`;

export type DocumentLibraryDestination =
  `/admin/document-library?scope_type=${"team" | "project"}&scope_id=${string}`;

export type AppDestination = AppRoute | DocumentLibraryDestination;

export type AppRouteMatch =
  | { kind: "static"; route: StaticAppRoute }
  | { kind: "workspace-conversation"; route: AppRoute; conversationId: string }
  | { kind: "workspace-projects"; route: AppRoute; projectId: string | null }
  | { kind: "workspace-teams"; route: AppRoute; teamId: string | null }
  | { kind: "project-knowledge"; route: AppRoute; projectId: string }
  | { kind: "team-knowledge"; route: AppRoute; teamId: string }
  | {
      kind: "scope-notes";
      route: NotesScopeRoute;
      workspace: boolean;
      scopeType: "project" | "team";
      scopeId: string;
      view: "list" | "trash" | "editor" | "history" | "preview";
      noteId?: string;
      savepointId?: string;
    }
  | { kind: "admin-user-detail"; route: AppRoute; actorId: string }
  | {
      kind: "admin-team-detail";
      route: AppRoute;
      teamId: string;
      section: "profile" | "members";
    }
  | {
      kind: "admin-project-detail";
      route: AppRoute;
      projectId: string;
      section: "profile" | "access";
    }
  | {
      kind: "admin-audit-section";
      route: AppRoute;
      section: "conversations" | "events";
    }
  | {
      kind: "admin-audit-conversation";
      route: AppRoute;
      conversationId: string;
      section: "transcript" | "runtime";
      turnId?: string;
    }
  | {
      kind: "admin-audit-agent-research";
      route: AppRoute;
      section: "list" | "detail" | "runtime";
      researchId?: string;
    };

const STATIC_ROUTES = new Set<StaticAppRoute>([
  "/login",
  "/setup",
  "/accept-invite",
  "/workspace",
  "/projects",
  "/workspace/projects",
  "/workspace/teams",
  "/teams",
  "/settings",
  "/admin/document-library",
  "/admin/directory",
  "/admin/users",
  "/admin/teams",
  "/admin/projects",
  "/admin/models",
  "/admin/prompt-skills",
  "/admin/plugins",
  "/admin/agents",
  "/admin/audit",
  "/admin/ops",
]);

export function adminUserDetailRoute(actorId: string): AppRoute {
  return `/admin/users/${encodeURIComponent(actorId)}`;
}

export function workspaceConversationRoute(conversationId: string): AppRoute {
  return `/workspace/conversations/${encodeURIComponent(conversationId)}`;
}
export function workspaceProjectKnowledgeRoute(projectId: string): AppRoute {
  return `/workspace/projects/${encodeURIComponent(projectId)}/knowledge`;
}

export function workspaceTeamKnowledgeRoute(teamId: string): AppRoute {
  return `/workspace/teams/${encodeURIComponent(teamId)}/knowledge`;
}
export function projectKnowledgeRoute(projectId: string): AppRoute {
  return `/projects/${encodeURIComponent(projectId)}/knowledge`;
}

export function teamKnowledgeRoute(teamId: string): AppRoute {
  return `/teams/${encodeURIComponent(teamId)}/knowledge`;
}
export function scopeNotesRoute(
  scopeType: "project" | "team",
  scopeId: string,
  view:
    | { kind: "list" }
    | { kind: "trash" }
    | { kind: "editor"; noteId: string }
    | { kind: "history"; noteId: string }
    | { kind: "preview"; noteId: string; savepointId: string },
  workspace = false,
): NotesScopeRoute {
  const family = scopeType === "project" ? "projects" : "teams";
  const prefix = `${workspace ? "/workspace" : ""}/${family}/${encodeURIComponent(scopeId)}/notes`;
  if (view.kind === "list") return prefix as NotesScopeRoute;
  if (view.kind === "trash") return `${prefix}/trash` as NotesScopeRoute;
  const note = `${prefix}/${encodeURIComponent(view.noteId)}`;
  if (view.kind === "editor") return note as NotesScopeRoute;
  if (view.kind === "history") return `${note}/history` as NotesScopeRoute;
  return `${note}/history/${encodeURIComponent(view.savepointId)}` as NotesScopeRoute;
}


export function adminTeamDetailRoute(
  teamId: string,
  section: "profile" | "members",
): AppRoute {
  return `/admin/teams/${encodeURIComponent(teamId)}/${section}`;
}

export function adminProjectDetailRoute(
  projectId: string,
  section: "profile" | "access",
): AppRoute {
  return `/admin/projects/${encodeURIComponent(projectId)}/${section}`;
}

export function documentLibraryDestination(
  scopeType: "team" | "project",
  scopeId: string,
): DocumentLibraryDestination {
  return `/admin/document-library?scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`;
}

export function adminAuditSectionRoute(
  section: "conversations" | "events",
): AppRoute {
  return `/admin/audit/${section}`;
}

export function adminAgentResearchAuditRoute(): AppRoute;
export function adminAgentResearchAuditRoute(
  researchId: string,
  section?: "detail" | "runtime",
): AppRoute;
export function adminAgentResearchAuditRoute(
  researchId?: string,
  section: "detail" | "runtime" = "detail",
): AppRoute {
  if (!researchId) return "/admin/audit/agent-research";
  const base = `/admin/audit/agent-research/${encodeURIComponent(researchId)}`;
  return (section === "runtime" ? `${base}/runtime` : base) as AppRoute;
}

export function adminAuditConversationRoute(
  conversationId: string,
  section: "transcript",
): AppRoute;
export function adminAuditConversationRoute(
  conversationId: string,
  section: "runtime",
  turnId: string,
): AppRoute;
export function adminAuditConversationRoute(
  conversationId: string,
  section: "transcript" | "runtime",
  turnId?: string,
): AppRoute {
  return section === "runtime"
    ? `/admin/audit/conversations/${encodeURIComponent(conversationId)}/runtime/${encodeURIComponent(turnId ?? "")}`
    : `/admin/audit/conversations/${encodeURIComponent(conversationId)}/transcript`;
}


export function matchAppRoute(route: AppRoute): AppRouteMatch {
  if (route === "/workspace/projects") {
    return { kind: "workspace-projects", route, projectId: null };
  }
  if (route === "/workspace/teams") {
    return { kind: "workspace-teams", route, teamId: null };
  }
  if (STATIC_ROUTES.has(route as StaticAppRoute)) {
    return { kind: "static", route: route as StaticAppRoute };
  }
  const segments = route.split("/").filter(Boolean);
  const notesMatch = matchNotesRoute(route);
  if (notesMatch) return notesMatch;
  if (segments[0] === "workspace") {
    if (
      segments[1] === "projects" &&
      segments.length === 4 &&
      segments[3] === "knowledge"
    ) {
      return {
        kind: "workspace-projects",
        route,
        projectId: decodeURIComponent(segments[2]),
      };
    }
    if (
      segments[1] === "teams" &&
      segments.length === 4 &&
      segments[3] === "knowledge"
    ) {
      return {
        kind: "workspace-teams",
        route,
        teamId: decodeURIComponent(segments[2]),
      };
    }
    return {
      kind: "workspace-conversation",
      route,
      conversationId: decodeURIComponent(segments[2]),
    };
  }
  if (
    segments[0] === "projects" &&
    segments.length === 3 &&
    segments[2] === "knowledge"
  ) {
    return {
      kind: "project-knowledge",
      route,
      projectId: decodeURIComponent(segments[1]),
    };
  }
  if (
    segments[0] === "teams" &&
    segments.length === 3 &&
    segments[2] === "knowledge"
  ) {
    return {
      kind: "team-knowledge",
      route,
      teamId: decodeURIComponent(segments[1]),
    };
  }
  if (segments[1] === "users") {
    return {
      kind: "admin-user-detail",
      route,
      actorId: decodeURIComponent(segments[2]),
    };
  }
  if (segments[1] === "teams") {
    return {
      kind: "admin-team-detail",
      route,
      teamId: decodeURIComponent(segments[2]),
      section: segments[3] as "profile" | "members",
    };
  }
  if (segments[1] === "audit") {
    if (segments[2] === "agent-research") {
      if (segments.length === 3) {
        return {
          kind: "admin-audit-agent-research",
          route,
          section: "list",
        };
      }
      return {
        kind: "admin-audit-agent-research",
        route,
        researchId: decodeURIComponent(segments[3]),
        section: segments[4] === "runtime" ? "runtime" : "detail",
      };
    }
    if (segments.length === 3) {
      return {
        kind: "admin-audit-section",
        route,
        section: segments[2] as "conversations" | "events",
      };
    }
    return {
      kind: "admin-audit-conversation",
      route,
      conversationId: decodeURIComponent(segments[3]),
      section: segments[4] as "transcript" | "runtime",
      turnId: segments[5] ? decodeURIComponent(segments[5]) : undefined,
    };
  }
  return {
    kind: "admin-project-detail",
    route,
    projectId: decodeURIComponent(segments[2]),
    section: segments[3] as "profile" | "access",
  };
}

export function productRouteFamily(route: AppRoute): StaticAppRoute | null {
  const match = matchAppRoute(route);
  if (
    match.kind === "project-knowledge" ||
    (match.kind === "scope-notes" && !match.workspace && match.scopeType === "project")
  ) return "/projects";
  if (
    match.kind === "team-knowledge" ||
    (match.kind === "scope-notes" && !match.workspace && match.scopeType === "team")
  ) return "/teams";
  if (
    match.kind === "static" &&
    (match.route === "/workspace" ||
      match.route === "/projects" ||
      match.route === "/teams" ||
      match.route === "/settings")
  ) {
    return match.route;
  }
  if (match.kind === "workspace-conversation") return "/workspace";
  if (
    match.kind === "workspace-projects" ||
    match.kind === "workspace-teams" ||
    (match.kind === "scope-notes" && match.workspace)
  ) {
    return "/workspace";
  }
  return null;
}

export function managementRouteFamily(route: AppRoute): StaticAppRoute | null {
  const match = matchAppRoute(route);
  if (match.kind === "admin-user-detail") return "/admin/users";
  if (match.kind === "admin-team-detail") return "/admin/teams";
  if (match.kind === "admin-project-detail") return "/admin/projects";
  if (
    match.kind === "admin-audit-section" ||
    match.kind === "admin-audit-conversation" ||
    match.kind === "admin-audit-agent-research"
  ) return "/admin/audit";
  if (match.kind === "static" && match.route.startsWith("/admin/")) {
    return match.route;
  }
  return null;
}
function matchNotesRoute(route: AppRoute): Extract<AppRouteMatch, { kind: "scope-notes" }> | null {
  const segments = route.split("/").filter(Boolean);
  const workspace = segments[0] === "workspace";
  const offset = workspace ? 1 : 0;
  const family = segments[offset];
  if (
    (family !== "projects" && family !== "teams") ||
    segments[offset + 2] !== "notes"
  ) return null;
  const scopeId = decodeResourceId(segments[offset + 1]);
  if (!scopeId) return null;
  const tail = segments.slice(offset + 3);
  const base = {
    kind: "scope-notes" as const,
    route: route as NotesScopeRoute,
    workspace,
    scopeType: family === "projects" ? "project" as const : "team" as const,
    scopeId,
  };
  if (tail.length === 0) {
    return { ...base, view: "list", noteId: undefined, savepointId: undefined };
  }
  if (tail.length === 1 && tail[0] === "trash") {
    return { ...base, view: "trash", noteId: undefined, savepointId: undefined };
  }
  const noteId = decodeResourceId(tail[0]);
  if (!noteId) return null;
  if (tail.length === 1) return { ...base, view: "editor", noteId };
  if (tail.length === 2 && tail[1] === "history") {
    return { ...base, view: "history", noteId };
  }
  const savepointId = tail.length === 3 && tail[1] === "history"
    ? decodeResourceId(tail[2])
    : null;
  if (savepointId) return { ...base, view: "preview", noteId, savepointId };
  return null;
}

function decodeResourceId(segment: string): string | null {
  const value = decodeURIComponent(segment);
  if (!value || value.includes("/") || value.includes("\\")) return null;
  return value;
}
