"""Audit-owned immutable safe strict-turn terminal drafts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasTurnAuditDraftReleaseRow,
    AtlasTurnAuditDraftRow,
)
from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV1,
    MaterializeTurnAuditDraftV2,
    ReleaseTurnAuditDraftV1,
    TurnAuditDraftReleaseV1,
    TurnAuditDraftV1,
    TurnAuditDraftV2,
)


SessionFactory = Callable[[], Session]


class TurnAuditDraftStoreConflict(RuntimeError):
    """An immutable audit draft or release identity conflicts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _semantic_payload(command: MaterializeTurnAuditDraftV1) -> dict[str, object]:
    payload = command.model_dump(mode="json")
    payload.pop("draft_ref")
    payload.pop("idempotency_key")
    return {
        "operation": "materialize_turn_audit_draft",
        "schema_version": "turn-audit-draft-v1",
        **payload,
    }


def _semantic_payload_v2(command: MaterializeTurnAuditDraftV2) -> dict[str, object]:
    payload = command.model_dump(mode="json")
    payload.pop("draft_ref")
    payload.pop("idempotency_key")
    return {
        "operation": "materialize_turn_audit_draft",
        "schema_version": "turn-audit-draft-v2",
        **payload,
    }


def _draft_from_row(row: AtlasTurnAuditDraftRow) -> TurnAuditDraftV1:
    draft = TurnAuditDraftV1.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.digest != row.digest
        or draft.terminal_status != row.terminal_status
        or draft.retrieval_status != row.retrieval_status
        or draft.verification_status != row.verification_status
    ):
        raise TurnAuditDraftStoreConflict("turn audit draft row projection changed")
    return draft


def _draft_v2_from_row(row: AtlasTurnAuditDraftRow) -> TurnAuditDraftV2:
    draft = TurnAuditDraftV2.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.digest != row.digest
        or draft.terminal_status != row.terminal_status
        or draft.retrieval_status != row.retrieval_status
        or draft.evidence_review_status != row.verification_status
    ):
        raise TurnAuditDraftStoreConflict("turn audit V2 draft row projection changed")
    return draft


def _require_contiguous_steps(
    command: MaterializeTurnAuditDraftV1 | MaterializeTurnAuditDraftV2,
) -> None:
    ordinals = [step.ordinal for step in command.steps]
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError("audit step ordinals must be contiguous and monotonic")


