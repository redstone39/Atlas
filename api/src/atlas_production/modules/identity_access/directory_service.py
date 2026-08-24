from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unicodedata
from uuid import uuid4

from .api_models import (
    DirectoryConnectionCreateRequest,
    DirectoryConnectionListResult,
    DirectoryConnectionStatus,
    DirectoryConnectionTestResult,
    DirectoryConnectionUpdateRequest,
    DirectoryProfileSummary,
    DirectoryUserCandidate,
    DirectoryUserImportRequest,
    DirectoryUserImportResult,
    DirectoryUserSearchRequest,
    DirectoryUserSearchResult,
    ScopedDirectoryConnectionListResult,
    ScopedDirectoryConnectionSummary,
    ScopedDirectoryUserCandidate,
    ScopedDirectoryUserSearchRequest,
    ScopedDirectoryUserSearchResult,
)
from .contracts import IdentityAccessError, IdentityAuditCommand, LoginOutcome
from .directory_ports import (
    DirectoryCredentialCipher,
    DirectoryFilterValidator,
    DirectoryGateway,
    DirectoryRepository,
    ScopedDirectoryImportPreparation,
)
from .directory_records import (
    DirectoryConnectionRecord,
    DirectoryGatewayError,
    DirectoryPrincipal,
    DirectorySecretRecord,
    ExternalIdentityRecord,
    directory_record_revision,
    validate_directory_transport,
)
from .records import UserRecord
from atlas_production.shared.public import utc_now_iso


_BIND_DOMAIN = "identity_directory_bind_password"
_CA_DOMAIN = "identity_directory_custom_ca"
_SECRET_OWNER_KIND = "directory_connection"


