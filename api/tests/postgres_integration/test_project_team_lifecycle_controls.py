from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from sqlalchemy import delete

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasSessionRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    DocumentLifecycleDenied,
    DocumentLifecycleMutationCommand,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
)
from atlas_production.shared.public import AuditEventRecord


NOW = "2026-08-18T00:00:00+00:00"


def _audit(
    event_id: str,
    *,
    actor_id: str,
    document_id: str,
    team_id: str,
    event_type: str,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event_id,
        event_type=event_type,
        actor_id=actor_id,
        target_ref=f"document:{document_id}",
        project_id=None,
        message_code=(
            "document.changing_document_status_requires_scope_admin_access"
            if event_type == "update_denied"
            else "document.settings_are_updated"
        ),
        metadata={},
        created_at=NOW,
        scope_type="team",
        scope_id=team_id,
        document_id=document_id,
    )


@pytest.mark.parametrize("authority", ("system_admin", "uploader", "team_admin"))
def test_retired_team_denies_document_lifecycle_then_reactivation_restores_authority(
    postgres_runtime: PostgresRuntime,
    authority: str,
) -> None:
    suffix = authority.replace("_", "-")
    actor_id = f"user-team-lifecycle-{suffix}"
    session_token = f"session-team-lifecycle-{suffix}"
    team_id = f"team-lifecycle-{suffix}"
    document_id = f"document-team-lifecycle-{suffix}"
    membership_id = f"membership-team-lifecycle-{suffix}"
    denial_event_id = f"audit-team-lifecycle-denied-{suffix}"
    success_event_id = f"audit-team-lifecycle-success-{suffix}"
    expected = DocumentRecord(
        document_id=document_id,
        title="Original title",
        source_digest="a" * 64,
        uploader_actor_id=actor_id if authority == "uploader" else "user-other",
        scope_type="team",
        scope_id=team_id,
        lifecycle_status="active",
    )
    updated = replace(expected, title="Updated title")
    tag = DocumentTagRecord(document_id, "team", team_id, NOW)
    denial_audit = _audit(
        denial_event_id,
        actor_id=actor_id,
        document_id=document_id,
        team_id=team_id,
        event_type="update_denied",
    )
    success_audit = _audit(
        success_event_id,
        actor_id=actor_id,
        document_id=document_id,
        team_id=team_id,
        event_type="updated",
    )

    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasTeamRow(
                team_id=team_id,
                name=f"Lifecycle {authority}",
                parent_team_id=None,
                status="retired",
                created_at=NOW,
                inherit_parent_documents=True,
            )
        )
        session.add(
            AtlasUserRow(
                actor_id=actor_id,
                display_name=authority,
                email=None,
                system_role="admin" if authority == "system_admin" else "user",
                password_digest=None,
                active=True,
                actor_type="user",
                created_at=NOW,
            )
        )
        session.add(AtlasSessionRow(session_token=session_token, actor_id=actor_id))
        if authority == "team_admin":
            session.add(
                AtlasTeamMembershipRow(
                    membership_id=membership_id,
                    team_id=team_id,
                    member_actor_type="user",
                    member_actor_id=actor_id,
                    role="admin",
                    status="active",
                    created_at=NOW,
                    removed_at=None,
                )
            )
        session.add(AtlasDocumentRow(**asdict(expected)))
        session.add(AtlasDocumentTagRow(**asdict(tag)))
        session.commit()

    command = DocumentLifecycleMutationCommand(postgres_runtime.session_factory)
    control_action = "edit" if authority == "uploader" else "admin"
    try:
        with pytest.raises(DocumentLifecycleDenied):
            command.execute(
                expected_document=expected,
                document=updated,
                tags=(tag,),
                audit_events=(success_audit,),
                presented_browser_session_token=session_token,
                expected_actor_type="user",
                expected_actor_id=actor_id,
                control_action=control_action,
                denial_audit_event=denial_audit,
            )

        with postgres_runtime.session_factory() as session:
            row = session.get(AtlasDocumentRow, document_id)
            assert row is not None and row.title == expected.title
            assert session.get(AtlasAuditEventRow, denial_event_id) is not None
            assert session.get(AtlasAuditEventRow, success_event_id) is None
            team = session.get(AtlasTeamRow, team_id)
            assert team is not None
            team.status = "active"
            session.commit()

        command.execute(
            expected_document=expected,
            document=updated,
            tags=(tag,),
            audit_events=(success_audit,),
            presented_browser_session_token=session_token,
            expected_actor_type="user",
            expected_actor_id=actor_id,
            control_action=control_action,
            denial_audit_event=denial_audit,
        )

        with postgres_runtime.session_factory() as session:
            row = session.get(AtlasDocumentRow, document_id)
            assert row is not None and row.title == updated.title
            assert session.get(AtlasAuditEventRow, success_event_id) is not None
    finally:
        with postgres_runtime.session_factory() as session:
            session.execute(
                delete(AtlasAuditEventRow).where(
                    AtlasAuditEventRow.event_id.in_((denial_event_id, success_event_id))
                )
            )
            session.execute(
                delete(AtlasDocumentTagRow).where(
                    AtlasDocumentTagRow.document_id == document_id
                )
            )
            session.execute(
                delete(AtlasDocumentRow).where(AtlasDocumentRow.document_id == document_id)
            )
            session.execute(
                delete(AtlasSessionRow).where(AtlasSessionRow.session_token == session_token)
            )
            session.execute(
                delete(AtlasTeamMembershipRow).where(
                    AtlasTeamMembershipRow.membership_id == membership_id
                )
            )
            session.execute(delete(AtlasTeamRow).where(AtlasTeamRow.team_id == team_id))
            session.execute(delete(AtlasUserRow).where(AtlasUserRow.actor_id == actor_id))
            session.commit()
