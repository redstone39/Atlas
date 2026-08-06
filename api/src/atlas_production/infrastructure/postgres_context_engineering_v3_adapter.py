"""Public Context Engineering V3 adapter over owner-local records."""

from __future__ import annotations

from atlas_production.infrastructure.postgres_owner.context_engineering import (
    ContextMessageInput,
    ContextPackRecord,
    CreateInputProjectionInput,
    InputProjectionRecord,
    LineageEdgeRecord,
    LineageGraphRecord,
    MaterializeContextInput,
    PostgresContextEngineeringStore,
    RecentExchangeInput,
    RecordResolverProjectionInput,
    RecordRewriteProjectionInput,
    ReleaseContextInput,
    SourceLineageInput,
    SummaryInput,
    SummarySourceInput,
)
from atlas_production.modules.context_engineering.public import (
    ContextExchangeV3,
    ContextLineageEdgeV3,
    ContextLineageGraphV3,
    ContextMessageV3,
    ContextPackReleaseV3,
    ContextPackV3,
    ContextSummarySourceV3,
    ContextSummaryV4,
    CreateTurnInputProjectionV1,
    MaterializeContextPackV3,
    RecordResolverProjectionV1,
    RecordRewriteProjectionV1,
    ReleaseContextPackV3,
    TurnInputProjectionV1,
)


