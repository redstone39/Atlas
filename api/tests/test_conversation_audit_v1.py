from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_production.infrastructure.persistence.audit_events import (
    _audit_metadata_payload,
)
from atlas_production.infrastructure.postgres_audit_adapter import (
    build_audit_event,
)
from atlas_production.modules.conversation.public import ConversationV1
from atlas_production.modules.conversation_audit.contracts import ConversationAuditError
from atlas_production.modules.conversation_audit.service import ConversationAuditService
from atlas_production.modules.citation_preview.public import (
    ProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    ExecutionState,
    RoutePolicyV1,
    RuntimeEventV1,
)
from atlas_production.modules.workspace_turn.public import (
    WorkspaceConversationDetailV1,
)
from tests.turn_runtime_fixtures import route_snapshot


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
ADMIN = UserRecord("admin-1", "Admin", None, "admin", None)


def conversation(identity: str, minute: int) -> ConversationV1:
    timestamp = NOW + timedelta(minutes=minute)
    return ConversationV1(
        conversation_id=identity,
        owner_actor_id=f"owner-{identity}",
        title=identity,
        status="active",
        response_language="zh-TW",
        created_at=timestamp,
        updated_at=timestamp,
    )


def snapshot() -> ExecutionSnapshotV1:
    budget = BudgetSnapshotV1(
        tool_invocations=2,
        catalog_pages=1,
        document_candidates=3,
        search_rounds=2,
        unique_evidence=2,
        provider_invocations=4,
        context_tokens=100,
        tool_tokens=80,
    )
    lease = ExecutionLeaseV1(
        execution_id="execution-1",
        holder_id="holder-1",
        lease_version=1,
        fencing_token=7,
        acquired_at=NOW,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(seconds=15),
    )
    return ExecutionSnapshotV1(
        execution_id="execution-1",
        turn_id="turn-1",
        conversation_id="conversation-1",
        actor_id="owner-1",
        state=ExecutionState.TERMINAL_FAILED,
        version=9,
        policy=RoutePolicyV1(),
        route=route_snapshot(),
        input_digest="0" * 64,
        response_language="zh-TW",
        applied_guidance_revision=0,
        applied_guidance_digest=None,
        lease=lease,
        budget=budget,
        terminal_failure_code="execution_carrier_lost",
        deadline_at=NOW + timedelta(minutes=2),
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=10),
    )


class AuditWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def append_read_audit(self, event_type: str, **facts: object) -> object:
        metadata = facts.get("metadata")
        assert metadata is None or isinstance(metadata, dict)
        _audit_metadata_payload(metadata)
        actor_id = facts.get("actor_id")
        target_ref = facts.get("target_ref")
        message_code = facts.get("message_code")
        assert actor_id is None or isinstance(actor_id, str)
        assert target_ref is None or isinstance(target_ref, str)
        assert isinstance(message_code, str)
        build_audit_event(
            event_type=event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=None,
            message_code=message_code,
            metadata=metadata or {},
        )
        self.calls.append((event_type, facts))
        return object()


class Workspace:
    def __init__(self) -> None:
        self.items = [conversation("conversation-3", 3), conversation("conversation-2", 2), conversation("conversation-1", 1)]
        self.detail_calls: list[tuple[str, str]] = []
        self.runtime_calls: list[tuple[str, str, str]] = []
        self.declared_calls: list[tuple[str, str, str, str]] = []

    def audit_list_conversations(self, *, actor_id: str) -> list[ConversationV1]:
        assert actor_id == "admin-1"
        return self.items

    def audit_get_conversation(self, *, actor_id: str, conversation_id: str) -> WorkspaceConversationDetailV1:
        self.detail_calls.append((actor_id, conversation_id))
        return WorkspaceConversationDetailV1(conversation=self.items[-1], turns=[])

    def audit_execution(self, *, actor_id: str, conversation_id: str, turn_id: str):
        self.runtime_calls.append((actor_id, conversation_id, turn_id))
        assert (conversation_id, turn_id) == ("conversation-1", "turn-1")
        event = RuntimeEventV1(
            event_id="event-1",
            execution_id="execution-1",
            sequence=1,
            event_type="terminal_failed",
            state=ExecutionState.TERMINAL_FAILED,
            failure_code="execution_carrier_lost",
            created_at=NOW + timedelta(seconds=10),
        )
        return snapshot(), [event], []

    def audit_read_declared_evidence(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        turn_id: str,
        protected_open_ref: str,
    ) -> ProtectedDeclaredEvidenceV1:
        self.declared_calls.append(
            (actor_id, conversation_id, turn_id, protected_open_ref)
        )
        return ProtectedDeclaredEvidenceV1(
            evidence_handle="kh_evidence_one",
            locator_label="Page 1",
            snippet="declared",
            content="declared evidence",
            modality="text",
        )


