from __future__ import annotations

from typing import ContextManager, Protocol

from .directory_records import (
    DirectoryConnectionRecord,
    DirectoryPrincipal,
    DirectorySecretRecord,
    ExternalIdentityRecord,
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
        query: str,
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
