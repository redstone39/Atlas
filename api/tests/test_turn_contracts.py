from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from atlas_production.modules.conversation.public import (
    AppendTurnMemberV1,
    ConversationCreateV1,
)
from atlas_production.modules.authorization.public import CreateTurnAccessGrantV1
from atlas_production.modules.context_engineering.public import (
    ContextLineageGraphV3,
)
from atlas_production.modules.model_routing.api_models import ModelRouteCreateRequest
from atlas_production.modules.retrieval.public import (
    FindKnowledgeDocumentsV1,
    KnowledgeSearchResultV1,
    KnowledgeToolObservationV1,
    KnowledgeToolObservationEnvelopeV1,
    SearchKnowledgeV1,
    knowledge_tool_observation_schema,
)
from atlas_production.modules.turn_execution.public import (
    FinalizeAnswerV1,
    TurnModelInputV3,
    TurnActionV1,
    turn_action_schema,
)
from atlas_production.modules.turn_runtime.public import (
    ExecutionState,
    FailCarrierExecutionV1,
    FinalizeExpiredExecutionV1,
    LeasePolicyV1,
    ProcessScoreV1,
    ReasoningPlanItemV2,
    ReasoningPlanV2,
    ReasoningTraceV3,
    RoutePolicyV1,
    TERMINAL_STATES,
)
from atlas_production.providers import build_native_json_schema
from tests import model_route_runtime_policy


def _assert_closed_objects(schema: Any) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, schema
        for value in schema.values():
            _assert_closed_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_closed_objects(value)


def test_all_turn_action_objects_are_closed() -> None:
    _assert_closed_objects(turn_action_schema())
    _assert_closed_objects(turn_action_schema(finalize_only=True))
    _assert_closed_objects(TypeAdapter(KnowledgeToolObservationV1).json_schema())


def test_turn_action_schemas_are_accepted_by_the_provider_native_contract() -> None:
    complete = build_native_json_schema("turn_action_v1", turn_action_schema())
    finalize = build_native_json_schema("finalize_action_v1", turn_action_schema(finalize_only=True))
    assert complete.strict is True
    assert finalize.strict is True


def test_tool_observation_schema_is_accepted_by_the_provider_native_contract() -> None:
    observation = build_native_json_schema(
        "knowledge_tool_observation_v1",
        knowledge_tool_observation_schema(),
    )
    assert observation.strict is True
    with pytest.raises(ValidationError):
        KnowledgeToolObservationEnvelopeV1.model_validate(
            {
                "observation": {
                    "result_type": "knowledge_search_result",
                    "evidence": [],
                    "next_cursor": None,
                    "document_id": "secret",
                }
            }
        )


def test_unknown_action_and_unknown_fields_are_rejected() -> None:
    adapter = TypeAdapter(TurnActionV1)
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "delete_knowledge", "query_text": "x"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "action": "search_knowledge",
                "query_text": "policy",
                "document_handles": [],
                "required_modalities": [],
                "facet_hints": {
                    "document_types": [],
                    "date_from": None,
                    "date_to": None,
                    "languages": [],
                    "tags": [],
                },
                "limit": 20,
                "max_output_tokens": 16000,
                "acl_decision": "allow",
            }
        )


def test_finalize_action_cannot_claim_runtime_verification_or_citation_urls() -> None:
    with pytest.raises(ValidationError):
        FinalizeAnswerV1.model_validate(
            {
                "action": "finalize_answer",
                "segments": [
                    {
                        "segment_id": "s1",
                        "text": "answer",
                        "verification_status": "verified",
                        "citation_url": "https://example.invalid",
                    }
                ],
            }
        )


