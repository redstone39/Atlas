from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.modules.authorization.public import TurnAccessGrantRefV1
from atlas_production.modules.context_engineering.public import (
    ContextLineageGraphV3,
    ContextPackV3,
    CreateTurnInputProjectionV1,
    ModelUserInputV3,
    ModelUserTextSegmentV3,
    RecordResolverProjectionV1,
    RecordRewriteProjectionV1,
    ContextSummarySourceV3,
    ContextSummaryV4,
    TurnInputProjectionV1,
)
from atlas_production.modules.conversation.public import (
    ConversationArchiveError,
    ConversationArchiveResultV1,
    ConversationMembershipConflict,
    ConversationTurnMemberV1,
    ConversationV1,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.retrieval.public import KnowledgeCatalogSnapshotRefV1
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    ExecutionState,
    RuntimeEventV1,
    TurnRuntimeReplayConflict,
)
from atlas_production.modules.turn_execution.public import AnswerBehaviorRevisionV1
from atlas_production.modules.workspace_turn.public import (
    WorkspaceTurnApplication,
    WorkspaceAnswerSegmentV2,
    WorkspaceTurnCreateV1,
    WorkspaceTurnError,
    WorkspaceTurnProjectionV1,
    WorkspaceTurnRetryV1,
    _historical_exchange_content_digest,
)
from tests.answer_behavior_fixtures import NullAnswerBehavior


NOW = datetime.now(timezone.utc)
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


def _budget():
    return BudgetSnapshotV1(
        tool_invocations=0, catalog_pages=0, document_candidates=0,
        search_rounds=0, model_visible_items=0, provider_invocations=0,
        context_tokens=0, tool_tokens=0, retrieval_repairs=0, schema_retries=0,
    )


class Runtime:
    def __init__(self):
        self.current = None
        self.executions = {}
        self.failed = []
        self.input_digests = {}
        self.staged = []
        self.event_overrides = {}

    def allocate(self, command):
        previous_digest = self.input_digests.get(command.execution_id)
        if previous_digest is not None and previous_digest != command.input_digest:
            raise TurnRuntimeReplayConflict("allocation replay payload conflicts")
        self.input_digests[command.execution_id] = command.input_digest
        self.current = self.executions.get(command.execution_id)
        if self.current is None:
            lease = ExecutionLeaseV1(
                execution_id=command.execution_id,
                holder_id=command.holder_id,
                lease_version=1,
                fencing_token=1,
                acquired_at=NOW,
                heartbeat_at=NOW,
                expires_at=NOW + timedelta(seconds=30),
            )
            self.current = ExecutionSnapshotV1(
                execution_id=command.execution_id,
                turn_id=command.turn_id,
                conversation_id=command.conversation_id,
                actor_id=command.actor_id,
                state=ExecutionState.ALLOCATED,
                version=1,
                policy=command.route_policy,
                route=command.route,
                input_digest=command.input_digest,
                response_language=command.response_language,
                reasoning_mode=command.reasoning_mode,
                applied_guidance_revision=command.applied_guidance_revision,
                applied_guidance_digest=command.applied_guidance_digest,
                lease=lease,
                budget=_budget(),
                deadline_at=NOW + timedelta(seconds=120),
                created_at=NOW,
                updated_at=NOW,
            )
            self.executions[command.execution_id] = self.current
        return self.current

    def stage_acceptance_resource(self, command):
        self.staged.append((command.resource_owner, command.release_kind))

    def accept(self, command):
        self.current = self.current.model_copy(
            update={
                "state": ExecutionState.ACCEPTED,
                "version": 2,
                "grant_ref": command.grant_ref,
                "catalog_ref": command.catalog_ref,
            }
        )
        self.executions[command.execution_id] = self.current
        return self.current

    def bind_context(self, command):
        self.current = self.current.model_copy(
            update={
                "state": ExecutionState.CONTEXT_READY,
                "version": 3,
                "context_pack_ref": command.context_pack_ref,
            }
        )
        self.executions[command.execution_id] = self.current
        return self.current

    def snapshot(self, execution_id):
        return self.executions[execution_id]

    def find_execution(self, execution_id):
        return self.executions.get(execution_id)

    def terminal_outcome(self, _execution_id):
        return None

    def events(self, execution_id):
        if execution_id in self.event_overrides:
            return self.event_overrides[execution_id]
        snapshot = self.executions[execution_id]
        return [RuntimeEventV1(
            event_id=f"event-{execution_id}-terminal",
            execution_id=execution_id,
            sequence=1,
            event_type="terminal_failed",
            state=ExecutionState.TERMINAL_FAILED,
            failure_code=snapshot.terminal_failure_code,
            created_at=NOW,
        )] if snapshot.state is ExecutionState.TERMINAL_FAILED else []

    def fail_carrier(self, command):
        self.failed.append(command)
        self.current = self.current.model_copy(
            update={
                "state": ExecutionState.TERMINAL_FAILED,
                "version": self.current.version + 1,
                "terminal_failure_code": command.failure_code,
            }
        )
        self.executions[command.execution_id] = self.current
        return self.current


class Conversations:
    def __init__(self):
        self.member = None
        self._retry_sources = {}
        self.conversation = CONVERSATION

    def list_for_actor(self, _actor_id):
        return [self.conversation]

    def archive(self, *, actor_id, conversation_id, command):
        assert actor_id == self.conversation.owner_actor_id
        assert conversation_id == self.conversation.conversation_id
        assert command.idempotency_key
        assert command.expected_next_ordinal == (
            1 if self.member is None else self.member.ordinal + 1
        )
        self.conversation = self.conversation.model_copy(
            update={"status": "archived"}
        )
        return ConversationArchiveResultV1(
            conversation=self.conversation,
            audit_event_ref="audit-conversation-archived",
        )

    def get(self, _conversation_id):
        return self.conversation

    def get_turn(self, turn_id):
        return self.member if self.member is not None and self.member.turn_id == turn_id else None

    def append_turn_member(self, *, actor_id, command):
        if command.operation == "create_turn":
            self.conversation = self.conversation.model_copy(
                update={"reasoning_mode": command.reasoning_mode}
            )
        if self.member is None:
            self.member = ConversationTurnMemberV1(
                turn_id=command.turn_id,
                conversation_id=command.conversation_id,
                execution_id=command.execution_id,
                role=command.role,
                ordinal=1,
                created_at=NOW,
            )
        return self.member

    def append_retry_turn_member(self, *, actor_id, command, retry_of_turn_id):
        member = self.append_turn_member(actor_id=actor_id, command=command)
        self._retry_sources[member.turn_id] = retry_of_turn_id
        return member

    def retry_sources(self, _conversation_id):
        return dict(self._retry_sources)

    def candidate_turns(self, _conversation_id):
        return [] if self.member is None else [self.member]


class Authorization:
    def __init__(self):
        self.materialized = 0

    def create_grant(self, command):
        return TurnAccessGrantRefV1(
            grant_ref="grant-1",
            digest="a" * 64,
            actor_id=command.actor_id,
            authorization_revision=7,
            issued_at=NOW,
            deadline_at=command.deadline_at,
        )

    def materialize_grant_document_resources(self, command):
        self.materialized += 1
        return SimpleNamespace()


class KnowledgeSource:
    def __init__(self):
        self.revisions = []

    def resources_for_grant(self, **facts):
        self.revisions.append(facts["authorization_revision"])
        return []


