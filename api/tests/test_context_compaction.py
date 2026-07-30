from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.context_compaction import (
    ContextCompactionFailure,
    ProviderContextSummaryGenerator,
    SynchronousContextCompactor,
)
from atlas_production.infrastructure.strict_turn_model_adapter import (
    StrictProviderTurnModel,
)
from atlas_production.modules.context_engineering.public import (
    ContextExchangeV3,
    ContextLineageEdgeV3,
    ContextMessageV3,
    ContextSummaryInputV3,
    ContextSummarySourceV3,
    ModelUserInputV3,
    ModelUserTextSegmentV3,
    MaterializeContextPackV3,
)
from atlas_production.modules.model_routing.public import (
    ProviderAssistantMessage,
    ProviderCompleted,
    ProviderIncomplete,
    ProviderSystemMessage,
    ProviderUserMessage,
)

from tests.test_turn_model_loop import Runtime
from tests.answer_behavior_fixtures import NullAnswerBehavior


def _exchange(index: int) -> ContextExchangeV3:
    return ContextExchangeV3(
        logical_turn_id=f"logical-{index}",
        representative_turn_id=f"turn-{index}",
        representative_content_digest=f"{index:064x}",
        user_message=ContextMessageV3(role="user", text=f"question {index}"),
        assistant_message=ContextMessageV3(
            role="assistant",
            text=f"answer {index}",
            verification_status="verified",
        ),
        direct_document_ids=[f"document-{index}"],
    )


def _command(
    exchanges: list[ContextExchangeV3],
    *,
    summary: ContextSummaryInputV3 | None = None,
) -> MaterializeContextPackV3:
    edges = [
        ContextLineageEdgeV3(
            dependent_turn_id="current-turn",
            dependent_context_pack_ref="context-current",
            source_turn_id=item.representative_turn_id,
            source_resource_kind="turn",
            dependency_kind="recent_turn",
        )
        for item in exchanges
    ]
    if summary is not None:
        edges.extend(
            ContextLineageEdgeV3(
                dependent_turn_id="current-turn",
                dependent_context_pack_ref="context-current",
                source_turn_id=source.representative_turn_id,
                source_resource_ref=summary.summary_ref,
                source_resource_kind="summary",
                dependency_kind="summary_source",
            )
            for source in summary.sources
        )
    return MaterializeContextPackV3(
        context_pack_ref="context-current",
        execution_id="exec-1",
        input_projection_ref="input-projection-1",
        conversation_id="conversation-1",
        dependent_turn_id="current-turn",
        model_user_input=ModelUserInputV3(
            content_segments=[ModelUserTextSegmentV3(text="current question")]
        ),
        recent_tail=exchanges,
        summary=summary,
        source_lineage=edges,
        token_budget=112000,
        idempotency_key="context-key",
    )


class _Sizer:
    def __init__(self, tokens: int | list[int]) -> None:
        self.tokens = [tokens] if isinstance(tokens, int) else tokens
        self.calls = 0

    def _next(self) -> int:
        value = self.tokens[min(self.calls, len(self.tokens) - 1)]
        self.calls += 1
        return value

    def estimate_initial_request_tokens_unchecked(self, _model_input) -> int:
        return self._next()

    def estimate_initial_request_tokens(self, _model_input) -> int:
        return self._next()


class _Generator:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return "compacted summary", 3


class _Projector:
    def __init__(self, rewritten: str = "current question") -> None:
        self.rewritten = rewritten
        self.calls = []

    def project(self, **kwargs):
        self.calls.append(kwargs)
        return self.rewritten


