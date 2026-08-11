from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.persistence.audit_events import (
    _audit_metadata_payload,
)
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.modules.audit.public import (
    AdminAuditEventReadService,
    AuditEventList,
    AuditEventReadError,
)
from atlas_production.modules.identity_access.records import UserRecord


ADMIN = UserRecord("admin-1", "Admin", None, "admin", None)
USER = UserRecord("user-1", "User", None, "user", None)


class Principal:
    def __init__(self, users_by_token: dict[str, UserRecord] | None = None) -> None:
        self.users_by_token = users_by_token or {}

    def current_user(self, token: str | None) -> UserRecord | None:
        return self.users_by_token.get(token or "")


class RecordingAuditReader:
    def __init__(self, call_log: list[str] | None = None) -> None:
        self.call_log = call_log if call_log is not None else []
        self.limits: list[int] = []

    def recent_events(self, *, limit: int = 50):
        self.call_log.append("read")
        self.limits.append(limit)
        return [
            build_audit_event(
                event_type="existing_event",
                actor_id="actor-2",
                target_ref="target:2",
                project_id=None,
                message_code="audit.admin_listed_conversation_history",
                metadata={"admin_global_history_access": True},
            )
        ]


class RecordingAuditWriter:
    def __init__(self, call_log: list[str] | None = None) -> None:
        self.call_log = call_log if call_log is not None else []
        self.calls: list[tuple[str, dict[str, object]]] = []

    def append_read_audit(self, event_type: str, **facts: object) -> object:
        self.call_log.append("audit")
        metadata = facts.get("metadata")
        assert isinstance(metadata, dict)
        _audit_metadata_payload(metadata)
        build_audit_event(
            event_type=event_type,
            actor_id=facts.get("actor_id"),
            target_ref=facts.get("target_ref"),
            project_id=None,
            message_code=facts["message_code"],
            metadata=metadata,
        )
        self.calls.append((event_type, facts))
        return object()


class RaisingAuditWriter(RecordingAuditWriter):
    def append_read_audit(self, event_type: str, **facts: object) -> object:
        self.call_log.append("audit")
        raise RuntimeError("forced audit failure")


@dataclass
class RecordingAdminAuditEventService:
    result: AuditEventList

    def __post_init__(self) -> None:
        self.calls: list[tuple[UserRecord | None, int]] = []

    def list_admin(
        self,
        actor: UserRecord | None,
        *,
        limit: int = 50,
    ) -> AuditEventList:
        self.calls.append((actor, limit))
        return self.result


def _composition(
    service: object,
    principal: Principal | None = None,
) -> ApiComposition:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(
        current_principal=principal or Principal(),
        admin_audit_events=service,
    )
    return ApiComposition(**values)


def test_admin_audit_events_audits_before_bounded_read() -> None:
    call_log: list[str] = []
    reader = RecordingAuditReader(call_log)
    writer = RecordingAuditWriter(call_log)
    service = AdminAuditEventReadService(reader, writer)

    result = service.list_admin(ADMIN, limit=50)

    assert isinstance(result, AuditEventList)
    assert len(result.events) == 1
    assert call_log == ["audit", "read"]
    assert reader.limits == [50]
    assert writer.calls == [
        (
            "read_audit_events",
            {
                "actor_id": "admin-1",
                "target_ref": "audit-events:*",
                "message_code": "audit.admin_listed_audit_events",
                "metadata": {"admin_global_history_access": True},
            },
        )
    ]
    assert not (
        {"session_token", "request", "response", "returned_event", "event_metadata"}
        & writer.calls[0][1].keys()
    )


@pytest.mark.parametrize(
    "actor",
    [USER, UserRecord("admin-2", "Inactive", None, "admin", None, active=False)],
)
def test_admin_audit_events_rejects_non_admin_before_audit_or_read(
    actor: UserRecord,
) -> None:
    reader = RecordingAuditReader()
    writer = RecordingAuditWriter()
    service = AdminAuditEventReadService(reader, writer)

    with pytest.raises(AuditEventReadError) as error:
        service.list_admin(actor)

    assert error.value.error_code == "access_denied"
    assert error.value.status_code == 403
    assert writer.calls == []
    assert reader.limits == []


def test_admin_audit_events_rejects_tampered_session_before_audit_or_read() -> None:
    reader = RecordingAuditReader()
    writer = RecordingAuditWriter()
    service = AdminAuditEventReadService(reader, writer)
    client = TestClient(create_app(_composition(service, Principal())))

    client.cookies.set("atlas_session", "tampered-session")
    response = client.get("/api/v1/admin/audit/events")

    assert response.status_code == 401
    payload = response.json()
    assert payload["error_code"] == "unauthenticated"
    assert (
        payload["message_code"]
        == "auth.please_sign_in_before_using_admin_tools"
    )
    assert payload["message_params"] == {}
    assert writer.calls == []
    assert reader.limits == []


def test_admin_audit_events_audit_write_failure_fails_closed_before_read() -> None:
    reader = RecordingAuditReader()
    writer = RaisingAuditWriter()
    service = AdminAuditEventReadService(reader, writer)

    with pytest.raises(RuntimeError, match="forced audit failure"):
        service.list_admin(ADMIN)

    assert writer.call_log == ["audit"]
    assert reader.limits == []


def test_admin_audit_events_route_uses_named_application_service() -> None:
    result = AuditEventList(events=[])
    service = RecordingAdminAuditEventService(result)
    client = TestClient(
        create_app(
            _composition(
                service,
                Principal({"valid-admin-session": ADMIN}),
            )
        )
    )

    client.cookies.set("atlas_session", "valid-admin-session")
    response = client.get("/api/v1/admin/audit/events")

    assert response.status_code == 200
    assert response.json() == result.model_dump()
    assert service.calls == [(ADMIN, 50)]
