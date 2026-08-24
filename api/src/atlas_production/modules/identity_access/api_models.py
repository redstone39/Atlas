from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from atlas_production.shared.user_messages import MessageReferenceModel
from .directory_records import validate_directory_transport



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


class StrictIdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictIdentityMessageModel(MessageReferenceModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictIdentityModel):
    identifier: str = Field(min_length=1, max_length=320)
    password: str


class DirectoryConnectionConfig(StrictIdentityModel):
    connection_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=0)
    provider_type: Literal["active_directory", "ldap"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    tls_mode: Literal["ldaps", "start_tls", "plain"]
    connect_timeout_seconds: int = Field(ge=1, le=30)
    operation_timeout_seconds: int = Field(ge=1, le=30)
    bind_dn: str = Field(min_length=1, max_length=1000)
    user_base_dn: str = Field(min_length=1, max_length=1000)
    user_object_filter: str = Field(min_length=1, max_length=2000)
    login_attribute: str = Field(min_length=1, max_length=200)
    stable_id_attribute: str = Field(min_length=1, max_length=200)
    display_name_attribute: str = Field(min_length=1, max_length=200)
    email_attribute: str = Field(min_length=1, max_length=200)
    groups_attribute: str = Field(min_length=1, max_length=200)
    department_attribute: str = Field(min_length=1, max_length=200)
    title_attribute: str = Field(min_length=1, max_length=200)
    employee_id_attribute: str = Field(min_length=1, max_length=200)
    enabled: bool
    @model_validator(mode="after")
    def validate_transport(self) -> "DirectoryConnectionConfig":
        validate_directory_transport(self.provider_type, self.tls_mode)
        return self




class DirectoryConnectionCreateRequest(StrictIdentityModel):
    display_name: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=0)
    provider_type: Literal["active_directory", "ldap"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    tls_mode: Literal["ldaps", "start_tls", "plain"]
    connect_timeout_seconds: int = Field(ge=1, le=30)
    operation_timeout_seconds: int = Field(ge=1, le=30)
    bind_dn: str = Field(min_length=1, max_length=1000)
    user_base_dn: str = Field(min_length=1, max_length=1000)
    user_object_filter: str = Field(min_length=1, max_length=2000)
    login_attribute: str = Field(min_length=1, max_length=200)
    stable_id_attribute: str = Field(min_length=1, max_length=200)
    display_name_attribute: str = Field(min_length=1, max_length=200)
    email_attribute: str = Field(min_length=1, max_length=200)
    groups_attribute: str = Field(min_length=1, max_length=200)
    department_attribute: str = Field(min_length=1, max_length=200)
    title_attribute: str = Field(min_length=1, max_length=200)
    employee_id_attribute: str = Field(min_length=1, max_length=200)
    enabled: bool
    bind_password: SecretStr = Field(min_length=1)
    custom_ca_pem: SecretStr | None = Field(default=None, min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_transport(self) -> "DirectoryConnectionCreateRequest":
        validate_directory_transport(self.provider_type, self.tls_mode)
        return self


class DirectoryConnectionUpdateRequest(StrictIdentityModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    priority: int | None = Field(default=None, ge=0)
    provider_type: Literal["active_directory", "ldap"] | None = None
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    tls_mode: Literal["ldaps", "start_tls", "plain"] | None = None
    connect_timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    operation_timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    bind_dn: str | None = Field(default=None, min_length=1, max_length=1000)
    user_base_dn: str | None = Field(default=None, min_length=1, max_length=1000)
    user_object_filter: str | None = Field(default=None, min_length=1, max_length=2000)
    login_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    stable_id_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    display_name_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    email_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    groups_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    department_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    title_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    employee_id_attribute: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    bind_password: SecretStr | None = Field(default=None, min_length=1)
    clear_bind_password: bool = False
    custom_ca_pem: SecretStr | None = Field(default=None, min_length=1)
    clear_custom_ca: bool = False

    @model_validator(mode="after")
    def reject_secret_set_and_clear(self) -> "DirectoryConnectionUpdateRequest":
        if self.bind_password is not None and self.clear_bind_password:
            raise ValueError("bind password cannot be set and cleared together")
        if self.custom_ca_pem is not None and self.clear_custom_ca:
            raise ValueError("custom CA cannot be set and cleared together")
        return self


class DirectoryConnectionStatus(DirectoryConnectionConfig):
    bind_password_configured: bool
    custom_ca_configured: bool
    custom_ca_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DirectoryConnectionListResult(StrictIdentityModel):
    connections: list[DirectoryConnectionStatus]


class DirectoryConnectionTestResult(StrictIdentityMessageModel):
    validation_status: Literal["passed", "failed"]

class ScopedDirectoryConnectionSummary(StrictIdentityModel):
    connection_id: str
    display_name: str


class ScopedDirectoryConnectionListResult(StrictIdentityModel):
    connections: list[ScopedDirectoryConnectionSummary]


class ScopedDirectoryUserSearchRequest(StrictIdentityModel):
    search_mode: Literal["department", "member"]
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=100, ge=1, le=100)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("directory search query must not be blank")
        return normalized


class ScopedDirectoryUserCandidate(StrictIdentityModel):
    external_subject: str
    username: str
    display_name: str
    email: str | None


class ScopedDirectoryUserSearchResult(StrictIdentityModel):
    users: list[ScopedDirectoryUserCandidate]
    limit_reached: bool


class ScopedDirectoryMemberImportResult(StrictIdentityMessageModel):
    actor_ids: list[str]
    applied_count: int



class DirectoryUserSearchRequest(StrictIdentityModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=50, ge=1, le=50)


class DirectoryUserCandidate(StrictIdentityModel):
    external_subject: str
    username: str
    display_name: str
    email: str | None
    groups: list[str]
    department: str | None
    title: str | None
    employee_id: str | None
    directory_enabled: bool | None


class DirectoryUserSearchResult(StrictIdentityModel):
    users: list[DirectoryUserCandidate]


class DirectoryUserImportRequest(StrictIdentityModel):
    external_subjects: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_subjects(self) -> "DirectoryUserImportRequest":
        if len(self.external_subjects) != len(set(self.external_subjects)):
            raise ValueError("external subjects must be unique")
        return self


class DirectoryUserImportResult(StrictIdentityMessageModel):
    imported_actor_ids: list[str]
    imported_count: int


class DirectoryProfileSummary(StrictIdentityModel):
    connection_id: str
    connection_display_name: str
    username: str
    email: str | None
    groups: list[str]
    department: str | None
    title: str | None
    employee_id: str | None
    status: Literal["current", "stale", "missing", "disabled"]
    last_refreshed_at: str


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


class AgentUserCreateRequest(StrictIdentityModel):
    display_name: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


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
    account_source: Literal["local", "directory"] = "local"
    directory_profile: DirectoryProfileSummary | None = None


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


class TeamCreateRequest(StrictIdentityModel):
    name: str = Field(min_length=1, max_length=200)
    parent_team_id: str | None = None
    inherit_parent_documents: bool = True
    idempotency_key: str = Field(min_length=1, max_length=200)


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
class TeamDirectoryMemberImportRequest(StrictIdentityModel):
    external_subjects: list[str] = Field(min_length=1, max_length=100)
    role: Literal["member", "uploader", "admin"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_subjects(self) -> "TeamDirectoryMemberImportRequest":
        if len(self.external_subjects) != len(set(self.external_subjects)):
            raise ValueError("external subjects must be unique")
        return self




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
