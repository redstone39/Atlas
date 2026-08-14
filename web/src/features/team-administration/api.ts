import type { AdminActionResult } from "../../shared/api-contracts";
import { requestJson } from "../../shared/api-client";
import type { TeamScopeRole } from "../../shared/identity-access-contracts";
import type {
  TeamDirectoryConnectionListResult,
  TeamDirectoryMemberImportResult,
  TeamDirectoryUserSearchResult,
  TeamListResult,
  TeamMemberCandidatesResult,
  TeamMemberListResult,
} from "./types";

export const teamAdministrationApi = {
  listTeams: () => requestJson<TeamListResult>("/api/v1/admin/teams"),
  listTeamMembers: (teamId: string) =>
    requestJson<TeamMemberListResult>(`/api/v1/admin/teams/${teamId}/members`),
  listTeamMemberCandidates: (teamId: string) =>
    requestJson<TeamMemberCandidatesResult>(
      `/api/v1/admin/teams/${teamId}/member-candidates`,
    ),
  listDirectoryConnections: (teamId: string) =>
    requestJson<TeamDirectoryConnectionListResult>(
      `/api/v1/admin/teams/${teamId}/directory-connections`,
    ),
  searchDirectoryUsers: (
    teamId: string,
    connectionId: string,
    searchMode: "department" | "member",
    query: string,
  ) =>
    requestJson<TeamDirectoryUserSearchResult>(
      `/api/v1/admin/teams/${teamId}/directory-connections/${encodeURIComponent(connectionId)}/users/search`,
      {
        method: "POST",
        body: JSON.stringify({ search_mode: searchMode, query, limit: 100 }),
      },
    ),
  importDirectoryMembers: (
    teamId: string,
    connectionId: string,
    externalSubjects: string[],
    role: TeamScopeRole,
    idempotencyKey: string,
  ) =>
    requestJson<TeamDirectoryMemberImportResult>(
      `/api/v1/admin/teams/${teamId}/directory-connections/${encodeURIComponent(connectionId)}/users/import`,
      {
        method: "POST",
        body: JSON.stringify({
          external_subjects: externalSubjects,
          role,
          idempotency_key: idempotencyKey,
        }),
      },
    ),
  createTeam: (teamId: string, name: string, parentTeamId: string | null) =>
    requestJson<AdminActionResult>("/api/v1/admin/teams", {
      method: "POST",
      body: JSON.stringify({
        team_id: teamId,
        name,
        parent_team_id: parentTeamId || null,
        idempotency_key: `team-${teamId}`,
      }),
    }),
  updateTeam: (
    teamId: string,
    name: string,
    parentTeamId: string | null,
    status: "active" | "retired",
    inheritParentDocuments: boolean,
  ) =>
    requestJson<AdminActionResult>(`/api/v1/admin/teams/${teamId}`, {
      method: "PATCH",
      body: JSON.stringify({
        name,
        parent_team_id: parentTeamId,
        status,
        inherit_parent_documents: inheritParentDocuments,
        idempotency_key: `team-update-${teamId}`,
      }),
    }),
  addTeamMember: (
    teamId: string,
    memberActorType: "user" | "service_account",
    memberActorId: string,
    role: TeamScopeRole = "member",
  ) =>
    requestJson<AdminActionResult>(`/api/v1/admin/teams/${teamId}/members`, {
      method: "POST",
      body: JSON.stringify({
        member_actor_type: memberActorType,
        member_actor_id: memberActorId,
        role,
        idempotency_key: `team-member-${teamId}-${memberActorId}`,
      }),
    }),
  removeTeamMember: (teamId: string, membershipId: string) =>
    requestJson<AdminActionResult>(
      `/api/v1/admin/teams/${teamId}/members/${membershipId}`,
      { method: "DELETE" },
    ),
};
