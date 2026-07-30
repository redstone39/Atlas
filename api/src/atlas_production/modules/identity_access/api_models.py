from typing import Literal

from pydantic import BaseModel, Field

from atlas_production.shared.user_messages import MessageReferenceModel


class ActorContext(BaseModel):
    actor_id: str
    actor_type: Literal["user", "service_account", "system_task"]
    issuer: str
    display_name: str
    groups: list[str]
    correlation_id: str


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    membership_status: Literal["active", "revoked", "missing"]
    role: Literal["viewer", "contributor", "admin"] | None


class SessionState(BaseModel):
    authenticated: bool
    actor: ActorContext | None
    available_projects: list[ProjectSummary]
    system_role: Literal["user", "admin", "operator"] | None
    team_roles: dict[str, Literal["member", "uploader", "admin"]] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: str
    password: str
    idempotency_key: str | None = None


class AccountCreateRequest(BaseModel):
    actor_id: str
    display_name: str
    email: str
    system_role: Literal["user", "admin", "operator"]
    initial_password: str
    idempotency_key: str


class UserInviteCreateRequest(BaseModel):
    display_name: str
    email: str
    system_role: Literal["user", "admin", "operator"]
    scope_type: Literal["team", "project"] | None = None
    scope_id: str | None = None
    scope_role: Literal["member", "uploader", "admin"] | None = None
    idempotency_key: str


class UserInviteSummary(BaseModel):
    invite_id: str
    actor_id: str
    email: str
    display_name: str
    system_role: Literal["user", "admin", "operator"]
    status: Literal["pending", "accepted", "revoked", "expired"]
    created_at: str
    expires_at: str
    accepted_at: str | None = None
    revoked_at: str | None = None
    scope_type: Literal["team", "project"] | None = None
    scope_id: str | None = None
    scope_role: Literal["member", "uploader", "admin"] | None = None


class LocalPilotInviteAcceptance(BaseModel):
    mode: Literal["copy_link"]
    acceptance_token: str
    acceptance_url: str


class UserInviteCreateResult(MessageReferenceModel):
    request_id: str
    status: Literal["applied", "rejected", "access_denied"]
    invite: UserInviteSummary
    audit_event_ref: str
    local_pilot_acceptance: LocalPilotInviteAcceptance | None = None


class UserInviteListResult(BaseModel):
    invites: list[UserInviteSummary]


class UserInviteRevokeRequest(BaseModel):
    idempotency_key: str


class InviteAcceptRequest(BaseModel):
    invite_token: str
    password: str = Field(min_length=12)
    idempotency_key: str


class InviteAcceptResult(MessageReferenceModel):
    request_id: str
    status: Literal["applied", "rejected"]
    target_ref: str | None
    audit_event_ref: str


class AgentUserCreateRequest(BaseModel):
    actor_id: str
    display_name: str
    idempotency_key: str


class AgentUserUpdateRequest(BaseModel):
    display_name: str | None = None
    active: bool | None = None
    idempotency_key: str


class AgentTokenIssueRequest(BaseModel):
    idempotency_key: str


class UserAdminSummary(BaseModel):
    actor_id: str
    actor_type: Literal["user", "service_account"]
    display_name: str
    email: str | None
    system_role: str
    active: bool
    created_at: str
    invite_status: Literal["pending", "accepted", "revoked", "expired"] | None = None
    invite_id: str | None = None


class UserAdminListResult(BaseModel):
    users: list[UserAdminSummary]


class UserAdminUpdateRequest(BaseModel):
    display_name: str | None = None
    system_role: Literal["user", "admin", "operator"] | None = None
    active: bool | None = None
    idempotency_key: str


class TeamRecord(BaseModel):
    team_id: str
    name: str
    parent_team_id: str | None = None
    status: Literal["active", "retired"]
    created_at: str
    inherit_parent_documents: bool = True


class TeamMembershipRecord(BaseModel):
    membership_id: str
    team_id: str
    member_actor_type: Literal["user", "service_account"]
    member_actor_id: str
    role: Literal["member", "uploader", "admin"] = "member"
    status: Literal["active", "removed"]
    created_at: str
    removed_at: str | None = None


class TeamListResult(BaseModel):
    teams: list[TeamRecord]
    memberships: list[TeamMembershipRecord]


class TeamMemberSummary(BaseModel):
    membership_id: str
    team_id: str
    subject_type: Literal["user", "service_account"]
    subject_id: str
    display_name: str
    display_detail: str | None = None
    role: Literal["member", "uploader", "admin"]
    status: Literal["active"]
    created_at: str


class TeamMemberListResult(BaseModel):
    members: list[TeamMemberSummary]


class TeamMemberCandidate(BaseModel):
    subject_type: Literal["user"] = "user"
    subject_id: str
    display_name: str
    display_detail: str | None = None


class TeamMemberCandidatesResult(BaseModel):
    users: list[TeamMemberCandidate]


class TeamCreateRequest(BaseModel):
    team_id: str
    name: str
    parent_team_id: str | None = None
    inherit_parent_documents: bool = True
    idempotency_key: str


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    parent_team_id: str | None = None
    status: Literal["active", "retired"] | None = None
    inherit_parent_documents: bool | None = None
    idempotency_key: str


class TeamMembershipCreateRequest(BaseModel):
    member_actor_type: Literal["user", "service_account"]
    member_actor_id: str
    role: Literal["member", "uploader", "admin"] = "member"
    idempotency_key: str


class AccessDecision(BaseModel):
    decision_id: str
    actor_type: Literal["user", "service_account"]
    actor_id: str
    project_id: str
    action: str
    required_role: Literal["viewer", "contributor", "admin"]
    allowed: bool
    reason: str
    effective_role: Literal["viewer", "contributor", "admin"] | None = None
    source_type: Literal["user", "service_account", "team"] | None = None
    source_id: str | None = None
    explanation: str
    created_at: str


class AgentTokenStatus(BaseModel):
    token_id: str
    token_fingerprint: str
    status: Literal["active", "revoked"]
    created_at: str
    revoked_at: str | None = None


class AgentProjectGrantStatus(BaseModel):
    grant_id: str
    project_id: str
    role: Literal["viewer", "contributor", "admin"]
    effect: Literal["allow", "deny"]
    status: Literal["active"]


class AgentUserStatus(BaseModel):
    actor_id: str
    actor_type: Literal["service_account"]
    display_name: str
    status: Literal["active", "inactive"]
    tokens: list[AgentTokenStatus]
    project_grants: list[AgentProjectGrantStatus]


class AgentUserCreateResult(MessageReferenceModel):
    request_id: str
    status: Literal["applied", "rejected", "access_denied"]
    agent: AgentUserStatus
    audit_event_ref: str


class AgentTokenIssueResult(MessageReferenceModel):
    request_id: str
    status: Literal["applied", "rejected", "access_denied"]
    raw_token: str
    token: AgentTokenStatus
    audit_event_ref: str


class AgentUserListResult(BaseModel):
    agents: list[AgentUserStatus]
