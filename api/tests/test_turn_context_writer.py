from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.postgres_context_engineering_v3_adapter import (
    PostgresContextEngineeringV3Adapter,
)
from atlas_production.infrastructure.postgres_owner.context_engineering import (
    ContextPackRecord,
    CreateInputProjectionInput,
    InputProjectionRecord,
    LineageEdgeRecord,
    MaterializeContextInput,
    RecordResolverProjectionInput,
    RecordRewriteProjectionInput,
    SummaryRecord,
)
from atlas_production.modules.context_engineering.public import (
    ContextExchangeV3,
    ContextLineageEdgeV3,
    ContextMessageV3,
    ContextPackV3,
    ContextSummaryInputV4,
    ContextSummarySourceV3,
    CreateTurnInputProjectionV1,
    ModelUserInputV3,
    ModelUserTextSegmentV3,
    MaterializeContextPackV3,
    RecordResolverProjectionV1,
    RecordRewriteProjectionV1,
)


NOW = datetime.now(timezone.utc)
DIGEST = "a" * 64


class _Store:
    def __init__(self) -> None:
        self.commands: list[MaterializeContextInput] = []

    def materialize(self, command: MaterializeContextInput) -> ContextPackRecord:
        self.commands.append(command)
        summary = command.summary
        return ContextPackRecord(
            context_pack_ref=command.context_pack_ref,
            schema_version=command.schema_version,
            execution_id=command.execution_id,
            input_projection_ref=command.input_projection_ref,
            conversation_id=command.conversation_id,
            model_user_input=command.model_user_input,
            recent_tail=command.recent_tail,
            summary=(
                None
                if summary is None
                else SummaryRecord(
                    summary.summary_ref,
                    summary.parent_summary_ref,
                    summary.historical_user_context,
                    summary.assistant_pending_verification_context,
                    summary.token_count,
                    summary.sources,
                    DIGEST,
                )
            ),
            dependencies=tuple(
                LineageEdgeRecord(
                    command.dependent_turn_id,
                    command.context_pack_ref,
                    edge.source_turn_id,
                    edge.source_resource_ref,
                    edge.source_resource_kind,
                    edge.dependency_kind,
                    edge.lifecycle_epoch,
                    edge.version_ref,
                    edge.generation_ref,
                )
                for edge in command.source_lineage
            ),
            token_budget=command.token_budget,
            digest="b" * 64,
            created_at=NOW,
        )

    def create_input_projection(
        self, command: CreateInputProjectionInput
    ) -> InputProjectionRecord:
        return InputProjectionRecord(
            command.projection_ref,
            command.execution_id,
            command.original_user_input,
            None,
            None,
            None,
            None,
            None,
            None,
            NOW,
            NOW,
        )

    def get_input_projection(self, execution_id: str) -> InputProjectionRecord | None:
        return None

    def record_resolver_projection(
        self, command: RecordResolverProjectionInput
    ) -> InputProjectionRecord:
        return InputProjectionRecord(
            "projection-1",
            command.execution_id,
            "raw input",
            command.resolver_output,
            None,
            command.resolver_invocation_ref,
            None,
            command.failure_code,
            None,
            NOW,
            NOW,
        )

    def record_rewrite_projection(
        self, command: RecordRewriteProjectionInput
    ) -> InputProjectionRecord:
        return InputProjectionRecord(
            "projection-1",
            command.execution_id,
            "raw input",
            "resolved context",
            command.rewritten_user_input,
            "resolver-invocation",
            command.rewrite_invocation_ref,
            None,
            command.failure_code,
            NOW,
            NOW,
        )


def _exchange(turn: str, *, documents: list[str] | None = None) -> ContextExchangeV3:
    return ContextExchangeV3(
        logical_turn_id=f"root-{turn}",
        representative_turn_id=turn,
        representative_content_digest=DIGEST,
        user_message=ContextMessageV3(role="user", text=f"question-{turn}"),
        assistant_message=ContextMessageV3(
            role="assistant", text=f"answer-{turn}", verification_status="verified"
        ),
        direct_document_ids=documents or [],
    )


def _edge(turn: str, *, summary_ref: str | None = None) -> ContextLineageEdgeV3:
    return ContextLineageEdgeV3(
        dependent_turn_id="turn-current",
        dependent_context_pack_ref="context-pack-1",
        source_turn_id=turn,
        source_resource_ref=summary_ref,
        source_resource_kind="summary" if summary_ref else "turn",
        dependency_kind="summary_source" if summary_ref else "recent_turn",
    )


def _command(**updates: object) -> MaterializeContextPackV3:
    values: dict[str, object] = {
        "context_pack_ref": "context-pack-1",
        "execution_id": "execution-1",
        "input_projection_ref": "input-projection-1",
        "conversation_id": "conversation-1",
        "dependent_turn_id": "turn-current",
        "model_user_input": ModelUserInputV3(
            content_segments=[ModelUserTextSegmentV3(text="What changed?")]
        ),
        "recent_tail": [],
        "summary": None,
        "source_lineage": [],
        "token_budget": 16000,
        "idempotency_key": "context-key-1",
    }
    values.update(updates)
    return MaterializeContextPackV3.model_validate(values)


