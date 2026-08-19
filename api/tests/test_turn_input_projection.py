from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.turn_input_projection import (
    ProviderTurnInputProjector,
    TurnInputProjectionFailure,
)
from atlas_production.modules.context_engineering.public import (
    ContextExchangeV3,
    ContextMessageV3,
    ContextSummaryInputV4,
    ContextSummarySourceV3,
    CreateTurnInputProjectionV1,
    RecordResolverProjectionV1,
    RecordRewriteProjectionV1,
    TurnInputProjectionV1,
)
from atlas_production.modules.model_routing.public import (
    ProviderAssistantMessage,
    ProviderCompleted,
    ProviderIncomplete,
    ProviderRefused,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogV1,
    PromptSkillInstructionsV1,
    PromptSkillRefV1,
    PromptSkillSelectorCandidateV1,
)
from atlas_production.modules.turn_execution.public import (
    SkillSelectionDecisionV1,
    DeepReasoningContractError,
    SkillSelectionResultV1,
)
from atlas_production.modules.turn_runtime.public import ExecutionState
from tests.test_turn_model_loop import Runtime


NOW = datetime.now(timezone.utc)


def _completed(output: dict[str, str], ordinal: int) -> ProviderCompleted:
    return ProviderCompleted(
        provider_request_id=f"provider-{ordinal}",
        model_ref="model-1",
        finish_reason="stop",
        usage={"input_tokens": ordinal * 10, "output_tokens": ordinal},
        output=output,
        assistant_message=ProviderAssistantMessage(content=f"output-{ordinal}"),
    )


class _Projections:
    def __init__(self) -> None:
        self.value = TurnInputProjectionV1(
            projection_ref="projection-1",
            execution_id="exec-1",
            original_user_input="它跟上一份有什麼差異？",
            created_at=NOW,
            updated_at=NOW,
        )
        self.stages = []

    def create_input_projection(self, _command: CreateTurnInputProjectionV1):
        return self.value

    def get_input_projection(self, execution_id):
        return self.value if execution_id == self.value.execution_id else None

    def record_resolver_projection(self, command: RecordResolverProjectionV1):
        self.stages.append(("resolver", command))
        self.value = self.value.model_copy(
            update={
                "resolver_output": command.resolver_output,
                "resolver_invocation_ref": command.resolver_invocation_ref,
                "resolver_failure_code": command.failure_code,
                "updated_at": NOW,
            }
        )
        return self.value

    def record_rewrite_projection(self, command: RecordRewriteProjectionV1):
        self.stages.append(("rewrite", command))
        self.value = self.value.model_copy(
            update={
                "rewritten_user_input": command.rewritten_user_input,
                "rewrite_invocation_ref": command.rewrite_invocation_ref,
                "rewrite_failure_code": command.failure_code,
                "updated_at": NOW,
            }
        )
        return self.value


class _Routing:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []
        self.prepared = []
        self.started = []
        self.completed = []
        self.failed = []

    def open_tested_attempt(self, _route_id):
        snapshot = Runtime().snapshot_value.route
        policy = SimpleNamespace(
            revision=snapshot.runtime_policy_revision,
            tokenizer_profile=snapshot.tokenizer_profile,
            context_window_tokens=snapshot.context_window_tokens,
            max_input_tokens_per_invocation=snapshot.max_input_tokens_per_invocation,
            max_output_tokens_per_invocation=snapshot.max_output_tokens_per_invocation,
            max_tool_result_tokens_per_execution=(
                snapshot.max_tool_result_tokens_per_execution
            ),
            max_total_tokens_per_conversation=(
                snapshot.max_total_tokens_per_conversation
            ),
            provider_invocation_timeout_seconds=30,
        )
        return SimpleNamespace(
            route=SimpleNamespace(
                route_id=snapshot.route_id,
                revision=snapshot.route_revision,
                runtime_policy=policy,
            )
        )

    def prepare_invocation(self, _route, _schema, **facts):
        handle = SimpleNamespace(
            invocation_id=f"invocation-{len(self.prepared) + 1}",
            **facts,
        )
        self.prepared.append(handle)
        return handle

    def record_invocation_started(self, handle):
        self.started.append(handle.invocation_id)

    def record_invocation_success(self, handle, usage):
        self.completed.append((handle.invocation_id, usage))

    def record_invocation_failure(self, handle, error_code):
        self.failed.append((handle.invocation_id, error_code))

    def invoke(self, _attempt, request, _schema):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _snapshot():
    return Runtime().snapshot_value.model_copy(
        update={
            "execution_id": "exec-1",
            "response_language": "en",
            "applied_guidance_revision": 3,
            "applied_guidance_digest": "d" * 64,
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=120),
        }
    )


