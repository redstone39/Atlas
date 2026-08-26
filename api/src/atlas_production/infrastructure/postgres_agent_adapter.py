from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime
from dataclasses import dataclass, field, replace
from secrets import token_urlsafe
from typing import Callable
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AccessDecisionWriter
from atlas_production.infrastructure.postgres_lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityAuthorizationConflict,
    IdentityCreatePrepared,
    IdentityCreateReplayConflict,
    IdentityCurrentnessConflict,
    IdentityRepository,
    IdentitySessionChangeSet,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    ProjectAclRepository,
)
from atlas_production.infrastructure.persistence.agent_runtime import (
    AtlasAgentResearchRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAgentTokenRow,
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.payload_policy import (
    validate_typed_payload,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.modules.agent_runtime.public import (
    AcceptedResearchSnapshotV1,
    AgentResearchAcceptanceV1,
    AgentResearchAuthorizationV1,
    AgentResearchAuditSummaryV1,
    AgentResearchRecordV1,
    AgentResearchReplayConflict,
    AgentResearchScopeRefV1,
    AgentResearchTerminalConflict,
    CreateAcceptedAgentResearchV1,
    ResearchPacketV1,
    StartAgentResearchV1,
)
from atlas_production.modules.identity_access.agent_contracts import (
    AgentAccessError,
    AgentAuditCommand,
    AgentCreateOutcome,
    AgentProjectGrantView,
)
from atlas_production.modules.identity_access.agent_ports import AgentAccessRepository
from atlas_production.modules.identity_access.agent_service import AgentAccessService
from atlas_production.modules.identity_access.api_models import (
    AgentUserCreateRequest,
    AgentUserCreateResult,
    AgentUserStatus,
)
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
    def __init__(
        self,
        session_factory: SessionFactory,
        id_allocator: Callable[[], str] | None = None,
    ) -> None:
        self.identity_owner = IdentityRepository(
            session_factory,
            id_allocator=id_allocator or (lambda: uuid4().hex),
        )
        self.project_owner = ProjectAclRepository(session_factory)
        self._buffer: ContextVar[_AgentMutationBuffer | None] = ContextVar(
            f"atlas_postgres_agent_buffer_{id(self)}",
            default=None,
        )

    def create_agent_once(
        self,
        actor: UserRecord,
        payload: AgentUserCreateRequest,
    ) -> AgentCreateOutcome:
        fingerprint = hashlib.sha256(
            json.dumps(
                {"display_name": payload.display_name},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        def prepare(actor_id: str) -> IdentityCreatePrepared:
            agent = UserRecord(
                actor_id=actor_id,
                display_name=payload.display_name,
                email=None,
                system_role="agent",
                password_digest=None,
                actor_type="service_account",
                created_at=utc_now_iso(),
            )
            audit = build_audit_event(
                event_type="agent_user_created",
                actor_id=actor.actor_id,
                target_ref=f"agent:{actor_id}",
                project_id=None,
                message_code="agent.user_is_ready_for_token_issue",
                metadata={"agent_actor_id": actor_id},
            )
            result = AgentUserCreateResult(
                request_id=payload.idempotency_key,
                status="applied",
                agent=AgentUserStatus(
                    actor_id=agent.actor_id,
                    actor_type="service_account",
                    display_name=agent.display_name,
                    status="active",
                    tokens=[],
                    project_grants=[],
                ),
                message_code="agent.user_is_ready_for_token_issue",
                audit_event_ref=audit.event_id,
            )
            return IdentityCreatePrepared(
                target_ref=f"agent:{actor_id}",
                response_json=result.model_dump_json(),
                change_set=IdentitySessionChangeSet(
                    users=(agent,),
                    expected_users=((actor_id, None),),
                    audit_events=(audit,),
                    authorization_actor_id=actor.actor_id,
                    authorization_requires_system_admin=True,
                ),
            )

        try:
            receipt = self.identity_owner.create_once(
                scope_actor_id=actor.actor_id,
                operation="create_agent",
                idempotency_key=payload.idempotency_key,
                request_fingerprint=fingerprint,
                target_prefix="agent-",
                prepare=prepare,
            )
        except IdentityCreateReplayConflict as exc:
            raise AgentAccessError(
                "idempotency_conflict",
                "agent.already_exists",
                409,
                request_id=payload.idempotency_key,
            ) from exc
        except IdentityAuthorizationConflict as exc:
            raise AgentAccessError(
                "access_denied",
                "permission.admin_permission_is_required",
                403,
                request_id=payload.idempotency_key,
            ) from exc
        except IdentityCurrentnessConflict as exc:
            raise AgentAccessError(
                "admin_action_rejected",
                "agent.already_exists",
                409,
                request_id=payload.idempotency_key,
            ) from exc
        return AgentCreateOutcome(
            AgentUserCreateResult.model_validate_json(receipt.response_json),
            200 if receipt.replayed else 201,
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






ResearchSnapshotBuilder = Callable[
    [
        Session,
        str,
        tuple[str, ...],
        tuple[AgentResearchScopeRefV1, ...],
        str,
        str,
        StartAgentResearchV1,
    ],
    AcceptedResearchSnapshotV1 | None,
]


@dataclass(frozen=True, slots=True)
class PostgresAgentResearchAuthority:
    """Authenticate once and fail closed while expanding a complete current scope."""

    session_factory: SessionFactory
    snapshot_builder: ResearchSnapshotBuilder
    failure_fencer: Callable[[str], None]

    def identify_replay_actor(self, *, raw_token: str | None) -> str | None:
        """Resolve only the immutable token owner for an accepted exact replay."""

        if not raw_token:
            return None
        digest = agent_token_digest(raw_token)
        with self.session_factory() as session:
            actor_ids = session.scalars(
                select(AtlasAgentTokenRow.actor_id)
                .where(AtlasAgentTokenRow.token_digest == digest)
                .order_by(AtlasAgentTokenRow.token_id)
                .limit(2)
            ).all()
            return actor_ids[0] if len(actor_ids) == 1 else None


    def _authorize_research_in_session(
        self,
        session: Session,
        *,
        raw_token: str | None,
        payload: StartAgentResearchV1,
        research_id: str,
        execution_id: str,
    ) -> AgentResearchAuthorizationV1:
        if not raw_token:
            return AgentResearchAuthorizationV1("invalid_token")
        digest = agent_token_digest(raw_token)
        token_rows = session.scalars(
            select(AtlasAgentTokenRow)
            .where(AtlasAgentTokenRow.token_digest == digest)
            .order_by(AtlasAgentTokenRow.token_id)
            .limit(2)
        ).all()
        if len(token_rows) != 1:
            return AgentResearchAuthorizationV1("invalid_token")
        token = _token_record(token_rows[0])
        actor = session.scalar(
            select(AtlasUserRow)
            .where(AtlasUserRow.actor_id == token.actor_id)
            .with_for_update()
        )
        if (
            actor is None
            or actor.actor_type != "service_account"
            or not actor.active
        ):
            return AgentResearchAuthorizationV1(
                "invalid_agent",
                actor_id=token.actor_id,
                token_fingerprint=token.token_fingerprint,
            )
        if token.status != "active":
            return AgentResearchAuthorizationV1(
                "revoked",
                actor_id=token.actor_id,
                token_id=token.token_id,
                token_fingerprint=token.token_fingerprint,
            )

        requested_refs = (
            ()
            if payload.scope.mode == "all_authorized"
            else tuple(payload.scope.refs)
        )
        candidates: set[str] = set()
        candidate_groups: list[set[str]] = []
        invalid = False
        if payload.scope.mode == "all_authorized":
            candidates.update(
                session.scalars(
                    select(AtlasProjectRow.project_id).where(
                        AtlasProjectRow.status == "active"
                    )
                ).all()
            )
        else:
            for ref in payload.scope.refs:
                if ref.kind == "project":
                    project_id = session.scalar(
                        select(AtlasProjectRow.project_id).where(
                            AtlasProjectRow.project_id == ref.id,
                            AtlasProjectRow.status == "active",
                        )
                    )
                    group = set() if project_id is None else {project_id}
                    invalid |= not group
                    candidate_groups.append(group)
                    candidates.update(group)
                    continue
                team = session.scalar(
                    select(AtlasTeamRow).where(
                        AtlasTeamRow.team_id == ref.id,
                        AtlasTeamRow.status == "active",
                    )
                )
                membership = session.scalar(
                    select(AtlasTeamMembershipRow.membership_id).where(
                        AtlasTeamMembershipRow.team_id == ref.id,
                        AtlasTeamMembershipRow.member_actor_type
                        == "service_account",
                        AtlasTeamMembershipRow.member_actor_id == token.actor_id,
                        AtlasTeamMembershipRow.status == "active",
                    )
                )
                if team is None or membership is None:
                    invalid = True
                    candidate_groups.append(set())
                    continue
                hierarchy: set[str] = set()
                while team is not None:
                    if team.team_id in hierarchy:
                        invalid = True
                        break
                    hierarchy.add(team.team_id)
                    parent_team_id = team.parent_team_id
                    if parent_team_id is None:
                        break
                    team = session.scalar(
                        select(AtlasTeamRow).where(
                            AtlasTeamRow.team_id == parent_team_id,
                            AtlasTeamRow.status == "active",
                        )
                    )
                    if team is None:
                        invalid = True
                        break
                group = set(
                    session.scalars(
                        select(AtlasPermissionGrantRow.project_id).where(
                            AtlasPermissionGrantRow.subject_type == "team",
                            AtlasPermissionGrantRow.subject_id.in_(hierarchy),
                            AtlasPermissionGrantRow.status == "active",
                        )
                    ).all()
                )
                invalid |= not group
                candidate_groups.append(group)
                candidates.update(group)

        allowed_projects: set[str] = set()
        for project_id in sorted(candidates):
            decision = ActionAwareAclAuthority.resolve_in_session(
                session,
                actor_type="service_account",
                actor_id=token.actor_id,
                project_id=project_id,
                action="agent_query",
                lock_rows=True,
            )
            AccessDecisionWriter(session).append(decision)
            if decision.allowed:
                allowed_projects.add(project_id)
        if payload.scope.mode == "selected":
            authorized_projects: set[str] = set()
            for group in candidate_groups:
                authorized_group = group & allowed_projects
                invalid |= not authorized_group
                authorized_projects.update(authorized_group)
            authorized = sorted(authorized_projects)
        else:
            authorized = sorted(allowed_projects)
        if invalid or not authorized:
            return AgentResearchAuthorizationV1(
                "denied",
                actor_id=token.actor_id,
                token_id=token.token_id,
                token_fingerprint=token.token_fingerprint,
            )
        snapshot = self.snapshot_builder(
            session,
            token.actor_id,
            tuple(authorized),
            requested_refs,
            research_id,
            execution_id,
            payload,
        )
        if snapshot is None:
            return AgentResearchAuthorizationV1(
                "denied",
                actor_id=token.actor_id,
                token_id=token.token_id,
                token_fingerprint=token.token_fingerprint,
            )
        if snapshot.scope.project_ids != authorized:
            raise ValueError(
                "snapshot builder changed the authorized canonical project set"
            )
        return AgentResearchAuthorizationV1(
            "allowed",
            actor_id=token.actor_id,
            token_id=token.token_id,
            token_fingerprint=token.token_fingerprint,
            snapshot=snapshot,
        )

    def accept_research(
        self,
        *,
        raw_token: str | None,
        payload: StartAgentResearchV1,
        research_id: str,
        execution_id: str,
        request_digest: str,
    ) -> AgentResearchAcceptanceV1:
        provisioned_actor_id: str | None = None
        try:
            with self.session_factory() as session, session.begin():
                session.execute(
                    text(
                        "LOCK TABLE atlas_agent_tokens, atlas_users, atlas_teams, "
                        "atlas_team_memberships, atlas_permission_grants, atlas_projects "
                        "IN SHARE MODE"
                    )
                )
                replay_actor_id: str | None = None
                if raw_token:
                    token_digest = agent_token_digest(raw_token)
                    replay_actor_ids = session.scalars(
                        select(AtlasAgentTokenRow.actor_id)
                        .where(AtlasAgentTokenRow.token_digest == token_digest)
                        .order_by(AtlasAgentTokenRow.token_id)
                        .limit(2)
                    ).all()
                    if len(replay_actor_ids) == 1:
                        replay_actor_id = replay_actor_ids[0]
                if replay_actor_id is not None:
                    acquire_owner_locks(
                        session,
                        identity_keys=(
                            f"agent-research:idempotency:{replay_actor_id}:"
                            f"{payload.idempotency_key}",
                        ),
                    )
                    replay_row = session.scalar(
                        select(AtlasAgentResearchRow).where(
                            AtlasAgentResearchRow.actor_id == replay_actor_id,
                            AtlasAgentResearchRow.idempotency_key
                            == payload.idempotency_key,
                        )
                    )
                    if replay_row is not None:
                        replay = _research_record(replay_row)
                        if replay.request_digest != request_digest:
                            raise AgentResearchReplayConflict(
                                "research replay payload conflicts with the original"
                            )
                        return AgentResearchAcceptanceV1(
                            authorization=AgentResearchAuthorizationV1(
                                "allowed",
                                actor_id=replay_actor_id,
                                snapshot=replay.snapshot,
                            ),
                            record=replay,
                            replayed=True,
                        )
                authorization = self._authorize_research_in_session(
                    session,
                    raw_token=raw_token,
                    payload=payload,
                    research_id=research_id,
                    execution_id=execution_id,
                )
                if authorization.status != "allowed":
                    return AgentResearchAcceptanceV1(authorization=authorization)
                assert authorization.actor_id is not None
                assert authorization.snapshot is not None
                provisioned_actor_id = authorization.actor_id
                record, replayed = _create_accepted_in_session(
                    session,
                    CreateAcceptedAgentResearchV1(
                        research_id=research_id,
                        execution_id=execution_id,
                        actor_id=authorization.actor_id,
                        idempotency_key=payload.idempotency_key,
                        request_digest=request_digest,
                        question_ref=f"agent-research-question:{research_id}",
                        question=payload.question,
                        output_mode=payload.output_mode,
                        snapshot=authorization.snapshot,
                    ),
                )
                return AgentResearchAcceptanceV1(
                    authorization=authorization,
                    record=record,
                    replayed=replayed,
                )
        except Exception:
            if provisioned_actor_id is None:
                raise
            committed = self._resolve_committed_acceptance(
                actor_id=provisioned_actor_id,
                idempotency_key=payload.idempotency_key,
            )
            if committed is not None and committed.request_digest == request_digest:
                return AgentResearchAcceptanceV1(
                    authorization=AgentResearchAuthorizationV1(
                        "allowed",
                        actor_id=committed.actor_id,
                        snapshot=committed.snapshot,
                    ),
                    record=committed,
                    replayed=True,
                )
            self.failure_fencer(execution_id)
            raise

    def _resolve_committed_acceptance(
        self, *, actor_id: str, idempotency_key: str
    ) -> AgentResearchRecordV1 | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasAgentResearchRow).where(
                    AtlasAgentResearchRow.actor_id == actor_id,
                    AtlasAgentResearchRow.idempotency_key == idempotency_key,
                )
            )
            return None if row is None else _research_record(row)


_ACCEPTED_SNAPSHOT_FIELDS = frozenset(
    {
        "scope",
        "grant_ref",
        "grant_digest",
        "catalog_ref",
        "catalog_digest",
        "policy_ref",
        "policy_digest",
        "budget_ref",
        "budget_digest",
    }
)
_PACKET_FIELDS = frozenset(
    {
        "schema_version",
        "research_id",
        "execution_id",
        "question_ref",
        "scope_ref",
        "scope_digest",
        "findings",
        "unresolved_questions",
        "research_limits",
        "evidence",
        "packet_digest",
    }
)


def _research_record(row: AtlasAgentResearchRow) -> AgentResearchRecordV1:
    packet = (
        None
        if row.packet_payload is None
        else ResearchPacketV1.model_validate(row.packet_payload)
    )
    return AgentResearchRecordV1(
        research_id=row.research_id,
        execution_id=row.execution_id,
        actor_id=row.actor_id,
        idempotency_key=row.idempotency_key,
        request_digest=row.request_digest,
        question_ref=row.question_ref,
        question=row.question,
        output_mode=row.output_mode,
        snapshot=AcceptedResearchSnapshotV1.model_validate(row.accepted_snapshot),
        status=row.status,
        packet=packet,
        packet_ref=row.packet_ref,
        packet_digest=row.packet_digest,
        accepted_at=row.accepted_at,
        completed_at=row.completed_at,
    )


def _create_accepted_in_session(
    session: Session,
    command: CreateAcceptedAgentResearchV1,
) -> tuple[AgentResearchRecordV1, bool]:
    snapshot_payload = validate_typed_payload(
        command.snapshot.model_dump(mode="json"),
        family="agent_research_accepted_snapshot_v1",
        allowed_fields=_ACCEPTED_SNAPSHOT_FIELDS,
    )
    inserted = session.scalar(
        insert(AtlasAgentResearchRow)
        .values(
            research_id=command.research_id,
            execution_id=command.execution_id,
            actor_id=command.actor_id,
            idempotency_key=command.idempotency_key,
            request_digest=command.request_digest,
            question_ref=command.question_ref,
            question=command.question,
            output_mode=command.output_mode,
            accepted_snapshot=snapshot_payload,
            status="accepted",
            packet_payload=None,
            packet_ref=None,
            packet_digest=None,
            accepted_at=func.clock_timestamp(),
            completed_at=None,
        )
        .on_conflict_do_nothing()
        .returning(AtlasAgentResearchRow.research_id)
    )
    if inserted is not None:
        session.flush()
        row = session.get(AtlasAgentResearchRow, command.research_id)
        assert row is not None
        return _research_record(row), False
    replay = session.scalar(
        select(AtlasAgentResearchRow).where(
            AtlasAgentResearchRow.actor_id == command.actor_id,
            AtlasAgentResearchRow.idempotency_key == command.idempotency_key,
        )
    )
    if replay is None or replay.request_digest != command.request_digest:
        raise AgentResearchReplayConflict(
            "research replay payload conflicts with the original"
        )
    return _research_record(replay), True


def _lock_research_packet_in_session(
    session: Session,
    *,
    research_id: str,
    execution_id: str,
    packet_ref: str,
    packet: ResearchPacketV1,
) -> AgentResearchRecordV1:
    if (
        not packet_ref
        or packet.research_id != research_id
        or packet.execution_id != execution_id
    ):
        raise AgentResearchTerminalConflict(
            "packet identity does not match accepted research"
        )
    packet_payload = validate_typed_payload(
        packet.model_dump(mode="json"),
        family="research_packet_v1",
        allowed_fields=_PACKET_FIELDS,
        max_bytes=2_097_152,
    )
    accepted = session.scalar(
        select(AtlasAgentResearchRow)
        .where(
            AtlasAgentResearchRow.research_id == research_id,
            AtlasAgentResearchRow.execution_id == execution_id,
        )
        .with_for_update()
    )
    if accepted is None:
        raise AgentResearchTerminalConflict(
            "accepted research identity does not exist"
        )
    snapshot = AcceptedResearchSnapshotV1.model_validate(
        accepted.accepted_snapshot
    )
    if (
        packet.question_ref != accepted.question_ref
        or packet.scope_ref != snapshot.scope.scope_ref
        or packet.scope_digest != snapshot.scope.scope_digest
    ):
        raise AgentResearchTerminalConflict(
            "packet question or scope does not match the immutable acceptance snapshot"
        )
    if accepted.status == "completed":
        if (
            accepted.packet_ref != packet_ref
            or accepted.packet_digest != packet.packet_digest
            or accepted.packet_payload != packet_payload
        ):
            raise AgentResearchTerminalConflict(
                "terminal research packet is immutable"
            )
    elif accepted.status != "accepted" or any(
        value is not None
        for value in (
            accepted.packet_payload,
            accepted.packet_ref,
            accepted.packet_digest,
            accepted.completed_at,
        )
    ):
        raise AgentResearchTerminalConflict(
            "research packet state is not publishable"
        )
    return _research_record(accepted)


def _attach_research_packet_in_session(
    session: Session,
    *,
    research_id: str,
    execution_id: str,
    packet_ref: str,
    packet: ResearchPacketV1,
) -> AgentResearchRecordV1:
    existing = _lock_research_packet_in_session(
        session,
        research_id=research_id,
        execution_id=execution_id,
        packet_ref=packet_ref,
        packet=packet,
    )
    if existing.status == "completed":
        return existing
    packet_payload = validate_typed_payload(
        packet.model_dump(mode="json"),
        family="research_packet_v1",
        allowed_fields=_PACKET_FIELDS,
        max_bytes=2_097_152,
    )
    changed = session.scalar(
        update(AtlasAgentResearchRow)
        .where(
            AtlasAgentResearchRow.research_id == research_id,
            AtlasAgentResearchRow.execution_id == execution_id,
            AtlasAgentResearchRow.status == "accepted",
            AtlasAgentResearchRow.packet_payload.is_(None),
        )
        .values(
            status="completed",
            packet_payload=packet_payload,
            packet_ref=packet_ref,
            packet_digest=packet.packet_digest,
            completed_at=func.clock_timestamp(),
        )
        .returning(AtlasAgentResearchRow)
    )
    if changed is None:
        raise AgentResearchTerminalConflict(
            "research packet publication lost its accepted state"
        )
    return _research_record(changed)


@dataclass(frozen=True, slots=True)
class PostgresAgentResearchStore:
    """Persist the sole accepted research record and its terminal packet."""

    session_factory: SessionFactory

    def find(self, research_id: str) -> AgentResearchRecordV1 | None:
        if not research_id:
            raise ValueError("research_id must be non-empty")
        with self.session_factory() as session:
            row = session.get(AtlasAgentResearchRow, research_id)
            return None if row is None else _research_record(row)

    def find_replay(
        self, *, actor_id: str, idempotency_key: str
    ) -> AgentResearchRecordV1 | None:
        if not actor_id or not idempotency_key:
            raise ValueError("replay identity must be non-empty")
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasAgentResearchRow).where(
                    AtlasAgentResearchRow.actor_id == actor_id,
                    AtlasAgentResearchRow.idempotency_key == idempotency_key,
                )
            )
            return None if row is None else _research_record(row)

    def list_audit_summaries(
        self,
        *,
        after: tuple[datetime, str] | None,
        upper: tuple[datetime, str] | None,
        limit: int,
    ) -> list[AgentResearchAuditSummaryV1]:
        if limit < 1 or limit > 101:
            raise ValueError("research audit limit must be between 1 and 101")
        statement = select(
            AtlasAgentResearchRow.research_id,
            AtlasAgentResearchRow.execution_id,
            AtlasAgentResearchRow.actor_id,
            AtlasAgentResearchRow.output_mode,
            AtlasAgentResearchRow.status,
            AtlasAgentResearchRow.accepted_at,
            AtlasAgentResearchRow.completed_at,
        )
        if upper is not None:
            accepted_at, research_id = upper
            statement = statement.where(
                or_(
                    AtlasAgentResearchRow.accepted_at < accepted_at,
                    and_(
                        AtlasAgentResearchRow.accepted_at == accepted_at,
                        AtlasAgentResearchRow.research_id <= research_id,
                    ),
                )
            )
        if after is not None:
            accepted_at, research_id = after
            statement = statement.where(
                or_(
                    AtlasAgentResearchRow.accepted_at < accepted_at,
                    and_(
                        AtlasAgentResearchRow.accepted_at == accepted_at,
                        AtlasAgentResearchRow.research_id < research_id,
                    ),
                )
            )
        statement = statement.order_by(
            AtlasAgentResearchRow.accepted_at.desc(),
            AtlasAgentResearchRow.research_id.desc(),
        ).limit(limit)
        with self.session_factory() as session:
            rows = session.execute(statement).all()
        return [
            AgentResearchAuditSummaryV1(
                research_id=row.research_id,
                execution_id=row.execution_id,
                actor_id=row.actor_id,
                output_mode=row.output_mode,
                status=row.status,
                accepted_at=row.accepted_at,
                completed_at=row.completed_at,
            )
            for row in rows
        ]




def build_postgres_agent_access(
    session_factory: SessionFactory,
) -> AgentAccessService:
    return AgentAccessService(PostgresAgentAccessRepository(session_factory))


__all__ = [
    "PostgresAgentAccessRepository",
    "PostgresAgentResearchAuthority",
    "PostgresAgentResearchStore",
    "build_postgres_agent_access",
]