def service() -> tuple[ConversationAuditService, Workspace, AuditWriter]:
    workspace = Workspace()
    writer = AuditWriter()
    return ConversationAuditService(workspace=workspace, audit_writer=writer), workspace, writer


def test_admin_list_is_paginated_and_every_read_is_audited() -> None:
    subject, _, writer = service()

    first = subject.list_admin(ADMIN, limit=2)
    second = subject.list_admin(ADMIN, limit=2, cursor=first.next_cursor)

    assert [item.conversation_id for item in first.conversations] == ["conversation-3", "conversation-2"]
    assert [item.conversation_id for item in second.conversations] == ["conversation-1"]
    assert first.next_cursor is not None
    assert [call[0] for call in writer.calls] == ["read_conversation", "read_conversation"]


@pytest.mark.parametrize("cursor", ["not-base64", "W10", "WyJhIiwiYiIsImMiXQ"])
def test_admin_list_rejects_malformed_or_wrong_shape_cursor(cursor: str) -> None:
    subject, _, _ = service()

    with pytest.raises(ConversationAuditError) as error:
        subject.list_admin(ADMIN, cursor=cursor)

    assert error.value.error_code == "invalid_cursor"


def test_admin_detail_delegates_to_request_time_workspace_projection() -> None:
    subject, workspace, writer = service()

    result = subject.get_admin(ADMIN, "conversation-1")

    assert result.conversation.conversation_id == "conversation-1"
    assert workspace.detail_calls == [("admin-1", "conversation-1")]
    assert writer.calls[0][1]["metadata"] == {
        "admin_global_history_access": True
    }


def test_admin_runtime_returns_strict_snapshot_and_durable_events() -> None:
    subject, workspace, writer = service()

    result = subject.get_runtime(ADMIN, "conversation-1", "turn-1")

    assert result.state is ExecutionState.TERMINAL_FAILED
    assert result.failure_code == "execution_carrier_lost"
    assert result.applied_guidance_revision == 0
    assert result.applied_guidance_digest is None
    assert "response_language" not in result.model_dump()
    assert "custom_guidance" not in result.model_dump()
    assert result.budget.tool_invocations == 2
    assert result.document_discovery == []
    assert [event.event_id for event in result.events] == ["event-1"]
    assert result.created_at == NOW
    assert workspace.runtime_calls == [("admin-1", "conversation-1", "turn-1")]
    assert writer.calls[0][0] == "read_runtime_trace"


def test_admin_declared_evidence_read_is_role_checked_and_audited() -> None:
    subject, workspace, writer = service()

    result = subject.read_declared_evidence(
        ADMIN, "conversation-1", "turn-1", "declared-open-1"
    )

    assert result.content == "declared evidence"
    assert workspace.declared_calls == [
        ("admin-1", "conversation-1", "turn-1", "declared-open-1")
    ]
    assert writer.calls[0][0] == "read_declared_evidence"
    assert writer.calls[0][1]["message_code"] == (
        "audit.admin_opened_declared_evidence"
    )

    user = UserRecord("user-1", "User", None, "user", None)
    with pytest.raises(ConversationAuditError):
        subject.read_declared_evidence(
            user, "conversation-1", "turn-1", "declared-open-1"
        )


def test_non_admin_is_denied_before_audit_or_projection() -> None:
    subject, workspace, writer = service()
    user = UserRecord("user-1", "User", None, "user", None)

    with pytest.raises(ConversationAuditError) as error:
        subject.list_admin(user)

    assert error.value.error_code == "access_denied"
    assert writer.calls == []
    assert workspace.detail_calls == []