def _history():
    return [
        ContextExchangeV3(
            logical_turn_id="logical-1",
            representative_turn_id="turn-1",
            representative_content_digest="a" * 64,
            user_message=ContextMessageV3(
                role="user", text="比較文件 A 與文件 B。"
            ),
            assistant_message=ContextMessageV3(
                role="assistant",
                text="文件 A 已完成比較。",
                verification_status="verified",
            ),
            direct_document_ids=["document-1"],
        )
    ]


def _summary():
    return ContextSummaryInputV4(
        summary_ref="summary-1",
        parent_summary_ref="summary-parent",
        historical_user_context="先前使用者主要討論文件 A。",
        assistant_pending_verification_context="先前助理認為文件 A 已完成。",
        token_count=10,
        sources=[
            ContextSummarySourceV3(
                logical_turn_id="logical-summary",
                representative_turn_id="turn-summary",
                representative_content_digest="b" * 64,
                direct_document_ids=["document-summary"],
            )
        ],
    )


def test_resolver_then_rewrite_use_one_fixed_no_tool_attempt_and_persist_usage() -> None:
    routing = _Routing(
        [
            _completed({"resolver_context": "指的是文件 B 與文件 A。"}, 1),
            _completed(
                {"rewritten_question": "文件 B 與文件 A 有哪些差異？"}, 2
            ),
        ]
    )
    projections = _Projections()

    _, rewritten = ProviderTurnInputProjector(routing, projections).project(
        snapshot=_snapshot(),
        recent_tail=_history(),
        summary=None,
    )

    assert rewritten == "文件 B 與文件 A 有哪些差異？"
    assert [stage for stage, _command in projections.stages] == [
        "resolver",
        "rewrite",
    ]
    assert [item.invocation_purpose for item in routing.prepared] == [
        "context_resolver",
        "context_rewrite",
    ]
    assert [item.subject_kind for item in routing.prepared] == [
        "turn_execution",
        "turn_execution",
    ]
    assert [item.subject_ref for item in routing.prepared] == ["exec-1", "exec-1"]
    assert [item.execution_key for item in routing.prepared] == [
        "exec-1:context-resolver:1",
        "exec-1:context-rewrite:1",
    ]
    assert all(request.tools == [] for request in routing.requests)
    assert all(request.tool_choice == "none" for request in routing.requests)
    wire = json.dumps(
        [request.to_payload() for request in routing.requests],
        ensure_ascii=False,
    )
    assert "response_language" not in wire
    assert "applied_guidance" not in wire
    assert "d" * 64 not in wire
    assert projections.value.resolver_invocation_ref == "invocation-1"
    assert projections.value.rewrite_invocation_ref == "invocation-2"
    assert len(routing.completed) == 2


