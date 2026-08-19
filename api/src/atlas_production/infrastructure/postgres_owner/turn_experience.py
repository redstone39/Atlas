from __future__ import annotations

import hashlib
import json
from typing import Callable
from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.payload_policy import validate_typed_payload
from atlas_production.infrastructure.persistence.turn_experience import AtlasTurnExperienceRow
from atlas_production.modules.turn_experience.public import (
    MaterializeTurnExperienceV1,
    TurnExperienceCursorV1,
    TurnExperienceV1,
)


SessionFactory = Callable[[], Session]
_PAYLOAD_FIELDS = frozenset(MaterializeTurnExperienceV1.model_fields) - {"idempotency_key"}


class TurnExperienceStoreConflict(RuntimeError):
    """An immutable Experience identity or semantic payload conflicts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _semantic_payload(command: MaterializeTurnExperienceV1) -> dict[str, object]:
    payload = command.model_dump(mode="json")
    payload.pop("idempotency_key")
    return validate_typed_payload(
        payload,
        family="turn_experience_v1",
        allowed_fields=_PAYLOAD_FIELDS,
        max_bytes=65_536,
    )


def _experience_from_row(row: AtlasTurnExperienceRow) -> TurnExperienceV1:
    experience = TurnExperienceV1.model_validate(
        {
            **row.payload,
            "idempotency_key": row.idempotency_key,
            "digest": row.digest,
            "scan_sequence": row.scan_sequence,
            "created_at": row.created_at,
        }
    )
    if (
        experience.experience_ref != row.experience_ref
        or experience.execution_id != row.execution_id
        or experience.schema_version != row.schema_version
        or experience.turn_id != row.turn_id
        or experience.terminal.committed_at != row.committed_at
        or experience.reasoning_mode != row.reasoning_mode
    ):
        raise TurnExperienceStoreConflict("turn experience row projection changed")
    expected_digest = hashlib.sha256(_canonical(row.payload)).hexdigest()
    if experience.digest != expected_digest:
        raise TurnExperienceStoreConflict("turn experience digest does not bind payload")
    return experience


class PostgresTurnExperienceStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def materialize(self, command: MaterializeTurnExperienceV1) -> TurnExperienceV1:
        payload = _semantic_payload(command)
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        with self._session_factory() as session, session.begin():
            inserted_ref = session.execute(
                pg_insert(AtlasTurnExperienceRow)
                .values(
                    experience_ref=command.experience_ref,
                    execution_id=command.execution_id,
                    schema_version=command.schema_version,
                    turn_id=command.turn_id,
                    committed_at=command.terminal.committed_at,
                    reasoning_mode=command.reasoning_mode,
                    digest=digest,
                    payload=payload,
                    idempotency_key=command.idempotency_key,
                )
                .on_conflict_do_nothing()
                .returning(AtlasTurnExperienceRow.experience_ref)
            ).scalar_one_or_none()
            row = session.scalar(
                select(AtlasTurnExperienceRow).where(
                    AtlasTurnExperienceRow.execution_id == command.execution_id,
                    AtlasTurnExperienceRow.schema_version == command.schema_version,
                )
            )
            if row is None:
                row = session.get(AtlasTurnExperienceRow, command.experience_ref)
            if row is None:
                row = session.scalar(
                    select(AtlasTurnExperienceRow).where(
                        AtlasTurnExperienceRow.idempotency_key == command.idempotency_key
                    )
                )
            if row is None:
                raise TurnExperienceStoreConflict(
                    "turn experience insert conflicted without a readable identity"
                )
            if (
                row.experience_ref != command.experience_ref
                or row.execution_id != command.execution_id
                or row.schema_version != command.schema_version
                or row.idempotency_key != command.idempotency_key
                or row.digest != digest
                or row.payload != payload
            ):
                raise TurnExperienceStoreConflict(
                    "turn experience replay payload conflicts with the original"
                )
            if inserted_ref is not None and inserted_ref != row.experience_ref:
                raise TurnExperienceStoreConflict("turn experience insert returned another identity")
            return _experience_from_row(row)

    def read_for_execution(
        self, execution_id: str, schema_version: str
    ) -> TurnExperienceV1 | None:
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasTurnExperienceRow).where(
                    AtlasTurnExperienceRow.execution_id == execution_id,
                    AtlasTurnExperienceRow.schema_version == schema_version,
                )
            )
            return _experience_from_row(row) if row is not None else None

    def list_after(
        self, cursor: TurnExperienceCursorV1 | None, limit: int
    ) -> list[TurnExperienceV1]:
        if limit < 1 or limit > 100:
            raise ValueError("turn experience scan limit must be between 1 and 100")
        statement = select(AtlasTurnExperienceRow)
        if cursor is not None:
            statement = statement.where(
                tuple_(
                    AtlasTurnExperienceRow.scan_sequence,
                    AtlasTurnExperienceRow.execution_id,
                )
                > (cursor.scan_sequence, cursor.execution_id)
            )
        statement = statement.order_by(
            AtlasTurnExperienceRow.scan_sequence,
            AtlasTurnExperienceRow.execution_id,
        ).limit(limit)
        with self._session_factory() as session, session.begin():
            session.execute(
                text("LOCK TABLE atlas_turn_experiences IN SHARE MODE")
            )
            rows = session.scalars(statement).all()
            return [_experience_from_row(row) for row in rows]


__all__ = ["PostgresTurnExperienceStore", "TurnExperienceStoreConflict"]
