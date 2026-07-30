import { requestJson } from "../../shared/api-client";
import type { ProcessingPluginVersion, ProcessingProfile, ProcessingProfileRevision, ProcessingRun, ProcessingRunDetail } from "./types";

const idempotency = () => crypto.randomUUID();

export const processingPluginsApi = {
  listPlugins: () => requestJson<{ items: ProcessingPluginVersion[] }>("/api/v1/admin/processing-plugins"),
  upload: (file: File) => {
    const body = new FormData(); body.set("package", file);
    return requestJson<ProcessingPluginVersion>("/api/v1/admin/processing-plugins/packages", {
      method: "POST", body, headers: { "Idempotency-Key": idempotency() },
    });
  },
  mutatePlugin: (plugin: ProcessingPluginVersion, action: "validate" | "canary" | "disable") =>
    requestJson<ProcessingPluginVersion>(`/api/v1/admin/processing-plugins/${plugin.plugin_id}/versions/${plugin.plugin_version}/${action}`, {
      method: "POST", headers: { "Idempotency-Key": idempotency(), "If-Match": String(plugin.revision) }, body: "{}",
    }),
  listProfiles: () => requestJson<{ items: ProcessingProfile[] }>("/api/v1/admin/processing-profiles"),
  createProfile: (profile_id: string, display_name: string) => requestJson<ProcessingProfile>("/api/v1/admin/processing-profiles", {
    method: "POST", headers: { "Idempotency-Key": idempotency() }, body: JSON.stringify({ profile_id, display_name }),
  }),
  createRevision: (profileId: string, expectedRevision: number, payload: object) => requestJson<ProcessingProfileRevision>(`/api/v1/admin/processing-profiles/${profileId}/revisions`, {
    method: "POST", headers: { "Idempotency-Key": idempotency(), "If-Match": String(expectedRevision) }, body: JSON.stringify(payload),
  }),
  activate: (profileId: string, revision: number, expectedRevision: number) => requestJson<ProcessingProfileRevision>(`/api/v1/admin/processing-profiles/${profileId}/revisions/${revision}/activate`, {
    method: "POST", headers: { "Idempotency-Key": idempotency(), "If-Match": String(expectedRevision) }, body: "{}",
  }),
  listRuns: () => requestJson<{ items: ProcessingRun[] }>("/api/v1/admin/processing-runs"),
  showRun: (runId: string) => requestJson<ProcessingRunDetail>(`/api/v1/admin/processing-runs/${runId}`),
  retryRun: (runId: string) => requestJson<ProcessingRun>(`/api/v1/admin/processing-runs/${runId}/retry`, {
    method: "POST", headers: { "Idempotency-Key": idempotency() }, body: "{}",
  }),
};