class Retrieval:
    def create_catalog(self, **_facts):
        return KnowledgeCatalogSnapshotRefV1(
            catalog_ref="catalog-1",
            grant_ref="grant-1",
            generation_retention_ref="retention-1",
            retrieval_generation_ref="generation-1",
            document_count=0,
            digest="b" * 64,
            created_at=NOW,
        )


class GenerationRetention:
    def create_generation_retention(self, _command):
        return SimpleNamespace(retention_ref="retention-1")


class Contexts:
    def __init__(self):
        self.pack = None
        self.projections = {}
        self.projection_create_calls = 0

    def lineage_graph(self, turn_ids):
        return ContextLineageGraphV3(candidate_turn_ids=turn_ids, edges=[])

    def get(self, context_pack_ref):
        return self.pack if self.pack and self.pack.context_pack_ref == context_pack_ref else None

    def materialize(self, command):
        self.pack = ContextPackV3(
            context_pack_ref=command.context_pack_ref,
            execution_id=command.execution_id,
            input_projection_ref=command.input_projection_ref,
            model_user_input=command.model_user_input.as_text(),
            recent_tail=command.recent_tail,
            summary=command.summary,
            dependencies=command.source_lineage,
            token_budget=command.token_budget,
            digest="c" * 64,
            created_at=NOW,
        )
        return self.pack

    def create_input_projection(
        self, command: CreateTurnInputProjectionV1
    ) -> TurnInputProjectionV1:
        self.projection_create_calls += 1
        replay = self.projections.get(command.execution_id)
        if replay is not None:
            return replay
        projection = TurnInputProjectionV1(
            projection_ref=command.projection_ref,
            execution_id=command.execution_id,
            original_user_input=command.original_user_input,
            created_at=NOW,
            updated_at=NOW,
        )
        self.projections[command.execution_id] = projection
        return projection

    def get_input_projection(self, execution_id):
        return self.projections.get(execution_id)

    def record_resolver_projection(
        self, command: RecordResolverProjectionV1
    ) -> TurnInputProjectionV1:
        projection = self.projections[command.execution_id].model_copy(
            update={
                "resolver_output": command.resolver_output,
                "resolver_invocation_ref": command.resolver_invocation_ref,
                "resolver_failure_code": command.failure_code,
            }
        )
        self.projections[command.execution_id] = projection
        return projection

    def record_rewrite_projection(
        self, command: RecordRewriteProjectionV1
    ) -> TurnInputProjectionV1:
        projection = self.projections[command.execution_id].model_copy(
            update={
                "rewritten_user_input": command.rewritten_user_input,
                "rewrite_invocation_ref": command.rewrite_invocation_ref,
                "rewrite_failure_code": command.failure_code,
            }
        )
        self.projections[command.execution_id] = projection
        return projection


class Carrier:
    def __init__(self, *, fail=False):
        self.launched = []
        self.fail = fail

    def launch(self, execution_id):
        if self.fail:
            raise RuntimeError("carrier unavailable")
        self.launched.append(execution_id)


class ModelRoutes:
    def __init__(self):
        self.calls = 0

    def tested_route(self):
        self.calls += 1
        return SimpleNamespace(
            route_id="test-route",
            revision=1,
            runtime_policy=SimpleNamespace(
                revision=1,
                tokenizer_profile="cl100k_base",
                context_window_tokens=400000,
                max_input_tokens_per_invocation=272000,
                max_output_tokens_per_invocation=16000,
                max_tool_result_tokens_per_execution=64000,
                max_total_tokens_per_conversation=1000000,
                max_tool_executions=12,
                max_provider_invocations=26,
                max_reasoning_revision_cycles=2,
                max_schema_retries_per_turn=3,
                max_catalog_pages=5,
                max_search_rounds=6,
                max_model_visible_items_per_turn=40,
                max_retrieval_repairs=2,
                max_selected_anchor_pages_per_round=7,
                tool_execution_timeout_seconds=31,
                turn_timeout_seconds=240,
            ),
        )


class ContextPreparer:
    def prepare(
        self, command, _snapshot, *, catalog_document_count
    ):
        return command


class RewritingContextPreparer:
    def __init__(self, rewritten: str) -> None:
        self.rewritten = rewritten

    def prepare(self, command, _snapshot, *, catalog_document_count):
        return command.model_copy(
            update={
                "model_user_input": ModelUserInputV3(
                    content_segments=[
                        ModelUserTextSegmentV3(text=self.rewritten)
                    ]
                )
            }
        )


class ConversationUsage:
    def __init__(self, tokens=0):
        self.tokens = tokens
        self.calls = []

    def observed_tokens(self, conversation_id):
        self.calls.append(conversation_id)
        return self.tokens


class MutableAnswerBehavior:
    def __init__(self) -> None:
        self.value = AnswerBehaviorRevisionV1(
            revision=0,
            custom_guidance=None,
            guidance_digest=None,
            created_at=None,
        )
        self.current_calls = 0

    def current(self):
        self.current_calls += 1
        return self.value


def _app(
    carrier,
    *,
    authorization=None,
    retrieval=None,
    contexts=None,
    generation_retention=None,
    model_routes=None,
    context_preparer=None,
    conversation_usage=None,
    answer_behavior=None,
    conversations=None,
):
    runtime = Runtime()
    source = KnowledgeSource()
    conversations = conversations or Conversations()
    selected_contexts = contexts or Contexts()
    application = WorkspaceTurnApplication(
        conversations=conversations,
        retry_lineage=conversations,
        authorization=authorization or Authorization(),
        knowledge_source=source,
        contexts=selected_contexts,
        input_projections=selected_contexts,
        retrieval=retrieval or Retrieval(),
        generation_retention=generation_retention or GenerationRetention(),
        runtime=runtime,
        results=SimpleNamespace(),
        citations=SimpleNamespace(),
        audits=SimpleNamespace(),
        citation_reader=SimpleNamespace(),
        declared_evidence_reader=SimpleNamespace(),
        carrier=carrier,
        model_routes=model_routes or ModelRoutes(),
        answer_behavior=answer_behavior or NullAnswerBehavior(),
        context_preparer=context_preparer or ContextPreparer(),
        conversation_usage=conversation_usage or ConversationUsage(),
    )
    return application, runtime, source


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ExecutionState.ALLOCATED, "processing"),
        (ExecutionState.TOOL_COMPLETED, "processing"),
        (ExecutionState.TERMINAL_COMPLETED, "completed"),
        (ExecutionState.TERMINAL_FAILED, "failed_closed"),
    ],
)
def test_list_conversations_projects_latest_turn_status(state, expected):
    application, runtime, _source = _app(SimpleNamespace())
    member = ConversationTurnMemberV1(
        turn_id="turn-list",
        conversation_id=CONVERSATION.conversation_id,
        execution_id="execution-list",
        role="assistant",
        ordinal=1,
        created_at=NOW,
    )
    application._conversations.member = member
    runtime.executions[member.execution_id] = SimpleNamespace(state=state)

    result = application.list_conversations(ACTOR)

    assert result.conversations[0].last_turn_status == expected


def test_list_conversations_projects_empty_conversation_without_status():
    application, _runtime, _source = _app(SimpleNamespace())

    result = application.list_conversations(ACTOR)

    assert result.conversations[0].last_turn_status is None


