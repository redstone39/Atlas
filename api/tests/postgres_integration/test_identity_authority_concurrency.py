from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, or_, select

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAccessDecisionRow,
    AtlasAgentTokenRow,
    AtlasPermissionGrantRow,
    AtlasSessionRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserInviteRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_agent_adapter import (
    PostgresAgentAccessRepository,
    PostgresAgentQueryAuthority,
)
from atlas_production.infrastructure.postgres_identity_adapter import (
    PostgresCurrentPrincipal,
    PostgresIdentityAccessRepository,
)
from atlas_production.infrastructure.postgres_team_adapter import (
    PostgresTeamAccessRepository,
)
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityCurrentnessConflict,
    IdentityInvariantViolation,
    IdentityRepository,
    IdentitySessionChangeSet,
    InviteTransition,
    IssueBrowserSessionCommand,
    RevokeBrowserSessionCommand,
)
from atlas_production.infrastructure.postgres_owner.team import (
    TeamGovernanceChangeSet,
    TeamInvariantViolation,
    TeamRepository,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.identity_access.records import (
    TeamMembershipRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.agent_contracts import AgentAuditCommand
from atlas_production.modules.identity_access.team_contracts import (
    TeamAccessError,
    TeamAuditCommand,
)
from atlas_production.modules.identity_access.security import agent_token_digest
from atlas_production.shared.public import AuditEventRecord


NOW = "2026-07-18T00:00:00+00:00"
EMAIL = "t011-concurrent@example.test"
PREFIX = "t011-authority"


def _user(actor_id: str, *, actor_type: str = "user") -> UserRecord:
    return UserRecord(
        actor_id, actor_id, EMAIL if actor_type == "user" else None,
        "user" if actor_type == "user" else "agent", None,
        True, actor_type, NOW,
    )


def _invite(invite_id: str, actor_id: str) -> UserInviteRecord:
    return UserInviteRecord(
        invite_id, actor_id, EMAIL, actor_id, "user",
        f"digest-{invite_id}", f"finger-{invite_id}"[:12], "pending",
        NOW, "2099-07-18T00:00:00+00:00",
    )


def _event(event_id: str, actor_id: str, target_ref: str) -> AuditEventRecord:
    return AuditEventRecord(
        event_id, "user_invite_created", actor_id, target_ref, None,
        'invite.user_invite_has_been_created', {"email": EMAIL}, NOW,
    )


def _cleanup(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session:
        session.execute(delete(AtlasAccessDecisionRow).where(
            AtlasAccessDecisionRow.actor_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasAgentTokenRow).where(
            AtlasAgentTokenRow.actor_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasSessionRow).where(
            AtlasSessionRow.actor_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasPermissionGrantRow).where(
            AtlasPermissionGrantRow.subject_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasTeamMembershipRow).where(
            AtlasTeamMembershipRow.membership_id.like(f"tm-{PREFIX}%")
        ))
        session.execute(delete(AtlasTeamRow).where(
            AtlasTeamRow.team_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasUserInviteRow).where(
            AtlasUserInviteRow.actor_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasAuditEventRow).where(
            or_(
                AtlasAuditEventRow.event_id.like(f"audit-{PREFIX}%"),
                AtlasAuditEventRow.target_ref.like(f"%{PREFIX}%"),
            )
        ))
        session.execute(delete(AtlasUserRow).where(
            AtlasUserRow.actor_id.like(f"{PREFIX}%")
        ))
        session.execute(delete(AtlasProjectRow).where(
            AtlasProjectRow.project_id == f"{PREFIX}-project"
        ))
        session.commit()


def test_same_email_invite_publication_has_one_winner(
    postgres_runtime: PostgresRuntime,
) -> None:
    _cleanup(postgres_runtime)
    owner = IdentityRepository(postgres_runtime.session_factory)
    changes = []
    for index in range(2):
        actor_id = f"{PREFIX}-user-{index}"
        invite = _invite(f"{PREFIX}-invite-{index}", actor_id)
        changes.append(IdentitySessionChangeSet(
            users=(_user(actor_id),),
            expected_users=((actor_id, None),),
            invite_transitions=(InviteTransition(invite, None),),
            audit_events=(_event(
                f"audit-{PREFIX}-invite-{index}",
                actor_id,
                f"invite:{invite.invite_id}",
            ),),
            identity_lock_keys=(f"identity-email:{EMAIL}",),
            expected_pending_invite_absent_emails=(EMAIL,),
        ))

    def publish(change_set: IdentitySessionChangeSet) -> str:
        try:
            owner.identity_session(change_set)
            return "committed"
        except IdentityCurrentnessConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, changes))
        assert sorted(results) == ["committed", "conflict"]
        with postgres_runtime.session_factory() as session:
            pending = session.scalars(select(AtlasUserInviteRow).where(
                AtlasUserInviteRow.email == EMAIL,
                AtlasUserInviteRow.status == "pending",
            )).all()
            assert len(pending) == 1
            audits = session.scalars(select(AtlasAuditEventRow).where(
                AtlasAuditEventRow.event_id.like(f"audit-{PREFIX}-invite-%")
            )).all()
            assert len(audits) == 1
    finally:
        _cleanup(postgres_runtime)


def test_identity_audit_failure_rolls_back_business_row(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cleanup(postgres_runtime)
    actor_id = f"{PREFIX}-rollback"
    change_set = IdentitySessionChangeSet(
        users=(_user(actor_id),),
        expected_users=((actor_id, None),),
        audit_events=(_event(
            f"audit-{PREFIX}-rollback", actor_id, f"user:{actor_id}"
        ),),
    )

    def fail_audit(_self, _events) -> None:
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(AuditEventWriter, "append_many", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="forced audit failure"):
            IdentityRepository(postgres_runtime.session_factory).identity_session(
                change_set
            )
        with postgres_runtime.session_factory() as session:
            assert session.get(AtlasUserRow, actor_id) is None
            assert session.get(
                AtlasAuditEventRow,
                f"audit-{PREFIX}-rollback",
            ) is None
    finally:
        _cleanup(postgres_runtime)


def test_browser_session_and_raw_agent_authority_use_current_rows(
    postgres_runtime: PostgresRuntime,
) -> None:
    _cleanup(postgres_runtime)
    human_id = f"{PREFIX}-human"
    agent_id = f"{PREFIX}-agent"
    project_id = f"{PREFIX}-project"
    raw_agent_token = "atlas_agent_t011_current"
    digest = agent_token_digest(raw_agent_token)
    with postgres_runtime.session_factory() as session:
        session.add(AtlasUserRow(
            actor_id=human_id, display_name="Human", email=EMAIL,
            system_role="admin", password_digest=None, active=True,
            actor_type="user", created_at=NOW,
        ))
        session.add(AtlasUserRow(
            actor_id=agent_id, display_name="Agent", email=None,
            system_role="agent", password_digest=None, active=True,
            actor_type="service_account", created_at=NOW,
        ))
        session.add(AtlasProjectRow(
            project_id=project_id,
            name="T011",
            policy_profile_id="policy-default",
            status="active",
        ))
        session.add(AtlasPermissionGrantRow(
            grant_id=f"grant-{PREFIX}-agent", project_id=project_id,
            subject_type="service_account", subject_id=agent_id,
            role="viewer", effect="allow", status="active",
            created_at=NOW, revoked_at=None,
        ))
        session.add(AtlasAgentTokenRow(
            token_id=f"token-{PREFIX}-agent", actor_id=agent_id,
            token_digest=digest, token_fingerprint=digest[:12],
            status="active", created_at=NOW, revoked_at=None,
        ))
        session.commit()
    session_token = f"session-{PREFIX}"
    try:
        issue = IssueBrowserSessionCommand(
            postgres_runtime.session_factory,
            token_factory=lambda: session_token,
        )
        assert issue.execute(human_id) == session_token
        identity = PostgresIdentityAccessRepository(postgres_runtime.session_factory)
        principal = PostgresCurrentPrincipal(identity)
        assert principal.current_user(session_token).actor_id == human_id  # type: ignore[union-attr]
        with postgres_runtime.session_factory() as session:
            session.get(AtlasUserRow, human_id).active = False
            session.commit()
        assert principal.current_user(session_token) is None
        with postgres_runtime.session_factory() as session:
            session.get(AtlasUserRow, human_id).active = True
            session.commit()
        revoke = RevokeBrowserSessionCommand(postgres_runtime.session_factory)
        assert revoke.execute(session_token) is True
        assert revoke.execute(session_token) is False
        assert principal.current_user(session_token) is None

        authority = PostgresAgentQueryAuthority(postgres_runtime.session_factory)
        allowed = authority.authorize(
            raw_token=raw_agent_token,
            project_id=project_id,
        )
        assert allowed.status == "allowed"
        assert allowed.access_decision_id
        with postgres_runtime.session_factory() as session:
            token = session.get(AtlasAgentTokenRow, f"token-{PREFIX}-agent")
            token.status = "revoked"
            token.revoked_at = NOW
            session.commit()
        assert authority.authorize(
            raw_token=raw_agent_token,
            project_id=project_id,
        ).status == "revoked"
    finally:
        _cleanup(postgres_runtime)


def test_user_disable_and_new_team_admin_cannot_both_commit(
    postgres_runtime: PostgresRuntime,
) -> None:
    _cleanup(postgres_runtime)
    actor_id = f"{PREFIX}-team-admin"
    team_id = f"{PREFIX}-team"
    membership_id = f"tm-{PREFIX}-team-admin"
    active = _user(actor_id)
    with postgres_runtime.session_factory() as session:
        session.add(AtlasUserRow(
            actor_id=active.actor_id, display_name=active.display_name,
            email=active.email, system_role=active.system_role,
            password_digest=None, active=True, actor_type="user",
            created_at=NOW,
        ))
        session.add(AtlasTeamRow(
            team_id=team_id, name="Race Team", parent_team_id=None,
            status="active", created_at=NOW, inherit_parent_documents=True,
        ))
        session.commit()
    membership = TeamMembershipRecord(
        membership_id, team_id, "user", actor_id, "active", NOW, role="admin",
    )
    disable = IdentitySessionChangeSet(
        users=(UserRecord(
            actor_id, actor_id, EMAIL, "user", None, False, "user", NOW,
        ),),
        expected_users=((actor_id, active),),
        audit_events=(_event(
            f"audit-{PREFIX}-disable-team-admin", actor_id, f"user:{actor_id}",
        ),),
    )
    add_admin = TeamGovernanceChangeSet(
        memberships=(membership,),
        expected_memberships=((membership_id, None),),
        audit_events=(_event(
            f"audit-{PREFIX}-add-team-admin", actor_id,
            f"team-membership:{membership_id}",
        ),),
        protected_admin_team_ids=(team_id,),
    )

    def disable_user() -> str:
        try:
            IdentityRepository(postgres_runtime.session_factory).identity_session(
                disable
            )
            return "disable-committed"
        except IdentityInvariantViolation:
            return "disable-rejected"

    def add_membership() -> str:
        try:
            TeamRepository(postgres_runtime.session_factory).team_governance(
                add_admin
            )
            return "membership-committed"
        except TeamInvariantViolation:
            return "membership-rejected"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda fn: fn(), (disable_user, add_membership)))
        assert results.count("disable-committed") + results.count(
            "membership-committed"
        ) == 1
    finally:
        _cleanup(postgres_runtime)


