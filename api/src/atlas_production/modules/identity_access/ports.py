from __future__ import annotations

from typing import ContextManager, Protocol

from .api_models import (
    SessionState,
)
from atlas_production.shared.public import (
    AuditEventRecord,
)
from .records import (
    UserInviteRecord,
    UserRecord,
)
from .contracts import IdentityAccessError, IdentityAuditCommand


class IdentityAccessRepository(Protocol):
    def identity_mutation(
        self,
        owner_key: str,
        *,
        actor_ids: tuple[str, ...] = (),
        authorization_actor_ids: tuple[str, ...] = (),
        user_email: str | None = None,
        invite_id: str | None = None,
        invite_digest: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        protect_admin_count: bool = False,
    ) -> ContextManager[None]: ...

    def actor_for_token(self, token: str | None) -> UserRecord | None: ...

    def session_state(self, user: UserRecord) -> SessionState: ...

    def user_by_email(self, email: str) -> UserRecord | None: ...

    def get_user(self, actor_id: str) -> UserRecord | None: ...

    def list_users(self) -> list[UserRecord]: ...

    def put_user(self, user: UserRecord) -> None: ...

    def issue_session(self, actor_id: str) -> str: ...

    def revoke_session(self, token: str | None) -> bool: ...

    def invite_for_token(self, raw_token: str | None) -> UserInviteRecord | None: ...

    def pending_invite_for_email(self, email: str) -> UserInviteRecord | None: ...

    def get_invite(self, invite_id: str) -> UserInviteRecord | None: ...

    def list_invites(self) -> list[UserInviteRecord]: ...

    def put_invite(self, invite: UserInviteRecord) -> None: ...

    def is_system_admin(self, actor: UserRecord) -> bool: ...

    def active_admin_count(self) -> int: ...

    def append_audit(self, command: IdentityAuditCommand) -> AuditEventRecord: ...

    def persist(self) -> None: ...


class InviteScopeGrantPort(Protocol):
    def validate_scope_values(
        self,
        scope_type: str | None,
        scope_id: str | None,
        scope_role: str | None,
        request_id: str,
    ) -> IdentityAccessError | None: ...

    def can_manage_scope(
        self,
        actor: UserRecord,
        scope_type: str,
        scope_id: str,
    ) -> bool: ...

    def apply_invite_scope(self, invite: UserInviteRecord) -> None: ...