def test_archive_conversation_hides_idle_owner_conversation() -> None:
    application, _runtime, _source = _app(SimpleNamespace())

    result = application.archive_conversation(
        ACTOR,
        CONVERSATION.conversation_id,
        SimpleNamespace(idempotency_key="archive-key"),
    )

    assert result.conversation.status == "archived"
    assert result.audit_event_ref == "audit-conversation-archived"
    assert application.list_conversations(ACTOR).conversations == []


def test_archive_conversation_exact_replay_returns_same_result() -> None:
    application, _runtime, _source = _app(SimpleNamespace())
    command = SimpleNamespace(idempotency_key="archive-key")

    first = application.archive_conversation(
        ACTOR, CONVERSATION.conversation_id, command
    )
    replay = application.archive_conversation(
        ACTOR, CONVERSATION.conversation_id, command
    )

    assert replay == first
    assert replay.conversation.status == "archived"
    assert replay.audit_event_ref == "audit-conversation-archived"


def test_archive_conversation_rejects_processing_turn() -> None:
    application, runtime, _source = _app(SimpleNamespace())
    member = ConversationTurnMemberV1(
        turn_id="turn-processing",
        conversation_id=CONVERSATION.conversation_id,
        execution_id="execution-processing",
        role="assistant",
        ordinal=1,
        created_at=NOW,
    )
    application._conversations.member = member
    runtime.executions[member.execution_id] = SimpleNamespace(
        state=ExecutionState.AWAITING_MODEL_ACTION
    )

    with pytest.raises(WorkspaceTurnError) as error:
        application.archive_conversation(
            ACTOR,
            CONVERSATION.conversation_id,
            SimpleNamespace(idempotency_key="archive-key"),
        )

    assert error.value.status_code == 409
    assert application._conversations.conversation.status == "active"


def test_archive_conversation_rejects_earlier_processing_turn() -> None:
    application, runtime, _source = _app(SimpleNamespace())
    earlier = ConversationTurnMemberV1(
        turn_id="turn-processing-earlier",
        conversation_id=CONVERSATION.conversation_id,
        execution_id="execution-processing-earlier",
        role="assistant",
        ordinal=1,
        created_at=NOW,
    )
    latest = ConversationTurnMemberV1(
        turn_id="turn-terminal-latest",
        conversation_id=CONVERSATION.conversation_id,
        execution_id="execution-terminal-latest",
        role="assistant",
        ordinal=2,
        created_at=NOW,
    )
    application._conversations.candidate_turns = lambda _conversation_id: [
        earlier,
        latest,
    ]
    runtime.executions[earlier.execution_id] = SimpleNamespace(
        state=ExecutionState.AWAITING_MODEL_ACTION
    )
    runtime.executions[latest.execution_id] = SimpleNamespace(
        state=ExecutionState.TERMINAL_COMPLETED
    )

    with pytest.raises(WorkspaceTurnError) as error:
        application.archive_conversation(
            ACTOR,
            CONVERSATION.conversation_id,
            SimpleNamespace(idempotency_key="archive-key"),
        )

    assert error.value.status_code == 409
    assert application._conversations.conversation.status == "active"


def test_archive_conversation_rejects_membership_added_after_runtime_scan() -> None:
    application, _runtime, _source = _app(SimpleNamespace())

    def append_before_archive(*, actor_id, conversation_id, command):
        assert actor_id == ACTOR.actor_id
        assert conversation_id == CONVERSATION.conversation_id
        assert command.expected_next_ordinal == 1
        application._conversations.member = ConversationTurnMemberV1(
            turn_id="turn-concurrent",
            conversation_id=CONVERSATION.conversation_id,
            execution_id="execution-concurrent",
            role="assistant",
            ordinal=1,
            created_at=NOW,
        )
        raise ConversationArchiveError("conflict")

    application._conversations.archive = append_before_archive

    with pytest.raises(WorkspaceTurnError) as error:
        application.archive_conversation(
            ACTOR,
            CONVERSATION.conversation_id,
            SimpleNamespace(idempotency_key="archive-key"),
        )

    assert error.value.status_code == 409
    assert application._conversations.conversation.status == "active"


def test_archive_conversation_hides_non_owned_target() -> None:
    application, _runtime, _source = _app(SimpleNamespace())
    other_actor = UserRecord("actor-2", "Other", None, "user", None)

    with pytest.raises(WorkspaceTurnError) as error:
        application.archive_conversation(
            other_actor,
            CONVERSATION.conversation_id,
            SimpleNamespace(idempotency_key="archive-key"),
        )

    assert error.value.status_code == 404


@pytest.mark.parametrize(
    ("failure_point", "expected_staged"),
    [
        ("grant", [("authorization", "release_turn_grant")]),
        ("grant_resources", [("authorization", "release_turn_grant")]),
        (
            "generation_retention",
            [
                ("authorization", "release_turn_grant"),
                ("processing_pipeline", "release_generation_retention"),
            ],
        ),
        (
            "catalog",
            [
                ("authorization", "release_turn_grant"),
                ("processing_pipeline", "release_generation_retention"),
                ("retrieval", "release_knowledge_catalog"),
            ],
        ),
        (
            "context",
            [
                ("authorization", "release_turn_grant"),
                ("processing_pipeline", "release_generation_retention"),
                ("retrieval", "release_knowledge_catalog"),
                ("context_engineering", "release_context_pack"),
            ],
        ),
    ],
)
def test_acceptance_failure_is_staged_before_each_owner_call(
    failure_point, expected_staged
) -> None:
    authorization = Authorization()
    retrieval = Retrieval()
    contexts = Contexts()
    generation_retention = GenerationRetention()

    if failure_point == "grant":
        authorization.create_grant = lambda _command: (_ for _ in ()).throw(
            RuntimeError("grant failed")
        )
    elif failure_point == "grant_resources":
        authorization.materialize_grant_document_resources = (
            lambda _command: (_ for _ in ()).throw(RuntimeError("resources failed"))
        )
    elif failure_point == "generation_retention":
        generation_retention.create_generation_retention = (
            lambda _command: (_ for _ in ()).throw(RuntimeError("retention failed"))
        )
    elif failure_point == "catalog":
        retrieval.create_catalog = lambda **_facts: (_ for _ in ()).throw(
            RuntimeError("catalog failed")
        )
    else:
        contexts.materialize = lambda _command: (_ for _ in ()).throw(
            RuntimeError("context failed")
        )

    application, runtime, _source = _app(
        Carrier(),
        authorization=authorization,
        retrieval=retrieval,
        contexts=contexts,
        generation_retention=generation_retention,
    )
    command = WorkspaceTurnCreateV1(
        input_text="question", idempotency_key=f"failure-{failure_point}"
    )

    accepted = application.accept_turn(ACTOR, "conversation-1", command)

    assert accepted.execution_id == runtime.current.execution_id
    assert runtime.current.state is ExecutionState.TERMINAL_FAILED
    assert runtime.staged == expected_staged
    status = application.execution_status(ACTOR, runtime.current.execution_id)
    assert status.state is ExecutionState.TERMINAL_FAILED
    assert status.failure_code == "contract_violation"


