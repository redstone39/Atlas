import { requestJson } from "../../shared/api-client";
import type { AdminActionResult } from "../../shared/api-contracts";
import type {
  ProjectAccessGrant,
  ProjectAccessGrantListResult,
  ProjectAdminListResult,
  ProjectDirectoryConnectionListResult,
  ProjectDirectoryMemberImportResult,
  ProjectDirectoryUserSearchResult,
  ProjectMemberCandidatesResult,
  ProjectMemberEffect,
  ProjectMemberRole,
  ProjectMemberSubjectType,
} from "./types";

export const projectGovernanceApi = {
  listProjects: () => requestJson<ProjectAdminListResult>("/api/v1/admin/projects"),
  createProject: (name: string, idempotencyKey: string) =>
    requestJson<AdminActionResult>("/api/v1/admin/projects", {
      method: "POST",
      body: JSON.stringify({
        name,
        policy_profile_id: "policy-default-governed",
        idempotency_key: idempotencyKey,
      }),
    }),
  updateProject: (
    projectId: string,
    updates: {
      name?: string;
      policy_profile_id?: string;
      status?: "active" | "retired";
    },
  ) =>
    requestJson<AdminActionResult>(`/api/v1/admin/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify({
        ...updates,
        idempotency_key: `project-update-${projectId}`,
      }),
    }),
  listProjectMembers: (projectId: string) =>
    requestJson<ProjectAccessGrantListResult>(
      `/api/v1/admin/projects/${projectId}/members`,
    ),
  listProjectMemberCandidates: (projectId: string) =>
    requestJson<ProjectMemberCandidatesResult>(
      `/api/v1/admin/projects/${projectId}/member-candidates`,
    ),
  listDirectoryConnections: (projectId: string) =>
    requestJson<ProjectDirectoryConnectionListResult>(
      `/api/v1/admin/projects/${projectId}/directory-connections`,
    ),
  searchDirectoryUsers: (
    projectId: string,
    connectionId: string,
    searchMode: "department" | "member",
    query: string,
  ) =>
    requestJson<ProjectDirectoryUserSearchResult>(
      `/api/v1/admin/projects/${projectId}/directory-connections/${encodeURIComponent(connectionId)}/users/search`,
      {
        method: "POST",
        body: JSON.stringify({ search_mode: searchMode, query, limit: 100 }),
      },
    ),
  importDirectoryMembers: (
    projectId: string,
    connectionId: string,
    externalSubjects: string[],
    role: ProjectMemberRole,
    idempotencyKey: string,
  ) =>
    requestJson<ProjectDirectoryMemberImportResult>(
      `/api/v1/admin/projects/${projectId}/directory-connections/${encodeURIComponent(connectionId)}/users/import`,
      {
        method: "POST",
        body: JSON.stringify({
          external_subjects: externalSubjects,
          role,
          idempotency_key: idempotencyKey,
        }),
      },
    ),
  addProjectMember: (
    projectId: string,
    subjectType: ProjectMemberSubjectType,
    subjectId: string,
    role: ProjectMemberRole,
    effect: ProjectMemberEffect,
  ) =>
    requestJson<ProjectAccessGrant>(`/api/v1/admin/projects/${projectId}/members`, {
      method: "POST",
      body: JSON.stringify({
        subject_type: subjectType,
        subject_id: subjectId,
        role,
        effect,
        idempotency_key: `project-member-${projectId}-${subjectType}-${subjectId}`,
      }),
    }),
  updateProjectMember: (
    projectId: string,
    grantId: string,
    role: ProjectMemberRole,
    effect: ProjectMemberEffect,
  ) =>
    requestJson<ProjectAccessGrant>(
      `/api/v1/admin/projects/${projectId}/members/${grantId}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          role,
          effect,
          idempotency_key: `project-member-role-${grantId}`,
        }),
      },
    ),
  removeProjectMember: (projectId: string, grantId: string) =>
    requestJson<ProjectAccessGrant>(
      `/api/v1/admin/projects/${projectId}/members/${grantId}`,
      { method: "DELETE" },
    ),
};