def test_stage_prompts_define_generic_success_without_fixture_leak() -> None:
    routing = _Routing(
        [
            _completed({"resolver_context": "指的是文件 B 與文件 A。"}, 1),
            _completed(
                {"rewritten_question": "文件 B 與文件 A 有哪些差異？"}, 2
            ),
        ]
    )

    ProviderTurnInputProjector(routing, _Projections()).project(
        snapshot=_snapshot(),
        recent_tail=_history(),
        summary=_summary(),
    )

    resolver_system = routing.requests[0].messages[0].content
    rewrite_system = routing.requests[1].messages[0].content
    assert "Success criteria:" in resolver_system
    assert "Prohibited behaviors:" in resolver_system
    assert "primary communicative intent" in resolver_system
    assert "no new information or action request" in resolver_system
    assert "Distinguish that current intent from the surrounding topic" in resolver_system
    assert "do not present that subject as work to continue" in resolver_system
    assert "Do not turn an acknowledgment" in resolver_system
    assert "assistant offer as the user's current request" in resolver_system
    assert "most specific stable names" in resolver_system
    assert "ordered from oldest to newest" in resolver_system
    assert "final exchange is the most recent" in resolver_system
    assert "highest recency priority" in resolver_system
    assert "most recently explicitly established stable subject" in resolver_system
    assert "Do not revive an older subject merely" in resolver_system
    assert "older context must not silently replace it" in resolver_system
    assert "supersedes conflicting earlier conversational associations" in resolver_system
    assert "materially change the question" in resolver_system
    assert "mark the referent unresolved" in resolver_system
    assert "Do not invent or guess" in resolver_system
    assert "Success criteria:" in rewrite_system
    assert "Prohibited behaviors:" in rewrite_system
    assert "primary communicative intent, dialogue act" in rewrite_system
    assert "same kind of non-request message" in rewrite_system
    assert "does not require adding a subject or action" in rewrite_system
    assert "Do not create, continue, resume, repeat" in rewrite_system
    assert "Do not copy a prior request merely" in rewrite_system
    assert "explicit stable names" in rewrite_system
    assert "message understandable without prior conversation" in rewrite_system
    assert "one concise clarification question" in rewrite_system
    assert "Do not add facts, assumptions, objects, actions" in rewrite_system

    for system_prompt in (resolver_system, rewrite_system):
        assert "文件 A" not in system_prompt
        assert "文件 B" not in system_prompt
        assert "它跟上一份有什麼差異" not in system_prompt
        assert "answer_policy_snapshot" not in system_prompt
        assert "conversation_reply_language" not in system_prompt
        assert "optional_custom_guidance" not in system_prompt

    resolver_payload = json.loads(routing.requests[0].messages[1].content)
    rewrite_payload = json.loads(routing.requests[1].messages[1].content)
    assert set(resolver_payload) == {
        "original_user_input",
        "authorized_rewritten_context",
    }
    assert set(rewrite_payload) == {
        "original_user_input",
        "resolver_context",
    }
    assert resolver_payload["authorized_rewritten_context"] == {
        "summary": {
            "historical_user_context": {
                "text": "先前使用者主要討論文件 A。",
                "authority": "user_provided_history",
            },
            "assistant_pending_verification_context": {
                "text": "先前助理認為文件 A 已完成。",
                "authority": "pending_verification",
                "usage_scope": "dialogue_context_only",
            },
        },
        "recent_exchanges": [
            {
                "user_message": {
                    "text": "比較文件 A 與文件 B。",
                    "authority": "user_provided_history",
                },
                "assistant_message": {
                    "text": "文件 A 已完成比較。",
                    "authority": "pending_verification",
                    "usage_scope": "dialogue_context_only",
                },
            }
        ],
    }
    resolver_policy = json.loads(resolver_system)["history_authority_policy"]
    rewrite_policy = json.loads(rewrite_system)["history_authority_policy"]
    assert resolver_policy == rewrite_policy
    assert resolver_policy["enforcement"] == "soft"
    assert rewrite_payload == {
        "original_user_input": "它跟上一份有什麼差異？",
        "resolver_context": "指的是文件 B 與文件 A。",
    }
    resolver_wire = routing.requests[0].messages[1].content
    rewrite_wire = routing.requests[1].messages[1].content
    for internal_metadata in (
        "logical_turn_id",
        "representative_turn_id",
        "representative_content_digest",
        "verification_status",
        "direct_document_ids",
        "summary_ref",
        "parent_summary_ref",
        "token_count",
        "document-1",
        "document-summary",
    ):
        assert internal_metadata not in resolver_wire
        assert internal_metadata not in rewrite_wire


