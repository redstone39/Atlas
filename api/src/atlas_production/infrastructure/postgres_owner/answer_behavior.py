"""Atomic PostgreSQL owner for global append-only Answer behavior revisions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.answer_behavior import (
    AtlasTurnAnswerBehaviorRevisionRow,
)
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.answer_behavior.public import (
    AnswerBehaviorError,
    AnswerBehaviorRevisionV1,
    AnswerBehaviorStatus,
    AnswerBehaviorUpdateRequest,
)


SessionFactory = Callable[[], Session]
_GLOBAL_LOCK = "turn-execution:answer-behavior"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_digest(payload: AnswerBehaviorUpdateRequest) -> str:
    return _canonical_digest(
        payload.model_dump(mode="json", exclude={"idempotency_key"})
    )


def _revision(row: AtlasTurnAnswerBehaviorRevisionRow) -> AnswerBehaviorRevisionV1:
    return AnswerBehaviorRevisionV1(
        revision=row.revision,
        custom_guidance=row.custom_guidance,
        guidance_digest=row.guidance_digest,
        created_at=row.created_at,
    )


def _status(row: AtlasTurnAnswerBehaviorRevisionRow) -> AnswerBehaviorStatus:
    return AnswerBehaviorStatus(
        revision=row.revision,
        custom_guidance=row.custom_guidance,
        guidance_digest=row.guidance_digest,
        updated_by=row.created_by,
        updated_at=row.created_at,
        audit_event_ref=row.audit_event_ref,
    )


def _empty_revision() -> AnswerBehaviorRevisionV1:
    return AnswerBehaviorRevisionV1(
        revision=0,
        custom_guidance=None,
        guidance_digest=None,
        created_at=None,
    )


def _empty_status() -> AnswerBehaviorStatus:
    return AnswerBehaviorStatus(
        revision=0,
        custom_guidance=None,
        guidance_digest=None,
        updated_by=None,
        updated_at=None,
        audit_event_ref=None,
    )


class PostgresAnswerBehaviorOwner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _latest(session: Session) -> AtlasTurnAnswerBehaviorRevisionRow | None:
        return session.scalar(
            select(AtlasTurnAnswerBehaviorRevisionRow)
            .order_by(AtlasTurnAnswerBehaviorRevisionRow.revision.desc())
            .limit(1)
        )

    @staticmethod
    def _validate_content(row: AtlasTurnAnswerBehaviorRevisionRow) -> None:
        if _canonical_digest(row.custom_guidance) != row.guidance_digest:
            raise AnswerBehaviorError(
                "answer_behavior_integrity_error",
                "answer_behavior.stored_revision_is_invalid",
                503,
            )

    def current(self) -> AnswerBehaviorRevisionV1:
        with self._session_factory() as session:
            row = self._latest(session)
            if row is None:
                return _empty_revision()
            self._validate_content(row)
            return _revision(row)

    def status(self) -> AnswerBehaviorStatus:
        with self._session_factory() as session:
            row = self._latest(session)
            if row is None:
                return _empty_status()
            self._validate_content(row)
            return _status(row)

    def read_exact(
        self, *, revision: int, guidance_digest: str | None
    ) -> AnswerBehaviorRevisionV1:
        if revision == 0:
            if guidance_digest is not None:
                raise AnswerBehaviorError(
                    "answer_behavior_integrity_error",
                    "answer_behavior.stored_revision_is_invalid",
                    503,
                )
            return _empty_revision()
        if revision < 0 or guidance_digest is None:
            raise AnswerBehaviorError(
                "answer_behavior_integrity_error",
                "answer_behavior.stored_revision_is_invalid",
                503,
            )
        with self._session_factory() as session:
            row = session.get(AtlasTurnAnswerBehaviorRevisionRow, revision)
            if row is None or row.guidance_digest != guidance_digest:
                raise AnswerBehaviorError(
                    "answer_behavior_integrity_error",
                    "answer_behavior.stored_revision_is_invalid",
                    503,
                )
            self._validate_content(row)
            return _revision(row)

    def update(
        self, *, actor_id: str, payload: AnswerBehaviorUpdateRequest
    ) -> AnswerBehaviorStatus:
        digest = _request_digest(payload)
        session = self._session_factory()
        with session:
            try:
                acquire_owner_locks(session, domain_keys=(_GLOBAL_LOCK,))
                replay = session.scalar(
                    select(AtlasTurnAnswerBehaviorRevisionRow).where(
                        AtlasTurnAnswerBehaviorRevisionRow.idempotency_key
                        == payload.idempotency_key
                    )
                )
                if replay is not None:
                    if replay.request_digest != digest:
                        raise AnswerBehaviorError(
                            "idempotency_conflict",
                            "answer_behavior.idempotency_key_was_reused",
                            409,
                        )
                    self._validate_content(replay)
                    session.rollback()
                    return _status(replay)

                current = self._latest(session)
                current_revision = 0 if current is None else current.revision
                if current_revision != payload.expected_revision:
                    raise AnswerBehaviorError(
                        "revision_conflict",
                        "answer_behavior.revision_changed_before_update",
                        409,
                    )

                next_revision = current_revision + 1
                guidance_digest = _canonical_digest(payload.custom_guidance)
                created_at = _now()
                message_code = (
                    "answer_behavior.custom_guidance_was_cleared"
                    if payload.custom_guidance is None
                    else "answer_behavior.custom_guidance_was_updated"
                )
                audit = build_audit_event(
                    event_type="answer_behavior_updated",
                    actor_id=actor_id,
                    target_ref=f"answer-behavior:{next_revision}",
                    project_id=None,
                    message_code=message_code,
                    metadata={
                        "revision": next_revision,
                        "guidance_digest": guidance_digest,
                        "guidance_character_count": len(
                            payload.custom_guidance or ""
                        ),
                        "request_id": payload.idempotency_key,
                        "status": (
                            "cleared"
                            if payload.custom_guidance is None
                            else "configured"
                        ),
                    },
                )
                row = AtlasTurnAnswerBehaviorRevisionRow(
                    revision=next_revision,
                    custom_guidance=payload.custom_guidance,
                    guidance_digest=guidance_digest,
                    created_by=actor_id,
                    idempotency_key=payload.idempotency_key,
                    request_digest=digest,
                    audit_event_ref=audit.event_id,
                    created_at=created_at,
                )
                session.add(row)
                AuditEventWriter(session).append(audit)
                session.commit()
                return _status(row)
            except Exception:
                session.rollback()
                raise


__all__ = ["PostgresAnswerBehaviorOwner"]
