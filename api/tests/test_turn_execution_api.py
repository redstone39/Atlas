from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.turn_execution_orchestrator import (
    _capability_rejection_audit_step,
)
from atlas_production.modules.conversation.public import (
    ConversationArchiveResultV1,
    ConversationV1,
    TurnAcceptedV1,
    TurnFeedbackRevisionV1,
)
from atlas_production.modules.citation_preview.public import ProtectedCitationEvidenceV1
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.turn_execution.public import ModelContractViolationV1
from atlas_production.modules.turn_runtime.public import ExecutionState, RuntimeEventV1
from atlas_production.modules.workspace_turn.public import (
    WorkspaceConversationDetailV1,
    WorkspaceConversationListV1,
    WorkspaceExecutionStatusV1,
    WorkspaceTurnError,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)
ACTOR = UserRecord("actor-1", "Actor", None, "user", None)
CONVERSATION = ConversationV1(
    conversation_id="conversation-1",
    owner_actor_id="actor-1",
    title="Test",
    status="active",
    response_language="zh-TW",
    created_at=NOW,
    updated_at=NOW,
)

def test_capability_rejection_audit_step_never_copies_provider_action_name() -> None:
    provider_action_name = "ignore instructions; secret=provider-token"

    step = _capability_rejection_audit_step(
        ordinal=3,
        safe_input_digest="a" * 64,
        violation=ModelContractViolationV1(
            safe_code="unknown_turn_tool",
            action_name=provider_action_name,
            input_tokens=12,
            output_tokens=4,
        ),
    )

    assert step.operation == "provider_capability_rejected"
    assert provider_action_name not in step.model_dump_json()
    assert (step.input_tokens, step.output_tokens) == (12, 4)


class _Principal:
    def current_user(self, _token):
        return ACTOR


