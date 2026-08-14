import type { SessionState } from "../features/identity-session/index";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

export function createDocumentsHandler(
  getSession: () => SessionState,
): MockApiHandler {
  let processingJobStatus: "failed" | "queued" | "cancelled" = "failed";
  let libraryProcessingJobStatus: "processing" | "queued" | "cancelled" = "processing";
  return ({ url, method }) => {
    const session = getSession();
    if (url.pathname === "/api/v1/workspace/tag-scope") {
      const teamLabels: Record<string, string> = {
        "team-platform": "Platform",
        "team-si": "Signal Integrity",
      };
      const teamIds = new Set(["team-platform", ...Object.keys(session.team_roles)]);
      return jsonResponse({
        tags: [
          ...session.available_projects
            .filter((project) => project.membership_status === "active")
            .map((project) => ({
              tag_type: "project",
              tag_id: project.project_id,
              label: project.name,
            })),
          ...[...teamIds].map((teamId) => ({
            tag_type: "team",
            tag_id: teamId,
            label: teamLabels[teamId] ?? teamId,
          })),
        ],
      });
    }
    if (url.pathname === "/api/v1/library/documents" && method === "GET") {
      const hasKnowledgeScope =
        session.system_role === "admin" ||
        session.available_projects.some((project) => project.membership_status === "active") ||
        Object.keys(session.team_roles).length > 0;
      return jsonResponse({
        documents: hasKnowledgeScope
          ? [
              {
                document_id: "doc-member-guide",
                title: "Signal Integrity Guide",
                document_format: "docx",
                description: "Controlled layout guidance available to your current access.",
                authorized_scopes: [
                  {
                    scope_type: "project",
                    scope_id: "proj-signal-integrity-alpha",
                    scope_label: "Signal Integrity Alpha",
                  },
                ],
                source_filename: "signal-integrity-guide.docx",
                source_byte_size: 4096,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: true,
              },
              {
                document_id: "doc-project-beta",
                title: "Project Beta Runbook",
                document_format: "pdf",
                description: null,
                authorized_scopes: [
                  {
                    scope_type: "project",
                    scope_id: "project-beta",
                    scope_label: "Project Beta",
                  },
                ],
                source_filename: "project-beta-runbook.pdf",
                source_byte_size: 3072,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: true,
              },
              {
                document_id: "doc-view-only",
                title: "Protected Fabrication Note",
                document_format: "pdf",
                description: null,
                authorized_scopes: [
                  {
                    scope_type: "team",
                    scope_id: "team-platform",
                    scope_label: "Platform",
                  },
                ],
                source_filename: "fabrication-note.pdf",
                source_byte_size: 2048,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: false,
              },
              {
                document_id: "doc-team-beta",
                title: "Team Beta Checklist",
                document_format: "pdf",
                description: null,
                authorized_scopes: [
                  {
                    scope_type: "team",
                    scope_id: "team-beta",
                    scope_label: "Team Beta",
                  },
                ],
                source_filename: "team-beta-checklist.pdf",
                source_byte_size: 1024,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: true,
              },
              {
                document_id: "doc-multi-scope",
                title: "Shared Signal Review",
                document_format: "pdf",
                description: "Available through both the Project and Team scope.",
                authorized_scopes: [
                  {
                    scope_type: "project",
                    scope_id: "proj-signal-integrity-alpha",
                    scope_label: "Signal Integrity Alpha",
                  },
                  {
                    scope_type: "team",
                    scope_id: "team-platform",
                    scope_label: "Platform",
                  },
                ],
                source_filename: "shared-signal-review.pdf",
                source_byte_size: 5120,
                uploaded_at: "2026-07-09T00:00:00Z",
                download_available: true,
              },
            ]
          : [],
      });
    }
    if (
      url.pathname.match(/^\/api\/v1\/library\/documents\/[^/]+\/content$/) &&
      (method === "GET" || method === "HEAD")
    ) {
      return Promise.resolve(
        new Response(method === "HEAD" ? null : new Blob(["original document"], { type: "application/octet-stream" }), {
          status: 200,
          headers: { "Content-Type": "application/octet-stream" },
        }),
      );
    }
    if (url.pathname === "/api/v1/admin/document-library" && method === "GET") {
      const scopeType = url.searchParams.get("scope_type");
      const scopeId = url.searchParams.get("scope_id");
      const documents = [
        {
          document_id: "doc-team-uploader-owned",
          title: "Uploader-owned Team note",
          description: "Maintained by the signed-in uploader.",
          intake_status: libraryProcessingJobStatus,
          document_format: "docx",
          profile_id: "default-office",
          profile_revision: 1,
          current_stage: libraryProcessingJobStatus === "queued" ? "queued" : "parsing",
          warning_codes: ["office_preview_unavailable"],
          failure_code: null,
          job_id: "job-team-uploader-owned",
          lifecycle_status: "active",
          uploader_actor_id: "user-team-uploader-001",
          scope_type: "team",
          scope_id: "team-si",
          direct_tags: [
            { tag_type: "team", tag_id: "team-si", label: "Signal Integrity" },
            { tag_type: "project", tag_id: "proj-admin-live", label: "Admin Live Project" },
          ],
          allow_member_download: false,
          download_available:
            session.system_role === "admin" ||
            session.team_roles["team-si"] === "admin",
          source_filename: "uploader-note.docx",
          source_byte_size: 2048,
          content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          raw_sha256: "sha256:test-only",
          uploaded_at: "2026-07-09T00:00:00Z",
          disabled_at: null,
          restored_at: null,
          evidence_count: 3,
        },
        {
          document_id: "doc-project-uploader-owned",
          title: "Uploader-owned Project note",
          description: "Maintained in an authorized Project.",
          intake_status: "registered",
          document_format: "pdf",
          profile_id: null,
          profile_revision: null,
          current_stage: null,
          warning_codes: [],
          failure_code: null,
          job_id: null,
          lifecycle_status: "active",
          uploader_actor_id: "user-project-uploader-001",
          scope_type: "project",
          scope_id: "proj-admin-live",
          direct_tags: [
            { tag_type: "project", tag_id: "proj-admin-live", label: "Admin Live Project" },
          ],
          allow_member_download: false,
          download_available:
            session.system_role === "admin" ||
            session.available_projects.some(
              (project) =>
                project.project_id === "proj-admin-live" &&
                project.membership_status === "active" &&
                project.role === "admin",
            ),
          source_filename: "project-uploader-note.pdf",
          source_byte_size: 3072,
          content_type: "application/pdf",
          raw_sha256: "sha256:test-only",
          uploaded_at: "2026-07-09T00:00:00Z",
          disabled_at: null,
          restored_at: null,
          evidence_count: 0,
        },
        ...(session.system_role === "admin"
          ? [
              {
                document_id: "doc-team-disabled",
                title: "Disabled Team note",
                description: "Disabled document fixture.",
                intake_status: "ready",
                document_format: "pdf",
                profile_id: "default-pdf",
                profile_revision: 1,
                current_stage: "completed",
                warning_codes: [],
                failure_code: null,
                job_id: "job-team-disabled",
                lifecycle_status: "disabled",
                uploader_actor_id: "user-admin-001",
                scope_type: "team",
                scope_id: "team-si",
                direct_tags: [
                  { tag_type: "team", tag_id: "team-si", label: "Signal Integrity" },
                ],
                allow_member_download: false,
                download_available: false,
                source_filename: "disabled-note.pdf",
                source_byte_size: 1024,
                content_type: "application/pdf",
                raw_sha256: "sha256:disabled-test-only",
                uploaded_at: "2026-07-09T00:00:00Z",
                disabled_at: "2026-07-10T00:00:00Z",
                restored_at: null,
                evidence_count: 1,
              },
            ]
          : []),
      ];
      return jsonResponse({
        documents: documents.filter(
          (document) =>
            (!scopeType || !scopeId || document.direct_tags.some(
              (tag) => tag.tag_type === scopeType && tag.tag_id === scopeId,
            )),
        ),
      });
    }
    if (url.pathname === "/api/v1/admin/document-library" && method === "POST") {
      return jsonResponse(
        {
          request_id: "doclib-upload-test",
          status: "applied",
          target_ref: "document:doc-uploaded-test",
          message_code: "document.upload_is_accepted_for_asynchronous_processing", message_params: {},
          audit_event_ref: "audit-doclib-upload-test",
          document: null,
        },
        201,
      );
    }
    if (
      url.pathname.startsWith("/api/v1/admin/document-library/") &&
      url.pathname.endsWith("/events") &&
      method === "GET"
    ) {
      return jsonResponse({
        events: [
          {
            event_id: "audit-doclib-event-test",
            event_type: "document_library_uploaded",
            actor_id: "user-admin-001",
            target_ref: "document:doc-team-uploader-owned",
            project_id: null,
            scope_type: "team",
            scope_id: "team-si",
            document_id: "doc-team-uploader-owned",
            message_code: "document.upload_is_accepted_for_asynchronous_processing", message_params: {},
            metadata: {},
            created_at: "2026-07-10T00:00:00Z",
          },
        ],
      });
    }
    if (url.pathname.startsWith("/api/v1/admin/document-library/") && method === "PATCH") {
      return jsonResponse({
        request_id: "doclib-update-test",
        status: "applied",
        target_ref: "document:doc-team-uploader-owned",
        message_code: "document.settings_are_updated", message_params: {},
        audit_event_ref: "audit-doclib-update-test",
        document: null,
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/document-library/") &&
      ["refresh-searchable-content", "disable", "restore"].some((action) =>
        url.pathname.endsWith(`/${action}`),
      ) &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "doclib-action-test",
        status: "applied",
        target_ref: "document:doc-team-uploader-owned",
        message_code: "document.settings_are_updated", message_params: {},
        audit_event_ref: "audit-doclib-action-test",
      });
    }
    if (url.pathname === "/api/v1/admin/processing-runs" && method === "GET") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/processing/jobs" && method === "GET") {
      return jsonResponse({
        jobs: [
          {
            document_id: "doc-team-uploader-owned",
            document_format: "docx",
            profile_id: "default-office",
            profile_revision: 1,
            current_stage: libraryProcessingJobStatus === "queued" ? "queued" : "parsing",
            warning_codes: ["office_preview_unavailable"],
            failure_code: null,
            job_id: "job-team-uploader-owned",
            status: libraryProcessingJobStatus,
            status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
            retry_available: libraryProcessingJobStatus === "cancelled",
            cancel_available: libraryProcessingJobStatus !== "cancelled",
            review_available: true,
            progress_current: 3,
            progress_total: 10,
            progress_unit: "page",
            elapsed_seconds: 45,
            attempt_started_at: "2026-07-15T00:00:00Z",
            is_current: true,
            created_at: "2026-07-15T00:00:00Z",
            updated_at: "2026-07-15T00:00:45Z",
          },
          {
            document_id: "doc-failed-office-handbook",
            document_format: "docx",
            profile_id: "default-office",
            profile_revision: 1,
            current_stage: processingJobStatus === "queued" ? "queued" : "failed",
            warning_codes: [],
            failure_code: processingJobStatus === "failed" ? "no_searchable_evidence" : null,
            job_id: "job-failed-office-handbook",
            status: processingJobStatus,
            status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
            retry_available: processingJobStatus !== "queued",
            cancel_available: processingJobStatus === "queued",
            review_available: true,
            progress_current: 0,
            progress_total: 12,
            progress_unit: "page",
            elapsed_seconds: 65,
            attempt_started_at: "2026-07-15T00:00:00Z",
            is_current: true,
            created_at: "2026-07-15T00:00:00Z",
            updated_at: "2026-07-15T00:00:00Z",
          },
        ],
      });
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-team-uploader-owned/retry" &&
      method === "POST"
    ) {
      libraryProcessingJobStatus = "queued";
      return jsonResponse({
        document_id: "doc-team-uploader-owned",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "queued",
        warning_codes: ["office_preview_unavailable"],
        failure_code: null,
        job_id: "job-team-uploader-owned",
        status: "queued",
        status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
        retry_available: false,
        cancel_available: true,
        review_available: true,
        progress_current: 3,
        progress_total: 10,
        progress_unit: "page",
        elapsed_seconds: 0,
        attempt_started_at: "2026-07-15T00:01:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:01:00Z",
      }, 202);
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-team-uploader-owned/cancel" &&
      method === "POST"
    ) {
      libraryProcessingJobStatus = "cancelled";
      return jsonResponse({
        document_id: "doc-team-uploader-owned",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "parsing",
        warning_codes: ["office_preview_unavailable"],
        failure_code: null,
        job_id: "job-team-uploader-owned",
        status: "cancelled",
        status_url: "/api/v1/processing/jobs/job-team-uploader-owned",
        retry_available: true,
        cancel_available: false,
        review_available: true,
        progress_current: 3,
        progress_total: 10,
        progress_unit: "page",
        elapsed_seconds: 47,
        attempt_started_at: "2026-07-15T00:00:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:00:47Z",
      });
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-failed-office-handbook/retry" &&
      method === "POST"
    ) {
      processingJobStatus = "queued";
      return jsonResponse(
        {
          document_id: "doc-failed-office-handbook",
          document_format: "docx",
          profile_id: "default-office",
          profile_revision: 1,
          current_stage: "queued",
          warning_codes: [],
          failure_code: null,
          job_id: "job-failed-office-handbook",
          status: "queued",
          status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
          retry_available: false,
          cancel_available: true,
          review_available: true,
          progress_current: 0,
          progress_total: 12,
          progress_unit: "page",
          elapsed_seconds: 0,
          attempt_started_at: "2026-07-15T00:01:00Z",
          is_current: true,
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:01:00Z",
        },
        202,
      );
    }
    if (
      url.pathname ===
        "/api/v1/processing/jobs/job-failed-office-handbook/cancel" &&
      method === "POST"
    ) {
      processingJobStatus = "cancelled";
      return jsonResponse({
        document_id: "doc-failed-office-handbook",
        document_format: "docx",
        profile_id: "default-office",
        profile_revision: 1,
        current_stage: "queued",
        warning_codes: [],
        failure_code: null,
        job_id: "job-failed-office-handbook",
        status: "cancelled",
        status_url: "/api/v1/processing/jobs/job-failed-office-handbook",
        retry_available: true,
        cancel_available: false,
        review_available: true,
        progress_current: 0,
        progress_total: 12,
        progress_unit: "page",
        elapsed_seconds: 1,
        attempt_started_at: "2026-07-15T00:01:00Z",
        is_current: true,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:01:01Z",
      });
    }
    if (url.pathname === "/api/v1/admin/processing-plugins" && method === "GET") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/admin/processing-profiles" && method === "GET") {
      return jsonResponse({ items: [] });
    }
    return undefined;
  };
}
