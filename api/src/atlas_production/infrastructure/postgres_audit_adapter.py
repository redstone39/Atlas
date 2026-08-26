from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import audit_events

from atlas_production.infrastructure.postgres_owner.audit import (
    AuditEventWriter,
    AuditRepository,
)
from atlas_production.modules.audit.public import safe_audit_metadata
from atlas_production.shared.public import AuditEventRecord, utc_now_iso
from atlas_production.shared.user_messages import MessageParams


SessionFactory = Callable[[], Session]


def build_audit_event(
    *,
    event_type: str,
    actor_id: str | None,
    target_ref: str | None,
    project_id: str | None,
    message_code: str,
    metadata: dict[str, object],
    message_params: MessageParams | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    document_id: str | None = None,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"audit-{uuid4().hex}",
        event_type=event_type,
        actor_id=actor_id,
        target_ref=target_ref,
        project_id=project_id,
        message_code=message_code,
        metadata=safe_audit_metadata(metadata),
        created_at=utc_now_iso(),
        message_params=message_params or {},
        scope_type=scope_type,
        scope_id=scope_id,
        document_id=document_id,
    )


def persist_rejection_audit(
    session_factory: SessionFactory,
    *,
    candidate: AuditEventRecord,
    message_code: str,
    reason: str,
) -> AuditEventRecord:
    """Persist a post-rollback rejection fact with its own durable identifier."""

    return PostgresReadAuditWriter(session_factory).append(
        event_type="admin_action_rejected",
        actor_id=candidate.actor_id,
        target_ref=candidate.target_ref,
        project_id=candidate.project_id,
        message_code=message_code,
        metadata={
            "reason": reason,
            "candidate_event_id": candidate.event_id,
        },
        scope_type=candidate.scope_type,
        scope_id=candidate.scope_id,
        document_id=candidate.document_id,
    )


@dataclass(frozen=True, slots=True)
class PostgresAuditConsumerAdapter:
    """Bounded audit evidence projection; it is never an authorization seam."""

    owner: AuditRepository

    def recent_events(self, *, limit: int = 50) -> list[AuditEventRecord]:
        return self.owner.recent_events(limit=limit)


@dataclass(frozen=True, slots=True)
class PostgresAgentResearchAuditReader:
    """Agent Research-only audit projection with no arbitrary query surface."""

    session_factory: SessionFactory

    def denials(
        self,
        *,
        after: tuple[datetime, str] | None,
        upper: tuple[datetime, str] | None,
        limit: int,
    ) -> list[AuditEventRecord]:
        if limit < 1 or limit > 101:
            raise ValueError("research denial limit must be between 1 and 101")
        with self.session_factory() as session:
            return audit_events.read_agent_research_denials(
                session,
                after=after,
                upper=upper,
                limit=limit,
            )

    def timeline(
        self,
        *,
        research_id: str,
        limit: int = 200,
    ) -> list[AuditEventRecord]:
        if not research_id or limit < 1 or limit > 200:
            raise ValueError("research audit timeline query is invalid")
        with self.session_factory() as session:
            return audit_events.read_agent_research_timeline(
                session,
                research_id=research_id,
                limit=limit,
            )


@dataclass(frozen=True, slots=True)
class PostgresReadAuditWriter:
    """Persist one route read outcome after its owning authority has decided."""

    session_factory: SessionFactory

    def append(
        self,
        *,
        event_type: str,
        actor_id: str | None,
        target_ref: str | None,
        project_id: str | None,
        message_code: str,
        metadata: dict[str, object],
        message_params: MessageParams | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        document_id: str | None = None,
    ) -> AuditEventRecord:
        event = build_audit_event(
            event_type=event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=project_id,
            message_code=message_code,
            metadata=metadata,
            message_params=message_params,
            scope_type=scope_type,
            scope_id=scope_id,
            document_id=document_id,
        )
        session = self.session_factory()
        with session:
            try:
                AuditEventWriter(session).append(event)
                session.commit()
                return event
            except Exception:
                session.rollback()
                raise

    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        message_code: str,
        message_params: MessageParams | None = None,
        project_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        document_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditEventRecord:
        return self.append(
            event_type=event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=project_id,
            message_code=message_code,
            metadata=metadata or {},
            message_params=message_params,
            scope_type=scope_type,
            scope_id=scope_id,
            document_id=document_id,
        )


def build_postgres_audit_adapter(
    session_factory: SessionFactory,
) -> tuple[PostgresAuditConsumerAdapter, PostgresReadAuditWriter]:
    return (
        PostgresAuditConsumerAdapter(AuditRepository(session_factory)),
        PostgresReadAuditWriter(session_factory),
    )


__all__ = [
    "PostgresAuditConsumerAdapter",
    "PostgresReadAuditWriter",
    "build_audit_event",
    "build_postgres_audit_adapter",
    "persist_rejection_audit",
]