def test_exact_turn_replay_returns_same_execution_without_second_carrier() -> None:
    carrier = Carrier()
    routes = ModelRoutes()
    usage = ConversationUsage()
    contexts = Contexts()
    application, runtime, source = _app(
        carrier,
        contexts=contexts,
        model_routes=routes,
        conversation_usage=usage,
    )
    command = WorkspaceTurnCreateV1(input_text="question", idempotency_key="key-1")

    first = application.accept_turn(ACTOR, "conversation-1", command)
    replay = application.accept_turn(ACTOR, "conversation-1", command)

    assert replay == first
    assert carrier.launched == [first.execution_id]
    assert source.revisions == [7]
    assert runtime.current.state is ExecutionState.CONTEXT_READY
    assert runtime.current.policy.model_dump() == {
        "max_tool_invocations": 12,
        "max_catalog_pages": 5,
        "max_search_rounds": 6,
        "max_model_visible_items_per_turn": 40,
        "max_retrieval_repairs": 2,
        "max_selected_anchor_pages_per_round": 7,
        "max_provider_invocations": 26,
        "max_reasoning_revision_cycles": 2,
        "max_schema_retries_per_turn": 3,
        "context_token_budget": 272000,
        "tool_token_budget": 64000,
        "tool_execution_timeout_seconds": 31,
        "deadline_seconds": 240,
    }
    assert runtime.current.route.model_dump() == {
        "route_id": "test-route",
        "route_revision": 1,
        "runtime_policy_revision": 1,
        "tokenizer_profile": "cl100k_base",
        "context_window_tokens": 400000,
        "max_input_tokens_per_invocation": 272000,
        "max_output_tokens_per_invocation": 16000,
        "max_tool_result_tokens_per_execution": 64000,
        "max_total_tokens_per_conversation": 1000000,
    }
    assert routes.calls == 1
    assert usage.calls == ["conversation-1"]
    assert contexts.projection_create_calls == 1


def test_fresh_deep_turn_pins_mode_and_updates_conversation_default() -> None:
    application, runtime, _source = _app(Carrier())
    command = WorkspaceTurnCreateV1(
        input_text="question",
        idempotency_key="deep-key",
        reasoning_mode="deep",
    )

    accepted = application.accept_turn(ACTOR, "conversation-1", command)

    assert runtime.snapshot(accepted.execution_id).reasoning_mode == "deep"
    assert application._conversations.conversation.reasoning_mode == "deep"
    assert application.accept_turn(ACTOR, "conversation-1", command) == accepted
    with pytest.raises(WorkspaceTurnError) as error:
        application.accept_turn(
            ACTOR,
            "conversation-1",
            command.model_copy(update={"reasoning_mode": "standard"}),
        )
    assert error.value.error_code == "idempotency_conflict"


def test_workspace_projects_only_safe_deep_reasoning_timeline() -> None:
    application, runtime, _source = _app(Carrier())
    accepted = application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(
            input_text="question",
            idempotency_key="deep-timeline",
            reasoning_mode="deep",
        ),
    )
    runtime.event_overrides[accepted.execution_id] = [
        RuntimeEventV1(
            event_id="reasoning-event-1",
            execution_id=accepted.execution_id,
            sequence=4,
            event_type="reasoning_progressed",
            state=ExecutionState.AWAITING_MODEL_ACTION,
            reasoning_phase="planning",
            progress_status="completed",
            message_code="reasoning.planning_completed",
            message_params={"plan_items": 2},
            created_at=NOW,
        )
    ]

    status = application.execution_status(ACTOR, accepted.execution_id)
    member = application._conversations.member
    projection = application._project_turn(
        ACTOR.actor_id, member, runtime.current, None
    )

    assert status.reasoning_mode == "deep"
    assert status.reasoning_timeline == projection.reasoning_timeline
    assert status.reasoning_timeline[0].message_params == {"plan_items": 2}
    workspace_payload = projection.model_dump(mode="json")
    assert "reasoning_trace" not in workspace_payload
    assert "score" not in str(workspace_payload).casefold()


def test_workspace_displays_and_retries_original_while_context_stores_rewrite() -> None:
    contexts = Contexts()
    application, runtime, _source = _app(
        Carrier(),
        contexts=contexts,
        context_preparer=RewritingContextPreparer(
            "文件 B 與文件 A 有哪些差異？"
        ),
    )
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(
            input_text="它跟上一份有什麼差異？",
            idempotency_key="rewrite-boundary",
        ),
    )
    member = application._conversations.member
    assert member is not None
    assert contexts.pack.model_user_input == "文件 B 與文件 A 有哪些差異？"
    projected = application._project_turn("actor-1", member, runtime.current, None)
    assert projected.user_input == "它跟上一份有什麼差異？"

    captured = {}

    def accept_retry(actor, conversation_id, command, *, retry_of=None):
        captured.update(
            actor=actor,
            conversation_id=conversation_id,
            command=command,
            retry_of=retry_of,
        )
        return SimpleNamespace()

    application.accept_turn = accept_retry
    application.retry_turn(
        ACTOR,
        member.turn_id,
        WorkspaceTurnRetryV1(idempotency_key="retry-rewrite-boundary"),
    )
    assert captured["command"].input_text == "它跟上一份有什麼差異？"
    assert captured["command"].reasoning_mode == "standard"
    assert captured["retry_of"] == member


def test_conversation_quota_rejects_before_allocation_or_membership() -> None:
    carrier = Carrier()
    usage = ConversationUsage(tokens=1_000_000)
    application, runtime, source = _app(
        carrier,
        conversation_usage=usage,
    )

    with pytest.raises(WorkspaceTurnError) as error:
        application.accept_turn(
            ACTOR,
            "conversation-1",
            WorkspaceTurnCreateV1(
                input_text="question",
                idempotency_key="quota-key",
            ),
        )

    assert error.value.error_code == "conversation_token_quota_exceeded"
    assert error.value.status_code == 429
    assert runtime.executions == {}
    assert application._conversations.member is None
    assert source.revisions == []
    assert carrier.launched == []


def test_admitted_turn_may_cross_soft_quota_then_next_new_turn_is_rejected() -> None:
    usage = ConversationUsage(tokens=999_999)
    application, runtime, _source = _app(
        Carrier(),
        conversation_usage=usage,
    )
    command = WorkspaceTurnCreateV1(
        input_text="first question",
        idempotency_key="soft-quota-first",
    )

    accepted = application.accept_turn(ACTOR, "conversation-1", command)
    usage.tokens = 1_000_001

    assert application.accept_turn(ACTOR, "conversation-1", command) == accepted
    with pytest.raises(WorkspaceTurnError) as error:
        application.accept_turn(
            ACTOR,
            "conversation-1",
            WorkspaceTurnCreateV1(
                input_text="next question",
                idempotency_key="soft-quota-next",
            ),
        )

    assert error.value.error_code == "conversation_token_quota_exceeded"
    assert len(runtime.executions) == 1


@pytest.mark.parametrize(
    "failure_code",
    [
        "summary_generation_failed",
        "context_limit_exceeded",
        "resolver_failed",
        "rewrite_failed",
    ],
)
def test_context_prerequisite_failure_is_typed_terminal_status_and_event(
    failure_code,
) -> None:
    class Failure(RuntimeError):
        safe_code = failure_code

    class FailingPreparer:
        def prepare(self, _command, _snapshot, *, catalog_document_count):
            raise Failure(failure_code)

    application, runtime, _source = _app(
        Carrier(), context_preparer=FailingPreparer()
    )

    accepted = application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(
            input_text="question",
            idempotency_key=f"failure-{failure_code}",
        ),
    )
    status = application.execution_status(ACTOR, accepted.execution_id)
    events = application.execution_events(
        ACTOR, accepted.execution_id, after_event_id=None
    )

    assert status.state is ExecutionState.TERMINAL_FAILED
    assert status.failure_code == failure_code
    assert events[-1].failure_code == failure_code
    assert runtime.current.terminal_failure_code == failure_code


