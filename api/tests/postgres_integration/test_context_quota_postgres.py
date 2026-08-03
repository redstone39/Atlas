from __future__ import annotations

import pytest
from sqlalchemy import delete

from atlas_production.infrastructure.conversation_token_usage import (
    PostgresConversationTokenUsageReader,
)
from atlas_production.infrastructure.persistence.model_routing import (
    AtlasModelInvocationRow,
)
from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnExecutionRow,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.turn_runtime.public import (
    AllocateExecutionV1,
    LeasePolicyV1,
    RoutePolicyV1,
    TurnRouteSnapshotV2,
)


PREFIX = "context-quota-v3-"


def route_snapshot() -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id="test-route",
        route_revision=1,
        runtime_policy_revision=1,
        tokenizer_profile="cl100k_base",
        context_window_tokens=128000,
        max_input_tokens_per_invocation=112000,
        max_output_tokens_per_invocation=16000,
        max_tool_result_tokens_per_execution=16000,
        max_total_tokens_per_conversation=256000,
    )


@pytest.fixture(autouse=True)
def clean_rows(postgres_runtime: PostgresRuntime):
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasModelInvocationRow).where(
                AtlasModelInvocationRow.invocation_id.like(f"{PREFIX}%")
            )
        )
        session.execute(
            delete(AtlasTurnExecutionRow).where(
                AtlasTurnExecutionRow.execution_id.like(f"{PREFIX}%")
            )
        )
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasModelInvocationRow).where(
                AtlasModelInvocationRow.invocation_id.like(f"{PREFIX}%")
            )
        )
        session.execute(
            delete(AtlasTurnExecutionRow).where(
                AtlasTurnExecutionRow.execution_id.like(f"{PREFIX}%")
            )
        )


def _allocate(
    owner: PostgresTurnRuntimeOwner, suffix: str, conversation_id: str
):
    execution_id = f"{PREFIX}{suffix}"
    return owner.allocate(
        AllocateExecutionV1(
            execution_id=execution_id,
            turn_id=f"turn-{execution_id}",
            conversation_id=conversation_id,
            actor_id="actor-1",
            holder_id=f"holder-{suffix}",
            route_policy=RoutePolicyV1(
                max_tool_invocations=1,
                max_provider_invocations=7,
                max_reasoning_revision_cycles=0,
            ),
            route=route_snapshot(),
            lease_policy=LeasePolicyV1(),
            idempotency_key=f"allocate-{suffix}",
            operation="create_turn",
            retry_of_turn_id=None,
            input_digest="0" * 64,
            response_language="zh-TW",
            applied_guidance_revision=0,
            applied_guidance_digest=None,
        )
    )


def _invocation(
    invocation_id: str,
    *,
    subject_ref: str,
    status: str,
    token_usage: dict,
    purpose: str = "turn_execution",
) -> AtlasModelInvocationRow:
    return AtlasModelInvocationRow(
        invocation_id=invocation_id,
        route_id="test-route",
        provider_type="test",
        model_name="test-model",
        status=status,
        created_at="2026-07-23T00:00:00Z",
        prompt_snapshot_ref=f"prompt-{invocation_id}",
        response_schema_name="test_schema",
        response_schema_digest="a" * 64,
        token_usage=token_usage,
        error_code=None if status == "completed" else "provider_failed",
        route_revision=1,
                runtime_policy_schema_version="model-route-runtime-policy-v7",
        runtime_policy_revision=1,
        runtime_policy_snapshot={},
        invocation_purpose=purpose,
        subject_kind="turn_execution",
        subject_ref=subject_ref,
        request_artifact_ref=None,
        response_artifact_ref=None,
        execution_key=f"key-{invocation_id}",
        prompt_digest="b" * 64,
        input_digest=None,
        input_content_type=None,
        input_width=None,
        input_height=None,
        started_at="2026-07-23T00:00:00Z",
        completed_at="2026-07-23T00:00:01Z",
        duration_ms=1000,
        attempt_ordinal=1,
        repair_origin_error_codes=[],
    )


def test_fresh_postgres_exact_replay_read_and_soft_conversation_usage(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = PostgresTurnRuntimeOwner(postgres_runtime.session_factory)
    first = _allocate(owner, "one", "conversation-target")
    other = _allocate(owner, "other", "conversation-other")
    with postgres_runtime.session_factory() as session, session.begin():
        session.add_all(
            [
                _invocation(
                    f"{PREFIX}resolver",
                    subject_ref=first.execution_id,
                    status="completed",
                    token_usage={"input_tokens": 3, "output_tokens": 2},
                    purpose="context_resolver",
                ),
                _invocation(
                    f"{PREFIX}rewrite",
                    subject_ref=first.execution_id,
                    status="completed",
                    token_usage={"input_tokens": 4, "output_tokens": 1},
                    purpose="context_rewrite",
                ),
                _invocation(
                    f"{PREFIX}answer",
                    subject_ref=first.execution_id,
                    status="completed",
                    token_usage={"input_tokens": 2, "output_tokens": 2},
                    purpose="turn_execution",
                ),
                _invocation(
                    f"{PREFIX}failed",
                    subject_ref=first.execution_id,
                    status="failed",
                    token_usage={"input_tokens": 100, "output_tokens": 100},
                ),
                _invocation(
                    f"{PREFIX}other",
                    subject_ref=other.execution_id,
                    status="completed",
                    token_usage={"input_tokens": 1000, "output_tokens": 1000},
                ),
            ]
        )

    assert owner.find_execution("missing-execution") is None
    assert owner.find_execution(first.execution_id) == first
    assert (
        PostgresConversationTokenUsageReader(
            postgres_runtime.session_factory
        ).observed_tokens("conversation-target")
        == 14
    )