def test_below_85_percent_reuses_uncompacted_tail() -> None:
    command = _command([_exchange(index) for index in range(1, 5)])
    generator = _Generator()
    compactor = SynchronousContextCompactor(
        turn_model=_Sizer(90000),
        summary_generator=generator,
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    prepared = compactor.prepare(
        command, Runtime().snapshot_value, catalog_document_count=2
    )

    assert prepared == command
    assert generator.calls == []


def test_85_percent_compacts_eligible_history_and_keeps_last_two_exchanges() -> None:
    old_source = ContextSummarySourceV3(
        logical_turn_id="logical-0",
        representative_turn_id="turn-0",
        representative_content_digest="0" * 64,
        direct_document_ids=["document-0"],
    )
    parent = ContextSummaryInputV3(
        summary_ref="summary-0",
        text="old summary",
        token_count=2,
        sources=[old_source],
    )
    command = _command(
        [_exchange(index) for index in range(1, 5)], summary=parent
    )
    generator = _Generator()
    sizer = _Sizer([108800, 50000])
    compactor = SynchronousContextCompactor(
        turn_model=sizer,
        summary_generator=generator,
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    prepared = compactor.prepare(
        command, Runtime().snapshot_value, catalog_document_count=2
    )

    assert [item.logical_turn_id for item in prepared.recent_tail] == [
        "logical-3",
        "logical-4",
    ]
    assert prepared.summary is not None
    assert prepared.summary.parent_summary_ref == "summary-0"
    assert [source.logical_turn_id for source in prepared.summary.sources] == [
        "logical-0",
        "logical-1",
        "logical-2",
    ]
    assert generator.calls[0]["parent_summary"] is parent
    assert [item.logical_turn_id for item in generator.calls[0]["exchanges"]] == [
        "logical-1",
        "logical-2",
    ]
    assert sizer.calls == 2


def test_tool_followup_headroom_compacts_below_85_percent() -> None:
    command = _command([_exchange(index) for index in range(1, 5)])
    generator = _Generator()
    sizer = _Sizer([97000, 50000])
    compactor = SynchronousContextCompactor(
        turn_model=sizer,
        summary_generator=generator,
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    prepared = compactor.prepare(
        command, Runtime().snapshot_value, catalog_document_count=2
    )

    assert len(generator.calls) == 1
    assert [item.logical_turn_id for item in prepared.recent_tail] == [
        "logical-3",
        "logical-4",
    ]
    assert prepared.summary is not None


def test_compacted_context_without_tool_followup_headroom_fails_closed() -> None:
    command = _command([_exchange(index) for index in range(1, 5)])
    compactor = SynchronousContextCompactor(
        turn_model=_Sizer([97000, 97000]),
        summary_generator=_Generator(),
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    with pytest.raises(ContextCompactionFailure) as error:
        compactor.prepare(
            command, Runtime().snapshot_value, catalog_document_count=2
        )

    assert error.value.safe_code == "context_limit_exceeded"


def test_trigger_without_any_eligible_history_fails_closed() -> None:
    compactor = SynchronousContextCompactor(
        turn_model=_Sizer(108800),
        summary_generator=_Generator(),
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    with pytest.raises(ContextCompactionFailure) as error:
        compactor.prepare(
            _command([_exchange(1), _exchange(2)]),
            Runtime().snapshot_value,
            catalog_document_count=2,
        )

    assert error.value.safe_code == "context_limit_exceeded"


class _Routing:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def open_tested_attempt(self, _route_id):
        snapshot = Runtime().snapshot_value.route
        return SimpleNamespace(
            route=SimpleNamespace(
                route_id=snapshot.route_id,
                revision=snapshot.route_revision,
                runtime_policy=SimpleNamespace(
                    revision=snapshot.runtime_policy_revision,
                    tokenizer_profile=snapshot.tokenizer_profile,
                    context_window_tokens=snapshot.context_window_tokens,
                    max_input_tokens_per_invocation=(
                        snapshot.max_input_tokens_per_invocation
                    ),
                    max_output_tokens_per_invocation=(
                        snapshot.max_output_tokens_per_invocation
                    ),
                    max_tool_result_tokens_per_execution=(
                        snapshot.max_tool_result_tokens_per_execution
                    ),
                    max_total_tokens_per_conversation=(
                        snapshot.max_total_tokens_per_conversation
                    ),
                ),
            )
        )

    def invoke(self, _attempt, request, _schema):
        self.requests.append(request)
        return self.outcomes.pop(0)


def test_raw_context_above_hard_cap_still_compacts_before_enforcement() -> None:
    large_exchanges = [
        _exchange(index).model_copy(
            update={
                "user_message": ContextMessageV3(
                    role="user", text="user " + ("word " * 9990)
                ),
                "assistant_message": ContextMessageV3(
                    role="assistant",
                    text="assistant " + ("a " * 20000),
                    verification_status="verified",
                ),
            }
        )
        for index in range(1, 7)
    ]
    command = _command(large_exchanges)
    generator = _Generator()
    compactor = SynchronousContextCompactor(
        turn_model=StrictProviderTurnModel(
            _Routing([]), record_invocations=False
        ),
        summary_generator=generator,
        input_projector=_Projector(),
        answer_behavior=NullAnswerBehavior(),
    )

    prepared = compactor.prepare(
        command, Runtime().snapshot_value, catalog_document_count=2
    )

    assert len(generator.calls) == 1
    assert len(prepared.recent_tail) == 2
    assert prepared.summary is not None


def _completed(summary: str, *, finish_reason: str = "stop"):
    return ProviderCompleted(
        provider_request_id="provider-summary",
        model_ref="model-1",
        finish_reason=finish_reason,
        usage={},
        output={"summary": summary},
        assistant_message=ProviderAssistantMessage(content=summary),
    )


def test_summary_prompt_separates_system_rules_from_untrusted_history() -> None:
    routing = _Routing([_completed("safe summary")])
    generator = ProviderContextSummaryGenerator(
        routing, record_invocations=False
    )

    text, token_count = generator.generate(
        execution_id="exec-1",
        route=Runtime().snapshot_value.route,
        parent_summary=None,
        exchanges=[_exchange(1)],
    )

    assert text == "safe summary"
    assert token_count > 0
    request = routing.requests[0]
    assert isinstance(request.messages[0], ProviderSystemMessage)
    assert isinstance(request.messages[1], ProviderUserMessage)
    assert request.max_output_tokens == 6000
    assert "untrusted" in request.messages[0].content
    assert "untrusted_raw_transcript" in request.messages[1].content
    summary_wire = "\n".join(
        str(message.content) for message in request.messages
    )
    assert "answer_policy_snapshot" not in summary_wire
    assert "conversation_reply_language" not in summary_wire
    assert "optional_custom_guidance" not in summary_wire


def test_two_truncated_summary_attempts_fail_the_turn_prerequisite() -> None:
    routing = _Routing(
        [
            _completed("partial", finish_reason="length"),
            ProviderIncomplete(
                provider_request_id="provider-summary-2",
                model_ref="model-1",
                finish_reason="length",
                usage={},
                reason="max_output_tokens",
            ),
        ]
    )
    generator = ProviderContextSummaryGenerator(
        routing, record_invocations=False
    )

    with pytest.raises(ContextCompactionFailure) as error:
        generator.generate(
            execution_id="exec-1",
            route=Runtime().snapshot_value.route,
            parent_summary=None,
            exchanges=[_exchange(1)],
        )

    assert error.value.safe_code == "summary_generation_failed"
    assert len(routing.requests) == 2


def test_summary_output_over_6000_tokens_is_rejected_and_retried() -> None:
    routing = _Routing(
        [
            _completed(" word" * 6001),
            _completed("bounded summary"),
        ]
    )
    generator = ProviderContextSummaryGenerator(
        routing, record_invocations=False
    )

    text, token_count = generator.generate(
        execution_id="exec-1",
        route=Runtime().snapshot_value.route,
        parent_summary=None,
        exchanges=[_exchange(1)],
    )

    assert text == "bounded summary"
    assert token_count <= 6000
    assert len(routing.requests) == 2
