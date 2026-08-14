from __future__ import annotations

from dataclasses import dataclass
from typing import ContextManager, Protocol

from .directory_records import (
    DirectoryConnectionRecord,
    DirectoryPrincipal,
    DirectorySecretRecord,
    ExternalIdentityRecord,
)
from .records import PermissionGrantRecord, TeamMembershipRecord, UserRecord
from atlas_production.shared.public import AuditEventRecord
from .api_models import (
    ScopedDirectoryConnectionListResult,
    ScopedDirectoryUserSearchRequest,
    ScopedDirectoryUserSearchResult,
)
from .ports import IdentityAccessRepository


class DirectoryGateway(Protocol):
    def test_connection(
        self, connection: DirectoryConnectionRecord, bind_password: str
    ) -> None: ...

    def search_users(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        *,
        query: str | None,
        department: str | None,
        limit: int,
    ) -> tuple[DirectoryPrincipal, ...]: ...

    def fetch_user(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        external_subject: str,
    ) -> DirectoryPrincipal | None: ...

    def authenticate(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        external_subject: str,
        password: str,
    ) -> DirectoryPrincipal: ...


class ScopedDirectoryIdentityCapability(Protocol):
    def list_scoped_connections(self) -> ScopedDirectoryConnectionListResult: ...

    def search_scoped_users(
        self,
        connection_id: str,
        payload: ScopedDirectoryUserSearchRequest,
    ) -> ScopedDirectoryUserSearchResult: ...

    def prepare_scoped_import(
        self,
        connection_id: str,
        external_subjects: list[str],
    ) -> "ScopedDirectoryImportPreparation": ...


@dataclass(frozen=True, slots=True)
class ScopedDirectoryImportPreparation:
    connection_id: str
    source_revision: str
    credential_revision: str
    users: tuple[UserRecord, ...]
    new_users: tuple[UserRecord, ...]
    expected_users: tuple[tuple[str, UserRecord | None], ...]
    new_external_identities: tuple[ExternalIdentityRecord, ...]
    expected_external_identities: tuple[
        tuple[str, ExternalIdentityRecord | None], ...
    ]
    expected_subject_bindings: tuple[
        tuple[str, ExternalIdentityRecord | None], ...
    ]

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return tuple(user.actor_id for user in self.users)


@dataclass(frozen=True, slots=True)
class ScopedDirectoryImportChangeSet:
    authorization_actor_id: str
    authorization_scope_type: str
    authorization_scope_id: str
    preparation: ScopedDirectoryImportPreparation
    team_memberships: tuple[TeamMembershipRecord, ...] = ()
    expected_team_memberships: tuple[
        tuple[str, TeamMembershipRecord | None], ...
    ] = ()
    project_grants: tuple[PermissionGrantRecord, ...] = ()
    expected_project_grants: tuple[
        tuple[str, PermissionGrantRecord | None], ...
    ] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.authorization_scope_type not in {"team", "project"}:
            raise ValueError("scoped directory import requires a Team or Project")
        if (not self.team_memberships) == (not self.project_grants):
            raise ValueError("scoped directory import requires exactly one access writer")
        if self.authorization_scope_type == "team" and not self.team_memberships:
            raise ValueError("Team import requires Team memberships")
        if self.authorization_scope_type == "project" and not self.project_grants:
            raise ValueError("Project import requires Project grants")
        actor_ids = self.preparation.actor_ids
        if len(actor_ids) not in range(1, 101) or len(actor_ids) != len(set(actor_ids)):
            raise ValueError("scoped directory import requires 1 to 100 unique users")
        if any(
            not user.active or user.actor_type != "user"
            for user in self.preparation.users
        ):
            raise ValueError("scoped directory import targets must be active humans")
        if self.team_memberships:
            if (
                len(self.team_memberships) != len(actor_ids)
                or len(self.expected_team_memberships) != len(actor_ids)
                or any(
                    membership.team_id != self.authorization_scope_id
                    or membership.member_actor_type != "user"
                    or membership.member_actor_id not in actor_ids
                    or membership.status != "active"
                    or membership.removed_at is not None
                    for membership in self.team_memberships
                )
            ):
                raise ValueError("Team import access does not match prepared users")
        if self.project_grants:
            if (
                len(self.project_grants) != len(actor_ids)
                or len(self.expected_project_grants) != len(actor_ids)
                or any(
                    grant.project_id != self.authorization_scope_id
                    or grant.subject_type != "user"
                    or grant.subject_id not in actor_ids
                    or grant.effect != "allow"
                    or grant.status != "active"
                    or grant.revoked_at is not None
                    for grant in self.project_grants
                )
            ):
                raise ValueError("Project import access does not match prepared users")
        if not self.audit_events:
            raise ValueError("scoped directory import requires audit events")

class ScopedDirectoryImportAuthorizationConflict(RuntimeError):
    def __init__(self, audit_event_ref: str | None = None) -> None:
        self.audit_event_ref = audit_event_ref
        super().__init__("scoped directory import authorization changed")


class ScopedDirectoryImportCurrentnessConflict(RuntimeError):
    pass



class ScopedDirectoryImportCommitPort(Protocol):
    def commit_scoped_directory_import(
        self, change_set: ScopedDirectoryImportChangeSet
    ) -> None: ...


class DirectoryCredentialCipher(Protocol):
    def encrypt(
        self,
        *,
        domain: str,
        owner_id: str,
        owner_kind: str,
        secret_version: int,
        plaintext: str,
    ): ...

    def decrypt(
        self,
        secret,
        *,
        domain: str,
        owner_id: str,
        owner_kind: str,
    ) -> str: ...


class DirectoryFilterValidator(Protocol):
    def __call__(self, value: str) -> None: ...


class DirectoryRepository(IdentityAccessRepository, Protocol):
    def directory_mutation(
        self,
        owner_key: str,
        *,
        actor_ids: tuple[str, ...] = (),
        authorization_actor_ids: tuple[str, ...] = (),
        connection_ids: tuple[str, ...] = (),
    ) -> ContextManager[None]: ...

    def list_directory_connections(self) -> list[DirectoryConnectionRecord]: ...

    def get_directory_connection(self, connection_id: str) -> DirectoryConnectionRecord | None: ...

    def put_directory_connection(self, connection: DirectoryConnectionRecord) -> None: ...

    def expect_directory_connection(self, connection: DirectoryConnectionRecord) -> None: ...

    def get_directory_secret(
        self, connection_id: str, secret_kind: str
    ) -> DirectorySecretRecord | None: ...

    def put_directory_secret(self, secret: DirectorySecretRecord) -> None: ...

    def expect_directory_secret(self, secret: DirectorySecretRecord) -> None: ...

    def delete_directory_secret(self, connection_id: str, secret_kind: str) -> None: ...

    def get_external_identity(self, actor_id: str) -> ExternalIdentityRecord | None: ...

    def get_external_identity_by_subject(
        self, connection_id: str, external_subject: str
    ) -> ExternalIdentityRecord | None: ...

    def list_external_identities(self) -> list[ExternalIdentityRecord]: ...

    def put_external_identity(self, identity: ExternalIdentityRecord) -> None: ...

    def expect_external_identity(self, identity: ExternalIdentityRecord) -> None: ...

    def stage_session(self, actor_id: str) -> str: ...
