from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.turn_execution.public import (
    AnswerBehaviorError,
    AnswerBehaviorStatus,
    AnswerBehaviorUpdateRequest,
)
from atlas_production.modules.turn_execution.service import AnswerBehaviorService


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
ADMIN = UserRecord("admin-1", "Admin", None, "admin", None)
USER = UserRecord("user-1", "User", None, "user", None)


class Repository:
    def __init__(self) -> None:
        self.value = AnswerBehaviorStatus(
            revision=0,
            custom_guidance=None,
            guidance_digest=None,
            updated_by=None,
            updated_at=None,
            audit_event_ref=None,
        )
        self.updates = []

    def status(self) -> AnswerBehaviorStatus:
        return self.value

    def update(self, *, actor_id, payload) -> AnswerBehaviorStatus:
        self.updates.append((actor_id, payload))
        self.value = AnswerBehaviorStatus(
            revision=payload.expected_revision + 1,
            custom_guidance=payload.custom_guidance,
            guidance_digest="a" * 64,
            updated_by=actor_id,
            updated_at=NOW,
            audit_event_ref="audit-1",
        )
        return self.value


class Principal:
    def __init__(self, actor: UserRecord | None) -> None:
        self.actor = actor

    def current_user(self, _token):
        return self.actor


def _composition(
    service: AnswerBehaviorService,
    actor: UserRecord | None = ADMIN,
) -> ApiComposition:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(
        current_principal=Principal(actor),
        answer_behavior=service,
    )
    return ApiComposition(**values)


def test_answer_behavior_payload_normalizes_and_bounds_custom_guidance() -> None:
    normalized = AnswerBehaviorUpdateRequest(
        custom_guidance="  Prefer concise explanations.  ",
        expected_revision=0,
        idempotency_key="answer-behavior-1",
    )
    assert normalized.custom_guidance == "Prefer concise explanations."
    assert (
        AnswerBehaviorUpdateRequest(
            custom_guidance=" \n ",
            expected_revision=0,
            idempotency_key="answer-behavior-clear",
        ).custom_guidance
        is None
    )
    assert len(
        AnswerBehaviorUpdateRequest(
            custom_guidance="x" * 2000,
            expected_revision=0,
            idempotency_key="answer-behavior-2000",
        ).custom_guidance
        or ""
    ) == 2000
    with pytest.raises(ValidationError):
        AnswerBehaviorUpdateRequest(
            custom_guidance="x" * 2001,
            expected_revision=0,
            idempotency_key="answer-behavior-2001",
        )


@pytest.mark.parametrize("actor", [None, USER])
def test_answer_behavior_service_requires_active_system_admin(actor) -> None:
    service = AnswerBehaviorService(Repository())
    with pytest.raises(AnswerBehaviorError) as read_error:
        service.get(actor)
    with pytest.raises(AnswerBehaviorError) as update_error:
        service.update(
            actor,
            AnswerBehaviorUpdateRequest(
                custom_guidance=None,
                expected_revision=0,
                idempotency_key="denied",
            ),
        )
    assert read_error.value.status_code == 403
    assert update_error.value.status_code == 403


def test_answer_behavior_admin_routes_read_update_and_clear() -> None:
    repository = Repository()
    client = TestClient(
        create_app(_composition(AnswerBehaviorService(repository)))
    )

    empty = client.get("/api/v1/admin/answer-behavior")
    assert empty.status_code == 200
    assert empty.json() == {
        "revision": 0,
        "custom_guidance": None,
        "guidance_digest": None,
        "updated_by": None,
        "updated_at": None,
        "audit_event_ref": None,
    }

    updated = client.put(
        "/api/v1/admin/answer-behavior",
        json={
            "custom_guidance": "  Prefer concise explanations. ",
            "expected_revision": 0,
            "idempotency_key": "route-update",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert updated.json()["custom_guidance"] == "Prefer concise explanations."
    assert updated.json()["updated_by"] == "admin-1"
    assert repository.updates[0][0] == "admin-1"

    cleared = client.put(
        "/api/v1/admin/answer-behavior",
        json={
            "custom_guidance": " \n ",
            "expected_revision": 1,
            "idempotency_key": "route-clear",
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["revision"] == 2
    assert cleared.json()["custom_guidance"] is None


def test_answer_behavior_admin_route_returns_safe_acl_error() -> None:
    client = TestClient(
        create_app(
            _composition(
                AnswerBehaviorService(Repository()),
                actor=USER,
            )
        )
    )
    denied = client.get("/api/v1/admin/answer-behavior")
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "access_denied"
    assert (
        denied.json()["message_code"]
        == "permission.admin_permission_is_required"
    )
    assert denied.json()["message_params"] == {}
