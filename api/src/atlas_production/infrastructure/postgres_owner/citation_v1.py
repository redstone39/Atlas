"""Citation-owned immutable strict-turn verified-claim binding drafts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.citation_preview import (
    AtlasTurnCitationBindingDraftReleaseRow,
    AtlasTurnCitationBindingDraftRow,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftReleaseV1,
    CitationBindingDraftV1,
    CitationBindingDraftV2,
    CitationBindingV1,
    MaterializeCitationBindingDraftV1,
    MaterializeCitationBindingDraftV2,
    ReleaseCitationBindingDraftV1,
)


SessionFactory = Callable[[], Session]


class CitationBindingStoreConflict(RuntimeError):
    """An immutable citation draft or release identity conflicts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _semantic_payload(command: MaterializeCitationBindingDraftV1) -> dict[str, object]:
    return {
        "operation": "materialize_citation_binding_draft",
        "schema_version": "citation-binding-draft-v1",
        "execution_id": command.execution_id,
        "governed_answer": command.governed_answer.model_dump(mode="json"),
    }


def _semantic_payload_v2(
    command: MaterializeCitationBindingDraftV2,
) -> dict[str, object]:
    return {
        "operation": "materialize_citation_binding_draft",
        "schema_version": "citation-binding-draft-v2",
        "execution_id": command.execution_id,
        "governed_answer": command.governed_answer.model_dump(mode="json"),
    }


def _draft_from_row(row: AtlasTurnCitationBindingDraftRow) -> CitationBindingDraftV1:
    draft = CitationBindingDraftV1.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.governed_answer_draft_ref != row.governed_answer_draft_ref
        or draft.governed_answer_digest != row.governed_answer_digest
        or draft.digest != row.digest
    ):
        raise CitationBindingStoreConflict("citation binding draft row projection changed")
    return draft


def _draft_v2_from_row(
    row: AtlasTurnCitationBindingDraftRow,
) -> CitationBindingDraftV2:
    draft = CitationBindingDraftV2.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.governed_answer_draft_ref != row.governed_answer_draft_ref
        or draft.governed_answer_digest != row.governed_answer_digest
        or draft.digest != row.digest
    ):
        raise CitationBindingStoreConflict("citation binding draft row projection changed")
    return draft


def _eligible_bindings(
    command: MaterializeCitationBindingDraftV1,
) -> list[CitationBindingV1]:
    answer = command.governed_answer
    if answer.execution_id != command.execution_id:
        raise ValueError("governed answer belongs to another execution")
    bindings: list[CitationBindingV1] = []
    seen: set[tuple[str, str]] = set()
    for segment in answer.segments:
        for claim in segment.claims:
            if claim.verification_status != "verified":
                continue
            for evidence_ref in claim.evidence_refs:
                pair = (claim.claim_id, evidence_ref)
                if pair in seen:
                    continue
                seen.add(pair)
                citation_ref = "citation-binding-" + _digest(
                    {
                        "answer_draft_ref": answer.draft_ref,
                        "segment_id": segment.segment_id,
                        "claim_id": claim.claim_id,
                        "evidence_ref": evidence_ref,
                    }
                )
                bindings.append(
                    CitationBindingV1(
                        citation_ref=citation_ref,
                        segment_id=segment.segment_id,
                        claim_id=claim.claim_id,
                        evidence_ref=evidence_ref,
                    )
                )
    return bindings


