from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import audit_events
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAccessDecisionRow,
)
from atlas_production.modules.identity_access.records import AccessDecisionRecord
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]


def _access_decision_row(record: AccessDecisionRecord) -> AtlasAccessDecisionRow:
    return AtlasAccessDecisionRow(
        decision_id=record.decision_id,
        actor_type=record.actor_type,
        actor_id=record.actor_id,
        project_id=record.project_id,
        scope_type=record.scope_type,
        scope_id=record.scope_id,
        action=record.action,
        required_role=record.required_role,
        allowed=record.allowed,
        reason=record.reason,
        effective_role=record.effective_role,
        source_type=record.source_type,
        source_id=record.source_id,
        explanation=record.explanation,
        created_at=record.created_at,
    )


@dataclass(frozen=True, slots=True)
class AuditEventWriter:
    _session: Session

    def append(self, event: AuditEventRecord) -> None:
        audit_events.add_event_rows(self._session, (event,))

    def append_many(self, events: tuple[AuditEventRecord, ...]) -> None:
        audit_events.add_event_rows(self._session, events)


@dataclass(frozen=True, slots=True)
class AccessDecisionWriter:
    _session: Session

    def append(self, decision: AccessDecisionRecord) -> None:
        self._session.add(_access_decision_row(decision))


@dataclass(frozen=True, slots=True)
class AuditRepository:
    session_factory: SessionFactory

    def recent_events(self, *, limit: int = 50) -> list[AuditEventRecord]:
        if limit < 1 or limit > 200:
            raise ValueError("audit event limit must be between 1 and 200")
        with self.session_factory() as session:
            return audit_events.read_recent_events(session, limit=limit)



__all__ = [
    "AccessDecisionWriter",
    "AuditEventWriter",
    "AuditRepository",
]
