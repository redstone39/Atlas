from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    policy_profile_id: str
    idempotency_key: str


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    policy_profile_id: str | None = None
    status: Literal["active", "retired"] | None = None
    idempotency_key: str


class ProjectAdminSummary(BaseModel):
    project_id: str
    name: str
    policy_profile_id: str
    status: Literal["active", "retired"]


class ProjectAdminListResult(BaseModel):
    projects: list[ProjectAdminSummary]


class ProjectAccessGrant(BaseModel):
    grant_id: str
    project_id: str
    subject_type: Literal["user", "team", "service_account"]
    subject_id: str
    role: Literal["viewer", "contributor", "admin"]
    effect: Literal["allow", "deny"]
    status: Literal["active", "revoked"]
    created_at: str
    revoked_at: str | None = None


class ProjectMemberCandidate(BaseModel):
    subject_type: Literal["user", "team", "service_account"]
    subject_id: str
    display_name: str
    display_detail: str | None = None


class ProjectAccessGrantListResult(BaseModel):
    grants: list[ProjectAccessGrant]
    subjects: list[ProjectMemberCandidate]


class ProjectMemberCandidatesResult(BaseModel):
    users: list[ProjectMemberCandidate]
    teams: list[ProjectMemberCandidate]
    service_accounts: list[ProjectMemberCandidate]


class ProjectAccessGrantCreateRequest(BaseModel):
    subject_type: Literal["user", "team", "service_account"]
    subject_id: str
    role: Literal["viewer", "contributor", "admin"]
    effect: Literal["allow", "deny"]
    idempotency_key: str
class ProjectDirectoryMemberImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_subjects: list[str] = Field(min_length=1, max_length=100)
    role: Literal["viewer", "contributor", "admin"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_subjects(self) -> "ProjectDirectoryMemberImportRequest":
        if len(self.external_subjects) != len(set(self.external_subjects)):
            raise ValueError("external subjects must be unique")
        return self




class ProjectAccessGrantUpdateRequest(BaseModel):
    role: Literal["viewer", "contributor", "admin"]
    effect: Literal["allow", "deny"]
    idempotency_key: str