def test_same_idempotency_key_with_changed_input_is_typed_conflict() -> None:
    carrier = Carrier()
    application, runtime, _source = _app(carrier)
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="first", idempotency_key="key-conflict"),
    )

    with pytest.raises(WorkspaceTurnError) as caught:
        application.accept_turn(
            ACTOR,
            "conversation-1",
            WorkspaceTurnCreateV1(input_text="changed", idempotency_key="key-conflict"),
        )

    assert caught.value.status_code == 409
    assert runtime.current.state is ExecutionState.CONTEXT_READY
    assert len(carrier.launched) == 1


def test_execution_acceptance_pins_guidance_and_exact_replay_does_not_reread() -> None:
    behavior = MutableAnswerBehavior()
    behavior.value = AnswerBehaviorRevisionV1(
        revision=1,
        custom_guidance="Prefer concise answers.",
        guidance_digest="1" * 64,
        created_at=NOW,
    )
    application, runtime, _source = _app(
        Carrier(), answer_behavior=behavior
    )
    command = WorkspaceTurnCreateV1(
        input_text="question",
        idempotency_key="guidance-snapshot",
    )

    accepted = application.accept_turn(ACTOR, "conversation-1", command)
    first = runtime.executions[accepted.execution_id]
    behavior.value = AnswerBehaviorRevisionV1(
        revision=2,
        custom_guidance="Prefer numbered comparisons.",
        guidance_digest="2" * 64,
        created_at=NOW + timedelta(seconds=1),
    )

    replayed = application.accept_turn(ACTOR, "conversation-1", command)
    assert replayed == accepted
    assert behavior.current_calls == 1
    assert first.response_language == "zh-TW"
    assert first.applied_guidance_revision == 1
    assert first.applied_guidance_digest == "1" * 64

    retry_source = ConversationTurnMemberV1(
        turn_id="guidance-source",
        conversation_id="conversation-1",
        execution_id="guidance-source-execution",
        role="user",
        ordinal=1,
        created_at=NOW,
    )
    runtime.executions[retry_source.execution_id] = first.model_copy(
        update={
            "execution_id": retry_source.execution_id,
            "turn_id": retry_source.turn_id,
        }
    )
    retried = application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(
            input_text="question",
            idempotency_key="guidance-retry",
        ),
        retry_of=retry_source,
    )
    retry_snapshot = runtime.executions[retried.execution_id]
    assert behavior.current_calls == 2
    assert retry_snapshot.response_language == "zh-TW"
    assert retry_snapshot.applied_guidance_revision == 2
    assert retry_snapshot.applied_guidance_digest == "2" * 64


def test_submit_and_explicit_retries_scope_same_key_to_fresh_execution_semantics() -> None:
    carrier = Carrier()
    application, runtime, _source = _app(carrier)
    command = WorkspaceTurnCreateV1(input_text="question", idempotency_key="same-key")
    submitted = application.accept_turn(ACTOR, "conversation-1", command)
    source_a = ConversationTurnMemberV1(
        turn_id="source-turn-a", conversation_id="conversation-1",
        execution_id="source-execution-a", role="user", ordinal=1, created_at=NOW,
    )
    source_b = source_a.model_copy(update={
        "turn_id": "source-turn-b", "execution_id": "source-execution-b", "ordinal": 2,
    })
    submitted_snapshot = runtime.snapshot(submitted.execution_id)
    for source in (source_a, source_b):
        runtime.executions[source.execution_id] = submitted_snapshot.model_copy(
            update={
                "execution_id": source.execution_id,
                "turn_id": source.turn_id,
            }
        )

    retried_a = application.accept_turn(
        ACTOR, "conversation-1", command, retry_of=source_a
    )
    replayed_a = application.accept_turn(
        ACTOR, "conversation-1", command, retry_of=source_a
    )
    retried_b = application.accept_turn(
        ACTOR, "conversation-1", command, retry_of=source_b
    )

    assert len({submitted.execution_id, retried_a.execution_id, retried_b.execution_id}) == 3
    assert replayed_a == retried_a
    assert carrier.launched == [
        submitted.execution_id, retried_a.execution_id, retried_b.execution_id,
    ]


def test_max_length_public_idempotency_key_uses_bounded_internal_owner_keys() -> None:
    carrier = Carrier()
    application, runtime, _source = _app(carrier)
    command = WorkspaceTurnCreateV1(input_text="question", idempotency_key="k" * 200)

    accepted = application.accept_turn(ACTOR, "conversation-1", command)
    source = ConversationTurnMemberV1(
        turn_id="max-key-source", conversation_id="conversation-1",
        execution_id="max-key-source-execution", role="user", ordinal=1, created_at=NOW,
    )
    runtime.executions[source.execution_id] = runtime.snapshot(
        accepted.execution_id
    ).model_copy(
        update={"execution_id": source.execution_id, "turn_id": source.turn_id}
    )
    retried = application.accept_turn(
        ACTOR, "conversation-1", command, retry_of=source
    )

    assert accepted.execution_id != retried.execution_id
    assert retried.execution_id == runtime.current.execution_id
    assert application._conversations.member is not None
    assert carrier.launched == [accepted.execution_id, retried.execution_id]


