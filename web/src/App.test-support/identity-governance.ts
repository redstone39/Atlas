import { agentList } from "../App.test-agent-fixtures";
import type {
  DirectoryConnectionStatus,
  DirectoryUserCandidate,
} from "../features/directory-administration";
import type { SessionState } from "../features/identity-session/index";
import type {
  ProjectAccessGrant,
  ProjectMemberRole,
} from "../features/project-governance/index";
import type {
  TeamMemberSummary,
  TeamMembershipRecord,
} from "../features/team-administration/index";
import type { UserAdminSummary } from "../features/user-administration/types";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

export function createIdentityGovernanceHandler(
  getSession: () => SessionState,
): MockApiHandler {
  let projectAccessGrants: ProjectAccessGrant[] = [
    {
      grant_id: "grant-project-member-proj-admin-live-user-user-project-admin-001",
      project_id: "proj-admin-live",
      subject_type: "user",
      subject_id: "user-project-admin-001",
      role: "admin",
      effect: "allow",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
      revoked_at: null,
    },
    {
      grant_id: "grant-agent-layout-review-001-signal-integrity-alpha",
      project_id: "proj-signal-integrity-alpha",
      subject_type: "service_account",
      subject_id: "agent-layout-review-001",
      role: "viewer",
      effect: "allow",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
      revoked_at: null,
    },
  ];
  const projectSubjectDirectory: Record<
    string,
    { display_name: string; display_detail: string | null }
  > = {
    "user:user-project-admin-001": {
      display_name: "Project Admin",
      display_detail: "project-admin@example.test",
    },
    "service_account:agent-layout-review-001": {
      display_name: "Layout Review Agent",
      display_detail: null,
    },
    "user:user-engineer-001": {
      display_name: "Engineer One",
      display_detail: "engineer@example.test",
    },
    "user:user-pending-001": {
      display_name: "Invited Engineer",
      display_detail: "pending@example.test",
    },
    "team:team-platform": {
      display_name: "Platform",
      display_detail: null,
    },
    "team:team-si": {
      display_name: "Signal Integrity",
      display_detail: null,
    },
  };
  let directoryConnections: DirectoryConnectionStatus[] = [];
  const directoryCandidates: DirectoryUserCandidate[] = [
    {
      external_subject: "subject-ada",
      username: "ada",
      display_name: "Ada Lovelace",
      email: "ada@example.test",
      groups: ["Research"],
      department: "Engineering",
      title: "Programmer",
      employee_id: "E-100",
      directory_enabled: true,
    },
    {
      external_subject: "subject-grace",
      username: "grace",
      display_name: "Grace Hopper",
      email: "grace@example.test",
      groups: ["Compiler"],
      department: "Engineering",
      title: "Admiral",
      employee_id: "E-101",
      directory_enabled: true,
    },
  ];
  let adminUsers: UserAdminSummary[] = [
    {
      actor_id: "user-admin-001",
      actor_type: "user",
      display_name: "Atlas Admin",
      email: "admin@example.test",
      system_role: "admin",
      active: true,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: null,
      invite_id: null,
      account_source: "local",
      directory_profile: null,
    },
    {
      actor_id: "user-engineer-001",
      actor_type: "user",
      display_name: "Engineer One",
      email: "engineer@example.test",
      system_role: "user",
      active: true,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: "accepted",
      invite_id: "inv-engineer",
      account_source: "local",
      directory_profile: null,
    },
    {
      actor_id: "user-pending-001",
      actor_type: "user",
      display_name: "Invited Engineer",
      email: "pending@example.test",
      system_role: "user",
      active: false,
      created_at: "2026-07-08T00:00:00Z",
      invite_status: "pending",
      invite_id: "inv-pending",
      account_source: "local",
      directory_profile: null,
    },
  ];
  let teamMemberships: TeamMembershipRecord[] = [
    {
      membership_id: "team-member-pending",
      team_id: "team-platform",
      member_actor_type: "user",
      member_actor_id: "user-pending-001",
      role: "member",
      status: "active",
      created_at: "2026-07-08T00:00:00Z",
      removed_at: null,
    },
    {
      membership_id: "team-member-engineer",
      team_id: "team-si",
      member_actor_type: "user",
      member_actor_id: "user-engineer-001",
      role: "member",
      status: "active",
      created_at: "2026-07-08T00:00:00Z",
      removed_at: null,
    },
  ];
  let scopedTeamMembers: TeamMemberSummary[] = [
    {
      membership_id: "tm-team-si-user-team-admin-001",
      team_id: "team-si",
      subject_type: "user",
      subject_id: "user-team-admin-001",
      display_name: "Team Admin",
      display_detail: "team-admin@example.test",
      role: "admin",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
    },
    {
      membership_id: "tm-team-si-agent-layout-review-001",
      team_id: "team-si",
      subject_type: "service_account",
      subject_id: "agent-layout-review-001",
      display_name: "Layout Review Agent",
      display_detail: null,
      role: "member",
      status: "active",
      created_at: "2026-07-09T00:00:00Z",
    },
  ];
  return ({ url, method, init }) => {
    const session = getSession();
    if (url.pathname === "/api/v1/admin/user-invites" && method === "GET") {
      return jsonResponse({
        invites: [
          {
            invite_id: "inv-engineer",
            actor_id: "user-engineer-001",
            email: "engineer@example.test",
            display_name: "Engineer One",
            system_role: "user",
            status: "pending",
            created_at: "2026-07-08T00:00:00Z",
            expires_at: "2026-07-15T00:00:00Z",
            accepted_at: null,
            revoked_at: null,
          },
          {
            invite_id: "inv-pending",
            actor_id: "user-pending-001",
            email: "pending@example.test",
            display_name: "Invited Engineer",
            system_role: "user",
            status: "pending",
            created_at: "2026-07-08T00:00:00Z",
            expires_at: "2026-07-15T00:00:00Z",
            accepted_at: null,
            revoked_at: null,
          },
        ],
      });
    }
    if (url.pathname === "/api/v1/admin/user-invites" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        request_id: `invite-${body.email ?? "engineer@example.test"}`,
        status: "applied",
        invite: {
          invite_id: "inv-engineer",
          actor_id: "user-invited-project-001",
          email: body.email ?? "engineer@example.test",
          display_name: body.display_name ?? "Engineer One",
          system_role: "user",
          status: "pending",
          created_at: "2026-07-08T00:00:00Z",
          expires_at: "2026-07-15T00:00:00Z",
          accepted_at: null,
          revoked_at: null,
          scope_type: body.scope_type ?? null,
          scope_id: body.scope_id ?? null,
          scope_role: body.scope_role ?? null,
        },
        message_code: "invite.is_ready_copy_the_local_acceptance_link", message_params: {},
        audit_event_ref: "audit-invite-created",
        local_pilot_acceptance: {
          mode: "copy_link",
          acceptance_token: "atlas_invite_visible_once",
          acceptance_url: "/accept-invite?token=atlas_invite_visible_once",
        },
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/user-invites/inv-engineer/revoke" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "revoke-inv-engineer",
        status: "applied",
        target_ref: "invite:inv-engineer",
        message_code: "invite.has_been_revoked", message_params: {},
        audit_event_ref: "audit-invite-revoked",
      });
    }
    if (
      url.pathname === "/api/v1/admin/user-invites/inv-pending/revoke" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "revoke-inv-pending",
        status: "applied",
        target_ref: "invite:inv-pending",
        message_code: "invite.has_been_revoked", message_params: {},
        audit_event_ref: "audit-invite-revoked",
      });
    }
    if (url.pathname === "/api/v1/admin/directory-connections" && method === "GET") {
      return jsonResponse({ connections: directoryConnections });
    }
    if (url.pathname === "/api/v1/admin/directory-connections" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const connection: DirectoryConnectionStatus = {
        connection_id: body.connection_id,
        display_name: body.display_name,
        priority: body.priority,
        provider_type: body.provider_type,
        host: body.host,
        port: body.port,
        tls_mode: body.tls_mode,
        connect_timeout_seconds: body.connect_timeout_seconds,
        operation_timeout_seconds: body.operation_timeout_seconds,
        bind_dn: body.bind_dn,
        user_base_dn: body.user_base_dn,
        user_object_filter: body.user_object_filter,
        login_attribute: body.login_attribute,
        stable_id_attribute: body.stable_id_attribute,
        display_name_attribute: body.display_name_attribute,
        email_attribute: body.email_attribute,
        groups_attribute: body.groups_attribute,
        department_attribute: body.department_attribute,
        title_attribute: body.title_attribute,
        employee_id_attribute: body.employee_id_attribute,
        enabled: body.enabled,
        bind_password_configured: Boolean(body.bind_password),
        custom_ca_configured: Boolean(body.custom_ca_pem),
        custom_ca_sha256: body.custom_ca_pem ? "a".repeat(64) : null,
      };
      directoryConnections = [...directoryConnections, connection];
      return jsonResponse(connection, 201);
    }
    const directoryConnectionMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)$/,
    );
    if (directoryConnectionMatch && method === "PATCH") {
      const connectionId = decodeURIComponent(directoryConnectionMatch[1]);
      const body = JSON.parse(String(init?.body ?? "{}"));
      const current = directoryConnections.find(
        (connection) => connection.connection_id === connectionId,
      )!;
      const updated: DirectoryConnectionStatus = {
        ...current,
        ...body,
        connection_id: connectionId,
        bind_password_configured: body.clear_bind_password
          ? false
          : Boolean(body.bind_password) || current.bind_password_configured,
        custom_ca_configured: body.clear_custom_ca
          ? false
          : Boolean(body.custom_ca_pem) || current.custom_ca_configured,
        custom_ca_sha256: body.clear_custom_ca
          ? null
          : body.custom_ca_pem
            ? "b".repeat(64)
            : current.custom_ca_sha256,
      };
      delete (updated as unknown as Record<string, unknown>).bind_password;
      delete (updated as unknown as Record<string, unknown>).custom_ca_pem;
      directoryConnections = directoryConnections.map((connection) =>
        connection.connection_id === connectionId ? updated : connection,
      );
      return jsonResponse(updated);
    }
    const directoryTestMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/test$/,
    );
    if (directoryTestMatch && method === "POST") {
      return jsonResponse({
        validation_status: "passed",
        message_code: "directory.connection_test_passed",
        message_params: {},
      });
    }
    const directorySearchMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/users\/search$/,
    );
    if (directorySearchMatch && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const query = String(body.query ?? "").toLowerCase();
      return jsonResponse({
        users: directoryCandidates.filter((candidate) =>
          [candidate.display_name, candidate.username, candidate.email ?? ""]
            .join(" ")
            .toLowerCase()
            .includes(query),
        ),
      });
    }
    const directoryImportMatch = url.pathname.match(
      /^\/api\/v1\/admin\/directory-connections\/([^/]+)\/users\/import$/,
    );
    if (directoryImportMatch && method === "POST") {
      const connectionId = decodeURIComponent(directoryImportMatch[1]);
      const body = JSON.parse(String(init?.body ?? "{}"));
      const selected = directoryCandidates.filter((candidate) =>
        body.external_subjects.includes(candidate.external_subject),
      );
      const connection = directoryConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      )!;
      const importedUsers: UserAdminSummary[] = selected.map((candidate) => ({
        actor_id: `user-directory-${candidate.username}`,
        actor_type: "user",
        display_name: candidate.display_name,
        email: candidate.email,
        system_role: "user",
        active: true,
        created_at: "2026-08-10T00:00:00Z",
        invite_status: null,
        invite_id: null,
        account_source: "directory",
        directory_profile: {
          connection_id: connectionId,
          connection_display_name: connection.display_name,
          username: candidate.username,
          email: candidate.email,
          groups: candidate.groups,
          department: candidate.department,
          title: candidate.title,
          employee_id: candidate.employee_id,
          status: "current",
          last_refreshed_at: "2026-08-10T00:00:00Z",
        },
      }));
      adminUsers = [...adminUsers, ...importedUsers];
      return jsonResponse({
        imported_actor_ids: importedUsers.map((user) => user.actor_id),
        imported_count: importedUsers.length,
        message_code: "directory.users_imported",
        message_params: {},
      });
    }
    const directoryRefreshMatch = url.pathname.match(
      /^\/api\/v1\/admin\/users\/([^/]+)\/directory-profile\/refresh$/,
    );
    if (directoryRefreshMatch && method === "POST") {
      const actorId = decodeURIComponent(directoryRefreshMatch[1]);
      const user = adminUsers.find((candidate) => candidate.actor_id === actorId)!;
      return jsonResponse(user.directory_profile);
    }
    if (url.pathname === "/api/v1/admin/users" && method === "GET") {
      const q = url.searchParams.get("q")?.toLowerCase();
      const source = url.searchParams.get("account_source");
      const active = url.searchParams.get("active");
      const profileStatus = url.searchParams.get("directory_profile_status");
      const connectionId = url.searchParams.get("directory_connection_id");
      const group = url.searchParams.get("directory_group")?.toLowerCase();
      const department = url.searchParams.get("department")?.toLowerCase();
      const title = url.searchParams.get("title")?.toLowerCase();
      const employeeId = url.searchParams.get("employee_id")?.toLowerCase();
      const users = adminUsers.filter((user) => {
        const profile = user.directory_profile;
        const searchable = [
          user.display_name,
          user.email ?? "",
          profile?.username ?? "",
          profile?.email ?? "",
          profile?.department ?? "",
          profile?.title ?? "",
          profile?.employee_id ?? "",
          ...(profile?.groups ?? []),
        ].join(" ").toLowerCase();
        return (
          (!q || searchable.includes(q)) &&
          (!source || user.account_source === source) &&
          (!active || user.active === (active === "true")) &&
          (!profileStatus || profile?.status === profileStatus) &&
          (!connectionId || profile?.connection_id === connectionId) &&
          (!group || profile?.groups.some((value) => value.toLowerCase() === group)) &&
          (!department || profile?.department?.toLowerCase().includes(department)) &&
          (!title || profile?.title?.toLowerCase().includes(title)) &&
          (!employeeId || profile?.employee_id?.toLowerCase().includes(employeeId))
        );
      });
      return jsonResponse({ users });
    }
    if (
      url.pathname === "/api/v1/admin/users/user-engineer-001" &&
      method === "PATCH"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        request_id: "user-engineer-001-update",
        status: "applied",
        target_ref: "user:user-engineer-001",
        message_code: body.active === false ? "identity.user_has_been_removed" : "processing.user_profile_is_updated", message_params: {},
        audit_event_ref: "audit-user-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/users/user-admin-001" &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "user-admin-001-update",
        status: "applied",
        target_ref: "user:user-admin-001",
        message_code: "processing.user_profile_is_updated", message_params: {},
        audit_event_ref: "audit-user-updated",
      });
    }
    if (url.pathname === "/api/v1/auth/invitations/accept" && method === "POST") {
      return jsonResponse({
        request_id: "accept-atlas",
        status: "applied",
        target_ref: "user:user-engineer-001",
        message_code: "invite.accepted_sign_in_with_your_email_and_new_password", message_params: {},
        audit_event_ref: "audit-invite-accepted",
      });
    }
    if (url.pathname === "/api/v1/admin/agent-users" && method === "GET") {
      return jsonResponse({
        agents: agentList.agents.map((agent) => ({
          ...agent,
          project_grants: projectAccessGrants
            .filter(
              (grant) =>
                grant.subject_type === "service_account" &&
                grant.subject_id === agent.actor_id &&
                grant.status === "active",
            )
            .map((grant) => ({
              grant_id: grant.grant_id,
              project_id: grant.project_id,
              role: grant.role,
              effect: grant.effect,
              status: "active" as const,
            })),
        })),
      });
    }
    if (url.pathname === "/api/v1/admin/agent-users" && method === "POST") {
      return jsonResponse({
        request_id: "agent-layout-review",
        status: "applied",
        agent: { ...agentList.agents[0], tokens: [], project_grants: [] },
        message_code: "agent.user_is_ready_for_token_issue", message_params: {},
        audit_event_ref: "audit-0001",
      }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/agent-users/") &&
      !url.pathname.endsWith("/tokens") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "agent-update",
        status: "applied",
        target_ref: "agent:agent-layout-review-001",
        message_code: "agent.user_is_updated", message_params: {},
        audit_event_ref: "audit-agent-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/agent-users/agent-layout-review-001/tokens" &&
      method === "POST"
    ) {
      return jsonResponse({
        request_id: "token-layout-review",
        status: "applied",
        raw_token: "atlas_agent_visible_once",
        token: agentList.agents[0].tokens[0],
        message_code: "agent.token_has_been_issued_copy_it_now", message_params: {},
        audit_event_ref: "audit-0002",
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/agent-tokens/agtok-layout-review" &&
      method === "DELETE"
    ) {
      return jsonResponse({
        request_id: "revoke-agtok-layout-review",
        status: "applied",
        target_ref: "agent-token:agtok-layout-review",
        message_code: "agent.token_has_been_revoked", message_params: {},
        audit_event_ref: "audit-0003",
      });
    }
    if (url.pathname === "/api/v1/admin/projects" && method === "GET") {
      const projects = [
        {
          project_id: "proj-admin-live",
          name: "Admin Live Project",
          policy_profile_id: "policy-default-governed",
        },
        {
          project_id: "proj-signal-integrity-alpha",
          name: "Signal Integrity Alpha",
          policy_profile_id: "policy-default-governed",
        },
      ];
      return jsonResponse({
        projects:
          session.system_role === "admin"
            ? projects
            : projects.filter((project) =>
                session.available_projects.some(
                  (availableProject) =>
                    availableProject.project_id === project.project_id &&
                    availableProject.role === "admin",
                ),
              ),
      });
    }
    const projectMembersMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/members$/,
    );
    if (projectMembersMatch && method === "GET") {
      const grants = projectAccessGrants.filter(
        (grant) => grant.project_id === projectMembersMatch[1],
      );
      return jsonResponse({
        grants,
        subjects: grants.flatMap((grant) => {
          const subject = projectSubjectDirectory[
            `${grant.subject_type}:${grant.subject_id}`
          ];
          return subject
            ? [{
                subject_type: grant.subject_type,
                subject_id: grant.subject_id,
                ...subject,
              }]
            : [];
        }),
      });
    }
    const projectCandidatesMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/member-candidates$/,
    );
    if (projectCandidatesMatch && method === "GET") {
      const projectId = projectCandidatesMatch[1];
      const activeSubjects = new Set(
        projectAccessGrants
          .filter((grant) => grant.project_id === projectId && grant.status === "active")
          .map((grant) => `${grant.subject_type}:${grant.subject_id}`),
      );
      return jsonResponse({
        users: [
          {
            subject_type: "user",
            subject_id: "user-engineer-001",
            display_name: "Engineer One",
            display_detail: "engineer@example.test",
          },
          {
            subject_type: "user",
            subject_id: "user-pending-001",
            display_name: "Invited Engineer",
            display_detail: "pending@example.test",
          },
        ].filter((candidate) => !activeSubjects.has(`${candidate.subject_type}:${candidate.subject_id}`)),
        teams: [
          {
            subject_type: "team",
            subject_id: "team-platform",
            display_name: "Platform",
            display_detail: null,
          },
          {
            subject_type: "team",
            subject_id: "team-si",
            display_name: "Signal Integrity",
            display_detail: null,
          },
        ].filter((candidate) => !activeSubjects.has(`${candidate.subject_type}:${candidate.subject_id}`)),
        service_accounts: [],
      });
    }
    if (projectMembersMatch && method === "POST") {
      const projectId = projectMembersMatch[1];
      const body = JSON.parse(String(init?.body ?? "{}"));
      const subjectType =
        body.subject_type === "team"
          ? "team"
          : body.subject_type === "service_account"
            ? "service_account"
            : "user";
      const role: ProjectMemberRole =
        body.role === "admin" ? "admin" : body.role === "contributor" ? "contributor" : "viewer";
      const grant: ProjectAccessGrant = {
        grant_id: `grant-project-member-${projectId}-${subjectType}-${body.subject_id}`,
        project_id: projectId,
        subject_type: subjectType,
        subject_id: body.subject_id,
        role,
        effect: body.effect === "deny" ? "deny" : "allow",
        status: "active",
        created_at: "2026-07-09T00:00:00Z",
        revoked_at: null,
      };
      projectAccessGrants = [
        ...projectAccessGrants.filter((candidate) => candidate.grant_id !== grant.grant_id),
        grant,
      ];
      return jsonResponse(grant, 201);
    }
    const projectMemberDetailMatch = url.pathname.match(
      /^\/api\/v1\/admin\/projects\/([^/]+)\/members\/([^/]+)$/,
    );
    if (projectMemberDetailMatch && method === "PATCH") {
      const [, projectId, grantId] = projectMemberDetailMatch;
      const body = JSON.parse(String(init?.body ?? "{}"));
      const role: ProjectMemberRole =
        body.role === "admin" ? "admin" : body.role === "contributor" ? "contributor" : "viewer";
      const effect = body.effect === "deny" ? "deny" : "allow";
      projectAccessGrants = projectAccessGrants.map((grant) =>
        grant.project_id === projectId && grant.grant_id === grantId
          ? { ...grant, role, effect }
          : grant,
      );
      return jsonResponse(
        projectAccessGrants.find(
          (grant) => grant.project_id === projectId && grant.grant_id === grantId,
        ),
      );
    }
    if (projectMemberDetailMatch && method === "DELETE") {
      const [, projectId, grantId] = projectMemberDetailMatch;
      projectAccessGrants = projectAccessGrants.map((grant) =>
        grant.project_id === projectId && grant.grant_id === grantId
          ? { ...grant, status: "revoked", revoked_at: "2026-07-10T00:00:00Z" }
          : grant,
      );
      return jsonResponse(
        projectAccessGrants.find(
          (grant) => grant.project_id === projectId && grant.grant_id === grantId,
        ),
      );
    }
    if (url.pathname === "/api/v1/admin/projects" && method === "POST") {
      return jsonResponse({ status: "applied", message_code: "project.is_ready_for_membership_setup", message_params: {} }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/projects/") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "project-update",
        status: "applied",
        target_ref: "project:proj-admin-live",
        message_code: "project.is_updated", message_params: {},
        audit_event_ref: "audit-project-updated",
      });
    }
    if (url.pathname === "/api/v1/admin/teams" && method === "GET") {
      const allTeams = [
        {
          team_id: "team-platform",
          name: "Platform",
          parent_team_id: null,
          status: "active" as const,
          created_at: "2026-07-08T00:00:00Z",
          inherit_parent_documents: true,
        },
        {
          team_id: "team-si",
          name: "Signal Integrity",
          parent_team_id: "team-platform",
          status: "active" as const,
          created_at: "2026-07-08T00:00:00Z",
          inherit_parent_documents: true,
        },
      ];
      const visibleTeamIds =
        session.system_role === "admin"
          ? new Set(allTeams.map((team) => team.team_id))
          : new Set(
              Object.entries(session.team_roles)
                .filter(([, role]) => role === "admin")
                .map(([teamId]) => teamId),
            );
      if (session.system_role !== "admin" && visibleTeamIds.size === 0) {
        return jsonResponse({ message_code: "team.admin_access_is_required", message_params: {} }, 403);
      }
      return jsonResponse({
        teams: allTeams.filter((team) => visibleTeamIds.has(team.team_id)),
        memberships: teamMemberships.filter(
          (membership) =>
            membership.status === "active" && visibleTeamIds.has(membership.team_id),
        ),
      });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members" &&
      method === "GET"
    ) {
      return jsonResponse({ members: scopedTeamMembers });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/member-candidates" &&
      method === "GET"
    ) {
      const memberIds = new Set(scopedTeamMembers.map((member) => member.subject_id));
      return jsonResponse({
        users: [
          {
            subject_type: "user",
            subject_id: "user-team-candidate-001",
            display_name: "Team Candidate",
            display_detail: "candidate@example.test",
          },
        ].filter((candidate) => !memberIds.has(candidate.subject_id)),
      });
    }
    if (url.pathname === "/api/v1/admin/teams" && method === "POST") {
      return jsonResponse({
        request_id: "team-signal-integrity",
        status: "applied",
        target_ref: "team:team-signal-integrity",
        message_code: "team.is_ready", message_params: {},
        audit_event_ref: "audit-team-created",
      }, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/teams/") &&
      !url.pathname.includes("/members") &&
      method === "PATCH"
    ) {
      return jsonResponse({
        request_id: "team-update",
        status: "applied",
        target_ref: "team:team-si",
        message_code: "team.is_updated", message_params: {},
        audit_event_ref: "audit-team-updated",
      });
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members" &&
      method === "POST"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const memberActorId = String(body.member_actor_id ?? "");
      const memberActorType =
        body.member_actor_type === "service_account" ? "service_account" : "user";
      const role = body.role === "uploader" || body.role === "admin" ? body.role : "member";
      if (session.system_role !== "admin") {
        const existing = scopedTeamMembers.find((member) => member.subject_id === memberActorId);
        if (existing) {
          scopedTeamMembers = scopedTeamMembers.map((member) =>
            member.subject_id === memberActorId ? { ...member, role } : member,
          );
        } else {
          scopedTeamMembers = [
            ...scopedTeamMembers,
            {
              membership_id: `tm-team-si-${memberActorId}`,
              team_id: "team-si",
              subject_type: "user",
              subject_id: memberActorId,
              display_name: "Team Candidate",
              display_detail: "candidate@example.test",
              role,
              status: "active",
              created_at: "2026-07-09T00:00:00Z",
            },
          ];
        }
        return jsonResponse({
          request_id: `team-member-${memberActorId}`,
          status: "applied",
          target_ref: `team-membership:tm-team-si-${memberActorId}`,
          message_code: existing ? "team.member_role_is_updated" : "team.member_is_active", message_params: {},
          audit_event_ref: "audit-team-member-scoped",
        }, existing ? 200 : 201);
      }
      const existingMembership = teamMemberships.find(
        (membership) =>
          membership.team_id === "team-si" &&
          membership.member_actor_id === memberActorId,
      );
      if (existingMembership) {
        teamMemberships = teamMemberships.map((membership) =>
          membership.membership_id === existingMembership.membership_id
            ? { ...membership, role, status: "active", removed_at: null }
            : membership,
        );
      } else {
        teamMemberships = [
          ...teamMemberships,
          {
            membership_id: `team-member-team-si-${memberActorId}`,
            team_id: "team-si",
            member_actor_type: memberActorType,
            member_actor_id: memberActorId,
            role,
            status: "active",
            created_at: "2026-07-09T00:00:00Z",
            removed_at: null,
          },
        ];
      }
      return jsonResponse({
        request_id: "team-member-user-engineer-001",
        status: "applied",
        target_ref: "team-membership:team-si:user-engineer-001",
        message_code: existingMembership
          ? "team.member_role_is_updated"
          : "team.member_is_active",
        message_params: {},
        audit_event_ref: "audit-team-member-added",
      }, existingMembership ? 200 : 201);
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-platform/members" &&
      method === "POST"
    ) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const memberActorId = String(body.member_actor_id ?? "");
      const memberActorType =
        body.member_actor_type === "service_account" ? "service_account" : "user";
      const role = body.role === "uploader" || body.role === "admin" ? body.role : "member";
      teamMemberships = [
        ...teamMemberships,
        {
          membership_id: `team-member-team-platform-${memberActorId}`,
          team_id: "team-platform",
          member_actor_type: memberActorType,
          member_actor_id: memberActorId,
          role,
          status: "active",
          created_at: "2026-07-09T00:00:00Z",
          removed_at: null,
        },
      ];
      return jsonResponse({
        request_id: "team-member-user-engineer-001",
        status: "applied",
        target_ref: "team-membership:team-platform:user-engineer-001",
        message_code: "team.member_is_active", message_params: {},
        audit_event_ref: "audit-team-member-added",
      }, 201);
    }
    if (
      url.pathname === "/api/v1/admin/teams/team-si/members/team-member-engineer" &&
      method === "DELETE"
    ) {
      teamMemberships = teamMemberships.map((membership) =>
        membership.membership_id === "team-member-engineer"
          ? { ...membership, status: "removed", removed_at: "2026-07-09T00:00:00Z" }
          : membership,
      );
      return jsonResponse({
        request_id: "remove-team-member-engineer",
        status: "applied",
        target_ref: "team-membership:team-member-engineer",
        message_code: "team.member_has_been_removed", message_params: {},
        audit_event_ref: "audit-team-member-removed",
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/teams/team-si/members/tm-team-si-") &&
      method === "DELETE"
    ) {
      const membershipId = url.pathname.split("/").at(-1) ?? "";
      scopedTeamMembers = scopedTeamMembers.filter(
        (member) => member.membership_id !== membershipId,
      );
      return jsonResponse({
        request_id: `remove-${membershipId}`,
        status: "applied",
        target_ref: `team-membership:${membershipId}`,
        message_code: "team.member_has_been_removed", message_params: {},
        audit_event_ref: "audit-team-member-scoped-removed",
      });
    }
    return undefined;
  };
}
