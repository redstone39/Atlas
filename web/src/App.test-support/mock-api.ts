import { vi } from "vitest";

import type { SessionState } from "../features/identity-session/index";
import type { ModelRouteStatus } from "../features/model-routing/index";
import type { ReadinessState } from "../features/ops/index";
import { createAuditHandler } from "./audit";
import { createDocumentsHandler } from "./documents";
import { createIdentityGovernanceHandler } from "./identity-governance";
import { createModelRoutingHandler } from "./model-routing";
import { createNotesHandler } from "./notes";
import { dispatchMockApi } from "./protocol";
import { createSessionHandler } from "./sessions";
import { createWorkspaceHandler } from "./workspace";

export function mockApi(
  initialSession: SessionState,
  readiness: ReadinessState,
  options: { modelRoutes?: ModelRouteStatus[] } = {},
) {
  const sessions = createSessionHandler(initialSession, readiness);
  const handlers = [
    sessions.handler,
    createNotesHandler(sessions.getSession),
    createDocumentsHandler(sessions.getSession),
    createIdentityGovernanceHandler(sessions.getSession, sessions.setSession),
    createAuditHandler(),
    createModelRoutingHandler(options),
    createWorkspaceHandler(),
  ];

  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    dispatchMockApi(
      {
        url: new URL(String(input), "http://localhost"),
        method: init?.method ?? "GET",
        init,
      },
      handlers,
    ),
  );
}