def test_context_collapses_retry_chain_and_preserves_user_assistant_exchange() -> None:
    contexts = Contexts()
    application, runtime, _source = _app(Carrier(), contexts=contexts)
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="current", idempotency_key="current-key"),
    )
    current = application._conversations.member
    assert current is not None
    members = [
        ConversationTurnMemberV1(
            turn_id="turn-root", conversation_id="conversation-1",
            execution_id="execution-root", role="user", ordinal=1, created_at=NOW,
        ),
        ConversationTurnMemberV1(
            turn_id="turn-retry-ok", conversation_id="conversation-1",
            execution_id="execution-retry-ok", role="user", ordinal=2,
            created_at=NOW,
        ),
        ConversationTurnMemberV1(
            turn_id="turn-retry-failed", conversation_id="conversation-1",
            execution_id="execution-retry-failed", role="user", ordinal=3,
            created_at=NOW,
        ),
        ConversationTurnMemberV1(
            turn_id="turn-independent", conversation_id="conversation-1",
            execution_id="execution-independent", role="user", ordinal=4, created_at=NOW,
        ),
        current.model_copy(update={"ordinal": 5}),
    ]
    application._conversations.candidate_turns = lambda _conversation_id: members
    application._retry_lineage.retry_sources = lambda _conversation_id: {
        "turn-retry-ok": "turn-root",
        "turn-retry-failed": "turn-retry-ok",
    }

    def projection(turn_id, ordinal, state, user_input, answer=None):
        segments = [] if answer is None else [WorkspaceAnswerSegmentV2(
            segment_id=f"segment-{turn_id}", text=answer,
        )]
        return WorkspaceTurnProjectionV1(
            turn_id=turn_id, execution_id=f"execution-{turn_id}", ordinal=ordinal,
            user_input=user_input, execution_status=state,
            evidence_review_status="evidence_aligned" if segments else None,
            segments=segments, citations=[], created_at=NOW,
            model_claimed_evidence=(
                [
                    {
                        "position": 1,
                        "handle": "kh_claim_must_not_reenter_context",
                        "resolution_status": "unresolved",
                        "review_resolution_reason": (
                            "unknown_or_out_of_execution"
                        ),
                    }
                ]
                if turn_id == "turn-retry-ok"
                else []
            ),
        )

    visible = [
        projection("turn-root", 1, ExecutionState.TERMINAL_FAILED, "root question"),
        projection("turn-retry-ok", 2, ExecutionState.TERMINAL_COMPLETED, "root question", "recovered answer"),
        projection("turn-retry-failed", 3, ExecutionState.TERMINAL_FAILED, "root question"),
        projection("turn-independent", 4, ExecutionState.TERMINAL_COMPLETED, "follow-up", "follow-up answer"),
    ]
    projection_by_turn = {item.turn_id: item for item in visible}
    packs = {
        "context-turn-retry-ok": ContextPackV3(
            context_pack_ref="context-turn-retry-ok",
            execution_id="execution-retry-ok",
            input_projection_ref="input-projection-retry-ok",
            model_user_input="root question",
            recent_tail=[],
            dependencies=[],
            token_budget=112000,
            digest="1" * 64,
            created_at=NOW,
        ),
        "context-turn-independent": ContextPackV3(
            context_pack_ref="context-turn-independent",
            execution_id="execution-independent",
            input_projection_ref="input-projection-independent",
            model_user_input="follow-up",
            recent_tail=[],
            dependencies=[],
            token_budget=112000,
            digest="2" * 64,
            created_at=NOW,
        ),
    }
    contexts.get = lambda ref: packs.get(ref)
    for member in members[:-1]:
        projected = projection_by_turn[member.turn_id]
        runtime.executions[member.execution_id] = runtime.current.model_copy(
            update={
                "execution_id": member.execution_id,
                "turn_id": member.turn_id,
                "state": projected.execution_status,
                "context_pack_ref": (
                    f"context-{member.turn_id}"
                    if projected.execution_status is ExecutionState.TERMINAL_COMPLETED
                    else None
                ),
            }
        )
    application._project_turn = (
        lambda _actor_id, member, _snapshot, _outcome: projection_by_turn[
            member.turn_id
        ]
    )
    command = application._context_command(
        snapshot=runtime.current, actor_id="actor-1", input_text="current"
    )

    assert [item.representative_turn_id for item in command.recent_tail] == [
        "turn-retry-ok", "turn-independent",
    ]
    assert command.recent_tail[0].user_message.text == "root question"
    assert command.recent_tail[0].assistant_message is not None
    assert command.recent_tail[0].assistant_message.text == "recovered answer"
    sentinel = "kh_claim_must_not_reenter_context"
    context_payload = command.model_dump_json()
    assert sentinel not in context_payload
    assert "evidence_review_status" not in context_payload
    assert "assessment_reason_code" not in context_payload
    assert "protected_open_ref" not in context_payload

    from atlas_production.infrastructure.strict_turn_model_adapter import (
        StrictProviderTurnModel,
    )
    from atlas_production.infrastructure.turn_input_projection import (
        ProviderTurnInputProjector,
    )
    from atlas_production.infrastructure.turn_model_input_adapter import (
        PublicOwnerTurnModelInputSource,
    )
    from atlas_production.modules.model_routing.public import (
        ProviderAssistantMessage,
        ProviderCompleted,
    )
    from tests.test_strict_turn_model_adapter import CapturingRouting
    from tests.test_turn_input_projection import (
        _Projections,
        _Routing,
        _completed,
        _snapshot,
    )

    middleware_routing = _Routing(
        [
            _completed({"resolver_context": "resolved"}, 1),
            _completed({"rewritten_question": "current"}, 2),
        ]
    )
    rewritten = ProviderTurnInputProjector(
        middleware_routing, _Projections()
    ).project(
        snapshot=_snapshot(),
        recent_tail=command.recent_tail,
        summary=command.summary,
    )
    assert sentinel not in repr(middleware_routing.requests)

    answer_snapshot = _snapshot().model_copy(
        update={
            "context_pack_ref": "context-claim-isolation",
            "catalog_ref": "catalog-claim-isolation",
            "grant_ref": "grant-claim-isolation",
            "response_language": "zh-TW",
            "applied_guidance_revision": 0,
            "applied_guidance_digest": None,
        }
    )
    answer_context = ContextPackV3(
        context_pack_ref="context-claim-isolation",
        execution_id=answer_snapshot.execution_id,
        input_projection_ref="input-projection-claim-isolation",
        model_user_input=rewritten,
        recent_tail=command.recent_tail,
        summary=None,
        dependencies=[],
        token_budget=112000,
        digest="9" * 64,
        created_at=NOW,
    )
    model_input = PublicOwnerTurnModelInputSource(
        contexts=SimpleNamespace(get=lambda _ref: answer_context),
        grant_resources=SimpleNamespace(
            grant_document_resources=lambda **_facts: SimpleNamespace(resources=[])
        ),
        answer_behavior=NullAnswerBehavior(),
    ).build(
        answer_snapshot,
        observations=[],
        contract_repair_remaining=1,
    )
    answer_routing = CapturingRouting(
        [
            ProviderCompleted(
                provider_request_id="provider-claim-isolation",
                model_ref="model-1",
                finish_reason="stop",
                usage={},
                output={
                    "action": "finalize_answer",
                    "segments": [{"segment_id": "s1", "text": "answer"}],
                    "claimed_evidence_handles": [],
                },
                assistant_message=ProviderAssistantMessage(content="{}"),
            )
        ]
    )
    session = StrictProviderTurnModel(
        answer_routing, record_invocations=False
    ).open_session(model_input)
    session.next_action(model_input, finalize_only=False)
    assert sentinel not in repr(answer_routing.requests[0])