def test_agent_disable_after_issue_preimage_fails_token_commit(
    postgres_runtime: PostgresRuntime,
) -> None:
    _cleanup(postgres_runtime)
    agent_id = f"{PREFIX}-issue-agent"
    with postgres_runtime.session_factory() as session:
        session.add(AtlasUserRow(
            actor_id=agent_id, display_name="Issue Agent", email=None,
            system_role="agent", password_digest=None, active=True,
            actor_type="service_account", created_at=NOW,
        ))
        session.commit()
    repository = PostgresAgentAccessRepository(postgres_runtime.session_factory)
    _raw, token = repository.issue_token(agent_id)
    with postgres_runtime.session_factory() as session:
        session.get(AtlasUserRow, agent_id).active = False
        session.commit()
    try:
        with pytest.raises(IdentityCurrentnessConflict):
            repository.append_audit(AgentAuditCommand(
                "agent_token_issued", None, f"agent-token:{token.token_id}",
                'agent.token_has_been_issued_copy_it_now', {},
            ))
        with postgres_runtime.session_factory() as session:
            assert session.get(AtlasAgentTokenRow, token.token_id) is None
    finally:
        _cleanup(postgres_runtime)


def test_commit_time_team_rejection_has_durable_non_dangling_audit(
    postgres_runtime: PostgresRuntime,
) -> None:
    _cleanup(postgres_runtime)
    actor_id = f"{PREFIX}-last-admin"
    team_id = f"{PREFIX}-audit-team"
    membership_id = f"tm-{PREFIX}-last-admin"
    with postgres_runtime.session_factory() as session:
        session.add(AtlasUserRow(
            actor_id=actor_id, display_name="Last Admin", email=EMAIL,
            system_role="user", password_digest=None, active=True,
            actor_type="user", created_at=NOW,
        ))
        session.add(AtlasTeamRow(
            team_id=team_id, name="Audit Team", parent_team_id=None,
            status="active", created_at=NOW, inherit_parent_documents=True,
        ))
        session.add(AtlasTeamMembershipRow(
            membership_id=membership_id, team_id=team_id,
            member_actor_type="user", member_actor_id=actor_id, role="admin",
            status="active", created_at=NOW, removed_at=None,
        ))
        session.commit()
    repository = PostgresTeamAccessRepository(postgres_runtime.session_factory)
    try:
        with pytest.raises(TeamAccessError) as caught:
            with repository.team_mutation(team_id):
                membership = repository.get_membership(membership_id)
                assert membership is not None
                membership.status = "removed"
                membership.removed_at = NOW
                repository.put_membership(membership)
                repository.append_audit(TeamAuditCommand(
                    "team_member_removed", None,
                    f"team-membership:{membership_id}",
                    'team.member_has_been_removed', {}, "team", team_id,
                ))
        audit_ref = caught.value.audit_event_ref
        assert audit_ref is not None
        with postgres_runtime.session_factory() as session:
            membership = session.get(AtlasTeamMembershipRow, membership_id)
            assert membership is not None and membership.status == "active"
            rejection = session.get(AtlasAuditEventRow, audit_ref)
            assert rejection is not None
            assert rejection.event_type == "admin_action_rejected"
            candidates = session.scalars(select(AtlasAuditEventRow).where(
                AtlasAuditEventRow.target_ref == f"team-membership:{membership_id}",
                AtlasAuditEventRow.event_type == "team_member_removed",
            )).all()
            assert candidates == []
    finally:
        _cleanup(postgres_runtime)