def test_materialize_v3_maps_model_input_and_unbounded_recent_exchanges() -> None:
    store = _Store()
    exchanges = [_exchange(f"turn-{index}") for index in range(1, 5)]
    command = _command(
        recent_tail=exchanges,
        source_lineage=[_edge(item.representative_turn_id) for item in exchanges],
    )

    pack = PostgresContextEngineeringV3Adapter(store).materialize(command)  # type: ignore[arg-type]

    assert pack.schema_version == "context-pack-v3"
    assert pack.input_projection_ref == "input-projection-1"
    assert pack.model_user_input == "What changed?"
    assert len(pack.recent_tail) == 4
    assert store.commands[0].recent_tail[0].user_message.text == "question-turn-1"
    assert pack.recent_tail[0].assistant_message is not None
    assert pack.recent_tail[0].assistant_message.text == "answer-turn-1"


def test_materialize_v3_round_trips_summary_lineage_and_direct_resources() -> None:
    source = ContextSummarySourceV3(
        logical_turn_id="root-turn-0",
        representative_turn_id="turn-0",
        representative_content_digest=DIGEST,
        direct_document_ids=["document-1"],
    )
    summary = ContextSummaryInputV4(
        summary_ref="summary-1",
        parent_summary_ref=None,
        historical_user_context="Older question",
        assistant_pending_verification_context="Older answer",
        token_count=12,
        sources=[source],
    )
    store = _Store()

    pack = PostgresContextEngineeringV3Adapter(store).materialize(  # type: ignore[arg-type]
        _command(
            summary=summary,
            source_lineage=[_edge("turn-0", summary_ref="summary-1")],
        )
    )

    assert pack.summary is not None
    assert pack.summary.schema_version == "context-summary-v4"
    assert pack.summary.historical_user_context == "Older question"
    assert pack.summary.assistant_pending_verification_context == "Older answer"
    assert pack.summary.sources[0].direct_document_ids == ["document-1"]
    assert pack.summary.parent_summary_ref is None


def test_summary_token_hard_limit_and_exact_lineage_are_enforced() -> None:
    source = ContextSummarySourceV3(
        logical_turn_id="root-turn-0",
        representative_turn_id="turn-0",
        representative_content_digest=DIGEST,
    )
    with pytest.raises(ValidationError):
        ContextSummaryInputV4(
            summary_ref="summary-1",
            historical_user_context="too large",
            assistant_pending_verification_context="",
            token_count=6001,
            sources=[source],
        )
    summary = ContextSummaryInputV4(
        summary_ref="summary-1",
        historical_user_context="valid",
        assistant_pending_verification_context="",
        token_count=1,
        sources=[source],
    )
    with pytest.raises(ValidationError, match="summary sources require exact"):
        _command(summary=summary)


def test_direct_resource_ids_are_unique_per_exchange() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        _exchange("turn-1", documents=["document-1", "document-1"])


def test_model_user_input_remains_user_only() -> None:
    with pytest.raises(ValidationError):
        ModelUserInputV3.model_validate(
            {
                "role": "assistant",
                "content_segments": [
                    {"kind": "text", "text": "public-synthetic-x"}
                ],
            }
        )


def test_context_pack_v3_rejects_raw_user_input_fields() -> None:
    pack = PostgresContextEngineeringV3Adapter(_Store()).materialize(_command())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ContextPackV3.model_validate(
            {
                **pack.model_dump(mode="json"),
                "original_user_input": "raw input must stay audit-only",
            }
        )


def test_input_projection_adapter_keeps_raw_on_audit_contract_only() -> None:
    adapter = PostgresContextEngineeringV3Adapter(_Store())  # type: ignore[arg-type]
    created = adapter.create_input_projection(
        CreateTurnInputProjectionV1(
            projection_ref="projection-1",
            execution_id="execution-1",
            original_user_input="它指的是哪一份？",
        )
    )
    assert created.original_user_input == "它指的是哪一份？"
    resolved = adapter.record_resolver_projection(
        RecordResolverProjectionV1(
            execution_id="execution-1",
            resolver_output="指的是文件 B。",
            resolver_invocation_ref="resolver-invocation",
        )
    )
    rewritten = adapter.record_rewrite_projection(
        RecordRewriteProjectionV1(
            execution_id="execution-1",
            rewritten_user_input="文件 B 的內容是什麼？",
            rewrite_invocation_ref="rewrite-invocation",
        )
    )
    assert resolved.resolver_output == "指的是文件 B。"
    assert rewritten.rewritten_user_input == "文件 B 的內容是什麼？"