def test_questionable_answer_is_excluded_and_invalidates_legacy_authority_digest() -> None:
    contexts = Contexts()
    application, runtime, _source = _app(Carrier(), contexts=contexts)
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="current", idempotency_key="current-key"),
    )
    current = application._conversations.member
    assert current is not None
    aligned = ConversationTurnMemberV1(
        turn_id="turn-aligned",
        conversation_id="conversation-1",
        execution_id="execution-aligned",
        role="user",
        ordinal=1,
        created_at=NOW,
    )
    questionable = ConversationTurnMemberV1(
        turn_id="turn-questionable",
        conversation_id="conversation-1",
        execution_id="execution-questionable",
        role="user",
        ordinal=2,
        created_at=NOW,
    )
    application._conversations.candidate_turns = lambda _conversation_id: [
        aligned,
        questionable,
        current.model_copy(update={"ordinal": 3}),
    ]
    projections = {
        aligned.turn_id: WorkspaceTurnProjectionV1(
            turn_id=aligned.turn_id,
            execution_id=aligned.execution_id,
            ordinal=1,
            user_input="aligned original question",
            execution_status=ExecutionState.TERMINAL_COMPLETED,
            evidence_review_status="evidence_aligned",
            segments=[
                WorkspaceAnswerSegmentV2(
                    segment_id="segment-aligned",
                    text="aligned answer",
                )
            ],
            citations=[],
            created_at=NOW,
        ),
        questionable.turn_id: WorkspaceTurnProjectionV1(
            turn_id=questionable.turn_id,
            execution_id=questionable.execution_id,
            ordinal=2,
            user_input="questionable original question",
            execution_status=ExecutionState.TERMINAL_COMPLETED,
            evidence_review_status="questionable",
            segments=[
                WorkspaceAnswerSegmentV2(
                    segment_id="segment-questionable",
                    text="unsupported synthetic value alpha answer",
                )
            ],
            citations=[],
            created_at=NOW,
        ),
    }
    application._project_turn = (
        lambda _actor_id, member, _snapshot, _outcome: projections[member.turn_id]
    )
    for member in (aligned, questionable):
        runtime.executions[member.execution_id] = runtime.current.model_copy(
            update={
                "execution_id": member.execution_id,
                "turn_id": member.turn_id,
                "state": ExecutionState.TERMINAL_COMPLETED,
                "context_pack_ref": f"context-{member.turn_id}",
            }
        )
    sources = [
        ContextSummarySourceV3(
            logical_turn_id=aligned.turn_id,
            representative_turn_id=aligned.turn_id,
            representative_content_digest=hashlib.sha256(
                b"aligned rewritten question\0aligned answer"
            ).hexdigest(),
        ),
        ContextSummarySourceV3(
            logical_turn_id=questionable.turn_id,
            representative_turn_id=questionable.turn_id,
            representative_content_digest=hashlib.sha256(
                b"questionable rewritten question\0unsupported synthetic value alpha answer"
            ).hexdigest(),
        ),
    ]
    contaminated_summary = ContextSummaryV4(
        summary_ref="summary-contaminated",
        historical_user_context="The user asked for the pin.",
        assistant_pending_verification_context="The unsupported value is synthetic value alpha.",
        token_count=8,
        sources=sources,
        digest="c" * 64,
    )
    packs = {
        "context-turn-aligned": ContextPackV3(
            context_pack_ref="context-turn-aligned",
            execution_id=aligned.execution_id,
            input_projection_ref="input-projection-aligned",
            model_user_input="aligned rewritten question",
            recent_tail=[],
            dependencies=[],
            token_budget=112000,
            digest="a" * 64,
            created_at=NOW,
        ),
        "context-turn-questionable": ContextPackV3(
            context_pack_ref="context-turn-questionable",
            execution_id=questionable.execution_id,
            input_projection_ref="input-projection-questionable",
            model_user_input="questionable rewritten question",
            recent_tail=[],
            summary=contaminated_summary,
            dependencies=[],
            token_budget=112000,
            digest="b" * 64,
            created_at=NOW,
        ),
    }
    contexts.get = lambda ref: packs.get(ref)

    command = application._context_command(
        snapshot=runtime.current,
        actor_id="actor-1",
        input_text="current",
    )

    assert command.summary is None
    assert [item.user_message.text for item in command.recent_tail] == [
        "aligned rewritten question",
        "questionable rewritten question",
    ]
    assert command.recent_tail[0].assistant_message is not None
    assert command.recent_tail[0].assistant_message.text == "aligned answer"
    assert command.recent_tail[1].assistant_message is None
    assert command.recent_tail[1].direct_document_ids == []
    assert command.recent_tail[1].representative_content_digest == (
        _historical_exchange_content_digest(
            user_text="questionable rewritten question",
            assistant_text="",
            direct_document_ids=[],
        )
    )
    assert "synthetic pending answer" not in command.model_dump_json()
    assert "summary-contaminated" not in command.model_dump_json()


def test_revocation_rebuild_projection_is_direct_only_and_keeps_all_user_messages() -> None:
    authorization = Authorization()
    authorization.current_visibility = lambda *, actor_id, resources: [
        SimpleNamespace(
            resource_ref=item.resource_ref,
            decision=(
                "hidden" if item.resource_ref == "document-revoked" else "visible"
            ),
        )
        for item in resources
    ]
    retrieval = Retrieval()
    contexts = Contexts()
    application, runtime, _source = _app(
        Carrier(),
        authorization=authorization,
        retrieval=retrieval,
        contexts=contexts,
    )
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="current", idempotency_key="current-key"),
    )
    current = application._conversations.member
    assert current is not None
    prior = [
        ConversationTurnMemberV1(
            turn_id=f"turn-{index}",
            conversation_id="conversation-1",
            execution_id=f"execution-{index}",
            role="user",
            ordinal=index,
            created_at=NOW,
        )
        for index in (1, 2, 3)
    ]
    application._conversations.candidate_turns = lambda _conversation_id: [
        *prior,
        current.model_copy(update={"ordinal": 4}),
    ]
    projections = {
        member.turn_id: WorkspaceTurnProjectionV1(
            turn_id=member.turn_id,
            execution_id=member.execution_id,
            ordinal=member.ordinal,
            user_input=f"question {member.ordinal}",
            execution_status=ExecutionState.TERMINAL_COMPLETED,
            evidence_review_status="evidence_aligned",
            segments=[
                WorkspaceAnswerSegmentV2(
                    segment_id=f"segment-{member.ordinal}",
                    text=f"answer {member.ordinal}",
                )
            ],
            citations=[],
            created_at=NOW,
        )
        for member in prior
    }
    for member in prior:
        runtime.executions[member.execution_id] = runtime.current.model_copy(
            update={
                "execution_id": member.execution_id,
                "turn_id": member.turn_id,
                "state": ExecutionState.TERMINAL_COMPLETED,
                "context_pack_ref": f"context-{member.ordinal}",
            }
        )
    application._project_turn = (
        lambda _actor_id, member, _snapshot, _outcome: projections[member.turn_id]
    )
    runtime.terminal_outcome = lambda execution_id: SimpleNamespace(
        outcome="completed",
        evidence_pack_ref=f"evidence-{execution_id}",
    )

    def evidence_pack(ref):
        index = int(ref.rsplit("-", 1)[-1])
        resource_ref = "document-revoked" if index == 1 else f"document-{index}"
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    resource_ref=resource_ref,
                    lifecycle_epoch=1,
                    document_version_ref=f"version-{index}",
                    processing_generation_ref=f"processing-{index}",
                    index_generation_ref=f"index-{index}",
                )
            ]
        )

    retrieval.read_evidence_pack = evidence_pack
    sources = [
        ContextSummarySourceV3(
            logical_turn_id=f"turn-{index}",
            representative_turn_id=f"turn-{index}",
            representative_content_digest=_historical_exchange_content_digest(
                user_text=f"question {index}",
                assistant_text=f"answer {index}",
                direct_document_ids=[
                    "document-revoked" if index == 1 else f"document-{index}"
                ],
            ),
            direct_document_ids=[
                "document-revoked" if index == 1 else f"document-{index}"
            ],
        )
        for index in (1, 2)
    ]
    previous_summary = ContextSummaryV4(
        summary_ref="summary-old",
        historical_user_context="old user summary",
        assistant_pending_verification_context="old assistant summary",
        token_count=2,
        sources=sources,
        digest="f" * 64,
    )
    packs = {
        f"context-{index}": ContextPackV3(
            context_pack_ref=f"context-{index}",
            execution_id=f"execution-{index}",
            input_projection_ref=f"input-projection-{index}",
            model_user_input=f"question {index}",
            recent_tail=[],
            summary=previous_summary if index == 3 else None,
            dependencies=[],
            token_budget=112000,
            digest=f"{index:064x}",
            created_at=NOW,
        )
        for index in (1, 2, 3)
    }
    contexts.get = lambda ref: packs.get(ref)

    command = application._context_command(
        snapshot=runtime.current,
        actor_id="actor-1",
        input_text="current",
    )

    assert command.summary is None
    assert [item.user_message.text for item in command.recent_tail] == [
        "question 1",
        "question 2",
        "question 3",
    ]
    assert command.recent_tail[0].assistant_message is None
    assert command.recent_tail[1].assistant_message is not None
    assert command.recent_tail[2].assistant_message is not None


