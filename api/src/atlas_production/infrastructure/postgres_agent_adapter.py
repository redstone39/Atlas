from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from secrets import token_urlsafe
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AccessDecisionWriter
from atlas_production.infrastructure.postgres_lock_keys import (identity_actor_owner_key,
project_acl_subject_owner_key,
project_owner_key,
team_subject_owner_key,)
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityRepository,
    IdentitySessionChangeSet,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    ProjectAclRepository,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAgentTokenRow,
    AtlasUserRow,
)
from atlas_production.modules.agent_runtime.public import AgentQueryAuthorizationV1
from atlas_production.modules.identity_access.agent_contracts import (
    AgentAuditCommand,
    AgentProjectGrantView,
)
from atlas_production.modules.identity_access.agent_ports import AgentAccessRepository
from atlas_production.modules.identity_access.agent_service import AgentAccessService
from atlas_production.modules.identity_access.records import (
    AgentTokenRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.security import agent_token_digest
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]


def _user_record(row: AtlasUserRow) -> UserRecord:
    return UserRecord(
        actor_id=row.actor_id,
        display_name=row.display_name,
        email=row.email,
        system_role=row.system_role,
        password_digest=row.password_digest,
        active=row.active,
        actor_type=row.actor_type,
        created_at=row.created_at,
    )