def canonical_identifier(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


class DirectoryIdentityService:
    def __init__(
        self,
        repository: DirectoryRepository,
        gateway: DirectoryGateway,
        cipher: DirectoryCredentialCipher,
        validate_filter: DirectoryFilterValidator,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.cipher = cipher
        self.validate_filter = validate_filter

    def list_connections(self, actor: UserRecord | None) -> DirectoryConnectionListResult:
        self._require_admin(actor)
        return DirectoryConnectionListResult(
            connections=[
                self._connection_status(connection)
                for connection in self.repository.list_directory_connections()
            ]
        )

    def create_connection(
        self,
        actor: UserRecord | None,
        payload: DirectoryConnectionCreateRequest,
    ) -> DirectoryConnectionStatus:
        actor = self._require_admin(actor)
        self._validate_directory_filter(payload.user_object_filter)

        def prepare_material(connection_id: str):
            now = utc_now_iso()
            connection = DirectoryConnectionRecord(
                connection_id=connection_id,
                **payload.model_dump(
                    exclude={
                        "bind_password",
                        "custom_ca_pem",
                        "idempotency_key",
                    }
                ),
                created_at=now,
                updated_at=now,
            )
            secrets = [
                self._encrypt_secret(
                    connection_id,
                    "bind_password",
                    payload.bind_password.get_secret_value(),
                    1,
                    now,
                )
            ]
            ca_sha256 = None
            if payload.custom_ca_pem is not None:
                ca_plaintext = payload.custom_ca_pem.get_secret_value()
                secrets.append(
                    self._encrypt_secret(
                        connection_id,
                        "custom_ca",
                        ca_plaintext,
                        1,
                        now,
                    )
                )
                ca_sha256 = sha256(ca_plaintext.encode("utf-8")).hexdigest()
            status = DirectoryConnectionStatus(
                **{
                    field_name: getattr(connection, field_name)
                    for field_name in DirectoryConnectionStatus.model_fields
                    if hasattr(connection, field_name)
                },
                bind_password_configured=True,
                custom_ca_configured=payload.custom_ca_pem is not None,
                custom_ca_sha256=ca_sha256,
            )
            return connection, tuple(secrets), status

        create_once = getattr(
            self.repository,
            "create_directory_connection_once",
            None,
        )
        if create_once is not None:
            return create_once(actor, payload, prepare_material)

        connection_id = f"directory-{uuid4().hex}"
        connection, secrets, status = prepare_material(connection_id)
        with self.repository.directory_mutation(
            f"identity:directory-connection:{connection_id}",
            actor_ids=(actor.actor_id,),
            authorization_actor_ids=(actor.actor_id,),
            connection_ids=(connection_id,),
        ):
            self.repository.put_directory_connection(connection)
            for secret in secrets:
                self.repository.put_directory_secret(secret)
            self._append_audit(
                actor.actor_id,
                "directory_connection_created",
                f"directory-connection:{connection_id}",
                "directory.connection_created",
                {"connection_id": connection_id, "status": "created"},
            )
            self.repository.persist()
        return status

    def update_connection(
        self,
        actor: UserRecord | None,
        connection_id: str,
        payload: DirectoryConnectionUpdateRequest,
    ) -> DirectoryConnectionStatus:
        actor = self._require_admin(actor)
        current = self._require_connection(connection_id)
        changes = payload.model_dump(
            exclude={
                "bind_password",
                "clear_bind_password",
                "custom_ca_pem",
                "clear_custom_ca",
            },
            exclude_none=True,
        )
        candidate = replace(current, **changes, updated_at=utc_now_iso())
        try:
            validate_directory_transport(candidate.provider_type, candidate.tls_mode)
        except ValueError:
            self._reject("directory.entry_is_invalid", 422)
        self._validate_directory_filter(candidate.user_object_filter)
        current_bind = self.repository.get_directory_secret(connection_id, "bind_password")
        if candidate.enabled and payload.clear_bind_password:
            self._reject("directory.enabled_connection_requires_bind_password", 422)
        if candidate.enabled and current_bind is None and payload.bind_password is None:
            self._reject("directory.enabled_connection_requires_bind_password", 422)
        with self.repository.directory_mutation(
            f"identity:directory-connection:{connection_id}",
            actor_ids=(actor.actor_id,),
            authorization_actor_ids=(actor.actor_id,),
            connection_ids=(connection_id,),
        ):
            self.repository.expect_directory_connection(current)
            self.repository.put_directory_connection(candidate)
            now = candidate.updated_at
            if payload.bind_password is not None:
                self.repository.put_directory_secret(
                    self._encrypt_secret(
                        connection_id,
                        "bind_password",
                        payload.bind_password.get_secret_value(),
                        (current_bind.version + 1) if current_bind else 1,
                        now,
                    )
                )
            elif payload.clear_bind_password:
                self.repository.delete_directory_secret(connection_id, "bind_password")
            current_ca = self.repository.get_directory_secret(connection_id, "custom_ca")
            if payload.custom_ca_pem is not None:
                self.repository.put_directory_secret(
                    self._encrypt_secret(
                        connection_id,
                        "custom_ca",
                        payload.custom_ca_pem.get_secret_value(),
                        (current_ca.version + 1) if current_ca else 1,
                        now,
                    )
                )
            elif payload.clear_custom_ca:
                self.repository.delete_directory_secret(connection_id, "custom_ca")
            self._append_audit(
                actor.actor_id,
                "directory_connection_updated",
                f"directory-connection:{connection_id}",
                "directory.connection_updated",
                {"connection_id": connection_id, "status": "updated"},
            )
            self.repository.persist()
        return self._connection_status(candidate)

    def test_connection(
        self, actor: UserRecord | None, connection_id: str
    ) -> DirectoryConnectionTestResult:
        self._require_admin(actor)
        connection = self._require_connection(connection_id)
        bind_password, _secret = self._bind_secret(connection_id)
        try:
            self.gateway.test_connection(connection, bind_password)
        except DirectoryGatewayError as exc:
            if exc.code == "directory_unavailable":
                self._raise_unavailable()
            return DirectoryConnectionTestResult(
                validation_status="failed",
                message_code="directory.connection_test_failed",
            )
        return DirectoryConnectionTestResult(
            validation_status="passed",
            message_code="directory.connection_test_passed",
        )

    def search_users(
        self,
        actor: UserRecord | None,
        connection_id: str,
        payload: DirectoryUserSearchRequest,
    ) -> DirectoryUserSearchResult:
        self._require_admin(actor)
        connection = self._require_enabled_connection(connection_id)
        bind_password, _secret = self._bind_secret(connection_id)
        try:
            principals = self.gateway.search_users(
                connection,
                bind_password,
                query=payload.query.strip(),
                department=None,
                limit=payload.limit,
            )
        except DirectoryGatewayError as exc:
            self._raise_gateway(exc)
        return DirectoryUserSearchResult(
            users=[self._candidate(principal) for principal in principals]
        )
    def list_scoped_connections(self) -> ScopedDirectoryConnectionListResult:
        return ScopedDirectoryConnectionListResult(
            connections=[
                ScopedDirectoryConnectionSummary(
                    connection_id=connection.connection_id,
                    display_name=connection.display_name,
                )
                for connection in self.repository.list_directory_connections()
                if connection.enabled
            ]
        )

    def search_scoped_users(
        self,
        connection_id: str,
        payload: ScopedDirectoryUserSearchRequest,
    ) -> ScopedDirectoryUserSearchResult:
        connection = self._require_scoped_enabled_connection(connection_id)
        bind_password, _secret = self._bind_secret(connection_id)
        try:
            principals = self.gateway.search_users(
                connection,
                bind_password,
                query=payload.query if payload.search_mode == "member" else None,
                department=(
                    payload.query if payload.search_mode == "department" else None
                ),
                limit=payload.limit + 1,
            )
        except DirectoryGatewayError as exc:
            self._raise_gateway(exc)
        eligible = [
            principal
            for principal in principals
            if principal.directory_enabled is not False
        ]
        return ScopedDirectoryUserSearchResult(
            users=[
                self._scoped_candidate(principal)
                for principal in eligible[: payload.limit]
            ],
            limit_reached=len(eligible) > payload.limit,
        )

    def prepare_scoped_import(
        self,
        connection_id: str,
        external_subjects: list[str],
    ) -> ScopedDirectoryImportPreparation:
        if not 1 <= len(external_subjects) <= 100:
            self._reject("directory.entry_is_invalid", 422)
        if len(external_subjects) != len(set(external_subjects)):
            self._reject("directory.import_conflict", 409)
        connection = self._require_scoped_enabled_connection(connection_id)
        bind_password, bind_secret = self._bind_secret(connection_id)
        principals: list[DirectoryPrincipal] = []
        try:
            for external_subject in external_subjects:
                principal = self.gateway.fetch_user(
                    connection,
                    bind_password,
                    external_subject,
                )
                if principal is None or principal.directory_enabled is False:
                    self._reject("directory.import_entry_unavailable", 409)
                principals.append(principal)
        except DirectoryGatewayError as exc:
            self._raise_gateway(exc)

        durable_identities = self.repository.list_external_identities()
        identities_by_subject: dict[str, list[ExternalIdentityRecord]] = {}
        for identity in durable_identities:
            if identity.connection_id == connection_id:
                identities_by_subject.setdefault(identity.external_subject, []).append(
                    identity
                )

        now = utc_now_iso()
        users: list[UserRecord] = []
        new_users: list[UserRecord] = []
        expected_users: list[tuple[str, UserRecord | None]] = []
        new_identities: list[ExternalIdentityRecord] = []
        expected_identities: list[
            tuple[str, ExternalIdentityRecord | None]
        ] = []
        expected_subjects: list[
            tuple[str, ExternalIdentityRecord | None]
        ] = []
        selected_bindings: dict[str, ExternalIdentityRecord | None] = {}
        for principal in principals:
            bindings = identities_by_subject.get(principal.external_subject, [])
            if len(bindings) > 1:
                self._reject("directory.import_conflict", 409)
            existing_identity = bindings[0] if bindings else None
            if existing_identity is not None:
                user = self.repository.get_user(existing_identity.actor_id)
                if user is None or not user.active or user.actor_type != "user":
                    self._reject("directory.import_conflict", 409)
                users.append(user)
                expected_users.append((user.actor_id, user))
                expected_identities.append((user.actor_id, existing_identity))
            else:
                actor_id = f"user-{uuid4().hex}"
                user = UserRecord(
                    actor_id=actor_id,
                    display_name=principal.display_name,
                    email=principal.email,
                    system_role="user",
                    password_digest=None,
                    actor_type="user",
                    created_at=now,
                )
                identity = self._identity(actor_id, connection_id, principal, now)
                users.append(user)
                new_users.append(user)
                expected_users.append((actor_id, None))
                new_identities.append(identity)
                expected_identities.append((actor_id, None))
            expected_subjects.append((principal.external_subject, existing_identity))
            selected_bindings[principal.external_subject] = existing_identity

        self._validate_scoped_import_aliases(
            connection_id,
            principals,
            selected_bindings,
            durable_identities,
        )
        return ScopedDirectoryImportPreparation(
            connection_id=connection.connection_id,
            source_revision=directory_record_revision(connection),
            credential_revision=directory_record_revision(bind_secret),
            users=tuple(users),
            new_users=tuple(new_users),
            expected_users=tuple(expected_users),
            new_external_identities=tuple(new_identities),
            expected_external_identities=tuple(expected_identities),
            expected_subject_bindings=tuple(expected_subjects),
        )


    def import_users(
        self,
        actor: UserRecord | None,
        connection_id: str,
        payload: DirectoryUserImportRequest,
    ) -> DirectoryUserImportResult:
        actor = self._require_admin(actor)
        connection = self._require_enabled_connection(connection_id)
        bind_password, bind_secret = self._bind_secret(connection_id)
        principals: list[DirectoryPrincipal] = []
        try:
            for external_subject in payload.external_subjects:
                principal = self.gateway.fetch_user(
                    connection,
                    bind_password,
                    external_subject,
                )
                if principal is None or principal.directory_enabled is False:
                    self._reject("directory.import_entry_unavailable", 409)
                principals.append(principal)
        except DirectoryGatewayError as exc:
            self._raise_gateway(exc)
        self._validate_import_aliases(connection_id, principals)
        now = utc_now_iso()
        users: list[UserRecord] = []
        identities: list[ExternalIdentityRecord] = []
        for principal in principals:
            actor_id = f"user-{uuid4().hex}"
            users.append(
                UserRecord(
                    actor_id=actor_id,
                    display_name=principal.display_name,
                    email=principal.email,
                    system_role="user",
                    password_digest=None,
                    actor_type="user",
                    created_at=now,
                )
            )
            identities.append(self._identity(actor_id, connection_id, principal, now))
        with self.repository.directory_mutation(
            f"identity:directory-import:{connection_id}",
            actor_ids=(actor.actor_id, *(user.actor_id for user in users)),
            authorization_actor_ids=(actor.actor_id,),
            connection_ids=(connection_id,),
        ):
            self.repository.expect_directory_connection(connection)
            self.repository.expect_directory_secret(bind_secret)
            for principal in principals:
                if self.repository.get_external_identity_by_subject(
                    connection_id, principal.external_subject
                ) is not None:
                    self._reject("directory.import_conflict", 409)
            for user, identity in zip(users, identities, strict=True):
                self.repository.put_user(user)
                self.repository.put_external_identity(identity)
            self._append_audit(
                actor.actor_id,
                "directory_users_imported",
                f"directory-connection:{connection_id}",
                "directory.users_imported",
                {
                    "connection_id": connection_id,
                    "actor_ids": [user.actor_id for user in users],
                    "count": len(users),
                    "status": "imported",
                },
            )
            self.repository.persist()
        actor_ids = [user.actor_id for user in users]
        return DirectoryUserImportResult(
            imported_actor_ids=actor_ids,
            imported_count=len(actor_ids),
            message_code="directory.users_imported",
            message_params={"count": len(actor_ids)},
        )

    def refresh_profile(
        self, actor: UserRecord | None, actor_id: str
    ) -> DirectoryProfileSummary:
        actor = self._require_admin(actor)
        identity = self.repository.get_external_identity(actor_id)
        if identity is None:
            self._reject("directory.profile_not_found", 404)
        connection = self._require_connection(identity.connection_id)
        if not connection.enabled:
            self._reject("directory.connection_disabled", 409)
        bind_password, bind_secret = self._bind_secret(connection.connection_id)
        unavailable = False
        try:
            principal = self.gateway.fetch_user(
                connection,
                bind_password,
                identity.external_subject,
            )
        except DirectoryGatewayError as exc:
            if exc.code != "directory_unavailable":
                self._raise_gateway(exc)
            principal = None
            unavailable = True
        now = utc_now_iso()
        if unavailable:
            refreshed = replace(identity, status="stale")
        elif principal is None:
            refreshed = replace(identity, status="missing")
        elif principal.directory_enabled is False:
            refreshed = replace(identity, status="disabled", directory_enabled=False)
        else:
            refreshed = self._identity(actor_id, connection.connection_id, principal, now)
        with self.repository.directory_mutation(
            f"identity:directory-profile:{actor_id}",
            actor_ids=(actor.actor_id, actor_id),
            authorization_actor_ids=(actor.actor_id,),
            connection_ids=(connection.connection_id,),
        ):
            self.repository.expect_directory_connection(connection)
            self.repository.expect_directory_secret(bind_secret)
            self.repository.expect_external_identity(identity)
            current_user = self.repository.get_user(actor_id)
            user_missing = current_user is None or current_user.actor_type != "user"
            if not user_missing:
                assert current_user is not None
                self.repository.put_external_identity(refreshed)
                if (
                    not unavailable
                    and principal is not None
                    and principal.directory_enabled is not False
                ):
                    self.repository.put_user(
                        replace(
                            current_user,
                            display_name=principal.display_name,
                            email=principal.email,
                        )
                    )
                self._append_audit(
                    actor.actor_id,
                    "directory_profile_refreshed",
                    f"user:{actor_id}",
                    "directory.profile_refreshed",
                    {
                        "connection_id": connection.connection_id,
                        "actor_ids": [actor_id],
                        "status": refreshed.status,
                    },
                )
                self.repository.persist()
        if user_missing:
            self._reject("user.was_not_found", 404)
        if unavailable:
            self._raise_unavailable()
        return self.profile_summary(refreshed, connection)

    def authenticate_imported(
        self, identifier: str, password: str
    ) -> LoginOutcome | None:
        canonical = canonical_identifier(identifier)
        candidates: list[tuple[DirectoryConnectionRecord, ExternalIdentityRecord]] = []
        connections = {
            item.connection_id: item
            for item in self.repository.list_directory_connections()
            if item.enabled
        }
        for identity in self.repository.list_external_identities():
            connection = connections.get(identity.connection_id)
            if connection is None:
                continue
            if canonical not in {
                identity.normalized_username,
                identity.normalized_email,
            }:
                continue
            user = self.repository.get_user(identity.actor_id)
            if user is None or user.actor_type != "user":
                continue
            candidates.append((connection, identity))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0].priority, item[0].connection_id))
        selected_connection = candidates[0][0]
        same_source = [
            item for item in candidates if item[0].connection_id == selected_connection.connection_id
        ]
        if len(same_source) != 1:
            self._invalid_credentials()
        connection, identity = same_source[0]
        user = self.repository.get_user(identity.actor_id)
        if user is None or not user.active or user.actor_type != "user":
            self._invalid_credentials()
        bind_password, bind_secret = self._bind_secret(connection.connection_id)
        try:
            principal = self.gateway.authenticate(
                connection,
                bind_password,
                identity.external_subject,
                password,
            )
        except DirectoryGatewayError as exc:
            if exc.code == "directory_unavailable" or exc.code == "directory_entry_invalid":
                self._raise_gateway(exc)
            self._invalid_credentials()
        if principal.directory_enabled is False:
            self._invalid_credentials()
        now = utc_now_iso()
        with self.repository.directory_mutation(
            f"identity:directory-login:{user.actor_id}",
            actor_ids=(user.actor_id,),
            connection_ids=(connection.connection_id,),
        ):
            self.repository.expect_directory_connection(connection)
            self.repository.expect_directory_secret(bind_secret)
            self.repository.expect_external_identity(identity)
            current_user = self.repository.get_user(identity.actor_id)
            login_rejected = (
                current_user is None
                or not current_user.active
                or current_user.actor_type != "user"
            )
            if not login_rejected:
                assert current_user is not None
                refreshed = self._identity(
                    current_user.actor_id,
                    connection.connection_id,
                    principal,
                    now,
                )
                updated_user = replace(
                    current_user,
                    display_name=principal.display_name,
                    email=principal.email,
                )
                self.repository.put_user(updated_user)
                self.repository.put_external_identity(refreshed)
                token = self.repository.stage_session(current_user.actor_id)
                self._append_audit(
                    current_user.actor_id,
                    "directory_login_succeeded",
                    f"user:{current_user.actor_id}",
                    "directory.login_succeeded",
                    {
                        "connection_id": connection.connection_id,
                        "actor_ids": [current_user.actor_id],
                        "status": "authenticated",
                    },
                )
                self.repository.persist()
        if login_rejected:
            self._invalid_credentials()
        return LoginOutcome(
            session=self.repository.session_state(updated_user),
            raw_session_token=token,
        )

    def profile_summary(
        self,
        identity: ExternalIdentityRecord,
        connection: DirectoryConnectionRecord | None = None,
    ) -> DirectoryProfileSummary:
        connection = connection or self._require_connection(identity.connection_id)
        return DirectoryProfileSummary(
            connection_id=connection.connection_id,
            connection_display_name=connection.display_name,
            username=identity.username,
            email=identity.email,
            groups=list(identity.groups),
            department=identity.department,
            title=identity.title,
            employee_id=identity.employee_id,
            status=identity.status,
            last_refreshed_at=identity.last_refreshed_at,
        )

    @staticmethod
    def _scoped_candidate(
        principal: DirectoryPrincipal,
    ) -> ScopedDirectoryUserCandidate:
        return ScopedDirectoryUserCandidate(
            external_subject=principal.external_subject,
            username=principal.username,
            display_name=principal.display_name,
            email=principal.email,
        )

    def _validate_scoped_import_aliases(
        self,
        connection_id: str,
        principals: list[DirectoryPrincipal],
        selected_bindings: dict[str, ExternalIdentityRecord | None],
        durable_identities: list[ExternalIdentityRecord],
    ) -> None:
        seen_aliases: set[str] = set()
        source_identities = [
            identity
            for identity in durable_identities
            if identity.connection_id == connection_id
        ]
        local_emails: set[str] = set()
        for user in self.repository.list_users():
            if (
                user.email
                and self.repository.get_external_identity(user.actor_id) is None
            ):
                local_emails.add(canonical_identifier(user.email))

        for principal in principals:
            existing = selected_bindings[principal.external_subject]
            aliases = [canonical_identifier(principal.username)]
            if principal.email:
                aliases.append(canonical_identifier(principal.email))
            for alias in aliases:
                if alias in seen_aliases:
                    self._reject("directory.import_conflict", 409)
                seen_aliases.add(alias)
                for durable_identity in source_identities:
                    if (
                        existing is not None
                        and durable_identity.actor_id == existing.actor_id
                    ):
                        continue
                    if alias in {
                        durable_identity.normalized_username,
                        durable_identity.normalized_email,
                    }:
                        self._reject("directory.import_conflict", 409)
            if any(alias in local_emails for alias in aliases):
                self._reject("directory.import_conflict", 409)

    def _connection_status(
        self, connection: DirectoryConnectionRecord
    ) -> DirectoryConnectionStatus:
        bind_secret = self.repository.get_directory_secret(
            connection.connection_id, "bind_password"
        )
        ca_secret = self.repository.get_directory_secret(connection.connection_id, "custom_ca")
        ca_sha256 = None
        if ca_secret is not None:
            ca_sha256 = sha256(self._decrypt_secret(ca_secret).encode("utf-8")).hexdigest()
        return DirectoryConnectionStatus(
            **{
                field_name: getattr(connection, field_name)
                for field_name in DirectoryConnectionStatus.model_fields
                if hasattr(connection, field_name)
            },
            bind_password_configured=bind_secret is not None,
            custom_ca_configured=ca_secret is not None,
            custom_ca_sha256=ca_sha256,
        )

    def _bind_secret(self, connection_id: str) -> tuple[str, DirectorySecretRecord]:
        secret = self.repository.get_directory_secret(connection_id, "bind_password")
        if secret is None:
            self._reject("directory.bind_password_not_configured", 422)
        return self._decrypt_secret(secret), secret

    def _encrypt_secret(
        self,
        connection_id: str,
        secret_kind: str,
        plaintext: str,
        version: int,
        updated_at: str,
    ) -> DirectorySecretRecord:
        encrypted = self.cipher.encrypt(
            domain=_BIND_DOMAIN if secret_kind == "bind_password" else _CA_DOMAIN,
            owner_id=connection_id,
            owner_kind=_SECRET_OWNER_KIND,
            secret_version=version,
            plaintext=plaintext,
        )
        return DirectorySecretRecord(
            connection_id=connection_id,
            secret_kind=secret_kind,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_id=encrypted.key_id,
            version=encrypted.version,
            algorithm=encrypted.algorithm,
            storage_backend=encrypted.storage_backend,
            updated_at=updated_at,
        )

    def _decrypt_secret(self, secret: DirectorySecretRecord) -> str:
        try:
            return self.cipher.decrypt(
                secret,
                domain=_BIND_DOMAIN if secret.secret_kind == "bind_password" else _CA_DOMAIN,
                owner_id=secret.connection_id,
                owner_kind=_SECRET_OWNER_KIND,
            )
        except Exception:
            self._raise_unavailable()

    def _validate_import_aliases(
        self,
        connection_id: str,
        principals: list[DirectoryPrincipal],
    ) -> None:
        aliases: set[str] = set()
        existing_local_emails = {
            canonical_identifier(user.email)
            for user in self.repository.list_users()
            if user.email
            and self.repository.get_external_identity(user.actor_id) is None
        }
        for principal in principals:
            username = canonical_identifier(principal.username)
            email = canonical_identifier(principal.email) if principal.email else None
            for alias in (username, email):
                if alias is None:
                    continue
                if alias in aliases:
                    self._reject("directory.import_conflict", 409)
                aliases.add(alias)
            if any(
                alias in existing_local_emails
                for alias in (username, email)
                if alias is not None
            ):
                self._reject("directory.import_conflict", 409)
            if self.repository.get_external_identity_by_subject(
                connection_id, principal.external_subject
            ) is not None:
                self._reject("directory.import_conflict", 409)

    @staticmethod
    def _identity(
        actor_id: str,
        connection_id: str,
        principal: DirectoryPrincipal,
        refreshed_at: str,
    ) -> ExternalIdentityRecord:
        return ExternalIdentityRecord(
            actor_id=actor_id,
            connection_id=connection_id,
            external_subject=principal.external_subject,
            normalized_username=canonical_identifier(principal.username),
            normalized_email=(
                canonical_identifier(principal.email) if principal.email else None
            ),
            username=principal.username,
            display_name=principal.display_name,
            email=principal.email,
            groups=principal.groups,
            department=principal.department,
            title=principal.title,
            employee_id=principal.employee_id,
            directory_enabled=principal.directory_enabled,
            status=("disabled" if principal.directory_enabled is False else "current"),
            last_refreshed_at=refreshed_at,
        )

    @staticmethod
    def _candidate(principal: DirectoryPrincipal) -> DirectoryUserCandidate:
        return DirectoryUserCandidate(
            external_subject=principal.external_subject,
            username=principal.username,
            display_name=principal.display_name,
            email=principal.email,
            groups=list(principal.groups),
            department=principal.department,
            title=principal.title,
            employee_id=principal.employee_id,
            directory_enabled=principal.directory_enabled,
        )

    def _validate_directory_filter(self, value: str) -> None:
        try:
            self.validate_filter(value)
        except ValueError:
            self._reject("directory.entry_is_invalid", 422)

    def _require_connection(self, connection_id: str) -> DirectoryConnectionRecord:
        connection = self.repository.get_directory_connection(connection_id)
        if connection is None:
            self._reject("directory.connection_not_found", 404)
        return connection

    def _require_enabled_connection(self, connection_id: str) -> DirectoryConnectionRecord:
        connection = self._require_connection(connection_id)
        if not connection.enabled:
            self._reject("directory.connection_disabled", 409)
        return connection

    def _require_scoped_enabled_connection(
        self,
        connection_id: str,
    ) -> DirectoryConnectionRecord:
        connection = self.repository.get_directory_connection(connection_id)
        if connection is None or not connection.enabled:
            self._reject("directory.import_entry_unavailable", 409)
        return connection

    def _require_admin(self, actor: UserRecord | None) -> UserRecord:
        if actor is None:
            raise IdentityAccessError(
                "unauthenticated", "auth.sign_in_is_required", 401
            )
        if not self.repository.is_system_admin(actor):
            raise IdentityAccessError(
                "access_denied", "permission.admin_permission_is_required", 403
            )
        return actor

    def _append_audit(
        self,
        actor_id: str,
        event_type: str,
        target_ref: str,
        message_code: str,
        metadata: dict[str, object],
    ) -> None:
        self.repository.append_audit(
            IdentityAuditCommand(
                event_type=event_type,
                actor_id=actor_id,
                target_ref=target_ref,
                scope_type=None,
                scope_id=None,
                message_code=message_code,
                metadata=metadata,
            )
        )

    @staticmethod
    def _reject(message_code: str, status_code: int) -> None:
        raise IdentityAccessError("directory_action_rejected", message_code, status_code)

    @staticmethod
    def _invalid_credentials() -> None:
        raise IdentityAccessError(
            "invalid_credentials",
            "auth.the_email_or_password_was_not_accepted",
            401,
        )

    @staticmethod
    def _raise_unavailable() -> None:
        raise IdentityAccessError(
            "directory_unavailable", "directory.is_unavailable", 503
        )

    def _raise_gateway(self, error: DirectoryGatewayError) -> None:
        if error.code == "directory_unavailable":
            self._raise_unavailable()
        if error.code == "invalid_credentials":
            self._invalid_credentials()
        self._reject("directory.entry_is_invalid", 422)