def _input_projection(record: InputProjectionRecord) -> TurnInputProjectionV1:
    return TurnInputProjectionV1(
        projection_ref=record.projection_ref,
        execution_id=record.execution_id,
        original_user_input=record.original_user_input,
        resolver_output=record.resolver_output,
        rewritten_user_input=record.rewritten_user_input,
        resolver_invocation_ref=record.resolver_invocation_ref,
        rewrite_invocation_ref=record.rewrite_invocation_ref,
        resolver_failure_code=record.resolver_failure_code,
        rewrite_failure_code=record.rewrite_failure_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _edge(record: LineageEdgeRecord) -> ContextLineageEdgeV3:
    return ContextLineageEdgeV3(
        dependent_turn_id=record.dependent_turn_id,
        dependent_context_pack_ref=record.dependent_context_pack_ref,
        source_turn_id=record.source_turn_id,
        source_resource_ref=record.source_resource_ref,
        source_resource_kind=record.source_resource_kind,
        dependency_kind=record.dependency_kind,
        lifecycle_epoch=record.lifecycle_epoch,
        version_ref=record.version_ref,
        generation_ref=record.generation_ref,
    )


def _source(source: SummarySourceInput) -> ContextSummarySourceV3:
    return ContextSummarySourceV3(
        logical_turn_id=source.logical_turn_id,
        representative_turn_id=source.representative_turn_id,
        representative_content_digest=source.representative_content_digest,
        direct_document_ids=list(source.direct_document_ids),
    )


def _exchange(exchange: RecentExchangeInput) -> ContextExchangeV3:
    return ContextExchangeV3(
        logical_turn_id=exchange.logical_turn_id,
        representative_turn_id=exchange.representative_turn_id,
        representative_content_digest=exchange.representative_content_digest,
        user_message=ContextMessageV3(
            role="user",
            text=exchange.user_message.text,
            verification_status=exchange.user_message.verification_status,
        ),
        assistant_message=(
            None
            if exchange.assistant_message is None
            else ContextMessageV3(
                role="assistant",
                text=exchange.assistant_message.text,
                verification_status=exchange.assistant_message.verification_status,
            )
        ),
        direct_document_ids=list(exchange.direct_document_ids),
    )


def _pack(record: ContextPackRecord) -> ContextPackV3:
    summary = record.summary
    return ContextPackV3(
        context_pack_ref=record.context_pack_ref,
        schema_version=record.schema_version,
        execution_id=record.execution_id,
        input_projection_ref=record.input_projection_ref,
        model_user_input=record.model_user_input,
        recent_tail=[_exchange(exchange) for exchange in record.recent_tail],
        summary=(
            None
            if summary is None
            else ContextSummaryV4(
                summary_ref=summary.summary_ref,
                parent_summary_ref=summary.parent_summary_ref,
                historical_user_context=summary.historical_user_context,
                assistant_pending_verification_context=(
                    summary.assistant_pending_verification_context
                ),
                token_count=summary.token_count,
                sources=[_source(source) for source in summary.sources],
                digest=summary.digest,
            )
        ),
        dependencies=[_edge(edge) for edge in record.dependencies],
        token_budget=record.token_budget,
        digest=record.digest,
        created_at=record.created_at,
    )


class PostgresContextEngineeringV3Adapter:
    def __init__(self, store: PostgresContextEngineeringStore) -> None:
        self._store = store

    def get(self, context_pack_ref: str) -> ContextPackV3 | None:
        record = self._store.get(context_pack_ref)
        return None if record is None else _pack(record)

    def create_input_projection(
        self, command: CreateTurnInputProjectionV1
    ) -> TurnInputProjectionV1:
        return _input_projection(
            self._store.create_input_projection(
                CreateInputProjectionInput(
                    projection_ref=command.projection_ref,
                    execution_id=command.execution_id,
                    original_user_input=command.original_user_input,
                )
            )
        )

    def get_input_projection(
        self, execution_id: str
    ) -> TurnInputProjectionV1 | None:
        record = self._store.get_input_projection(execution_id)
        return None if record is None else _input_projection(record)

    def record_resolver_projection(
        self, command: RecordResolverProjectionV1
    ) -> TurnInputProjectionV1:
        return _input_projection(
            self._store.record_resolver_projection(
                RecordResolverProjectionInput(
                    execution_id=command.execution_id,
                    resolver_output=command.resolver_output,
                    resolver_invocation_ref=command.resolver_invocation_ref,
                    failure_code=command.failure_code,
                )
            )
        )

    def record_rewrite_projection(
        self, command: RecordRewriteProjectionV1
    ) -> TurnInputProjectionV1:
        return _input_projection(
            self._store.record_rewrite_projection(
                RecordRewriteProjectionInput(
                    execution_id=command.execution_id,
                    rewritten_user_input=command.rewritten_user_input,
                    rewrite_invocation_ref=command.rewrite_invocation_ref,
                    failure_code=command.failure_code,
                )
            )
        )

    def materialize(self, command: MaterializeContextPackV3) -> ContextPackV3:
        summary = command.summary
        record = self._store.materialize(
            MaterializeContextInput(
                context_pack_ref=command.context_pack_ref,
                execution_id=command.execution_id,
                input_projection_ref=command.input_projection_ref,
                conversation_id=command.conversation_id,
                dependent_turn_id=command.dependent_turn_id,
                model_user_input=command.model_user_input.as_text(),
                recent_tail=tuple(
                    RecentExchangeInput(
                        logical_turn_id=exchange.logical_turn_id,
                        representative_turn_id=exchange.representative_turn_id,
                        representative_content_digest=exchange.representative_content_digest,
                        user_message=ContextMessageInput(
                            role="user",
                            text=exchange.user_message.text,
                            verification_status=exchange.user_message.verification_status,
                        ),
                        assistant_message=(
                            None
                            if exchange.assistant_message is None
                            else ContextMessageInput(
                                role="assistant",
                                text=exchange.assistant_message.text,
                                verification_status=exchange.assistant_message.verification_status,
                            )
                        ),
                        direct_document_ids=tuple(exchange.direct_document_ids),
                    )
                    for exchange in command.recent_tail
                ),
                summary=(
                    None
                    if summary is None
                    else SummaryInput(
                        summary_ref=summary.summary_ref,
                        parent_summary_ref=summary.parent_summary_ref,
                        historical_user_context=summary.historical_user_context,
                        assistant_pending_verification_context=(
                            summary.assistant_pending_verification_context
                        ),
                        token_count=summary.token_count,
                        sources=tuple(
                            SummarySourceInput(
                                logical_turn_id=source.logical_turn_id,
                                representative_turn_id=source.representative_turn_id,
                                representative_content_digest=source.representative_content_digest,
                                direct_document_ids=tuple(source.direct_document_ids),
                            )
                            for source in summary.sources
                        ),
                    )
                ),
                source_lineage=tuple(
                    SourceLineageInput(
                        source_turn_id=edge.source_turn_id,
                        source_resource_ref=edge.source_resource_ref,
                        source_resource_kind=edge.source_resource_kind,
                        dependency_kind=edge.dependency_kind,
                        lifecycle_epoch=edge.lifecycle_epoch,
                        version_ref=edge.version_ref,
                        generation_ref=edge.generation_ref,
                    )
                    for edge in command.source_lineage
                ),
                token_budget=command.token_budget,
                idempotency_key=command.idempotency_key,
            )
        )
        return _pack(record)

    def release(self, command: ReleaseContextPackV3) -> ContextPackReleaseV3:
        record = self._store.release(
            ReleaseContextInput(
                release_ref=command.release_ref,
                execution_id=command.execution_id,
                context_pack_ref=command.context_pack_ref,
                idempotency_key=command.idempotency_key,
            )
        )
        return ContextPackReleaseV3(
            release_ref=record.release_ref,
            execution_id=record.execution_id,
            context_pack_ref=record.context_pack_ref,
            released_at=record.released_at,
        )

    def release_execution_context(
        self, *, execution_id: str, idempotency_key: str
    ) -> None:
        pack = self._store.get_for_execution(execution_id)
        if pack is None:
            return
        self.release(
            ReleaseContextPackV3(
                release_ref=f"context-release-{idempotency_key}",
                execution_id=execution_id,
                context_pack_ref=pack.context_pack_ref,
                idempotency_key=idempotency_key,
            )
        )

    def lineage_graph(self, turn_ids: list[str]) -> ContextLineageGraphV3:
        record: LineageGraphRecord = self._store.lineage_graph(turn_ids)
        return ContextLineageGraphV3(
            candidate_turn_ids=list(record.candidate_turn_ids),
            edges=[_edge(edge) for edge in record.edges],
        )


__all__ = ["PostgresContextEngineeringV3Adapter"]