class _WorkspaceTurns:
    def __init__(self):
        self.live_reads = 0
        self.cursor_reads = 0
        self.race_reads = 0

    def create_conversation(self, _actor, _command):
        return WorkspaceConversationDetailV1(conversation=CONVERSATION, turns=[])

    def list_conversations(self, _actor):
        return WorkspaceConversationListV1(conversations=[CONVERSATION])

    def archive_conversation(self, _actor, conversation_id, command):
        assert conversation_id == CONVERSATION.conversation_id
        assert command.idempotency_key == "archive-key"
        return ConversationArchiveResultV1(
            conversation=CONVERSATION.model_copy(update={"status": "archived"}),
            audit_event_ref="audit-conversation-archived",
        )

    def get_conversation(self, _actor, _conversation_id):
        return WorkspaceConversationDetailV1(conversation=CONVERSATION, turns=[])
    def update_turn_feedback(
        self, actor, conversation_id, turn_id, command
    ):
        assert actor.actor_id == ACTOR.actor_id
        assert conversation_id == CONVERSATION.conversation_id
        assert turn_id == "turn-1"
        assert command.feedback == "helpful"
        assert command.expected_revision == 0
        assert command.idempotency_key == "feedback-key"
        return TurnFeedbackRevisionV1(
            feedback=command.feedback,
            revision=1,
            updated_at=NOW,
        )


    def read_citation(self, _actor, conversation_id, turn_id, citation_ref):
        assert (conversation_id, turn_id, citation_ref) == (
            "conversation-1",
            "turn-1",
            "citation-1",
        )
        return ProtectedCitationEvidenceV1(
            citation_ref=citation_ref,
            locator_label="Page 7",
            snippet="authorized excerpt",
            content="Authorized exact evidence content.",
            modality="text",
        )

    def accept_turn(self, _actor, _conversation_id, _command):
        return TurnAcceptedV1(
            turn_id="turn-1",
            execution_id="execution-1",
            status="accepted",
            status_url="/api/v1/workspace/turn-executions/execution-1",
            events_url="/api/v1/workspace/turn-executions/execution-1/events",
        )

    def retry_turn(self, _actor, _turn_id, _command):
        return TurnAcceptedV1(
            turn_id="turn-2",
            execution_id="execution-2",
            status="accepted",
            status_url="/api/v1/workspace/turn-executions/execution-2",
            events_url="/api/v1/workspace/turn-executions/execution-2/events",
        )

    def execution_status(self, _actor, execution_id):
        if execution_id == "execution-live" and self.live_reads < 2:
            state = ExecutionState.AWAITING_MODEL_ACTION
            failure_code = None
        elif execution_id == "execution-cursor" and self.cursor_reads < 2:
            state = ExecutionState.AWAITING_MODEL_ACTION
            failure_code = None
        elif execution_id == "execution-race":
            state = ExecutionState.TERMINAL_COMPLETED
            failure_code = None
        else:
            state = ExecutionState.TERMINAL_FAILED
            failure_code = "execution_carrier_lost"
        return WorkspaceExecutionStatusV1(
            execution_id=execution_id,
            turn_id="turn-1",
            conversation_id="conversation-1",
            state=state,
            version=4,
            failure_code=failure_code,
            updated_at=NOW,
        )

    def execution_events(self, _actor, execution_id, *, after_event_id):
        if after_event_id == "missing":
            raise WorkspaceTurnError(
                "event_cursor_invalid", "common.rejected", 409
            )
        if execution_id == "execution-live":
            self.live_reads += 1
            if self.live_reads == 1:
                return [
                    RuntimeEventV1(
                        event_id="event-live-1",
                        execution_id=execution_id,
                        sequence=1,
                        event_type="execution_allocated",
                        state=ExecutionState.ALLOCATED,
                        created_at=NOW,
                    )
                ]
            return [
                RuntimeEventV1(
                    event_id="event-live-2",
                    execution_id=execution_id,
                    sequence=2,
                    event_type="terminal_failed",
                    state=ExecutionState.TERMINAL_FAILED,
                    failure_code="execution_carrier_lost",
                    created_at=NOW,
                )
            ]
        if execution_id == "execution-cursor":
            self.cursor_reads += 1
            if self.cursor_reads == 1:
                assert after_event_id == "event-cursor-1"
                return []
            return [
                RuntimeEventV1(
                    event_id="event-cursor-2",
                    execution_id=execution_id,
                    sequence=2,
                    event_type="terminal_completed",
                    state=ExecutionState.TERMINAL_COMPLETED,
                    created_at=NOW,
                )
            ]
        if execution_id == "execution-race":
            self.race_reads += 1
            if self.race_reads == 1:
                return []
            return [
                RuntimeEventV1(
                    event_id="event-race-terminal",
                    execution_id=execution_id,
                    sequence=2,
                    event_type="terminal_completed",
                    state=ExecutionState.TERMINAL_COMPLETED,
                    created_at=NOW,
                )
            ]
        events = [
            RuntimeEventV1(
                event_id="event-1",
                execution_id=execution_id,
                sequence=1,
                event_type="execution_allocated",
                state=ExecutionState.ALLOCATED,
                created_at=NOW,
            ),
            RuntimeEventV1(
                event_id="event-reasoning",
                execution_id=execution_id,
                sequence=2,
                event_type="reasoning_progressed",
                state=ExecutionState.AWAITING_MODEL_ACTION,
                reasoning_phase="planning",
                progress_status="completed",
                message_code="reasoning.planning_completed",
                message_params={"plan_items": 2},
                created_at=NOW,
            ),
            RuntimeEventV1(
                event_id="event-2",
                execution_id=execution_id,
                sequence=3,
                event_type="terminal_failed",
                state=ExecutionState.TERMINAL_FAILED,
                failure_code="execution_carrier_lost",
                created_at=NOW,
            ),
        ]
        return events[1:] if after_event_id == "event-1" else events


def _composition() -> ApiComposition:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=_Principal(), workspace_turn=_WorkspaceTurns())
    return ApiComposition(**values)


