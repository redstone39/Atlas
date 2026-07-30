"""Context-engineering-owned immutable Context/Summary V3 records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.context_engineering import (
    AtlasTurnContextLineageEdgeRow,
    AtlasTurnContextPackRecentExchangeRow,
    AtlasTurnContextPackRecentResourceRow,
    AtlasTurnContextPackReleaseRow,
    AtlasTurnContextPackRow,
    AtlasTurnContextSummaryRow,
    AtlasTurnContextSummarySourceResourceRow,
    AtlasTurnContextSummarySourceRow,
    AtlasTurnInputProjectionRow,
)


SessionFactory = Callable[[], Session]
CONTEXT_PACK_SCHEMA_VERSION = "context-pack-v3"
CONTEXT_SUMMARY_SCHEMA_VERSION = "context-summary-v3"


class ContextStoreConflict(RuntimeError):
    """A Context identity or monotonic projection stage changed on replay."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CreateInputProjectionInput:
    projection_ref: str
    execution_id: str
    original_user_input: str


@dataclass(frozen=True, slots=True)
class RecordResolverProjectionInput:
    execution_id: str
    resolver_output: str | None
    resolver_invocation_ref: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class RecordRewriteProjectionInput:
    execution_id: str
    rewritten_user_input: str | None
    rewrite_invocation_ref: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class InputProjectionRecord:
    projection_ref: str
    execution_id: str
    original_user_input: str
    resolver_output: str | None
    rewritten_user_input: str | None
    resolver_invocation_ref: str | None
    rewrite_invocation_ref: str | None
    resolver_failure_code: str | None
    rewrite_failure_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ContextMessageInput:
    role: Literal["user", "assistant"]
    text: str
    verification_status: Literal[
        "verified", "partially_verified", "unverified", "not_applicable"
    ] = "not_applicable"


@dataclass(frozen=True, slots=True)
class RecentExchangeInput:
    logical_turn_id: str
    representative_turn_id: str
    representative_content_digest: str
    user_message: ContextMessageInput
    assistant_message: ContextMessageInput | None
    direct_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummarySourceInput:
    logical_turn_id: str
    representative_turn_id: str
    representative_content_digest: str
    direct_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryInput:
    summary_ref: str
    parent_summary_ref: str | None
    text: str
    token_count: int
    sources: tuple[SummarySourceInput, ...]


@dataclass(frozen=True, slots=True)
class SourceLineageInput:
    source_turn_id: str
    source_resource_ref: str | None
    source_resource_kind: Literal["turn", "summary", "document", "evidence", "citation"]
    dependency_kind: Literal["recent_turn", "summary_source", "knowledge_hint"]
    lifecycle_epoch: int | None = None
    version_ref: str | None = None
    generation_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseContextInput:
    release_ref: str
    execution_id: str
    context_pack_ref: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ContextReleaseRecord:
    release_ref: str
    execution_id: str
    context_pack_ref: str
    released_at: datetime