def test_resolver_failure_records_stage_and_never_calls_rewrite() -> None:
    routing = _Routing(
        [
            ProviderRefused(
                provider_request_id="provider-refused",
                model_ref="model-1",
                finish_reason="stop",
                usage={},
                reason_code="provider_refused",
                message_code=None,
            )
        ]
    )
    projections = _Projections()

    with pytest.raises(TurnInputProjectionFailure) as error:
        ProviderTurnInputProjector(routing, projections).project(
            snapshot=_snapshot(),
            recent_tail=_history(),
            summary=None,
        )

    assert error.value.safe_code == "resolver_failed"
    assert [stage for stage, _command in projections.stages] == ["resolver"]
    assert projections.value.resolver_failure_code == "provider_refused"
    assert projections.value.resolver_invocation_ref == "invocation-1"
    assert len(routing.requests) == 1
    assert routing.completed == []
    assert routing.failed == [("invocation-1", "provider_refused")]


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        (
            ProviderIncomplete(
                provider_request_id="provider-incomplete",
                model_ref="model-1",
                finish_reason="length",
                usage={},
                reason="max_output_tokens",
            ),
            "provider_incomplete",
        ),
        (RuntimeError("transport failed"), "resolver_provider_failed"),
    ],
)
def test_resolver_noncompleted_failures_preserve_invocation_ref(
    outcome, failure_code
) -> None:
    routing = _Routing([outcome])
    projections = _Projections()

    with pytest.raises(TurnInputProjectionFailure):
        ProviderTurnInputProjector(routing, projections).project(
            snapshot=_snapshot(),
            recent_tail=_history(),
            summary=None,
        )

    assert projections.value.resolver_invocation_ref == "invocation-1"
    assert projections.value.resolver_failure_code == failure_code
    assert routing.failed == [("invocation-1", failure_code)]


def test_completed_invalid_resolver_output_counts_usage_and_preserves_ref() -> None:
    routing = _Routing([_completed({"wrong_field": "value"}, 1)])
    projections = _Projections()

    with pytest.raises(TurnInputProjectionFailure):
        ProviderTurnInputProjector(routing, projections).project(
            snapshot=_snapshot(),
            recent_tail=_history(),
            summary=None,
        )

    assert routing.completed == [
        ("invocation-1", {"input_tokens": 10, "output_tokens": 1})
    ]
    assert routing.failed == []
    assert projections.value.resolver_invocation_ref == "invocation-1"
    assert projections.value.resolver_failure_code == "invalid_resolver_output"


def test_completed_invalid_rewrite_output_counts_usage_and_preserves_ref() -> None:
    routing = _Routing(
        [
            _completed({"resolver_context": "文件 A。"}, 1),
            _completed({"wrong_field": "value"}, 2),
        ]
    )
    projections = _Projections()

    with pytest.raises(TurnInputProjectionFailure) as error:
        ProviderTurnInputProjector(routing, projections).project(
            snapshot=_snapshot(),
            recent_tail=_history(),
            summary=None,
        )

    assert error.value.safe_code == "rewrite_failed"
    assert routing.completed == [
        ("invocation-1", {"input_tokens": 10, "output_tokens": 1}),
        ("invocation-2", {"input_tokens": 20, "output_tokens": 2}),
    ]
    assert routing.failed == []
    assert projections.value.rewrite_invocation_ref == "invocation-2"
    assert projections.value.rewrite_failure_code == "invalid_rewrite_output"


def test_invalid_resolver_output_retries_with_shared_turn_budget() -> None:
    routing = _Routing(
        [
            _completed({"wrong_field": "value"}, 1),
            _completed({"resolver_context": "文件 A。"}, 2),
            _completed({"rewritten_question": "比較文件 A 與上一份文件。"}, 3),
        ]
    )
    projections = _Projections()
    runtime = Runtime()
    snapshot = runtime.snapshot_value.model_copy(
        update={
            "execution_id": "exec-1",
            "response_language": "en",
            "applied_guidance_revision": 3,
            "applied_guidance_digest": "d" * 64,
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=120),
        }
    )

    _, rewritten = ProviderTurnInputProjector(
        routing, projections, runtime
    ).project(snapshot=snapshot, recent_tail=_history(), summary=None)

    assert rewritten == "比較文件 A 與上一份文件。"
    assert len(routing.requests) == 3
    assert runtime.snapshot_value.budget.schema_retries == 1
    assert routing.prepared[1].repair_origin_error_codes == [
        "invalid_resolver_output"
    ]
    assert routing.failed == []
    assert projections.value.rewrite_invocation_ref == "invocation-3"