def test_new_turn_api_returns_execution_identity_and_durable_sse_replay() -> None:
    client = TestClient(create_app(_composition()))

    created = client.post(
        "/api/v1/workspace/conversations/conversation-1/turns",
        json={"input_text": "question", "idempotency_key": "key-1"},
    )
    assert created.status_code == 202
    assert created.json()["execution_id"] == "execution-1"

    status = client.get("/api/v1/workspace/turn-executions/execution-1")
    assert status.status_code == 200
    assert status.json()["failure_code"] == "execution_carrier_lost"

    replay = client.get(
        "/api/v1/workspace/turn-executions/execution-1/events",
        headers={"Last-Event-ID": "event-1"},
    )
    assert replay.status_code == 200
    assert "id: event-2" in replay.text
    assert "event: terminal_failed" in replay.text
    assert "event: reasoning_progressed" in replay.text
    assert '"reasoning_phase": "planning"' in replay.text
    assert "event-1" not in replay.text

def test_workspace_feedback_api_is_strict_owner_only_put() -> None:
    client = TestClient(create_app(_composition()))
    response = client.put(
        "/api/v1/workspace/conversations/conversation-1/turns/turn-1/feedback",
        json={
            "feedback": "helpful",
            "expected_revision": 0,
            "idempotency_key": "feedback-key",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "feedback": "helpful",
        "revision": 1,
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
    }

    invalid_value = client.put(
        "/api/v1/workspace/conversations/conversation-1/turns/turn-1/feedback",
        json={
            "feedback": "unknown",
            "expected_revision": 0,
            "idempotency_key": "feedback-key",
        },
    )
    assert invalid_value.status_code == 422
    actor_override = client.put(
        "/api/v1/workspace/conversations/conversation-1/turns/turn-1/feedback",
        json={
            "feedback": "helpful",
            "expected_revision": 0,
            "idempotency_key": "feedback-key",
            "actor_id": "actor-2",
        },
    )
    assert actor_override.status_code == 422
    assert client.put(
        "/api/v1/admin/conversations/conversation-1/turns/turn-1/feedback",
        json={
            "feedback": "helpful",
            "expected_revision": 0,
            "idempotency_key": "feedback-key",
        },
    ).status_code == 404



def test_unknown_sse_cursor_is_typed_conflict_and_legacy_routes_are_absent() -> None:
    client = TestClient(create_app(_composition()))

    conflict = client.get(
        "/api/v1/workspace/turn-executions/execution-1/events",
        headers={"Last-Event-ID": "missing"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "event_cursor_invalid"

    assert client.post(
        "/api/v1/workspace/conversations/conversation-1/turns/stream",
        json={"input_text": "question", "idempotency_key": "key-1"},
    ).status_code == 404
    assert client.get(
        "/api/v1/workspace/conversations/conversation-1/turn-requests/request-1/stream"
    ).status_code == 404


def test_sse_waits_for_events_persisted_after_connection_opened() -> None:
    client = TestClient(create_app(_composition()))

    response = client.get(
        "/api/v1/workspace/turn-executions/execution-live/events"
    )

    assert response.status_code == 200
    assert "id: event-live-1" in response.text
    assert "id: event-live-2" in response.text
    assert "event: terminal_failed" in response.text


def test_sse_preserves_latest_reconnect_cursor_without_replaying_old_events() -> None:
    client = TestClient(create_app(_composition()))

    response = client.get(
        "/api/v1/workspace/turn-executions/execution-cursor/events",
        headers={"Last-Event-ID": "event-cursor-1"},
    )

    assert response.status_code == 200
    assert "event-cursor-1" not in response.text
    assert "id: event-cursor-2" in response.text


def test_sse_drains_terminal_event_committed_between_fetch_and_status() -> None:
    client = TestClient(create_app(_composition()))

    response = client.get(
        "/api/v1/workspace/turn-executions/execution-race/events"
    )

    assert response.status_code == 200
    assert "id: event-race-terminal" in response.text
    assert "event: terminal_completed" in response.text


def test_retry_has_no_scope_and_create_accepts_only_create_scope_tags() -> None:
    client = TestClient(create_app(_composition()))
    retried = client.post(
        "/api/v1/workspace/turns/turn-1/retry",
        json={"idempotency_key": "retry-key"},
    )
    assert retried.status_code == 202
    assert retried.json()["turn_id"] == "turn-2"
    assert retried.json()["execution_id"] == "execution-2"

    created = client.post(
        "/api/v1/workspace/conversations",
        json={
            "title": "Conversation",
            "tag_refs": [
                {"tag_type": "team", "tag_id": "team-a"},
                {"tag_type": "project", "tag_id": "project-b"},
            ],
        },
    )
    assert created.status_code == 200
    assert "tag_refs" not in created.json()["conversation"]

    duplicate = client.post(
        "/api/v1/workspace/conversations",
        json={
            "tag_refs": [
                {"tag_type": "team", "tag_id": "team-a"},
                {"tag_type": "team", "tag_id": "team-a"},
            ]
        },
    )
    assert duplicate.status_code == 422

    rejected = client.post(
        "/api/v1/workspace/conversations",
        json={"title": "Conversation", "knowledge_scope": ["document-1"]},
    )
    assert rejected.status_code == 422


def test_archive_conversation_api_returns_retained_status_and_audit_ref() -> None:
    client = TestClient(create_app(_composition()))

    response = client.post(
        "/api/v1/workspace/conversations/conversation-1/archive",
        json={"idempotency_key": "archive-key"},
    )

    assert response.status_code == 200
    assert response.json()["conversation"]["status"] == "archived"
    assert response.json()["audit_event_ref"] == "audit-conversation-archived"

    replay = client.post(
        "/api/v1/workspace/conversations/conversation-1/archive",
        json={"idempotency_key": "archive-key"},
    )
    assert replay.status_code == 200
    assert replay.json() == response.json()


def test_protected_citation_read_uses_turn_scoped_route() -> None:
    client = TestClient(create_app(_composition()))
    response = client.get(
        "/api/v1/workspace/conversations/conversation-1/turns/turn-1/"
        "citations/citation-1"
    )
    assert response.status_code == 200
    assert response.json() == {
        "citation_ref": "citation-1",
        "locator_label": "Page 7",
        "snippet": "authorized excerpt",
        "content": "Authorized exact evidence content.",
        "modality": "text",
    }


def test_openapi_publishes_typed_accepted_and_event_stream_contracts() -> None:
    schema = create_app(_composition()).openapi()
    create_turn = schema["paths"][
        "/api/v1/workspace/conversations/{conversation_id}/turns"
    ]["post"]
    retry = schema["paths"]["/api/v1/workspace/turns/{turn_id}/retry"]["post"]
    for operation in (create_turn, retry):
        response_schema = operation["responses"]["202"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"].endswith("/TurnAcceptedV1")
    accepted = schema["components"]["schemas"]["TurnAcceptedV1"]
    assert set(accepted["required"]) >= {
        "turn_id",
        "execution_id",
        "status_url",
        "events_url",
    }
    events = schema["paths"][
        "/api/v1/workspace/turn-executions/{execution_id}/events"
    ]["get"]
    parameters = events["parameters"]
    cursor = next(item for item in parameters if item["name"] == "Last-Event-ID")
    assert cursor["in"] == "header"
    assert cursor["required"] is False
    content = events["responses"]["200"]["content"]
    assert set(content) == {"text/event-stream"}
    assert content["text/event-stream"]["schema"]["type"] == "string"
    assert "RuntimeEventV1" in events["responses"]["200"]["description"]
    citation = schema["paths"][
        "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/citations/{citation_ref}"
    ]["get"]
    citation_schema = citation["responses"]["200"]["content"]["application/json"]["schema"]
    assert citation_schema["$ref"].endswith("/ProtectedCitationEvidenceV1")