def test_catalog_discovery_and_selected_document_search_are_typed() -> None:
    find = FindKnowledgeDocumentsV1(
        action="find_knowledge_documents",
        keyword="Policy",
        cursor=None,
    )
    assert find.model_dump(mode="json") == {
        "action": "find_knowledge_documents",
        "keyword": "Policy",
        "cursor": None,
    }
    search = SearchKnowledgeV1(
        action="search_knowledge",
        query_text="retention period",
        document_handles=["kh_document_0001"],
        required_modalities=["text"],
        facet_hints={
            "document_types": [],
            "date_from": None,
            "date_to": None,
            "languages": [],
            "tags": [],
        },
        limit=20,
        max_output_tokens=16000,
    )
    assert search.document_handles == ["kh_document_0001"]
    with pytest.raises(ValidationError):
        SearchKnowledgeV1(
            action="search_knowledge",
            query_text="retention period",
            document_handles=[],
            required_modalities=[],
            facet_hints={
                "document_types": [],
                "date_from": None,
                "date_to": None,
                "languages": [],
                "tags": [],
            },
            limit=20,
            max_output_tokens=16000,
        )


def test_document_discovery_requires_a_meaningful_identity_keyword() -> None:
    with pytest.raises(ValidationError):
        FindKnowledgeDocumentsV1(
            action="find_knowledge_documents",
            keyword=" -- ",
            cursor=None,
        )


def test_tool_results_and_non_turn_public_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchResultV1.model_validate(
            {"result_type": "knowledge_search_result", "evidence": [], "next_cursor": None, "document_id": "secret"}
        )
    with pytest.raises(ValidationError):
        ConversationCreateV1.model_validate({"title": "x", "knowledge_scope": ["legacy"]})


def test_policy_defaults_match_the_approved_hard_limits() -> None:
    policy = RoutePolicyV1()
    assert policy.max_tool_invocations == 12
    assert policy.max_catalog_pages == 5
    assert "max_document_candidates" not in RoutePolicyV1.model_fields
    assert policy.max_search_rounds == 6
    assert policy.max_unique_evidence == 40
    assert policy.context_token_budget == 272_000
    assert policy.tool_token_budget == 64_000
    assert policy.deadline_seconds == 240
    assert policy.max_reasoning_revision_cycles == 2
    assert policy.max_provider_invocations == 26
    assert policy.max_provider_invocations >= (
        policy.max_tool_invocations
        + 4 * policy.max_reasoning_revision_cycles
        + 6
    )
    lease = LeasePolicyV1()
    assert (lease.heartbeat_interval_seconds, lease.ttl_seconds, lease.failure_sweep_interval_seconds) == (5, 15, 5)
    with pytest.raises(ValidationError):
        RoutePolicyV1(max_tool_invocations=12, max_provider_invocations=19)
    assert RoutePolicyV1(
        max_reasoning_revision_cycles=0,
        max_provider_invocations=18,
    ).max_provider_invocations == 18
    assert RoutePolicyV1(
        max_reasoning_revision_cycles=3,
        max_provider_invocations=30,
    ).max_provider_invocations == 30
    with pytest.raises(ValidationError):
        RoutePolicyV1(
            max_reasoning_revision_cycles=3,
            max_provider_invocations=29,
        )
    with pytest.raises(ValidationError):
        LeasePolicyV1(heartbeat_interval_seconds=15, ttl_seconds=15)


def test_reasoning_contracts_are_closed_bounded_and_do_not_claim_accuracy() -> None:
    score = ProcessScoreV1(
        plan_coverage=2,
        evidence_handling=1,
        conflict_handling=1,
        gap_resolution=2,
        revision_completion=1,
        total=7,
    )
    assert score.total == 7
    with pytest.raises(ValidationError):
        ProcessScoreV1.model_validate({**score.model_dump(), "accuracy": 0.9})
    trace = ReasoningTraceV3(
        trace_revision=1,
        trace_digest="a" * 64,
        status="running",
        plans=[ReasoningPlanV2(
            generation=1,
            next_objective="確認需求",
            completion_condition="需求已確認",
            items=[ReasoningPlanItemV2(item_id="plan-1", summary="確認需求")],
        )],
    )
    assert len(trace.model_dump_json().encode("utf-8")) <= 32768
    with pytest.raises(ValidationError):
        ReasoningTraceV3.model_validate(
            {**trace.model_dump(), "raw_draft": "private model output"}
        )
    with pytest.raises(ValidationError):
        ReasoningTraceV3(
            trace_revision=1,
            trace_digest="b" * 64,
            status="running",
            plans=[
                trace.plans[0],
                ReasoningPlanV2(
                    generation=2,
                    parent_generation=1,
                    next_objective="補查",
                    completion_condition="完成",
                    items=[
                        ReasoningPlanItemV2(
                            item_id="plan-2",
                            summary="遺漏既有 pending 項目",
                        )
                    ],
                ),
            ],
        )


