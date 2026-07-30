import { requestJson } from "../../shared/api-client";
import type { AdminActionResult } from "../../shared/api-contracts";
import type {
  ProjectAccessGrant,
  ProjectAccessGrantListResult,
  ProjectAdminListResult,
  ProjectMemberCandidatesResult,
  ProjectMemberEffect,
  ProjectMemberRole,
  ProjectMemberSubjectType,
} from "./types";

export const projectGovernanceApi = {
  listProjects: () => requestJson<ProjectAdminListResult>("/api/v1/admin/projects"),
  createProject: (projectId: string, name: string) =>
    requestJson<AdminActionResult>("/api/v1/admin/projects", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        name,
        policy_profile_id: "policy-default-governed",
        idempotency_key: `project-${projectId}`,
      }),
    }),
  updateProject: (projectId: string, name: string, policyProfileId: string) =>
    requestJson<AdminActionResult>(`/api/v1/admin/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify({
        name,
        policy_profile_id: policyProfileId,
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
