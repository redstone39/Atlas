from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from atlas_production.shared.public import (
    AdminActionResult,
)
from .api_models import (
    InviteAcceptRequest,
    InviteAcceptResult,
    LocalPilotInviteAcceptance,
    LoginRequest,
    SessionState,
    UserAdminListResult,
    UserAdminSummary,
    UserAdminUpdateRequest,
    UserInviteCreateRequest,
    UserInviteCreateResult,
    UserInviteListResult,
    UserInviteRevokeRequest,
    UserInviteSummary,
)
from .records import (
    UserInviteRecord,
    UserRecord,
)
from .security import (
    invite_token_digest,
    password_digest,
    verify_password,
)
from atlas_production.shared.public import (
    utc_now_iso,
)
from .contracts import IdentityAccessError, IdentityAuditCommand, LoginOutcome
from .ports import IdentityAccessRepository, InviteScopeGrantPort


class IdentityAccessService:
    def __init__(
        self,
        repository: IdentityAccessRepository,
        scope_grants: InviteScopeGrantPort,
    ) -> None:
        self.repository = repository
        self.scope_grants = scope_grants

    def _identity_mutation_context(self, owner_key: str, **kwargs):
        mutation = getattr(self.repository, "identity_mutation", None)
        if mutation is None:
            return nullcontext()
        return mutation(owner_key, **kwargs)

    @staticmethod
    def _run_identity_mutation(mutation, action):
        result = None
        rejection: IdentityAccessError | None = None
        with mutation:
            try:
                result = action()
            except IdentityAccessError as exc:
                rejection = exc
        if rejection is not None:
            raise rejection
        assert result is not None
        return result

    def session_for_token(self, token: str | None) -> SessionState:
        actor = self.repository.actor_for_token(token)
        if not actor:
            return SessionState(
                authenticated=False,
                actor=None,
                available_projects=[],
                system_role=None,
            )
        return self.repository.session_state(actor)

    def login(self, payload: LoginRequest) -> LoginOutcome:
        user = self.repository.user_by_email(payload.email)
        if not user or not user.active or not verify_password(payload.password, user.password_digest):
            raise IdentityAccessError(
                "invalid_credentials",
                'auth.the_email_or_password_was_not_accepted',
                401,
                "audit-p0-login-denied",
            )
        token = self.repository.issue_session(user.actor_id)
        return LoginOutcome(
            session=self.repository.session_state(user),
            raw_session_token=token,
        )

    def logout(self, token: str | None) -> bool:
        return self.repository.revoke_session(token)

    def create_invite(
        self,
        actor: UserRecord | None,
        payload: UserInviteCreateRequest,
    ) -> UserInviteCreateResult:
        normalized_email = payload.email.strip().lower()
        email_owner = sha256(normalized_email.encode("utf-8")).hexdigest()
        return self._run_identity_mutation(
            self._identity_mutation_context(
                f"identity-email:{email_owner}",
                actor_ids=(actor.actor_id,) if actor else (),
                authorization_actor_ids=(actor.actor_id,) if actor else (),
                user_email=normalized_email,
                scope_type=payload.scope_type,
                scope_id=payload.scope_id,
            ),
            lambda: self._create_invite_locked(
                actor,
                payload,
                normalized_email,
            ),
        )

    def _create_invite_locked(
        self,
        actor: UserRecord | None,
        payload: UserInviteCreateRequest,
        normalized_email: str,
    ) -> UserInviteCreateResult:
        actor = self._require_actor(actor)
        self._validate_invite_scope(actor, payload)
        existing_user = self.repository.user_by_email(normalized_email)
        if existing_user and existing_user.active:
            self._reject_admin_action(
                "identity.user_with_email_already_exists",
                "audit-user-invite-rejected",
                409,
            )
        if self.repository.pending_invite_for_email(normalized_email):
            self._reject_admin_action(
                "invite.already_pending_for_email",
                "audit-user-invite-rejected",
                409,
            )

        actor_id = existing_user.actor_id if existing_user else self._stable_actor_id(normalized_email)
        if not existing_user:
            self.repository.put_user(
                UserRecord(
                    actor_id=actor_id,
                    display_name=payload.display_name,
                    email=normalized_email,
                    system_role=payload.system_role,
                    password_digest=None,
                    active=False,
                    actor_type="user",
                    created_at=utc_now_iso(),
                )
            )
        else:
            existing_user.display_name = payload.display_name
            existing_user.system_role = payload.system_role
            self.repository.put_user(existing_user)

        raw_token = f"atlas_invite_{token_urlsafe(32)}"
        digest = invite_token_digest(raw_token)
        created_at = datetime.now(timezone.utc)
        invite = UserInviteRecord(
            invite_id=f"inv-{sha256(digest.encode('utf-8')).hexdigest()[:12]}",
            actor_id=actor_id,
            email=normalized_email,
            display_name=payload.display_name,
            system_role=payload.system_role,
            token_digest=digest,
            token_fingerprint=digest[:12],
            status="pending",
            created_at=created_at.isoformat(),
            expires_at=(created_at + timedelta(days=7)).isoformat(),
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            scope_role=payload.scope_role,
        )
        self.repository.put_invite(invite)
        audit = self.repository.append_audit(
            IdentityAuditCommand(
                event_type="user_invite_created",
                actor_id=actor.actor_id,
                target_ref=f"user:{actor_id}",
                scope_type=payload.scope_type,
                scope_id=payload.scope_id,
                message_code='invite.user_invite_has_been_created',
                metadata={
                    "invite_id": invite.invite_id,
                    "email": normalized_email,
                    "delivery_mode": "copy_link",
                    "scope_role": payload.scope_role,
                },
            )
        )
        self.repository.persist()
        return UserInviteCreateResult(
            request_id=payload.idempotency_key,
            status="applied",
            invite=self._invite_summary(invite),
            message_code='invite.is_ready_copy_the_local_acceptance_link',
            audit_event_ref=audit.event_id,
            local_pilot_acceptance=LocalPilotInviteAcceptance(
                mode="copy_link",
                acceptance_token=raw_token,
                acceptance_url=f"/accept-invite?token={raw_token}",
            ),
        )

    def list_invites(self, actor: UserRecord | None) -> UserInviteListResult:
        actor = self._require_actor(actor)
        invites = sorted(self.repository.list_invites(), key=lambda item: item.created_at)
        if not self.repository.is_system_admin(actor):
            invites = [
                invite
                for invite in invites
                if invite.scope_type
                and invite.scope_id
                and self.scope_grants.can_manage_scope(
                    actor,
                    invite.scope_type,
                    invite.scope_id,
                )
            ]
        return UserInviteListResult(invites=[self._invite_summary(invite) for invite in invites])

    def revoke_invite(
        self,
        actor: UserRecord | None,
        invite_id: str,
        payload: UserInviteRevokeRequest,
    ) -> AdminActionResult:
        return self._run_identity_mutation(
            self._identity_mutation_context(
                f"invite:{invite_id}",
                actor_ids=(actor.actor_id,) if actor else (),
                authorization_actor_ids=(actor.actor_id,) if actor else (),
                invite_id=invite_id,
            ),
            lambda: self._revoke_invite_locked(actor, invite_id, payload),
        )

    def _revoke_invite_locked(
        self,
        actor: UserRecord | None,
        invite_id: str,
        payload: UserInviteRevokeRequest,
    ) -> AdminActionResult:
        actor = self._require_actor(actor)
        invite = self.repository.get_invite(invite_id)
        if not invite:
            self._reject_admin_action(
                "invite.was_not_found",
                "audit-user-invite-revoke-rejected",
                404,
            )
        assert invite is not None
        if not self.repository.is_system_admin(actor):
            if (
                not invite.scope_type
                or not invite.scope_id
                or not self.scope_grants.can_manage_scope(actor, invite.scope_type, invite.scope_id)
            ):
                raise IdentityAccessError(
                    "access_denied",
                    'invite.management_requires_scope_admin_access',
                    403,
                )
        if invite.status != "pending":
            self._reject_admin_action(
                "invite.only_pending_can_be_revoked",
                "audit-user-invite-revoke-rejected",
                409,
            )
        invite.status = "revoked"
        invite.revoked_at = utc_now_iso()
        self.repository.put_invite(invite)
        user = self.repository.get_user(invite.actor_id)
        if user and not user.password_digest:
            user.active = False
            self.repository.put_user(user)
        audit = self.repository.append_audit(
            IdentityAuditCommand(
                event_type="user_invite_revoked",
                actor_id=actor.actor_id,
                target_ref=f"invite:{invite.invite_id}",
                scope_type=invite.scope_type,
                scope_id=invite.scope_id,
                message_code='invite.has_been_revoked',
                metadata={"invite_id": invite.invite_id, "email": invite.email},
            )
        )
        self.repository.persist()
        return AdminActionResult(
            request_id=payload.idempotency_key,
            status="applied",
            target_ref=f"invite:{invite.invite_id}",
            message_code='invite.has_been_revoked',
            audit_event_ref=audit.event_id,
        )

    def accept_invite(self, payload: InviteAcceptRequest) -> InviteAcceptResult:
        digest = invite_token_digest(payload.invite_token)
        return self._run_identity_mutation(
            self._identity_mutation_context(
                f"invite-digest:{digest}",
                invite_digest=digest,
            ),
            lambda: self._accept_invite_locked(payload),
        )

    def _accept_invite_locked(self, payload: InviteAcceptRequest) -> InviteAcceptResult:
        invite = self.repository.invite_for_token(payload.invite_token)
        if not invite:
            raise IdentityAccessError(
                "invite_not_found_or_invalid",
                'invite.was_not_found_or_is_no_longer_valid',
                404,
            )
        if invite.status == "accepted":
            raise IdentityAccessError(
                "invite_already_accepted",
                'invite.has_already_been_accepted',
                409,
            )
        if invite.status == "revoked":
            raise IdentityAccessError("invite_revoked", 'invite.has_been_revoked', 410)
        if self._invite_status(invite) == "expired":
            invite.status = "expired"
            self.repository.put_invite(invite)
            self.repository.persist()
            raise IdentityAccessError("invite_expired", 'invite.has_expired', 410)
        user = self.repository.get_user(invite.actor_id)
        if not user:
            raise IdentityAccessError(
                "invite_not_found_or_invalid",
                'invite.was_not_found_or_is_no_longer_valid',
                404,
            )
        user.password_digest = password_digest(payload.password)
        user.active = True
        user.display_name = invite.display_name
        user.email = invite.email
        user.system_role = invite.system_role
        invite.status = "accepted"
        invite.accepted_at = utc_now_iso()
        self.repository.put_user(user)
        self.repository.put_invite(invite)
        self.scope_grants.apply_invite_scope(invite)
        audit = self.repository.append_audit(
            IdentityAuditCommand(
                event_type="user_invite_accepted",
                actor_id=user.actor_id,
                target_ref=f"invite:{invite.invite_id}",
                scope_type=invite.scope_type,
                scope_id=invite.scope_id,
                message_code='invite.accepted_sign_in_with_your_email_and_new_password',
                metadata={"invite_id": invite.invite_id, "email": invite.email},
            )
        )
        self.repository.persist()
        return InviteAcceptResult(
            request_id=payload.idempotency_key,
            status="applied",
            target_ref=f"user:{user.actor_id}",
            message_code='invite.accepted_sign_in_with_your_email_and_new_password',
            audit_event_ref=audit.event_id,
        )

    def list_users(self, actor: UserRecord | None) -> UserAdminListResult:
        self._require_system_admin(actor)
        users: list[UserAdminSummary] = []
        for user in sorted(self.repository.list_users(), key=lambda item: item.actor_id):
            if user.actor_type not in {"user", "service_account"}:
                continue
            latest_invite = self._latest_invite_for_actor(user.actor_id)
            users.append(
                UserAdminSummary(
                    actor_id=user.actor_id,
                    actor_type=user.actor_type,
                    display_name=user.display_name,
                    email=user.email,
                    system_role=user.system_role,
                    active=user.active,
                    created_at=user.created_at,
                    invite_status=self._invite_status(latest_invite) if latest_invite else None,
                    invite_id=latest_invite.invite_id if latest_invite else None,
                )
            )
        return UserAdminListResult(users=users)

    def update_user(
        self,
        actor: UserRecord | None,
        actor_id: str,
        payload: UserAdminUpdateRequest,
    ) -> AdminActionResult:
        protect_admin_count = (
            payload.active is False
            or (
                payload.system_role is not None
                and payload.system_role != "admin"
            )
        )
        return self._run_identity_mutation(
            self._identity_mutation_context(
                f"identity:{actor_id}",
                actor_ids=tuple(
                    selected
                    for selected in (
                        actor.actor_id if actor else None,
                        actor_id,
                    )
                    if selected
                ),
                protect_admin_count=protect_admin_count,
            ),
            lambda: self._update_user_locked(actor, actor_id, payload),
        )

    def _update_user_locked(
        self,
        actor: UserRecord | None,
        actor_id: str,
        payload: UserAdminUpdateRequest,
    ) -> AdminActionResult:
        actor = self._require_system_admin(actor)
        target = self.repository.get_user(actor_id)
        if not target or target.actor_type != "user":
            self._reject_admin_action(
                "user.was_not_found",
                "audit-user-update-rejected",
                404,
            )
        assert target is not None
        removes_active_admin = self._would_remove_active_admin(
            target,
            payload.active,
            payload.system_role,
        )
        if removes_active_admin:
            if actor.actor_id == target.actor_id:
                self._reject_admin_action(
                    "identity.cannot_remove_own_admin_role",
                    "audit-user-update-rejected",
                    422,
                )
            if self.repository.active_admin_count() <= 1:
                self._reject_admin_action(
                    "identity.active_admin_required",
                    "audit-user-update-rejected",
                    422,
                )
        if payload.display_name is not None:
            target.display_name = payload.display_name
        if payload.system_role is not None:
            target.system_role = payload.system_role
        if payload.active is not None:
            target.active = payload.active
        self.repository.put_user(target)
        if removes_active_admin and self.repository.active_admin_count() < 1:
            self._reject_admin_action(
                "identity.active_admin_required",
                "audit-user-update-rejected",
                422,
            )
        message_code = 'processing.user_profile_is_updated'
        if payload.system_role is not None:
            message_code = 'identity.system_admin_access_is_updated'
        if payload.active is False:
            message_code = 'identity.user_has_been_removed'
        elif payload.active is True:
            message_code = 'identity.user_has_been_reactivated'
        audit = self.repository.append_audit(
            IdentityAuditCommand(
                event_type="user_lifecycle_updated",
                actor_id=actor.actor_id,
                target_ref=f"user:{actor_id}",
                scope_type=None,
                scope_id=None,
                message_code=message_code,
                metadata={"active": target.active, "system_role": target.system_role},
            )
        )
        return AdminActionResult(
            request_id=payload.idempotency_key,
            status="applied",
            target_ref=f"user:{actor_id}",
            message_code=message_code,
            audit_event_ref=audit.event_id,
        )

    def _validate_invite_scope(
        self,
        actor: UserRecord,
        payload: UserInviteCreateRequest,
    ) -> None:
        if self.repository.is_system_admin(actor):
            scope_error = self.scope_grants.validate_scope_values(
                payload.scope_type,
                payload.scope_id,
                payload.scope_role,
                payload.idempotency_key,
            )
            if scope_error:
                raise scope_error
            return
        if payload.system_role != "user":
            raise IdentityAccessError(
                "access_denied",
                'invite.only_system_admins_can_invite_system_admins',
                403,
            )
        if not payload.scope_type or not payload.scope_id:
            raise IdentityAccessError(
                "access_denied",
                'invite.scoped_admins_must_choose_a_team_or_project_for_the_invite',
                403,
            )
        scope_error = self.scope_grants.validate_scope_values(
            payload.scope_type,
            payload.scope_id,
            payload.scope_role,
            payload.idempotency_key,
        )
        if scope_error:
            raise scope_error
        if not self.scope_grants.can_manage_scope(actor, payload.scope_type, payload.scope_id):
            raise IdentityAccessError(
                "access_denied",
                'invite.requires_admin_access_to_this_team_or_project',
                403,
            )

    def _require_actor(self, actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise IdentityAccessError(
                "unauthenticated",
                'auth.please_sign_in_before_using_admin_tools',
                401,
            )
        return actor

    def _require_system_admin(self, actor: UserRecord | None) -> UserRecord:
        actor = self._require_actor(actor)
        if not self.repository.is_system_admin(actor):
            raise IdentityAccessError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            )
        return actor

    def _latest_invite_for_actor(self, actor_id: str) -> UserInviteRecord | None:
        matching = [
            invite
            for invite in self.repository.list_invites()
            if invite.actor_id == actor_id
        ]
        return max(matching, key=lambda item: item.created_at) if matching else None

    @staticmethod
    def _invite_status(invite: UserInviteRecord) -> str:
        if invite.status != "pending":
            return invite.status
        expires_at = datetime.fromisoformat(invite.expires_at)
        return "expired" if expires_at < datetime.now(timezone.utc) else invite.status

    @classmethod
    def _invite_summary(cls, invite: UserInviteRecord) -> UserInviteSummary:
        return UserInviteSummary(
            invite_id=invite.invite_id,
            actor_id=invite.actor_id,
            email=invite.email,
            display_name=invite.display_name,
            system_role=invite.system_role,
            status=cls._invite_status(invite),
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            accepted_at=invite.accepted_at,
            revoked_at=invite.revoked_at,
            scope_type=invite.scope_type,
            scope_id=invite.scope_id,
            scope_role=invite.scope_role,
        )

    @staticmethod
    def _stable_actor_id(email: str) -> str:
        return f"user-{sha256(email.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _would_remove_active_admin(
        target: UserRecord,
        active: bool | None,
        system_role: str | None,
    ) -> bool:
        if target.actor_type != "user" or target.system_role != "admin" or not target.active:
            return False
        next_active = target.active if active is None else active
        next_role = target.system_role if system_role is None else system_role
        return not next_active or next_role != "admin"

    @staticmethod
    def _reject_admin_action(message: str, audit_event_ref: str, status_code: int) -> None:
        raise IdentityAccessError(
            "admin_action_rejected",
            message,
            status_code,
            audit_event_ref,
        )