def test_fresh_membership_requires_mode_and_retry_cannot_change_it() -> None:
    base = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "role": "user",
        "idempotency_key": "member-1",
    }
    assert AppendTurnMemberV1(**base, reasoning_mode="deep").reasoning_mode == "deep"
    with pytest.raises(ValidationError):
        AppendTurnMemberV1(**base)
    retry = AppendTurnMemberV1(
        **base,
        operation="retry_turn",
        reasoning_mode=None,
    )
    assert retry.reasoning_mode is None
    with pytest.raises(ValidationError):
        AppendTurnMemberV1(**base, operation="retry_turn", reasoning_mode="standard")


def test_conversation_response_language_defaults_and_rejects_unknown_values() -> None:
    assert ConversationCreateV1().response_language == "zh-TW"
    assert (
        ConversationCreateV1(response_language="en").response_language == "en"
    )
    with pytest.raises(ValidationError):
        ConversationCreateV1.model_validate({"response_language": "ja"})


def test_model_route_policy_v4_api_requires_and_round_trips_execution_limits() -> None:
    payload = {
        "route_id": "route-v4",
        "display_name": "Route V4",
        "model_name": "gpt-test",
        "connection_id": "connection-1",
        "enabled": True,
        "supports_vision": False,
        "runtime_policy": model_route_runtime_policy(),
        "idempotency_key": "route-v4-create",
    }

    request = ModelRouteCreateRequest.model_validate(payload)

    assert request.runtime_policy.schema_version == "model-route-runtime-policy-v7"
    assert request.runtime_policy.max_catalog_pages == 5
    assert request.runtime_policy.max_search_rounds == 6
    assert request.runtime_policy.max_unique_evidence == 40
    assert request.model_dump(mode="json")["runtime_policy"] == (
        model_route_runtime_policy()
    )
    with pytest.raises(ValidationError):
        ModelRouteCreateRequest.model_validate(
            {
                **payload,
                "runtime_policy": {
                    **model_route_runtime_policy(),
                    "schema_version": "model-route-runtime-policy-v3",
                },
            }
        )
    with pytest.raises(ValidationError):
        ModelRouteCreateRequest.model_validate(
            {
                **payload,
                "runtime_policy": {
                    **model_route_runtime_policy(),
                    "max_provider_invocations": 13,
                },
            }
        )


def test_runtime_budget_commands_require_typed_usage_inputs() -> None:
    from atlas_production.modules.turn_runtime.public import (
        BeginToolInvocationV1,
        CompleteToolInvocationV1,
        RequestModelActionV1,
    )

    with pytest.raises(ValidationError):
        RequestModelActionV1.model_validate(
            {"execution_id": "exec-1", "expected_version": 1, "fencing_token": 1}
        )
    begin = BeginToolInvocationV1(
        execution_id="exec-1",
        expected_version=1,
        fencing_token=1,
        tool_invocation_id="tool-1",
        invocation_ordinal=1,
        tool_name="search_knowledge",
        schema_version="search-knowledge-v1",
        arguments_digest="b" * 64,
        reserve_catalog_pages=0,
        reserve_document_candidates=10,
        reserve_search_rounds=1,
        reserve_unique_evidence=10,
        reserve_tool_tokens=2000,
    )
    assert begin.reserve_search_rounds == 1
    with pytest.raises(ValidationError):
        BeginToolInvocationV1.model_validate(
            {**begin.model_dump(), "reserve_tool_tokens": -1}
        )
    complete = CompleteToolInvocationV1.model_validate(
        {
            "execution_id": "exec-1",
            "expected_version": 1,
            "fencing_token": 1,
            "tool_invocation_id": "tool-1",
            "invocation_ordinal": 1,
            "result_ref": "result-ref-1",
            "result_digest": "a" * 64,
            "document_candidate_handles": ["document-handle-1"],
            "unique_evidence_identities": ["evidence-identity-1"],
            "catalog_pages": 1,
            "search_rounds": 1,
            "tool_tokens": 20,
        }
    )
    assert complete.document_candidate_handles == ["document-handle-1"]
    assert (complete.catalog_pages, complete.search_rounds) == (1, 1)
    with pytest.raises(ValidationError):
        CompleteToolInvocationV1.model_validate(
            {**complete.model_dump(), "catalog_pages": -1}
        )