def test_transport_valid_semantically_wrong_rewrite_proceeds_without_validation() -> None:
    routing = _Routing(
        [
            _completed({"resolver_context": "可能是文件 B。"}, 1),
            _completed({"rewritten_question": "談談火星。"}, 2),
        ]
    )

    _, rewritten = ProviderTurnInputProjector(routing, _Projections()).project(
        snapshot=_snapshot(),
        recent_tail=_history(),
        summary=None,
    )

    assert rewritten == "談談火星。"

def test_understanding_selection_is_persisted_once_and_only_reaches_resolver() -> None:
    candidate_ref = PromptSkillRefV1(
        category="understanding",
        name="resolve-followups",
        revision=1,
        content_digest="a" * 64,
    )
    candidate = PromptSkillSelectorCandidateV1(
        selection_id="understanding:resolve-followups:1:" + "a" * 64,
        name="resolve-followups",
        description="Resolve follow-up references.",
        ref=candidate_ref,
    )

    class Catalog:
        def read_catalog(self, ref):
            return PromptSkillCatalogV1(ref=ref, skills=[candidate])

        def read_instructions(self, ref):
            assert ref == candidate_ref
            return PromptSkillInstructionsV1(
                name=ref.name,
                revision=ref.revision,
                content_digest=ref.content_digest,
                instructions="Prefer the most recent explicit stable subject.",
            )

    class Selector:
        def __init__(self):
            self.requests = []
            self.estimates = []

        def estimate_selection_request_tokens(self, snapshot, request):
            self.estimates.append((snapshot, request))
            return 7

        def select(self, snapshot, request):
            self.requests.append((snapshot, request))
            return SkillSelectionResultV1(
                decision=SkillSelectionDecisionV1(
                    selected_skill_ids=[candidate.selection_id]
                )
            )

    routing = _Routing(
        [
            _completed({"resolver_context": "文件 A。"}, 1),
            _completed({"rewritten_question": "比較文件 A。"}, 2),
        ]
    )
    projections = _Projections()
    runtime = Runtime()
    accepted = runtime.snapshot_value.model_copy(
        update={
            "state": ExecutionState.ACCEPTED,
            "version": 2,
            "prompt_skill_selections": [],
        }
    )
    runtime.snapshot_value = accepted
    selector = Selector()

    refreshed, rewritten = ProviderTurnInputProjector(
        routing,
        projections,
        runtime,
        prompt_skill_catalog=Catalog(),
        prompt_skill_exact_reader=Catalog(),
        skill_selector_model=selector,
    ).project(snapshot=accepted, recent_tail=_history(), summary=None)

    assert rewritten == "比較文件 A。"
    assert len(selector.requests) == 1
    assert len(selector.estimates) == 1
    assert selector.estimates[0][1] is selector.requests[0][1]
    assert selector.requests[0][0].budget.provider_invocations == 1
    assert refreshed.budget.provider_invocations == 1
    assert refreshed.budget.context_tokens == 7
    selector_context = selector.requests[0][1].node_context
    assert selector_context.original_user_input == projections.value.original_user_input
    assert selector_context.authorized_rewritten_context["recent_exchanges"]
    assert refreshed.prompt_skill_selections[0].selected_skills == [candidate_ref]
    resolver_contract = json.loads(routing.requests[0].messages[0].content)
    rewrite_contract = json.loads(routing.requests[1].messages[0].content)
    assert resolver_contract["optional_understanding_skills"][0][
        "instructions"
    ] == "Prefer the most recent explicit stable subject."
    assert "optional_understanding_skills" not in rewrite_contract

