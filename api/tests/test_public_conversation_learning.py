from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.conversation_review_reconciler import (
    ConversationReviewReconciler,
)
from atlas_production.infrastructure.learner_reconciler import LearnerReconciler
from atlas_production.modules.conversation_review.public import (
    ConversationLearningSettingsError,
    ConversationLearningSettingsService,
    ConversationLearningSettingsUpdateRequestV1,
    ConversationLearningSettingsV1,
)
from atlas_production.modules.identity_access.records import UserRecord


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


class SettingsOwner:
    def __init__(self, *, enabled: bool = True, fail_read: bool = False) -> None:
        self.settings = ConversationLearningSettingsV1(
            enabled=enabled,
            settings_revision=1,
            updated_actor_id="actor-public-synthetic-admin",
            updated_at=NOW,
        )
        self.fail_read = fail_read
        self.reads = 0
        self.updates: list[tuple[str, ConversationLearningSettingsUpdateRequestV1]] = []

    def get_learning_settings(self) -> ConversationLearningSettingsV1:
        self.reads += 1
        if self.fail_read:
            raise RuntimeError("public-synthetic settings read failure")
        return self.settings

    def update_learning_settings(
        self, actor_id: str, payload: ConversationLearningSettingsUpdateRequestV1
    ) -> ConversationLearningSettingsV1:
        self.updates.append((actor_id, payload))
        return self.settings


class ForbiddenIo:
    def __getattr__(self, name: str):
        raise AssertionError(f"disabled cycle attempted {name}")


@pytest.fixture
def admin() -> UserRecord:
    return UserRecord(
        actor_id="actor-public-synthetic-admin",
        display_name="Public Synthetic Admin",
        email="admin@example.test",
        system_role="admin",
        password_digest="public-synthetic-digest",
    )


def test_admin_service_enforces_acl_and_strict_update_contract(admin: UserRecord) -> None:
    owner = SettingsOwner()
    service = ConversationLearningSettingsService(owner)
    payload = ConversationLearningSettingsUpdateRequestV1(
        enabled=False,
        expected_settings_revision=1,
        idempotency_key="public-synthetic-learning-update",
    )

    assert service.get(admin).settings_revision == 1
    assert service.update(admin, payload).enabled is True
    assert owner.updates == [(admin.actor_id, payload)]

    member = UserRecord(
        actor_id="actor-public-synthetic-member",
        display_name="Public Synthetic Member",
        email="member@example.test",
        system_role="member",
        password_digest="public-synthetic-digest",
    )
    for actor in (None, member, admin.__class__(**{**admin.__dict__, "active": False})):
        with pytest.raises(ConversationLearningSettingsError) as error:
            service.get(actor)
        assert error.value.status_code == 403
    assert owner.reads == 1

    with pytest.raises(ValidationError):
        ConversationLearningSettingsUpdateRequestV1.model_validate(
            {
                "enabled": False,
                "expected_settings_revision": 1,
                "idempotency_key": "public-synthetic-learning-update",
                "legacy_flag": True,
            }
        )


@pytest.mark.parametrize("fail_read", [False, True])
def test_review_cycle_fails_closed_before_discovery_or_claim(fail_read: bool) -> None:
    settings = SettingsOwner(enabled=False, fail_read=fail_read)
    reconciler = ConversationReviewReconciler(
        conversations=ForbiddenIo(),
        source=ForbiddenIo(),
        reviews=settings,  # type: ignore[arg-type]
        reviewer=ForbiddenIo(),
        publication=ForbiddenIo(),
        clock=lambda: NOW,
    )

    assert reconciler.run_once() == 0
    assert settings.reads == 1


@pytest.mark.parametrize("fail_read", [False, True])
def test_learner_cycle_fails_closed_before_scan_claim_or_provider(fail_read: bool) -> None:
    settings = SettingsOwner(enabled=False, fail_read=fail_read)
    reconciler = LearnerReconciler(
        reviews=settings,  # type: ignore[arg-type]
        learners=ForbiddenIo(),
        source=ForbiddenIo(),
        provider=ForbiddenIo(),
        clock=lambda: NOW,
    )

    assert reconciler.run_once() == 0
    assert settings.reads == 1
