import type { AdminActionResult } from "../../shared/api-contracts";
import { requestJson } from "../../shared/api-client";
import type {
  DirectoryProfileSummary,
  EditableSystemRole,
  UserAdminFilters,
  UserAdminListResult,
  UserInviteCreateResult,
  UserInviteListResult,
} from "./types";

export const userAdministrationApi = {
  createInvite: (
    displayName: string,
    email: string,
    scope?: {
      scopeType: "team" | "project";
      scopeId: string;
      scopeRole: "member" | "uploader" | "viewer" | "contributor" | "admin";
    },
  ) =>
    requestJson<UserInviteCreateResult>("/api/v1/admin/user-invites", {
      method: "POST",
      body: JSON.stringify({
        display_name: displayName,
        email,
        system_role: "user",
        scope_type: scope?.scopeType,
        scope_id: scope?.scopeId,
        scope_role: scope?.scopeRole,
        idempotency_key: `invite-${email}`,
      }),
    }),
  listInvites: () => requestJson<UserInviteListResult>("/api/v1/admin/user-invites"),
  revokeInvite: (inviteId: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/user-invites/${inviteId}/revoke`, {
      method: "POST",
      body: JSON.stringify({ idempotency_key: `revoke-${inviteId}` }),
    }),
  listUsers: (filters: UserAdminFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    const query = params.toString();
    return requestJson<UserAdminListResult>(
      `/api/v1/admin/users${query ? `?${query}` : ""}`,
    );
  },
  updateUserDetails: (
    actorId: string,
    updates: { displayName?: string; systemRole?: EditableSystemRole },
  ) =>
    requestJson<AdminActionResult>(`/api/v1/admin/users/${actorId}`, {
      method: "PATCH",
      body: JSON.stringify({
        ...(updates.displayName !== undefined ? { display_name: updates.displayName } : {}),
        ...(updates.systemRole !== undefined ? { system_role: updates.systemRole } : {}),
        idempotency_key: `user-details-${actorId}-${updates.systemRole ?? "profile"}`,
      }),
    }),
  updateUserActive: (actorId: string, active: boolean) =>
    requestJson<AdminActionResult>(`/api/v1/admin/users/${actorId}`, {
      method: "PATCH",
      body: JSON.stringify({
        active,
        idempotency_key: `${active ? "reactivate" : "deactivate"}-${actorId}`,
      }),
    }),
  refreshDirectoryProfile: (actorId: string) =>
    requestJson<DirectoryProfileSummary>(
      `/api/v1/admin/users/${encodeURIComponent(actorId)}/directory-profile/refresh`,
      { method: "POST" },
    ),
};
