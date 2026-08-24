import type { SessionState } from "../features/identity-session/index";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

export function createNotesHandler(
  getSession: () => SessionState,
): MockApiHandler {
  let notesCheckpointInterval = 30;
  const notesScope = {
    scope_type: "project" as const,
    scope_id: "proj-admin-live",
    label: "Admin Live Project",
  };
  const notesDetail = {
    note_id: "note-shared-001",
    scope: notesScope,
    category_id: "category-decisions",
    title: "Architecture decisions",
    lifecycle_status: "active" as const,
    metadata_revision: 1,
    accepted_update_head: 2,
    savepoint_head: 1,
    collaboration_epoch: 1,
    updated_actor_id: "user-admin-001",
    updated_at: "2026-08-13T00:00:00Z",
    created_actor_id: "user-admin-001",
    created_at: "2026-08-13T00:00:00Z",
    trashed_actor_id: null,
    trashed_at: null,
  };
  return ({ url, method, init }) => {
    const session = getSession();
    if (url.pathname === "/api/v1/notes/scopes" && method === "GET") {
      return jsonResponse({
        items: [
          ...(session.system_role === "admin"
            ? [notesScope]
            : session.available_projects
                .filter((project) => project.membership_status === "active")
                .map((project) => ({
                  scope_type: "project" as const,
                  scope_id: project.project_id,
                  label: project.name,
                }))),
          ...Object.keys(session.team_roles).map((teamId) => ({
            scope_type: "team" as const,
            scope_id: teamId,
            label: teamId,
          })),
        ],
      });
    }
    if (url.pathname === "/api/v1/notes" && method === "GET") {
      return jsonResponse({
        items: url.searchParams.get("scope_id") !== notesScope.scope_id
          ? []
          : url.searchParams.get("lifecycle_status") === "trashed"
            ? [{ ...notesDetail, note_id: "note-trashed-001", title: "Archived decisions", lifecycle_status: "trashed", metadata_revision: 2, collaboration_epoch: 2, trashed_actor_id: "user-admin-001", trashed_at: "2026-08-13T01:00:00Z" }]
            : [notesDetail],
      });
    }
    if (url.pathname === "/api/v1/notes" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        ...notesDetail,
        note_id: "note-owner-created",
        title: body.title,
        category_id: body.category_id,
      }, 201);
    }
    if (url.pathname === "/api/v1/note-categories" && method === "GET") {
      return jsonResponse({
        items: url.searchParams.get("lifecycle_status") === "trashed"
          ? []
          : [{
              category_id: "category-decisions",
              scope: notesScope,
              name: "Decisions",
              lifecycle_status: "active",
              metadata_revision: 1,
              created_actor_id: "user-admin-001",
              created_at: "2026-08-13T00:00:00Z",
              updated_actor_id: "user-admin-001",
              updated_at: "2026-08-13T00:00:00Z",
              trashed_actor_id: null,
              trashed_at: null,
            }],
      });
    }
    if (url.pathname === "/api/v1/admin/notes/settings" && method === "GET") {
      return jsonResponse({
        checkpoint_interval_seconds: notesCheckpointInterval,
        settings_revision: 1,
        updated_actor_id: "user-admin-001",
        updated_at: "2026-08-13T00:00:00Z",
      });
    }
    if (url.pathname === "/api/v1/admin/notes/settings" && method === "PATCH") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      notesCheckpointInterval = body.checkpoint_interval_seconds;
      return jsonResponse({
        checkpoint_interval_seconds: notesCheckpointInterval,
        settings_revision: 2,
        updated_actor_id: "user-admin-001",
        updated_at: "2026-08-13T00:01:00Z",
      });
    }
    const noteDetailMatch = url.pathname.match(/^\/api\/v1\/notes\/([^/]+)$/);
    if (noteDetailMatch && method === "GET") {
      const trashed = noteDetailMatch[1] === "note-trashed-001";
      return jsonResponse(trashed
        ? { ...notesDetail, note_id: noteDetailMatch[1], title: "Archived decisions", lifecycle_status: "trashed", metadata_revision: 2, collaboration_epoch: 2, trashed_actor_id: "user-admin-001", trashed_at: "2026-08-13T01:00:00Z" }
        : notesDetail);
    }
    const revisionsMatch = url.pathname.match(/^\/api\/v1\/notes\/([^/]+)\/revisions$/);
    if (revisionsMatch && method === "GET") {
      return jsonResponse({ items: [{
        revision_id: "revision-2",
        note_id: revisionsMatch[1],
        sequence: 2,
        server_timestamp: "2026-08-13T00:00:10Z",
        actor_id: "user-admin-001",
        event_kind: "content_update",
        before_digest: "before",
        after_digest: "after",
        change_set: { text: [{ change: "insert", path: [0], before: "", after: "Decision", from_offset: 0, to_offset: 0 }], nodes: [], marks: [{ change: "add", path: [0], mark_type: "bold", before: null, after: {} }], attributes: [], moves: [] },
        restore_source_savepoint_id: null,
      }] });
    }
    const savepointsMatch = url.pathname.match(/^\/api\/v1\/notes\/([^/]+)\/savepoints$/);
    if (savepointsMatch && method === "GET") {
      return jsonResponse({ items: [{
        savepoint_id: "savepoint-1",
        note_id: savepointsMatch[1],
        sequence: 1,
        covered_revision: 2,
        body_digest: "after",
        aggregate_change_set: { text: [], nodes: [], marks: [], attributes: [], moves: [] },
        contributor_actor_ids: ["user-admin-001"],
        created_at: "2026-08-13T00:00:30Z",
      }] });
    }
    const savepointMatch = url.pathname.match(/^\/api\/v1\/notes\/([^/]+)\/savepoints\/([^/]+)$/);
    if (savepointMatch && method === "GET") {
      return jsonResponse({
        savepoint_id: savepointMatch[2],
        note_id: savepointMatch[1],
        sequence: 1,
        covered_revision: 2,
        body_digest: "after",
        aggregate_change_set: { text: [], nodes: [], marks: [], attributes: [], moves: [] },
        contributor_actor_ids: ["user-admin-001"],
        created_at: "2026-08-13T00:00:30Z",
        canonical_body: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "Decision" }] }] },
        document_schema: "tiptap-prosemirror-v1",
      });
    }
    return undefined;
  };
}