def test_carrier_failure_is_fenced_and_sweep_failure_is_privileged() -> None:
    carrier = FailCarrierExecutionV1(
        execution_id="exec-1",
        expected_version=3,
        holder_id="worker-1",
        expected_lease_version=2,
        fencing_token=9,
        failure_code="carrier_shutdown",
        detected_by="carrier",
    )
    assert carrier.fencing_token == 9
    sweep = FinalizeExpiredExecutionV1(
        execution_id="exec-1",
        expected_version=3,
        expected_lease_version=2,
        failure_code="lease_expired",
        detected_by="lease_sweep",
    )
    assert not hasattr(sweep, "fencing_token")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "execution_id": "",
            "expected_version": 1,
            "holder_id": "worker-1",
            "expected_lease_version": 1,
            "fencing_token": 1,
            "failure_code": "carrier_shutdown",
            "detected_by": "carrier",
        },
        {
            "execution_id": "exec-1",
            "expected_version": 1,
            "holder_id": "",
            "expected_lease_version": 1,
            "fencing_token": 1,
            "failure_code": "carrier_shutdown",
            "detected_by": "carrier",
        },
    ],
)
def test_carrier_failure_rejects_empty_authority_identity(payload: dict) -> None:
    with pytest.raises(ValidationError):
        FailCarrierExecutionV1.model_validate(payload)


def test_grant_deadline_requires_timezone_and_nonempty_identities() -> None:
    payload = {
        "execution_id": "exec-1",
        "actor_id": "actor-1",
        "conversation_id": "conversation-1",
        "deadline_at": "2026-07-20T12:00:00+08:00",
        "idempotency_key": "key-1",
    }
    assert CreateTurnAccessGrantV1.model_validate(payload).actor_id == "actor-1"
    with pytest.raises(ValidationError):
        CreateTurnAccessGrantV1.model_validate({**payload, "actor_id": ""})
    with pytest.raises(ValidationError):
        CreateTurnAccessGrantV1.model_validate({**payload, "deadline_at": "2026-07-20T12:00:00"})


def test_lineage_graph_identifies_both_dependent_and_source() -> None:
    graph = ContextLineageGraphV3.model_validate(
        {
            "candidate_turn_ids": ["turn-dependent"],
            "edges": [
                {
                    "dependent_turn_id": "turn-dependent",
                    "dependent_context_pack_ref": "context-pack-1",
                    "source_turn_id": "turn-source",
                    "source_resource_ref": "evidence-ref-1",
                    "source_resource_kind": "evidence",
                    "dependency_kind": "knowledge_hint",
                    "lifecycle_epoch": 1,
                    "version_ref": "document-version-1",
                    "generation_ref": "retrieval-generation-1",
                }
            ],
        }
    )
    assert graph.edges[0].source_turn_id == "turn-source"
    with pytest.raises(ValidationError):
        ContextLineageGraphV3.model_validate(
            {
                "candidate_turn_ids": ["another-turn"],
                "edges": [graph.edges[0].model_dump()],
            }
        )


def test_terminal_states_are_explicit_and_do_not_include_cleanup() -> None:
    assert TERMINAL_STATES == {ExecutionState.TERMINAL_COMPLETED, ExecutionState.TERMINAL_FAILED}