def test_summary_reuse_does_not_bind_to_prior_route_or_tokenizer_revision() -> None:
    contexts = Contexts()
    application, runtime, _source = _app(Carrier(), contexts=contexts)
    application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="current", idempotency_key="current-key"),
    )
    current = application._conversations.member
    assert current is not None
    prior = ConversationTurnMemberV1(
        turn_id="turn-prior",
        conversation_id="conversation-1",
        execution_id="execution-prior",
        role="user",
        ordinal=1,
        created_at=NOW,
    )
    application._conversations.candidate_turns = lambda _conversation_id: [
        prior,
        current.model_copy(update={"ordinal": 2}),
    ]
    old_route = runtime.current.route.model_copy(
        update={
            "route_revision": runtime.current.route.route_revision + 1,
            "runtime_policy_revision": (
                runtime.current.route.runtime_policy_revision + 1
            ),
            "tokenizer_profile": "o200k_base",
        }
    )
    runtime.executions[prior.execution_id] = runtime.current.model_copy(
        update={
            "execution_id": prior.execution_id,
            "turn_id": prior.turn_id,
            "state": ExecutionState.TERMINAL_COMPLETED,
            "context_pack_ref": "context-prior",
            "route": old_route,
        }
    )
    projection = WorkspaceTurnProjectionV1(
        turn_id=prior.turn_id,
        execution_id=prior.execution_id,
        ordinal=1,
        user_input="prior question",
        execution_status=ExecutionState.TERMINAL_COMPLETED,
        evidence_review_status="evidence_aligned",
        segments=[
            WorkspaceAnswerSegmentV2(
                segment_id="segment-prior",
                text="prior answer",
            )
        ],
        citations=[],
        created_at=NOW,
    )
    application._project_turn = (
        lambda _actor_id, _member, _snapshot, _outcome: projection
    )
    source = ContextSummarySourceV3(
        logical_turn_id=prior.turn_id,
        representative_turn_id=prior.turn_id,
        representative_content_digest=_historical_exchange_content_digest(
            user_text="prior question",
            assistant_text="prior answer",
            direct_document_ids=[],
        ),
    )
    summary = ContextSummaryV4(
        summary_ref="summary-prior",
        historical_user_context="prior user summary",
        assistant_pending_verification_context="prior assistant summary",
        token_count=2,
        sources=[source],
        digest="e" * 64,
    )
    prior_pack = ContextPackV3(
        context_pack_ref="context-prior",
        execution_id=prior.execution_id,
        input_projection_ref="input-projection-prior",
        model_user_input="prior question",
        recent_tail=[],
        summary=summary,
        dependencies=[],
        token_budget=112000,
        digest="d" * 64,
        created_at=NOW,
    )
    contexts.get = lambda ref: prior_pack if ref == "context-prior" else None

    command = application._context_command(
        snapshot=runtime.current,
        actor_id="actor-1",
        input_text="current",
    )

    assert command.summary is not None
    assert command.summary.summary_ref == "summary-prior"
    assert command.recent_tail == []
    assert "turn-retry-failed" not in {edge.source_turn_id for edge in command.source_lineage}


def test_first_http_response_keeps_owner_failure_execution_observable() -> None:
    authorization = Authorization()
    authorization.create_grant = lambda _command: (_ for _ in ()).throw(
        RuntimeError("grant failed")
    )
    application, _runtime, _source = _app(Carrier(), authorization=authorization)

    class Principal:
        def current_user(self, _token):
            return ACTOR

    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=Principal(), workspace_turn=application)
    client = TestClient(create_app(ApiComposition(**values)))

    accepted = client.post(
        "/api/v1/workspace/conversations/conversation-1/turns",
        json={"input_text": "question", "idempotency_key": "owner-failure"},
    )

    assert accepted.status_code == 202
    payload = accepted.json()
    status = client.get(payload["status_url"])
    events = client.get(payload["events_url"])
    assert status.status_code == 200
    assert status.json()["state"] == "terminal_failed"
    assert events.status_code == 200
    assert "event: terminal_failed" in events.text


def test_archive_race_during_membership_publication_returns_bounded_conflict() -> None:
    class ArchiveRaceConversations(Conversations):
        def append_turn_member(self, *, actor_id, command):
            self.conversation = self.conversation.model_copy(
                update={"status": "archived"}
            )
            raise ConversationMembershipConflict(
                "conversation archived before membership publication"
            )

    conversations = ArchiveRaceConversations()
    application, runtime, _source = _app(
        Carrier(), conversations=conversations
    )

    class Principal:
        def current_user(self, _token):
            return ACTOR

    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=Principal(), workspace_turn=application)
    client = TestClient(create_app(ApiComposition(**values)))

    conflict = client.post(
        "/api/v1/workspace/conversations/conversation-1/turns",
        json={"input_text": "question", "idempotency_key": "archive-race"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "conversation_changed"
    assert conflict.json()["message_code"] == (
        "conversation.changed_before_turn_was_accepted"
    )
    assert conversations.conversation.status == "archived"
    execution = next(iter(runtime.executions.values()))
    assert execution.state is ExecutionState.TERMINAL_FAILED
    assert conversations.member is None


@pytest.mark.parametrize(
    "failure_code",
    [
        "summary_generation_failed",
        "context_limit_exceeded",
        "resolver_failed",
        "rewrite_failed",
    ],
)
def test_http_status_and_events_expose_typed_context_prerequisite_failure(
    failure_code,
) -> None:
    class Failure(RuntimeError):
        safe_code = failure_code

    class FailingPreparer:
        def prepare(self, _command, _snapshot, *, catalog_document_count):
            raise Failure(failure_code)

    application, _runtime, _source = _app(
        Carrier(), context_preparer=FailingPreparer()
    )

    class Principal:
        def current_user(self, _token):
            return ACTOR

    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=Principal(), workspace_turn=application)
    client = TestClient(create_app(ApiComposition(**values)))

    accepted = client.post(
        "/api/v1/workspace/conversations/conversation-1/turns",
        json={
            "input_text": "question",
            "idempotency_key": f"http-{failure_code}",
        },
    )

    assert accepted.status_code == 202
    payload = accepted.json()
    status = client.get(payload["status_url"])
    events = client.get(payload["events_url"])
    assert status.status_code == 200
    assert status.json()["state"] == "terminal_failed"
    assert status.json()["failure_code"] == failure_code
    assert events.status_code == 200
    assert "event: terminal_failed" in events.text
    assert f'"failure_code": "{failure_code}"' in events.text


def test_carrier_start_failure_returns_identity_and_terminal_fails_without_retry() -> None:
    application, runtime, _source = _app(Carrier(fail=True))

    accepted = application.accept_turn(
        ACTOR,
        "conversation-1",
        WorkspaceTurnCreateV1(input_text="question", idempotency_key="key-2"),
    )

    assert accepted.execution_id == runtime.current.execution_id
    assert runtime.current.state is ExecutionState.TERMINAL_FAILED
    assert runtime.failed[0].failure_code == "contract_violation"