def test_understanding_selector_failure_runs_baseline_resolver_without_retry() -> None:
    candidate = PromptSkillSelectorCandidateV1(
        selection_id="understanding:baseline:1:" + "b" * 64,
        name="baseline",
        description="Optional understanding guidance.",
        ref=PromptSkillRefV1(
            category="understanding",
            name="baseline",
            revision=1,
            content_digest="b" * 64,
        ),
    )

    class Catalog:
        def read_catalog(self, ref):
            return PromptSkillCatalogV1(ref=ref, skills=[candidate])

        def read_instructions(self, ref):
            raise AssertionError("failed selector cannot exact-read instructions")

    class Selector:
        def __init__(self):
            self.calls = 0

        def estimate_selection_request_tokens(self, snapshot, request):
            return 7

        def select(self, snapshot, request):
            self.calls += 1
            raise DeepReasoningContractError("selector_contract_invalid")

    routing = _Routing(
        [
            _completed({"resolver_context": "baseline resolver context"}, 1),
            _completed({"rewritten_question": "baseline rewritten request"}, 2),
        ]
    )
    runtime = Runtime()
    accepted = runtime.snapshot_value.model_copy(
        update={
            "state": ExecutionState.ACCEPTED,
            "version": 2,
            "prompt_skill_selections": [],
        }
    )
    runtime.snapshot_value = accepted
    selector = Selector()

    refreshed, rewritten = ProviderTurnInputProjector(
        routing,
        _Projections(),
        runtime,
        prompt_skill_catalog=Catalog(),
        prompt_skill_exact_reader=Catalog(),
        skill_selector_model=selector,
    ).project(snapshot=accepted, recent_tail=_history(), summary=None)

    assert selector.calls == 1
    assert refreshed.budget.provider_invocations == 1
    assert refreshed.budget.context_tokens == 7
    assert rewritten == "baseline rewritten request"
    assert len(routing.requests) == 2
    selection = refreshed.prompt_skill_selections[0]
    assert selection.status == "baseline_fallback"
    assert selection.fallback_code == "selector_contract_invalid"
    assert selection.selected_skills == []
    assert "optional_understanding_skills" not in json.loads(
        routing.requests[0].messages[0].content
    )


def test_understanding_selector_budget_exhaustion_skips_provider_call() -> None:
    candidate = PromptSkillSelectorCandidateV1(
        selection_id="understanding:budget:1:" + "c" * 64,
        name="budget",
        description="Optional understanding guidance.",
        ref=PromptSkillRefV1(
            category="understanding",
            name="budget",
            revision=1,
            content_digest="c" * 64,
        ),
    )

    class Catalog:
        def read_catalog(self, ref):
            return PromptSkillCatalogV1(ref=ref, skills=[candidate])

        def read_instructions(self, ref):
            raise AssertionError("budget-exhausted selector cannot read instructions")

    class Selector:
        def __init__(self):
            self.calls = 0

        def estimate_selection_request_tokens(self, snapshot, request):
            return 7

        def select(self, snapshot, request):
            self.calls += 1
            raise AssertionError("budget-exhausted selector cannot call Provider")

    routing = _Routing(
        [
            _completed({"resolver_context": "baseline resolver context"}, 1),
            _completed({"rewritten_question": "baseline rewritten request"}, 2),
        ]
    )
    runtime = Runtime()
    exhausted_budget = runtime.snapshot_value.budget.model_copy(
        update={
            "provider_invocations": (
                runtime.snapshot_value.policy.max_provider_invocations
            )
        }
    )
    accepted = runtime.snapshot_value.model_copy(
        update={
            "state": ExecutionState.ACCEPTED,
            "version": 2,
            "budget": exhausted_budget,
            "prompt_skill_selections": [],
        }
    )
    runtime.snapshot_value = accepted
    selector = Selector()

    refreshed, rewritten = ProviderTurnInputProjector(
        routing,
        _Projections(),
        runtime,
        prompt_skill_catalog=Catalog(),
        prompt_skill_exact_reader=Catalog(),
        skill_selector_model=selector,
    ).project(snapshot=accepted, recent_tail=_history(), summary=None)

    assert selector.calls == 0
    assert rewritten == "baseline rewritten request"
    assert refreshed.budget.provider_invocations == (
        refreshed.policy.max_provider_invocations
    )
    selection = refreshed.prompt_skill_selections[0]
    assert selection.status == "baseline_fallback"
    assert selection.fallback_code == "selector_unavailable"
    assert selection.selected_skills == []
    assert "optional_understanding_skills" not in json.loads(
        routing.requests[0].messages[0].content
    )
