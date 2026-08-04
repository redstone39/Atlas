from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.postgres_owner import turn_runtime
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
    _bounded_limit,
    _digest_model,
    _stable_id,
)
from atlas_production.modules.turn_runtime.public import (
    AllocateExecutionV1,
    CompleteToolInvocationV1,
    LeasePolicyV1,
    RoutePolicyV1,
)
from tests.turn_runtime_fixtures import route_snapshot


def _allocation(**changes: object) -> AllocateExecutionV1:
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "actor_id": "actor-1",
        "holder_id": "holder-1",
        "route_policy": RoutePolicyV1(
            max_tool_invocations=1,
            max_provider_invocations=7,
            max_reasoning_revision_cycles=0,
        ),
        "route": route_snapshot(),
        "lease_policy": LeasePolicyV1(),
        "idempotency_key": "allocation-1",
        "operation": "create_turn",
        "retry_of_turn_id": None,
            "input_digest": "0" * 64,
            "response_language": "zh-TW",
            "applied_guidance_revision": 0,
            "applied_guidance_digest": None,
    }
    values.update(changes)
    return AllocateExecutionV1.model_validate(values)


def test_allocation_digest_is_exact_and_deterministic() -> None:
    command = _allocation()
    assert _digest_model(command) == _digest_model(command.model_copy(deep=True))
    assert _digest_model(command) != _digest_model(_allocation(holder_id="holder-2"))
    assert _digest_model(command) != _digest_model(
        _allocation(reasoning_mode="deep")
    )


def test_release_identity_is_deterministic_and_resource_scoped() -> None:
    first = _stable_id("release", "execution-1", "retrieval", "catalog-1", "release")
    assert first == _stable_id("release", "execution-1", "retrieval", "catalog-1", "release")
    assert first != _stable_id("release", "execution-1", "retrieval", "catalog-2", "release")


def test_all_persisted_budget_deltas_are_typed_and_nonnegative() -> None:
    fields = CompleteToolInvocationV1.model_fields
    assert {
        "catalog_pages",
        "search_rounds",
        "document_candidate_handles",
        "unique_evidence_identities",
        "tool_tokens",
    } <= fields.keys()
    payload = {
        "execution_id": "execution-1",
        "expected_version": 1,
        "fencing_token": 1,
        "tool_invocation_id": "tool-1",
        "invocation_ordinal": 1,
        "result_ref": "result-1",
        "result_digest": "a" * 64,
        "document_candidate_handles": [],
        "unique_evidence_identities": [],
        "catalog_pages": 0,
        "search_rounds": 0,
        "tool_tokens": 0,
    }
    assert CompleteToolInvocationV1.model_validate(payload).catalog_pages == 0
    for field in ("catalog_pages", "search_rounds", "tool_tokens"):
        with pytest.raises(ValidationError):
            CompleteToolInvocationV1.model_validate({**payload, field: -1})


def test_repository_has_complete_public_surface_and_no_cross_owner_imports() -> None:
    for method in (
        "find_execution", "snapshot", "allocate", "accept", "bind_context", "request_model_action", "claim_schema_retry", "record_reasoning_progress", "begin_tool",
        "complete_tool", "begin_governance", "prepare_terminal", "commit_terminal",
        "fail_carrier", "finalize_expired", "renew_lease", "fail_expired_leases",
        "pending_release_intents", "complete_release_intent", "events",
    ):
        assert callable(getattr(PostgresTurnRuntimeOwner, method))
    source = inspect.getsource(turn_runtime)
    assert "postgres_owner.authorization" not in source
    assert "postgres_owner.conversation" not in source
    assert "datetime.now" not in source
    assert "clock_timestamp" in source
    sweep = inspect.getsource(PostgresTurnRuntimeOwner.fail_expired_leases)
    assert "with_for_update" not in sweep
    assert "self.finalize_expired" in sweep


@pytest.mark.parametrize("limit", (0, 501))
def test_bounded_claim_and_sweep_limits(limit: int) -> None:
    with pytest.raises(ValueError):
        _bounded_limit(limit)
