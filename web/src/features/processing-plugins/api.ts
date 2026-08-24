import { requestJson } from "../../shared/api-client";
import { clientRequestId } from "../../shared/ids";
import type { ProcessingPluginVersion, ProcessingProfile, ProcessingProfileRevision, ProcessingRun, ProcessingRunDetail } from "./types";


export const processingPluginsApi = {
  listPlugins: () => requestJson<{ items: ProcessingPluginVersion[] }>("/api/v1/admin/processing-plugins"),
  upload: (file: File) => {
    const body = new FormData(); body.set("package", file);
    return requestJson<ProcessingPluginVersion>("/api/v1/admin/processing-plugins/packages", {
      method: "POST", body, headers: { "Idempotency-Key": clientRequestId("processing-plugin-upload") },
    });
  },
  mutatePlugin: (plugin: ProcessingPluginVersion, action: "validate" | "canary" | "disable") =>
    requestJson<ProcessingPluginVersion>(`/api/v1/admin/processing-plugins/${plugin.plugin_id}/versions/${plugin.plugin_version}/${action}`, {
      method: "POST", headers: { "Idempotency-Key": clientRequestId(`processing-plugin-${action}`), "If-Match": String(plugin.revision) }, body: "{}",
    }),
  listProfiles: () => requestJson<{ items: ProcessingProfile[] }>("/api/v1/admin/processing-profiles"),
  createProfile: (display_name: string, idempotencyKey: string) => requestJson<ProcessingProfile>("/api/v1/admin/processing-profiles", {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ display_name }),
  }),
  createRevision: (profileId: string, expectedRevision: number, payload: object, idempotencyKey: string) => requestJson<ProcessingProfileRevision>(`/api/v1/admin/processing-profiles/${profileId}/revisions`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey, "If-Match": String(expectedRevision) }, body: JSON.stringify(payload),
  }),
  activate: (profileId: string, revision: number, expectedRevision: number) => requestJson<ProcessingProfileRevision>(`/api/v1/admin/processing-profiles/${profileId}/revisions/${revision}/activate`, {
    method: "POST", headers: { "Idempotency-Key": clientRequestId("processing-profile-activate"), "If-Match": String(expectedRevision) }, body: "{}",
  }),
  listRuns: () => requestJson<{ items: ProcessingRun[] }>("/api/v1/admin/processing-runs"),
  showRun: (runId: string) => requestJson<ProcessingRunDetail>(`/api/v1/admin/processing-runs/${runId}`),
  retryRun: (runId: string) => requestJson<ProcessingRun>(`/api/v1/admin/processing-runs/${runId}/retry`, {
    method: "POST", headers: { "Idempotency-Key": clientRequestId("processing-run-retry") }, body: "{}",
  }),
};
