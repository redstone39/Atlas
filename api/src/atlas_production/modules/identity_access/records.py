from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserRecord:
    actor_id: str
    display_name: str
    email: str | None
    system_role: str
    password_digest: str | None
    active: bool = True
    actor_type: str = "user"
    created_at: str = ""


@dataclass
class UserInviteRecord:
    invite_id: str
    actor_id: str
    email: str
    display_name: str
    system_role: str
    token_digest: str
    token_fingerprint: str
    status: str
    created_at: str
    expires_at: str
    accepted_at: str | None = None
    revoked_at: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    scope_role: str | None = None


@dataclass
class TeamRecord:
    team_id: str
    name: str
    parent_team_id: str | None
    status: str
    created_at: str
    inherit_parent_documents: bool = True


@dataclass
class TeamMembershipRecord:
    membership_id: str
    team_id: str
    member_actor_type: str
    member_actor_id: str
    status: str
    created_at: str
    role: str = "member"
    removed_at: str | None = None


@dataclass
class PermissionGrantRecord:
    grant_id: str
    project_id: str
    subject_type: str
    subject_id: str
    role: str
    effect: str
    status: str
    created_at: str
    revoked_at: str | None = None


@dataclass
class AccessDecisionRecord:
    decision_id: str
    actor_type: str
    actor_id: str
    project_id: str | None
    action: str
    required_role: str
    allowed: bool
    reason: str
    effective_role: str | None
    source_type: str | None
    source_id: str | None
    explanation: str
    created_at: str
    scope_type: str | None = None
    scope_id: str | None = None


@dataclass
class AgentTokenRecord:
    token_id: str
    actor_id: str
    token_digest: str
    token_fingerprint: str
    status: str = "active"
    created_at: str = ""
    revoked_at: str | None = None