class PostgresAuditV1Store:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def materialize(self, command: MaterializeTurnAuditDraftV1) -> TurnAuditDraftV1:
        _require_contiguous_steps(command)
        semantic_digest = _digest(_semantic_payload(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnAuditDraftRow).where(
                    AtlasTurnAuditDraftRow.execution_id == command.execution_id,
                    AtlasTurnAuditDraftRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.draft_ref != command.draft_ref or replay.digest != semantic_digest:
                    raise TurnAuditDraftStoreConflict("turn audit draft replay payload changed")
                return _draft_from_row(replay)
            if (
                session.get(AtlasTurnAuditDraftRow, command.draft_ref) is not None
                or session.scalar(
                    select(AtlasTurnAuditDraftRow).where(
                        AtlasTurnAuditDraftRow.execution_id == command.execution_id
                    )
                ) is not None
            ):
                raise TurnAuditDraftStoreConflict("audit draft or execution identity already exists")
            draft = TurnAuditDraftV1(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                claimed_evidence_handles=command.claimed_evidence_handles,
                evidence_pack_ref=command.evidence_pack_ref,
                evidence_pack_digest=command.evidence_pack_digest,
                governed_answer_draft_ref=command.governed_answer_draft_ref,
                governed_answer_digest=command.governed_answer_digest,
                citation_binding_draft_ref=command.citation_binding_draft_ref,
                citation_binding_digest=command.citation_binding_digest,
                retrieval_status=command.retrieval_status,
                verification_status=command.verification_status,
                terminal_status=command.terminal_status,
                steps=command.steps,
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnAuditDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    terminal_status=draft.terminal_status,
                    retrieval_status=draft.retrieval_status,
                    verification_status=draft.verification_status,
                    digest=draft.digest,
                    payload=draft.model_dump(mode="json"),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def materialize_v2(
        self, command: MaterializeTurnAuditDraftV2
    ) -> TurnAuditDraftV2:
        _require_contiguous_steps(command)
        semantic_digest = _digest(_semantic_payload_v2(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnAuditDraftRow).where(
                    AtlasTurnAuditDraftRow.execution_id == command.execution_id,
                    AtlasTurnAuditDraftRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if (
                    replay.schema_version != "turn-audit-draft-v2"
                    or replay.draft_ref != command.draft_ref
                    or replay.digest != semantic_digest
                ):
                    raise TurnAuditDraftStoreConflict(
                        "turn audit V2 draft replay payload changed"
                    )
                return _draft_v2_from_row(replay)
            if (
                session.get(AtlasTurnAuditDraftRow, command.draft_ref) is not None
                or session.scalar(
                    select(AtlasTurnAuditDraftRow).where(
                        AtlasTurnAuditDraftRow.execution_id == command.execution_id
                    )
                ) is not None
            ):
                raise TurnAuditDraftStoreConflict(
                    "audit draft or execution identity already exists"
                )
            draft = TurnAuditDraftV2(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                claimed_evidence_handles=command.claimed_evidence_handles,
                evidence_pack_ref=command.evidence_pack_ref,
                evidence_pack_digest=command.evidence_pack_digest,
                governed_answer_draft_ref=command.governed_answer_draft_ref,
                governed_answer_digest=command.governed_answer_digest,
                citation_binding_draft_ref=command.citation_binding_draft_ref,
                citation_binding_digest=command.citation_binding_digest,
                retrieval_status=command.retrieval_status,
                evidence_review_status=command.evidence_review_status,
                terminal_status=command.terminal_status,
                steps=command.steps,
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnAuditDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    terminal_status=draft.terminal_status,
                    retrieval_status=draft.retrieval_status,
                    verification_status=draft.evidence_review_status,
                    digest=draft.digest,
                    payload=draft.model_dump(mode="json"),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def read(self, draft_ref: str) -> TurnAuditDraftV1 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnAuditDraftRow, draft_ref)
            if row is None:
                return None
            if row.schema_version != "turn-audit-draft-v1":
                raise TurnAuditDraftStoreConflict("turn audit draft is not V1")
            return _draft_from_row(row)

    def read_v2(self, draft_ref: str) -> TurnAuditDraftV2 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnAuditDraftRow, draft_ref)
            if row is None:
                return None
            if row.schema_version != "turn-audit-draft-v2":
                raise TurnAuditDraftStoreConflict("turn audit draft is not V2")
            return _draft_v2_from_row(row)

    def read_raw_declared_evidence(self, execution_id: str) -> list[str] | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasTurnAuditDraftRow).where(
                    AtlasTurnAuditDraftRow.execution_id == execution_id,
                    AtlasTurnAuditDraftRow.schema_version == "turn-audit-draft-v2",
                )
            )
            if row is None:
                return None
            return list(_draft_v2_from_row(row).claimed_evidence_handles)

    def release(self, command: ReleaseTurnAuditDraftV1) -> TurnAuditDraftReleaseV1:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnAuditDraftReleaseRow).where(
                    AtlasTurnAuditDraftReleaseRow.execution_id == command.execution_id,
                    AtlasTurnAuditDraftReleaseRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.release_ref != command.release_ref or replay.draft_ref != command.draft_ref:
                    raise TurnAuditDraftStoreConflict("turn audit release replay changed")
                return self._release_record(replay)
            if (
                session.get(AtlasTurnAuditDraftReleaseRow, command.release_ref) is not None
                or session.scalar(
                    select(AtlasTurnAuditDraftReleaseRow).where(
                        AtlasTurnAuditDraftReleaseRow.execution_id == command.execution_id,
                        AtlasTurnAuditDraftReleaseRow.draft_ref == command.draft_ref,
                    )
                ) is not None
            ):
                raise TurnAuditDraftStoreConflict("turn audit release identity already exists")
            draft = session.get(AtlasTurnAuditDraftRow, command.draft_ref)
            if draft is None or draft.execution_id != command.execution_id:
                raise TurnAuditDraftStoreConflict("turn audit release binding is invalid")
            row = AtlasTurnAuditDraftReleaseRow(
                release_ref=command.release_ref,
                execution_id=command.execution_id,
                draft_ref=command.draft_ref,
                idempotency_key=command.idempotency_key,
                released_at=_now(),
            )
            session.add(row)
            session.flush()
            return self._release_record(row)

    @staticmethod
    def _release_record(row: AtlasTurnAuditDraftReleaseRow) -> TurnAuditDraftReleaseV1:
        return TurnAuditDraftReleaseV1(
            release_ref=row.release_ref,
            execution_id=row.execution_id,
            draft_ref=row.draft_ref,
            released_at=row.released_at,
        )


__all__ = ["PostgresAuditV1Store", "TurnAuditDraftStoreConflict"]
