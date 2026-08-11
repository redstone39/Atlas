/** Stable application route contract shared by composition and pages. */
export type StaticAppRoute =
  | "/login"
  | "/accept-invite"
  | "/workspace"
  | "/library"
  | "/settings"
  | "/admin/document-library"
  | "/admin/directory"
  | "/admin/users"
  | "/admin/teams"
  | "/admin/projects"
  | "/admin/models"
  | "/admin/plugins"
  | "/admin/agents"
  | "/admin/audit"
  | "/admin/ops";

export type AppRoute =
  | StaticAppRoute
  | `/workspace/conversations/${string}`
  | "/admin/audit/conversations"
  | "/admin/audit/events"
  | `/admin/audit/conversations/${string}/transcript`
  | `/admin/audit/conversations/${string}/runtime/${string}`
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
    };

const STATIC_ROUTES = new Set<StaticAppRoute>([
  "/login",
  "/accept-invite",
  "/workspace",
  "/library",
  "/settings",
  "/admin/document-library",
  "/admin/directory",
  "/admin/users",
  "/admin/teams",
  "/admin/projects",
  "/admin/models",
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
  if (STATIC_ROUTES.has(route as StaticAppRoute)) {
    return { kind: "static", route: route as StaticAppRoute };
  }
  const segments = route.split("/").filter(Boolean);
  if (segments[0] === "workspace") {
    return {
      kind: "workspace-conversation",
      route,
      conversationId: decodeURIComponent(segments[2]),
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

export function managementRouteFamily(route: AppRoute): StaticAppRoute | null {
  const match = matchAppRoute(route);
  if (match.kind === "admin-user-detail") return "/admin/users";
  if (match.kind === "admin-team-detail") return "/admin/teams";
  if (match.kind === "admin-project-detail") return "/admin/projects";
  if (
    match.kind === "admin-audit-section" ||
    match.kind === "admin-audit-conversation"
  ) return "/admin/audit";
  if (match.kind === "static" && match.route.startsWith("/admin/")) {
    return match.route;
  }
  return null;
}

function decodeResourceId(segment: string): string | null {
  const value = decodeURIComponent(segment);
  if (!value || value.includes("/") || value.includes("\\")) return null;
  return value;
}
