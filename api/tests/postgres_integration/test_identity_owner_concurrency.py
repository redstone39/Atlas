from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserInviteRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityCurrentnessConflict,
    IdentityRepository,
    IdentityScopeAcceptanceChangeSet,
    IdentitySessionChangeSet,
    InviteTransition,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.identity_access.records import (
    TeamMembershipRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.shared.public import AuditEventRecord


def _event(event_id: str, event_type: str) -> AuditEventRecord:
    message_code = {
        "accepted": "invite.accepted_sign_in_with_your_email_and_new_password",
        "revoked": "invite.has_been_revoked",
    }[event_type]
    return AuditEventRecord(
        event_id=event_id,
        event_type=event_type,
        actor_id="user-concurrency",
        target_ref="invite:invite-concurrency",
        project_id=None,
        scope_type="team",
        scope_id="team-concurrency",
        message_code=message_code,
        metadata={"invite_id": "invite-concurrency"},
        created_at="2026-07-17T00:02:00+00:00",
    )


def _invite(*, status: str) -> UserInviteRecord:
    return UserInviteRecord(
        invite_id="invite-concurrency",
        actor_id="user-concurrency",
        email="concurrency@example.test",
        display_name="Concurrent User",
        system_role="member",
        token_digest="concurrency-digest",
        token_fingerprint="concurrenc",
        status=status,
        created_at="2026-07-17T00:00:00+00:00",
        expires_at="2026-07-24T00:00:00+00:00",
        accepted_at=(
            "2026-07-17T00:02:00+00:00" if status == "accepted" else None
        ),
        revoked_at=(
            "2026-07-17T00:02:00+00:00" if status == "revoked" else None
        ),
        scope_type="team",
        scope_id="team-concurrency",
        scope_role="member",
    )


def _seed_pending_invite(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session:
        for row_type in (
            AtlasAuditEventRow,
            AtlasTeamMembershipRow,
            AtlasUserInviteRow,
            AtlasUserRow,
            AtlasTeamRow,
        ):
            session.execute(delete(row_type))
        session.add(
            AtlasTeamRow(
                team_id="team-concurrency",
                name="Concurrency Team",
                parent_team_id=None,
                status="active",
                created_at="2026-07-17T00:00:00+00:00",
                inherit_parent_documents=True,
            )
        )
        session.add(
            AtlasUserRow(
                actor_id="user-concurrency",
                display_name="Concurrent User",
                email="concurrency@example.test",
                system_role="member",
                password_digest="old-digest",
                active=False,
                actor_type="user",
                created_at="2026-07-17T00:00:00+00:00",
            )
        )
        pending = _invite(status="pending")
        session.add(
            AtlasUserInviteRow(
                invite_id=pending.invite_id,
                actor_id=pending.actor_id,
                email=pending.email,
                display_name=pending.display_name,
                system_role=pending.system_role,
                token_digest=pending.token_digest,
                token_fingerprint=pending.token_fingerprint,
                status=pending.status,
                created_at=pending.created_at,
                expires_at=pending.expires_at,
                accepted_at=None,
                revoked_at=None,
                scope_type=pending.scope_type,
                scope_id=pending.scope_id,
                scope_role=pending.scope_role,
            )
        )
        session.commit()


def test_accept_and_revoke_same_invite_serialize_without_partial_scope(
    postgres_runtime: PostgresRuntime,
) -> None:
    _seed_pending_invite(postgres_runtime)
    repository = IdentityRepository(postgres_runtime.session_factory)

    accepted = _invite(status="accepted")
    acceptance = IdentityScopeAcceptanceChangeSet(
        user=UserRecord(
            actor_id="user-concurrency",
            display_name="Concurrent User",
            email="concurrency@example.test",
            system_role="member",
            password_digest="new-digest",
            active=True,
            actor_type="user",
            created_at="2026-07-17T00:00:00+00:00",
        ),
        expected_user=UserRecord(
            actor_id="user-concurrency",
            display_name="Concurrent User",
            email="concurrency@example.test",
            system_role="member",
            password_digest="old-digest",
            active=False,
            actor_type="user",
            created_at="2026-07-17T00:00:00+00:00",
        ),
        invite=accepted,
        team_membership=TeamMembershipRecord(
            membership_id="tm-team-concurrency-user-concurrency",
            team_id="team-concurrency",
            member_actor_type="user",
            member_actor_id="user-concurrency",
            role="member",
            status="active",
            created_at="2026-07-17T00:02:00+00:00",
        ),
        audit_events=(_event("audit-accept-concurrency", "accepted"),),
    )
    revocation = IdentitySessionChangeSet(
        invite_transitions=(
            InviteTransition(
                record=_invite(status="revoked"),
                expected_status="pending",
            ),
        ),
        audit_events=(_event("audit-revoke-concurrency", "revoked"),),
    )

    def accept() -> str:
        try:
            repository.identity_scope_acceptance(acceptance)
        except IdentityCurrentnessConflict:
            return "accept-conflict"
        return "accepted"

    def revoke() -> str:
        try:
            repository.identity_session(revocation)
        except IdentityCurrentnessConflict:
            return "revoke-conflict"
        return "revoked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        acceptance_future = pool.submit(accept)
        revocation_future = pool.submit(revoke)
        results = {acceptance_future.result(), revocation_future.result()}

    assert results in (
        {"accepted", "revoke-conflict"},
        {"revoked", "accept-conflict"},
    )
    with postgres_runtime.session_factory() as session:
        invite = session.scalar(
            select(AtlasUserInviteRow).where(
                AtlasUserInviteRow.invite_id == "invite-concurrency"
            )
        )
        membership = session.scalar(
            select(AtlasTeamMembershipRow).where(
                AtlasTeamMembershipRow.membership_id
                == "tm-team-concurrency-user-concurrency"
            )
        )
        assert invite is not None
        assert (invite.status, membership is not None) in {
            ("accepted", True),
            ("revoked", False),
        }