@dataclass(frozen=True, slots=True)
class MaterializeContextInput:
    context_pack_ref: str
    execution_id: str
    input_projection_ref: str
    conversation_id: str
    dependent_turn_id: str
    model_user_input: str
    recent_tail: tuple[RecentExchangeInput, ...]
    summary: SummaryInput | None
    source_lineage: tuple[SourceLineageInput, ...]
    token_budget: int
    idempotency_key: str
    schema_version: str = CONTEXT_PACK_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SummaryRecord:
    summary_ref: str
    parent_summary_ref: str | None
    text: str
    token_count: int
    sources: tuple[SummarySourceInput, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class LineageEdgeRecord:
    dependent_turn_id: str
    dependent_context_pack_ref: str
    source_turn_id: str
    source_resource_ref: str | None
    source_resource_kind: str
    dependency_kind: str
    lifecycle_epoch: int | None
    version_ref: str | None
    generation_ref: str | None


@dataclass(frozen=True, slots=True)
class ContextPackRecord:
    context_pack_ref: str
    schema_version: str
    execution_id: str
    input_projection_ref: str
    conversation_id: str
    model_user_input: str
    recent_tail: tuple[RecentExchangeInput, ...]
    summary: SummaryRecord | None
    dependencies: tuple[LineageEdgeRecord, ...]
    token_budget: int
    digest: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LineageGraphRecord:
    candidate_turn_ids: tuple[str, ...]
    edges: tuple[LineageEdgeRecord, ...]


def _semantic_payload(command: MaterializeContextInput) -> dict[str, object]:
    return {
        "operation": "materialize_context_pack",
        "schema_version": command.schema_version,
        "execution_id": command.execution_id,
        "input_projection_ref": command.input_projection_ref,
        "conversation_id": command.conversation_id,
        "dependent_turn_id": command.dependent_turn_id,
        "model_user_input": command.model_user_input,
        "recent_tail": [asdict(exchange) for exchange in command.recent_tail],
        "summary": None if command.summary is None else asdict(command.summary),
        "source_lineage": [asdict(edge) for edge in command.source_lineage],
        "token_budget": command.token_budget,
    }


def _summary_digest(summary: SummaryInput) -> str:
    return _digest(
        {
            "schema_version": CONTEXT_SUMMARY_SCHEMA_VERSION,
            "parent_summary_ref": summary.parent_summary_ref,
            "text": summary.text,
            "token_count": summary.token_count,
            "sources": [asdict(source) for source in summary.sources],
        }
    )


def _edge_id(pack_ref: str, position: int, edge: SourceLineageInput) -> str:
    return "ctxedge_" + _digest(
        {"context_pack_ref": pack_ref, "position": position, **asdict(edge)}
    )[:48]


def _validate(command: MaterializeContextInput) -> None:
    if command.schema_version != CONTEXT_PACK_SCHEMA_VERSION:
        raise ValueError("unsupported context pack schema version")
    if command.token_budget < 1:
        raise ValueError("token_budget must be positive")
    representatives = {
        exchange.representative_turn_id for exchange in command.recent_tail
    }
    if len(representatives) != len(command.recent_tail):
        raise ValueError("recent tail contains duplicate representative turns")
    recent_lineage = [
        edge for edge in command.source_lineage if edge.dependency_kind == "recent_turn"
    ]
    if (
        len(recent_lineage) != len(representatives)
        or {edge.source_turn_id for edge in recent_lineage} != representatives
        or any(
            edge.source_resource_kind != "turn" or edge.source_resource_ref is not None
            for edge in recent_lineage
        )
    ):
        raise ValueError("recent exchanges require exact base lineage edges")
    summary_representatives = (
        {source.representative_turn_id for source in command.summary.sources}
        if command.summary is not None
        else set()
    )
    if command.summary is not None:
        if not 1 <= command.summary.token_count <= 6000:
            raise ValueError("summary token_count exceeds the hard limit")
        if len(summary_representatives) != len(command.summary.sources):
            raise ValueError("summary sources contain duplicate representative turns")
    summary_lineage = [
        edge for edge in command.source_lineage if edge.dependency_kind == "summary_source"
    ]
    if (
        len(summary_lineage) != len(summary_representatives)
        or {edge.source_turn_id for edge in summary_lineage}
        != summary_representatives
        or any(
            command.summary is None
            or edge.source_resource_kind != "summary"
            or edge.source_resource_ref != command.summary.summary_ref
            for edge in summary_lineage
        )
    ):
        raise ValueError("summary sources require exact base lineage edges")


class PostgresContextEngineeringStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _input_projection(row: AtlasTurnInputProjectionRow) -> InputProjectionRecord:
        return InputProjectionRecord(
            projection_ref=row.projection_ref,
            execution_id=row.execution_id,
            original_user_input=row.original_user_input,
            resolver_output=row.resolver_output,
            rewritten_user_input=row.rewritten_user_input,
            resolver_invocation_ref=row.resolver_invocation_ref,
            rewrite_invocation_ref=row.rewrite_invocation_ref,
            resolver_failure_code=row.resolver_failure_code,
            rewrite_failure_code=row.rewrite_failure_code,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def create_input_projection(
        self, command: CreateInputProjectionInput
    ) -> InputProjectionRecord:
        with self._session_factory() as session, session.begin():
            now = _now()
            inserted_ref = session.execute(
                insert(AtlasTurnInputProjectionRow)
                .values(
                    projection_ref=command.projection_ref,
                    execution_id=command.execution_id,
                    original_user_input=command.original_user_input,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing()
                .returning(AtlasTurnInputProjectionRow.projection_ref)
            ).scalar_one_or_none()
            replay = session.scalar(
                select(AtlasTurnInputProjectionRow).where(
                    AtlasTurnInputProjectionRow.execution_id == command.execution_id
                )
            )
            if replay is None:
                if inserted_ref is not None:
                    raise ContextStoreConflict("input projection insert was not readable")
                raise ContextStoreConflict("input projection identity already exists")
            if (
                replay.projection_ref != command.projection_ref
                or replay.original_user_input != command.original_user_input
            ):
                raise ContextStoreConflict("input projection replay payload changed")
            return self._input_projection(replay)

    def get_input_projection(
        self, execution_id: str
    ) -> InputProjectionRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnInputProjectionRow).where(
                    AtlasTurnInputProjectionRow.execution_id == execution_id
                )
            )
            return None if row is None else self._input_projection(row)

    def record_resolver_projection(
        self, command: RecordResolverProjectionInput
    ) -> InputProjectionRecord:
        if (command.resolver_output is None) == (command.failure_code is None):
            raise ValueError("resolver stage requires exactly one output or failure")
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasTurnInputProjectionRow)
                .where(AtlasTurnInputProjectionRow.execution_id == command.execution_id)
                .with_for_update()
            )
            if row is None:
                raise ContextStoreConflict("input projection is missing")
            current = (
                row.resolver_output,
                row.resolver_invocation_ref,
                row.resolver_failure_code,
            )
            requested = (
                command.resolver_output,
                command.resolver_invocation_ref,
                command.failure_code,
            )
            if current != (None, None, None):
                if current != requested:
                    raise ContextStoreConflict("resolver projection replay changed")
                return self._input_projection(row)
            if row.rewritten_user_input is not None or row.rewrite_failure_code is not None:
                raise ContextStoreConflict("resolver projection cannot follow rewrite")
            row.resolver_output = command.resolver_output
            row.resolver_invocation_ref = command.resolver_invocation_ref
            row.resolver_failure_code = command.failure_code
            row.updated_at = _now()
            session.flush()
            return self._input_projection(row)

    def record_rewrite_projection(
        self, command: RecordRewriteProjectionInput
    ) -> InputProjectionRecord:
        if (command.rewritten_user_input is None) == (command.failure_code is None):
            raise ValueError("rewrite stage requires exactly one output or failure")
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasTurnInputProjectionRow)
                .where(AtlasTurnInputProjectionRow.execution_id == command.execution_id)
                .with_for_update()
            )
            if row is None:
                raise ContextStoreConflict("input projection is missing")
            if row.resolver_output is None or row.resolver_failure_code is not None:
                raise ContextStoreConflict("rewrite requires a successful resolver")
            current = (
                row.rewritten_user_input,
                row.rewrite_invocation_ref,
                row.rewrite_failure_code,
            )
            requested = (
                command.rewritten_user_input,
                command.rewrite_invocation_ref,
                command.failure_code,
            )
            if current != (None, None, None):
                if current != requested:
                    raise ContextStoreConflict("rewrite projection replay changed")
                return self._input_projection(row)
            row.rewritten_user_input = command.rewritten_user_input
            row.rewrite_invocation_ref = command.rewrite_invocation_ref
            row.rewrite_failure_code = command.failure_code
            row.updated_at = _now()
            session.flush()
            return self._input_projection(row)

    def materialize(self, command: MaterializeContextInput) -> ContextPackRecord:
        _validate(command)
        digest = _digest(_semantic_payload(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnContextPackRow).where(
                    AtlasTurnContextPackRow.execution_id == command.execution_id
                )
            )
            if replay is not None:
                if (
                    replay.digest != digest
                    or replay.context_pack_ref != command.context_pack_ref
                    or replay.idempotency_key != command.idempotency_key
                ):
                    raise ContextStoreConflict("context pack replay payload changed")
                return self._load_pack(session, replay)
            if session.get(AtlasTurnContextPackRow, command.context_pack_ref) is not None:
                raise ContextStoreConflict("context pack identity already exists")
            projection = session.get(
                AtlasTurnInputProjectionRow, command.input_projection_ref
            )
            if (
                projection is None
                or projection.execution_id != command.execution_id
                or projection.rewritten_user_input != command.model_user_input
            ):
                raise ContextStoreConflict(
                    "context pack input projection binding is invalid"
                )

            summary_ref = None
            if command.summary is not None:
                summary_ref = command.summary.summary_ref
                summary_digest = _summary_digest(command.summary)
                existing_summary = session.get(AtlasTurnContextSummaryRow, summary_ref)
                if existing_summary is None:
                    if (
                        command.summary.parent_summary_ref is not None
                        and session.get(
                            AtlasTurnContextSummaryRow, command.summary.parent_summary_ref
                        )
                        is None
                    ):
                        raise ContextStoreConflict("summary parent is missing")
                    session.add(
                        AtlasTurnContextSummaryRow(
                            summary_ref=summary_ref,
                            execution_id=command.execution_id,
                            schema_version=CONTEXT_SUMMARY_SCHEMA_VERSION,
                            parent_summary_ref=command.summary.parent_summary_ref,
                            text=command.summary.text,
                            token_count=command.summary.token_count,
                            digest=summary_digest,
                            created_at=_now(),
                        )
                    )
                    session.flush()
                    for ordinal, source in enumerate(command.summary.sources, start=1):
                        session.add(
                            AtlasTurnContextSummarySourceRow(
                                summary_ref=summary_ref,
                                representative_turn_id=source.representative_turn_id,
                                source_ordinal=ordinal,
                                logical_turn_id=source.logical_turn_id,
                                representative_content_digest=source.representative_content_digest,
                            )
                        )
                        for document_id in source.direct_document_ids:
                            session.add(
                                AtlasTurnContextSummarySourceResourceRow(
                                    summary_ref=summary_ref,
                                    representative_turn_id=source.representative_turn_id,
                                    document_id=document_id,
                                )
                            )
                elif existing_summary.digest != summary_digest:
                    raise ContextStoreConflict(
                        "summary identity was reused with changed content"
                    )

            row = AtlasTurnContextPackRow(
                context_pack_ref=command.context_pack_ref,
                execution_id=command.execution_id,
                input_projection_ref=command.input_projection_ref,
                conversation_id=command.conversation_id,
                schema_version=command.schema_version,
                model_user_input=command.model_user_input,
                summary_ref=summary_ref,
                token_budget=command.token_budget,
                digest=digest,
                idempotency_key=command.idempotency_key,
                created_at=_now(),
            )
            session.add(row)
            session.flush()
            for position, exchange in enumerate(command.recent_tail, start=1):
                session.add(
                    AtlasTurnContextPackRecentExchangeRow(
                        context_pack_ref=command.context_pack_ref,
                        position=position,
                        logical_turn_id=exchange.logical_turn_id,
                        representative_turn_id=exchange.representative_turn_id,
                        representative_content_digest=exchange.representative_content_digest,
                        user_text=exchange.user_message.text,
                        assistant_text=(
                            None
                            if exchange.assistant_message is None
                            else exchange.assistant_message.text
                        ),
                        assistant_verification_status=(
                            None
                            if exchange.assistant_message is None
                            else exchange.assistant_message.verification_status
                        ),
                    )
                )
                for document_id in exchange.direct_document_ids:
                    session.add(
                        AtlasTurnContextPackRecentResourceRow(
                            context_pack_ref=command.context_pack_ref,
                            representative_turn_id=exchange.representative_turn_id,
                            document_id=document_id,
                        )
                    )
            for position, edge in enumerate(command.source_lineage, start=1):
                session.add(
                    AtlasTurnContextLineageEdgeRow(
                        edge_id=_edge_id(command.context_pack_ref, position, edge),
                        dependent_turn_id=command.dependent_turn_id,
                        dependent_context_pack_ref=command.context_pack_ref,
                        source_turn_id=edge.source_turn_id,
                        source_resource_ref=edge.source_resource_ref,
                        source_resource_kind=edge.source_resource_kind,
                        dependency_kind=edge.dependency_kind,
                        lifecycle_epoch=edge.lifecycle_epoch,
                        version_ref=edge.version_ref,
                        generation_ref=edge.generation_ref,
                    )
                )
            session.flush()
            return self._load_pack(session, row)

    def get(self, context_pack_ref: str) -> ContextPackRecord | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnContextPackRow, context_pack_ref)
            return None if row is None else self._load_pack(session, row)

    def get_for_execution(self, execution_id: str) -> ContextPackRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnContextPackRow).where(
                    AtlasTurnContextPackRow.execution_id == execution_id
                )
            )
            return None if row is None else self._load_pack(session, row)

    def release(self, command: ReleaseContextInput) -> ContextReleaseRecord:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnContextPackReleaseRow).where(
                    AtlasTurnContextPackReleaseRow.execution_id == command.execution_id,
                    AtlasTurnContextPackReleaseRow.idempotency_key
                    == command.idempotency_key,
                )
            )
            if replay is not None:
                if (
                    replay.release_ref != command.release_ref
                    or replay.context_pack_ref != command.context_pack_ref
                ):
                    raise ContextStoreConflict("context release replay changed")
                return ContextReleaseRecord(
                    replay.release_ref,
                    replay.execution_id,
                    replay.context_pack_ref,
                    replay.released_at,
                )
            pack = session.get(AtlasTurnContextPackRow, command.context_pack_ref)
            if pack is None or pack.execution_id != command.execution_id:
                raise ContextStoreConflict("context release binding is invalid")
            row = AtlasTurnContextPackReleaseRow(
                release_ref=command.release_ref,
                execution_id=command.execution_id,
                context_pack_ref=command.context_pack_ref,
                idempotency_key=command.idempotency_key,
                released_at=_now(),
            )
            session.add(row)
            session.flush()
            return ContextReleaseRecord(
                row.release_ref, row.execution_id, row.context_pack_ref, row.released_at
            )

    def lineage_graph(self, turn_ids: tuple[str, ...] | list[str]) -> LineageGraphRecord:
        candidates = tuple(dict.fromkeys(turn_ids))
        if not candidates:
            return LineageGraphRecord(candidate_turn_ids=(), edges=())
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtlasTurnContextLineageEdgeRow)
                .where(AtlasTurnContextLineageEdgeRow.dependent_turn_id.in_(candidates))
                .order_by(
                    AtlasTurnContextLineageEdgeRow.dependent_turn_id,
                    AtlasTurnContextLineageEdgeRow.edge_id,
                )
            ).all()
            return LineageGraphRecord(
                candidate_turn_ids=candidates,
                edges=tuple(self._lineage(row) for row in rows),
            )

    @staticmethod
    def _lineage(row: AtlasTurnContextLineageEdgeRow) -> LineageEdgeRecord:
        return LineageEdgeRecord(
            dependent_turn_id=row.dependent_turn_id,
            dependent_context_pack_ref=row.dependent_context_pack_ref,
            source_turn_id=row.source_turn_id,
            source_resource_ref=row.source_resource_ref,
            source_resource_kind=row.source_resource_kind,
            dependency_kind=row.dependency_kind,
            lifecycle_epoch=row.lifecycle_epoch,
            version_ref=row.version_ref,
            generation_ref=row.generation_ref,
        )

    @staticmethod
    def _summary_sources(
        session: Session, summary_ref: str
    ) -> tuple[SummarySourceInput, ...]:
        rows = session.scalars(
            select(AtlasTurnContextSummarySourceRow)
            .where(AtlasTurnContextSummarySourceRow.summary_ref == summary_ref)
            .order_by(AtlasTurnContextSummarySourceRow.source_ordinal)
        ).all()
        resources = session.scalars(
            select(AtlasTurnContextSummarySourceResourceRow).where(
                AtlasTurnContextSummarySourceResourceRow.summary_ref == summary_ref
            )
        ).all()
        documents_by_turn: dict[str, list[str]] = {}
        for resource in resources:
            documents_by_turn.setdefault(resource.representative_turn_id, []).append(
                resource.document_id
            )
        return tuple(
            SummarySourceInput(
                logical_turn_id=row.logical_turn_id,
                representative_turn_id=row.representative_turn_id,
                representative_content_digest=row.representative_content_digest,
                direct_document_ids=tuple(
                    sorted(documents_by_turn.get(row.representative_turn_id, ()))
                ),
            )
            for row in rows
        )

    def _load_pack(
        self, session: Session, row: AtlasTurnContextPackRow
    ) -> ContextPackRecord:
        recent_rows = session.scalars(
            select(AtlasTurnContextPackRecentExchangeRow)
            .where(
                AtlasTurnContextPackRecentExchangeRow.context_pack_ref
                == row.context_pack_ref
            )
            .order_by(AtlasTurnContextPackRecentExchangeRow.position)
        ).all()
        recent_resources = session.scalars(
            select(AtlasTurnContextPackRecentResourceRow).where(
                AtlasTurnContextPackRecentResourceRow.context_pack_ref
                == row.context_pack_ref
            )
        ).all()
        recent_documents: dict[str, list[str]] = {}
        for resource in recent_resources:
            recent_documents.setdefault(resource.representative_turn_id, []).append(
                resource.document_id
            )
        edge_rows = session.scalars(
            select(AtlasTurnContextLineageEdgeRow)
            .where(
                AtlasTurnContextLineageEdgeRow.dependent_context_pack_ref
                == row.context_pack_ref
            )
            .order_by(AtlasTurnContextLineageEdgeRow.edge_id)
        ).all()
        summary = None
        if row.summary_ref is not None:
            summary_row = session.get(AtlasTurnContextSummaryRow, row.summary_ref)
            if summary_row is None:
                raise ContextStoreConflict("context pack summary is missing")
            summary = SummaryRecord(
                summary_ref=summary_row.summary_ref,
                parent_summary_ref=summary_row.parent_summary_ref,
                text=summary_row.text,
                token_count=summary_row.token_count,
                sources=self._summary_sources(session, summary_row.summary_ref),
                digest=summary_row.digest,
            )
        return ContextPackRecord(
            context_pack_ref=row.context_pack_ref,
            schema_version=row.schema_version,
            execution_id=row.execution_id,
            input_projection_ref=row.input_projection_ref,
            conversation_id=row.conversation_id,
            model_user_input=row.model_user_input,
            recent_tail=tuple(
                RecentExchangeInput(
                    logical_turn_id=recent.logical_turn_id,
                    representative_turn_id=recent.representative_turn_id,
                    representative_content_digest=recent.representative_content_digest,
                    user_message=ContextMessageInput("user", recent.user_text),
                    assistant_message=(
                        None
                        if recent.assistant_text is None
                        else ContextMessageInput(
                            "assistant",
                            recent.assistant_text,
                            recent.assistant_verification_status,  # type: ignore[arg-type]
                        )
                    ),
                    direct_document_ids=tuple(
                        sorted(
                            recent_documents.get(recent.representative_turn_id, ())
                        )
                    ),
                )
                for recent in recent_rows
            ),
            summary=summary,
            dependencies=tuple(self._lineage(edge) for edge in edge_rows),
            token_budget=row.token_budget,
            digest=row.digest,
            created_at=row.created_at,
        )


__all__ = [
    "CreateInputProjectionInput",
    "ContextMessageInput",
    "ContextPackRecord",
    "ContextReleaseRecord",
    "ContextStoreConflict",
    "InputProjectionRecord",
    "LineageEdgeRecord",
    "LineageGraphRecord",
    "MaterializeContextInput",
    "PostgresContextEngineeringStore",
    "RecentExchangeInput",
    "RecordResolverProjectionInput",
    "RecordRewriteProjectionInput",
    "ReleaseContextInput",
    "SourceLineageInput",
    "SummaryInput",
    "SummaryRecord",
    "SummarySourceInput",
]