class PostgresCitationV1Store:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def materialize(
        self, command: MaterializeCitationBindingDraftV1
    ) -> CitationBindingDraftV1:
        answer = command.governed_answer
        bindings = _eligible_bindings(command)
        semantic_digest = _digest(_semantic_payload(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnCitationBindingDraftRow).where(
                    AtlasTurnCitationBindingDraftRow.execution_id == command.execution_id,
                    AtlasTurnCitationBindingDraftRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.draft_ref != command.draft_ref or replay.digest != semantic_digest:
                    raise CitationBindingStoreConflict("citation draft replay payload changed")
                return _draft_from_row(replay)
            if (
                session.get(AtlasTurnCitationBindingDraftRow, command.draft_ref) is not None
                or session.scalar(
                    select(AtlasTurnCitationBindingDraftRow).where(
                        AtlasTurnCitationBindingDraftRow.execution_id == command.execution_id
                    )
                ) is not None
            ):
                raise CitationBindingStoreConflict("citation draft or execution identity already exists")
            draft = CitationBindingDraftV1(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                governed_answer_draft_ref=answer.draft_ref,
                governed_answer_digest=answer.digest,
                bindings=bindings,
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnCitationBindingDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    governed_answer_draft_ref=draft.governed_answer_draft_ref,
                    governed_answer_digest=draft.governed_answer_digest,
                    digest=draft.digest,
                    payload=draft.model_dump(mode="json"),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def read(self, draft_ref: str) -> CitationBindingDraftV1 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnCitationBindingDraftRow, draft_ref)
            return (
                _draft_from_row(row)
                if row is not None
                and row.schema_version == "citation-binding-draft-v1"
                else None
            )

    def materialize_v2(
        self, command: MaterializeCitationBindingDraftV2
    ) -> CitationBindingDraftV2:
        answer = command.governed_answer
        if answer.execution_id != command.execution_id:
            raise ValueError("governed answer belongs to another execution")
        semantic_digest = _digest(_semantic_payload_v2(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnCitationBindingDraftRow).where(
                    AtlasTurnCitationBindingDraftRow.execution_id
                    == command.execution_id,
                    AtlasTurnCitationBindingDraftRow.idempotency_key
                    == command.idempotency_key,
                )
            )
            if replay is not None:
                if (
                    replay.schema_version != "citation-binding-draft-v2"
                    or replay.draft_ref != command.draft_ref
                    or replay.digest != semantic_digest
                ):
                    raise CitationBindingStoreConflict(
                        "citation draft replay payload changed"
                    )
                return _draft_v2_from_row(replay)
            if (
                session.get(AtlasTurnCitationBindingDraftRow, command.draft_ref)
                is not None
                or session.scalar(
                    select(AtlasTurnCitationBindingDraftRow).where(
                        AtlasTurnCitationBindingDraftRow.execution_id
                        == command.execution_id
                    )
                )
                is not None
            ):
                raise CitationBindingStoreConflict(
                    "citation draft or execution identity already exists"
                )
            draft = CitationBindingDraftV2(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                governed_answer_draft_ref=answer.draft_ref,
                governed_answer_digest=answer.digest,
                bindings=[],
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnCitationBindingDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    governed_answer_draft_ref=draft.governed_answer_draft_ref,
                    governed_answer_digest=draft.governed_answer_digest,
                    digest=draft.digest,
                    payload=draft.model_dump(mode="json"),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def read_v2(self, draft_ref: str) -> CitationBindingDraftV2 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnCitationBindingDraftRow, draft_ref)
            return (
                _draft_v2_from_row(row)
                if row is not None
                and row.schema_version == "citation-binding-draft-v2"
                else None
            )

    def release(
        self, command: ReleaseCitationBindingDraftV1
    ) -> CitationBindingDraftReleaseV1:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnCitationBindingDraftReleaseRow).where(
                    AtlasTurnCitationBindingDraftReleaseRow.execution_id == command.execution_id,
                    AtlasTurnCitationBindingDraftReleaseRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.release_ref != command.release_ref or replay.draft_ref != command.draft_ref:
                    raise CitationBindingStoreConflict("citation release replay changed")
                return self._release_record(replay)
            if (
                session.get(AtlasTurnCitationBindingDraftReleaseRow, command.release_ref) is not None
                or session.scalar(
                    select(AtlasTurnCitationBindingDraftReleaseRow).where(
                        AtlasTurnCitationBindingDraftReleaseRow.execution_id == command.execution_id,
                        AtlasTurnCitationBindingDraftReleaseRow.draft_ref == command.draft_ref,
                    )
                ) is not None
            ):
                raise CitationBindingStoreConflict("citation release identity already exists")
            draft = session.get(AtlasTurnCitationBindingDraftRow, command.draft_ref)
            if draft is None or draft.execution_id != command.execution_id:
                raise CitationBindingStoreConflict("citation release binding is invalid")
            row = AtlasTurnCitationBindingDraftReleaseRow(
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
    def _release_record(
        row: AtlasTurnCitationBindingDraftReleaseRow,
    ) -> CitationBindingDraftReleaseV1:
        return CitationBindingDraftReleaseV1(
            release_ref=row.release_ref,
            execution_id=row.execution_id,
            draft_ref=row.draft_ref,
            released_at=row.released_at,
        )


__all__ = ["CitationBindingStoreConflict", "PostgresCitationV1Store"]
