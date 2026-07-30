import type { AdminActionResult } from "../../shared/api-contracts";
import { requestJson } from "../../shared/api-client";
import type {
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
  listUsers: () => requestJson<UserAdminListResult>("/api/v1/admin/users"),
  updateUserProfile: (actorId: string, displayName: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/users/${actorId}`, {
      method: "PATCH",
      body: JSON.stringify({
        display_name: displayName,
        idempotency_key: `user-profile-${actorId}`,
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
};