def _token_record(row: AtlasAgentTokenRow) -> AgentTokenRecord:
    return AgentTokenRecord(
        token_id=row.token_id,
        actor_id=row.actor_id,
        token_digest=row.token_digest,
        token_fingerprint=row.token_fingerprint,
        status=row.status,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


@dataclass(slots=True)
class _AgentMutationBuffer:
    users: dict[str, UserRecord] = field(default_factory=dict)
    original_users: dict[str, UserRecord | None] = field(default_factory=dict)
    tokens: dict[str, AgentTokenRecord] = field(default_factory=dict)
    expected_agent_users: dict[str, UserRecord] = field(default_factory=dict)
    audit_events: list[AuditEventRecord] = field(default_factory=list)


class PostgresAgentAccessRepository(AgentAccessRepository):
    def __init__(self, session_factory: SessionFactory) -> None:
        self.identity_owner = IdentityRepository(session_factory)
        self.project_owner = ProjectAclRepository(session_factory)
        self._buffer: ContextVar[_AgentMutationBuffer | None] = ContextVar(
            f"atlas_postgres_agent_buffer_{id(self)}",
            default=None,
        )

    def get_user(self, actor_id: str) -> UserRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and actor_id in buffer.users:
            return replace(buffer.users[actor_id])
        user = self.identity_owner.get_user(actor_id)
        return replace(user) if user else None

    def list_users(self) -> list[UserRecord]:
        users = {item.actor_id: item for item in self._all_users()}
        buffer = self._buffer.get()
        if buffer is not None:
            users.update(buffer.users)
        return [replace(users[key]) for key in sorted(users)]

    def put_user(self, user: UserRecord) -> None:
        buffer = self._pending()
        if user.actor_id not in buffer.original_users:
            buffer.original_users[user.actor_id] = self.identity_owner.get_user(
                user.actor_id
            )
        buffer.users[user.actor_id] = replace(user)

    def get_token(self, token_id: str) -> AgentTokenRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and token_id in buffer.tokens:
            return replace(buffer.tokens[token_id])
        token = self.identity_owner.get_agent_token(token_id)
        return replace(token) if token else None

    def list_tokens_for_agent(self, actor_id: str) -> list[AgentTokenRecord]:
        tokens: dict[str, AgentTokenRecord] = {}
        after_token_id: str | None = None
        while True:
            page = self.identity_owner.list_agent_tokens(
                actor_id,
                limit=500,
                after_token_id=after_token_id,
            )
            tokens.update((item.token_id, item) for item in page)
            if len(page) < 500:
                break
            after_token_id = page[-1].token_id
        buffer = self._buffer.get()
        if buffer is not None:
            tokens.update(
                (token_id, token)
                for token_id, token in buffer.tokens.items()
                if token.actor_id == actor_id
            )
        return [replace(tokens[key]) for key in sorted(tokens)]

    def put_token(self, token: AgentTokenRecord) -> None:
        self._pending().tokens[token.token_id] = replace(token)

    def list_project_grants(self) -> list[AgentProjectGrantView]:
        result: list[AgentProjectGrantView] = []
        after_grant_id: str | None = None
        while True:
            page = self.project_owner.list_subject_grants(
                subject_type="service_account",
                active_only=True,
                limit=500,
                after_grant_id=after_grant_id,
            )
            result.extend(
                AgentProjectGrantView(
                    grant_id=grant.grant_id,
                    project_id=grant.project_id,
                    subject_id=grant.subject_id,
                    role=grant.role,
                    effect=grant.effect,
                    status=grant.status,
                )
                for grant in page
            )
            if len(page) < 500:
                return result
            after_grant_id = page[-1].grant_id

    def is_system_admin(self, actor: UserRecord) -> bool:
        current = self.identity_owner.get_user(actor.actor_id)
        return bool(
            current
            and current.actor_type == actor.actor_type
            and current.active
            and current.system_role == "admin"
        )

    def issue_token(self, actor_id: str) -> tuple[str, AgentTokenRecord]:
        buffer = self._pending()
        current = self.identity_owner.get_user(actor_id)
        if (
            current is None
            or current.actor_type != "service_account"
            or not current.active
        ):
            raise ValueError("active Agent target is required")
        buffer.expected_agent_users.setdefault(actor_id, replace(current))
        raw_token = f"atlas_agent_{token_urlsafe(32)}"
        digest = agent_token_digest(raw_token)
        token = AgentTokenRecord(
            token_id=f"agtok-{token_urlsafe(12)}",
            actor_id=actor_id,
            token_digest=digest,
            token_fingerprint=digest[:12],
            created_at=utc_now_iso(),
        )
        self.put_token(token)
        return raw_token, replace(token)

    def append_audit(self, command: AgentAuditCommand) -> AuditEventRecord:
        buffer = self._pending()
        event = build_audit_event(
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=None,
            message_code=command.message_code,
            metadata=command.metadata,
            message_params=command.message_params,
        )
        buffer.audit_events.append(event)
        try:
            self.identity_owner.identity_session(
                IdentitySessionChangeSet(
                    users=tuple(buffer.users.values()),
                    expected_users=tuple(
                        (actor_id, buffer.original_users.get(actor_id))
                        for actor_id in buffer.users
                    ),
                    agent_tokens=tuple(buffer.tokens.values()),
                    expected_agent_users=tuple(buffer.expected_agent_users.items()),
                    audit_events=tuple(buffer.audit_events),
                    authorization_actor_id=command.actor_id,
                    authorization_requires_system_admin=command.actor_id is not None,
                )
            )
        finally:
            self._buffer.set(None)
        return event

    def _all_users(self) -> list[UserRecord]:
        result: list[UserRecord] = []
        after_actor_id: str | None = None
        while True:
            page = self.identity_owner.list_users(
                limit=500,
                after_actor_id=after_actor_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_actor_id = page[-1].actor_id

    def _pending(self) -> _AgentMutationBuffer:
        buffer = self._buffer.get()
        if buffer is None:
            buffer = _AgentMutationBuffer()
            self._buffer.set(buffer)
        return buffer



@dataclass(frozen=True, slots=True)
class PostgresAgentQueryAuthority:
    """Fence raw-token currentness and project action decision in one SQL unit."""

    session_factory: SessionFactory

    def authorize(
        self,
        *,
        raw_token: str | None,
        project_id: str,
    ) -> AgentQueryAuthorizationV1:
        if not raw_token:
            return AgentQueryAuthorizationV1("invalid_token")
        digest = agent_token_digest(raw_token)
        session = self.session_factory()
        with session:
            try:
                token_rows = session.scalars(
                    select(AtlasAgentTokenRow)
                    .where(AtlasAgentTokenRow.token_digest == digest)
                    .order_by(AtlasAgentTokenRow.token_id)
                    .limit(2)
                ).all()
                if len(token_rows) != 1:
                    session.rollback()
                    return AgentQueryAuthorizationV1("invalid_token")
                token_id = token_rows[0].token_id
                token_actor_id = token_rows[0].actor_id
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        "team:hierarchy-control",
                        "team:membership-control",
                        f"project:acl-control:{project_id}",
                    ),
                    identity_keys=(
                        f"identity:agent-token:{token_id}",
                        identity_actor_owner_key(token_actor_id),
                        project_owner_key(project_id),
                        project_acl_subject_owner_key(
                            "service_account",
                            token_actor_id,
                        ),
                        team_subject_owner_key(
                            "service_account",
                            token_actor_id,
                        ),
                    ),
                )
                token_row = session.scalar(
                    select(AtlasAgentTokenRow)
                    .where(
                        AtlasAgentTokenRow.token_id == token_id,
                        AtlasAgentTokenRow.token_digest == digest,
                        AtlasAgentTokenRow.actor_id == token_actor_id,
                    )
                    .with_for_update()
                )
                if token_row is None:
                    session.rollback()
                    return AgentQueryAuthorizationV1("invalid_token")
                token = _token_record(token_row)
                actor_row = session.scalar(
                    select(AtlasUserRow)
                    .where(AtlasUserRow.actor_id == token.actor_id)
                    .with_for_update()
                )
                if (
                    actor_row is None
                    or actor_row.actor_type != "service_account"
                    or not actor_row.active
                ):
                    session.rollback()
                    return AgentQueryAuthorizationV1(
                        "invalid_agent",
                        actor_id=token.actor_id,
                        token_fingerprint=token.token_fingerprint,
                    )
                agent = _user_record(actor_row)
                if token.status != "active":
                    session.rollback()
                    return AgentQueryAuthorizationV1(
                        "revoked",
                        actor_id=agent.actor_id,
                        token_id=token.token_id,
                        token_fingerprint=token.token_fingerprint,
                    )
                decision = ActionAwareAclAuthority.resolve_in_session(
                    session,
                    actor_type=agent.actor_type,
                    actor_id=agent.actor_id,
                    project_id=project_id,
                    action="agent_query",
                    lock_rows=True,
                )
                AccessDecisionWriter(session).append(decision)
                session.commit()
                return AgentQueryAuthorizationV1(
                    "allowed" if decision.allowed else "denied",
                    actor_id=agent.actor_id,
                    token_fingerprint=token.token_fingerprint,
                    access_decision_id=decision.decision_id,
                )
            except Exception:
                session.rollback()
                raise



def build_postgres_agent_access(
    session_factory: SessionFactory,
) -> tuple[AgentAccessService, PostgresAgentQueryAuthority]:
    return (
        AgentAccessService(PostgresAgentAccessRepository(session_factory)),
        PostgresAgentQueryAuthority(session_factory),
    )


__all__ = [
    "PostgresAgentAccessRepository",
    "PostgresAgentQueryAuthority",
    "build_postgres_agent_access",
]
