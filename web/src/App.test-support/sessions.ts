import type { SessionState } from "../features/identity-session/index";
import type { ReadinessState } from "../features/ops/index";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

export const unauthenticated: SessionState = {
  authenticated: false,
  actor: null,
  available_projects: [],
  system_role: null,
  team_roles: {},
};

export const adminSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-admin-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Atlas Admin",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [],
  system_role: "admin",
  team_roles: {},
};

export const memberSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-engineer-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Engineer One",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [
    {
      project_id: "proj-signal-integrity-alpha",
      name: "Signal Integrity Alpha",
      membership_status: "active",
      role: "viewer",
    },
  ],
  system_role: "user",
  team_roles: {},
};

export const memberWithUnauthorizedProjectSession: SessionState = {
  ...memberSession,
  available_projects: [
    ...memberSession.available_projects,
    {
      project_id: "proj-revoked-lab",
      name: "Revoked Lab",
      membership_status: "revoked",
      role: "viewer",
    },
  ],
};

export const adminWithProjectSession: SessionState = {
  ...adminSession,
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "admin",
    },
    {
      project_id: "proj-signal-integrity-alpha",
      name: "Signal Integrity Alpha",
      membership_status: "active",
      role: "viewer",
    },
  ],
};

export const memberWithoutProjects: SessionState = {
  ...memberSession,
  available_projects: [],
};

export const projectAdminSession: SessionState = {
  authenticated: true,
  actor: {
    actor_id: "user-project-admin-001",
    actor_type: "user",
    issuer: "atlas-local-dev",
    display_name: "Project Admin",
    groups: [],
    correlation_id: "corr-p0-local-dev",
  },
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "admin",
    },
  ],
  system_role: "user",
  team_roles: {},
};

export const projectUploaderSession: SessionState = {
  ...projectAdminSession,
  actor: {
    ...projectAdminSession.actor!,
    actor_id: "user-project-uploader-001",
    display_name: "Project Uploader",
  },
  available_projects: [
    {
      project_id: "proj-admin-live",
      name: "Admin Live Project",
      membership_status: "active",
      role: "contributor",
    },
  ],
};

export const teamAdminSession: SessionState = {
  ...memberWithoutProjects,
  actor: {
    ...memberSession.actor!,
    actor_id: "user-team-admin-001",
    display_name: "Team Admin",
  },
  team_roles: { "team-si": "admin" },
};

export const teamUploaderSession: SessionState = {
  ...memberWithoutProjects,
  actor: {
    ...memberSession.actor!,
    actor_id: "user-team-uploader-001",
    display_name: "Team Uploader",
  },
  team_roles: { "team-si": "uploader", "team-platform": "member" },
};

export const operatorSession: SessionState = {
  ...adminSession,
  actor: {
    ...adminSession.actor!,
    actor_id: "user-operator-001",
    display_name: "Ops Operator",
  },
  system_role: "operator",
};

export const incompleteReadiness: ReadinessState = {
  ready: false,
  health: "degraded",
  setup_blockers: [
    "ops.create_project",
    "ops.grant_active_project_permission",
    "ops.prepare_searchable_evidence",
    "ops.configure_and_test_model_route",
  ],
  evidence_ready_projects: [],
  message_code: "common.setup_is_incomplete", message_params: {},
};

export const readyReadiness: ReadinessState = {
  ready: true,
  health: "ok",
  setup_blockers: [],
  evidence_ready_projects: ["proj-signal-integrity-alpha"],
  message_code: "workspace.is_ready", message_params: {},
};
const claimedFirstAdmin = { claim_available: false } as const;


export function createSessionHandler(
  initialSession: SessionState,
  readiness: ReadinessState,
) {
  let session = {
    ...initialSession,
    team_roles: { ...initialSession.team_roles },
  };
  const handler: MockApiHandler = ({ url, method }) => {
    if (url.pathname === "/api/v1/auth/session" && method === "GET") {
      return jsonResponse(session);
    }
    if (url.pathname === "/api/v1/auth/first-admin" && method === "GET") {
      return jsonResponse(claimedFirstAdmin);
    }
    if (url.pathname === "/api/v1/auth/sessions" && method === "POST") {
      session = adminSession;
      return jsonResponse(session);
    }
    if (url.pathname === "/api/v1/auth/session" && method === "DELETE") {
      session = unauthenticated;
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url.pathname === "/api/v1/ops/readiness") {
      return jsonResponse(readiness);
    }
    return undefined;
  };
  return {
    handler,
    getSession: () => session,
    setSession: (next: SessionState) => {
      session = next;
    },
  };
}
