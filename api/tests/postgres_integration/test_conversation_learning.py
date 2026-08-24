from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from atlas_production.infrastructure.conversation_review_reconciler import (
    ConversationReviewReconciler,
)
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import AtlasUserRow
from atlas_production.infrastructure.postgres_owner.conversation_review import (
    PostgresConversationReviewOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.conversation_review.public import (
    ConversationLearningSettingsError,
    ConversationLearningSettingsUpdateRequestV1,
)


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)
ACTOR_ID = "actor-public-synthetic-learning-admin"


class ForbiddenIo:
    def __getattr__(self, name: str):
        raise AssertionError(f"enabled learning attempted {name}")


def _update(
    *, enabled: bool, revision: int, idempotency_key: str
) -> ConversationLearningSettingsUpdateRequestV1:
    return ConversationLearningSettingsUpdateRequestV1(
        enabled=enabled,
        expected_settings_revision=revision,
        idempotency_key=idempotency_key,
    )


def test_learning_settings_owner_enforces_transactional_admin_cas_and_admission(
    postgres_runtime: PostgresRuntime,
) -> None:
    postgres_runtime.bootstrap_schema()
    with postgres_runtime.session_factory() as session, session.begin():
        session.add(
            AtlasUserRow(
                actor_id=ACTOR_ID,
                display_name="Public Synthetic Learning Admin",
                email="learning-admin@example.test",
                system_role="admin",
                password_digest=None,
                active=True,
                actor_type="user",
                created_at=NOW.isoformat(),
            )
        )

    owner = PostgresConversationReviewOwner(postgres_runtime.session_factory)
    initial = owner.get_learning_settings()
    assert initial.enabled is True
    assert initial.settings_revision == 1

    disable = _update(
        enabled=False,
        revision=initial.settings_revision,
        idempotency_key="public-synthetic-learning-disable",
    )
    disabled = owner.update_learning_settings(ACTOR_ID, disable)
    replay = owner.update_learning_settings(ACTOR_ID, disable)
    assert replay == disabled
    assert disabled.enabled is False
    assert disabled.settings_revision == 2

    with postgres_runtime.session_factory() as session:
        learning_audits = session.scalars(
            select(AtlasAuditEventRow).where(
                AtlasAuditEventRow.event_type
                == "conversation_learning_settings_updated"
            )
        ).all()
    assert len(learning_audits) == 1

    with pytest.raises(ConversationLearningSettingsError) as reused:
        owner.update_learning_settings(
            ACTOR_ID,
            _update(
                enabled=True,
                revision=disabled.settings_revision,
                idempotency_key=disable.idempotency_key,
            ),
        )
    assert reused.value.error_code == "conversation_learning_settings_idempotency_conflict"
    assert reused.value.status_code == 409

    with pytest.raises(ConversationLearningSettingsError) as stale:
        owner.update_learning_settings(
            ACTOR_ID,
            _update(
                enabled=True,
                revision=initial.settings_revision,
                idempotency_key="public-synthetic-learning-stale",
            ),
        )
    assert stale.value.error_code == "conversation_learning_settings_revision_conflict"
    assert stale.value.status_code == 409

    with postgres_runtime.session_factory() as session, session.begin():
        actor = session.get(AtlasUserRow, ACTOR_ID)
        assert actor is not None
        actor.active = False
    with pytest.raises(ConversationLearningSettingsError) as revoked:
        owner.update_learning_settings(
            ACTOR_ID,
            _update(
                enabled=True,
                revision=disabled.settings_revision,
                idempotency_key="public-synthetic-learning-revoked",
            ),
        )
    assert revoked.value.error_code == "access_denied"
    assert revoked.value.status_code == 403
    assert owner.get_learning_settings() == disabled

    reconciler = ConversationReviewReconciler(
        conversations=ForbiddenIo(),
        source=ForbiddenIo(),
        reviews=owner,
        reviewer=ForbiddenIo(),
        publication=ForbiddenIo(),
        clock=lambda: NOW,
    )
    assert reconciler.run_once() == 0

    with postgres_runtime.session_factory() as session, session.begin():
        actor = session.get(AtlasUserRow, ACTOR_ID)
        assert actor is not None
        actor.active = True
        actor.system_role = "admin"
    reenabled = owner.update_learning_settings(
        ACTOR_ID,
        _update(
            enabled=True,
            revision=disabled.settings_revision,
            idempotency_key="public-synthetic-learning-reenable",
        ),
    )
    assert reenabled.enabled is True
    assert reenabled.settings_revision == 3
    with pytest.raises(AssertionError, match="list_active_updated_before"):
        reconciler.run_once()