def _model_input_payload() -> dict:
    return {
        "schema_version": "turn-model-input-v3",
        "execution_id": "execution-1",
        "model_user_input": "Compare the visible policies.",
        "recent_tail": [],
        "summary": None,
        "context_pack_ref": "context-pack-1",
        "knowledge_catalog_ref": "knowledge-catalog-1",
        "catalog_document_count": 2,
        "budget": {
            "tool_invocations": 0,
            "catalog_pages": 0,
            "document_candidates": 0,
            "search_rounds": 0,
            "unique_evidence": 0,
            "provider_invocations": 0,
            "context_tokens": 0,
            "tool_tokens": 0,
        },
        "policy": RoutePolicyV1().model_dump(),
        "route": {
            "route_id": "test-route",
            "route_revision": 1,
            "runtime_policy_revision": 1,
            "tokenizer_profile": "cl100k_base",
            "context_window_tokens": 128000,
            "max_input_tokens_per_invocation": 112000,
            "max_output_tokens_per_invocation": 16000,
            "max_tool_result_tokens_per_execution": 4000,
            "max_total_tokens_per_conversation": 1000000,
        },
        "answer_behavior": {
            "response_language": "zh-TW",
            "applied_guidance_revision": 0,
            "applied_guidance_digest": None,
            "custom_guidance": None,
        },
        "capabilities": {
            "schema_version": "turn-model-capabilities-v1",
            "execution_id": "execution-1",
            "catalog_ref": "knowledge-catalog-1",
            "allowed_actions": [
                "list_knowledge_documents",
                "find_knowledge_documents",
                "discover_relevant_documents",
                "finalize_answer",
            ],
            "documents": [],
            "evidence": [],
            "visuals": [],
            "allowed_modalities": ["text", "table", "figure"],
            "allowed_expand_directions": [
                "previous_page",
                "next_page",
                "figure_context",
                "related_evidence",
            ],
            "catalog_wide_search_allowed": False,
            "limits": {
                "max_page_size": 10,
                "max_discovery_limit": 20,
                "max_search_limit": 0,
                "max_expand_limit": 0,
                "max_output_tokens": 16000,
            },
            "contract_repair_remaining": 1,
            "digest": "b" * 64,
        },
        "previous_observation": None,
    }


def test_model_input_carries_only_bounded_visible_history() -> None:
    payload = _model_input_payload()
    assert TurnModelInputV3.model_validate(payload).recent_tail == []
    recent = {
        "logical_turn_id": "logical-1",
        "representative_turn_id": "turn-1",
        "user_text": "Question.",
        "assistant_text": "Only visible prior text.",
        "verification_status": "verified",
    }
    summary = {
        "summary_ref": "summary-ref-1",
        "text": "Visible older-history summary.",
        "digest": "a" * 64,
    }
    model_input = TurnModelInputV3.model_validate(
        {
            **payload,
            "recent_tail": [
                recent,
                {
                    **recent,
                    "logical_turn_id": "logical-2",
                    "representative_turn_id": "turn-2",
                },
            ],
            "summary": summary,
        }
    )
    assert model_input.summary is not None
    assert model_input.summary.summary_ref == "summary-ref-1"
    with pytest.raises(ValidationError):
        TurnModelInputV3.model_validate({**payload, "hidden_turns": ["turn-secret"]})


def test_strict_model_receives_the_exact_typed_history_projection() -> None:
    model_input = TurnModelInputV3.model_validate(_model_input_payload())

    class CapturingModel:
        received: TurnModelInputV3 | None = None

        def next_action(self, value: TurnModelInputV3, *, response_schema: dict) -> TurnActionV1:
            self.received = value
            assert response_schema == turn_action_schema()
            return FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "answer"}],
                claimed_evidence_handles=[],
            )

    model = CapturingModel()
    action = model.next_action(model_input, response_schema=turn_action_schema())
    assert action.action == "finalize_answer"
    assert model.received is model_input
