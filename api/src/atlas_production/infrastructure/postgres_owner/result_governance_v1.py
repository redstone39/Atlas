"""Result-governance-owned immutable strict-turn answer drafts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.result_governance import (
    AtlasTurnGovernedAnswerDraftReleaseRow,
    AtlasTurnGovernedAnswerDraftRow,
)
from atlas_production.modules.result_governance.public import (
    EvidenceReviewReasonCodeV2,
    GovernedAnswerDraftReleaseV1,
    GovernedAnswerDraftV1,
    GovernedAnswerDraftV2,
    GovernedAnswerSegmentV1,
    GovernedAnswerSegmentV2,
    GovernedClaimV1,
    MaterializeGovernedAnswerDraftV1,
    MaterializeGovernedAnswerDraftV2,
    ReleaseGovernedAnswerDraftV1,
)


SessionFactory = Callable[[], Session]


class ResultGovernanceStoreConflict(RuntimeError):
    """An immutable answer draft or release identity conflicts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _semantic_payload(command: MaterializeGovernedAnswerDraftV1) -> dict[str, object]:
    return {
        "operation": "materialize_governed_answer_draft",
        "schema_version": "governed-answer-draft-v1",
        "execution_id": command.execution_id,
        "finalized_answer": command.finalized_answer.model_dump(mode="json"),
        "retrieval_status": command.retrieval_status,
        "evidence_lineage": [item.model_dump(mode="json") for item in command.evidence_lineage],
        "assessment_succeeded": command.assessment_succeeded,
        "assessments": [item.model_dump(mode="json") for item in command.assessments],
    }


def _semantic_payload_v2(
    command: MaterializeGovernedAnswerDraftV2,
) -> dict[str, object]:
    return {
        "operation": "materialize_governed_answer_draft",
        "schema_version": "governed-answer-draft-v2",
        "execution_id": command.execution_id,
        "finalized_answer": command.finalized_answer.model_dump(mode="json"),
        "retrieval_status": command.retrieval_status,
        "declared_evidence_mappings": [
            item.model_dump(mode="json")
            for item in command.declared_evidence_mappings
        ],
        "evidence_lineage": [
            item.model_dump(mode="json") for item in command.evidence_lineage
        ],
        "assessment_state": command.assessment_state,
        "assessment_reason_code": command.assessment_reason_code,
        "assessment_version": command.assessment_version,
        "assessment_consistency": command.assessment_consistency,
        "assessment_answer_digest": command.assessment_answer_digest,
        "assessment_declared_subset_digest": command.assessment_declared_subset_digest,
        "assessment_visual_image_digests": command.assessment_visual_image_digests,
        "assessment_input_digest": command.assessment_input_digest,
        "assessment_output_digest": command.assessment_output_digest,
        "assessment_results": [
            item.model_dump(mode="json") for item in command.assessment_results
        ],
        "delivery_constraint": command.delivery_constraint,
    }


def _draft_payload(draft: GovernedAnswerDraftV1) -> dict[str, object]:
    return draft.model_dump(mode="json")


def _draft_from_row(row: AtlasTurnGovernedAnswerDraftRow) -> GovernedAnswerDraftV1:
    draft = GovernedAnswerDraftV1.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.digest != row.digest
        or draft.retrieval_status != row.retrieval_status
        or draft.verification_status != row.verification_status
    ):
        raise ResultGovernanceStoreConflict("governed answer draft row projection changed")
    return draft


def _draft_v2_from_row(row: AtlasTurnGovernedAnswerDraftRow) -> GovernedAnswerDraftV2:
    draft = GovernedAnswerDraftV2.model_validate(row.payload)
    if (
        draft.draft_ref != row.draft_ref
        or draft.execution_id != row.execution_id
        or draft.digest != row.digest
        or draft.retrieval_status != row.retrieval_status
        or draft.evidence_review_status != row.verification_status
    ):
        raise ResultGovernanceStoreConflict(
            "governed answer V2 draft row projection changed"
        )
    return draft


def _governed_segments(
    command: MaterializeGovernedAnswerDraftV1,
) -> tuple[list[GovernedAnswerSegmentV1], str]:
    proposal = command.finalized_answer
    lineage_by_handle = {item.evidence_handle: item for item in command.evidence_lineage}
    assessments_by_segment: dict[str, list] = {}
    for assessment in command.assessments:
        assessments_by_segment.setdefault(assessment.segment_id, []).append(assessment)
    governed_segments: list[GovernedAnswerSegmentV1] = []
    supported_claims = 0
    unsupported_claims = 0
    for segment in proposal.segments:
        governed_claims: list[GovernedClaimV1] = []
        for assessment in sorted(
            assessments_by_segment.get(segment.segment_id, []),
            key=lambda item: (item.start, item.end),
        ):
            claim_text = segment.text[assessment.start : assessment.end]
            claim_id = "claim:" + hashlib.sha256(
                _canonical(
                    {
                        "execution_id": command.execution_id,
                        "segment_id": segment.segment_id,
                        "start": assessment.start,
                        "end": assessment.end,
                        "claim_text_digest": hashlib.sha256(
                            claim_text.encode("utf-8")
                        ).hexdigest(),
                    }
                )
            ).hexdigest()
            verified = command.assessment_succeeded and assessment.decision == "supported"
            if verified:
                supported_claims += 1
            else:
                unsupported_claims += 1
            evidence_refs = [
                lineage_by_handle[handle].evidence_ref
                for handle in assessment.supporting_evidence_handles
            ]
            governed_claims.append(
                GovernedClaimV1(
                    claim_id=claim_id,
                    start=assessment.start,
                    end=assessment.end,
                    verification_status="verified" if verified else "unverified",
                    evidence_refs=evidence_refs if verified else [],
                )
            )
        segment_verified = command.assessment_succeeded and bool(governed_claims) and all(
            claim.verification_status == "verified" for claim in governed_claims
        )
        governed_segments.append(
            GovernedAnswerSegmentV1(
                segment_id=segment.segment_id,
                text=segment.text,
                verification_status="verified" if segment_verified else "unverified",
                claims=governed_claims,
            )
        )
    if command.assessment_succeeded and supported_claims and not unsupported_claims:
        turn_status = "verified"
    elif command.assessment_succeeded and supported_claims and unsupported_claims:
        turn_status = "partially_verified"
    else:
        turn_status = "unverified"
    return governed_segments, turn_status


def _governed_segments_v2(
    command: MaterializeGovernedAnswerDraftV2,
) -> tuple[
    list[GovernedAnswerSegmentV2],
    str,
    list[EvidenceReviewReasonCodeV2],
]:
    governed_segments = [
        GovernedAnswerSegmentV2(
            segment_id=segment.segment_id,
            text=segment.text,
        )
        for segment in command.finalized_answer.segments
    ]

    reason_codes: list[EvidenceReviewReasonCodeV2] = []
    if not command.declared_evidence_mappings:
        reason_codes.append("empty_declaration")
    if command.assessment_state != "completed":
        reason_codes.append("assessment_not_completed")
    if command.assessment_consistency != "aligned":
        reason_codes.append("declared_evidence_not_aligned")
    if any(
        item.status == "failure" for item in command.assessment_results
    ):
        reason_codes.append("answer_item_failed")
    if command.delivery_constraint == "correction_limit_reached":
        # The limit-final candidate is deliberately not sent through another
        # Process Evaluator cycle, so the combined soft review is incomplete
        # even when its final declared-evidence Gate aligns.
        reason_codes.append("assessment_not_completed")
    if reason_codes:
        return governed_segments, "questionable", reason_codes
    return governed_segments, "evidence_aligned", ["evidence_aligned"]


class PostgresResultGovernanceV1Store:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def materialize(
        self, command: MaterializeGovernedAnswerDraftV1
    ) -> GovernedAnswerDraftV1:
        governed_segments, turn_status = _governed_segments(command)

        semantic_digest = _digest(_semantic_payload(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnGovernedAnswerDraftRow).where(
                    AtlasTurnGovernedAnswerDraftRow.execution_id == command.execution_id,
                    AtlasTurnGovernedAnswerDraftRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.draft_ref != command.draft_ref or replay.digest != semantic_digest:
                    raise ResultGovernanceStoreConflict("answer draft replay payload changed")
                return _draft_from_row(replay)
            if (
                session.get(AtlasTurnGovernedAnswerDraftRow, command.draft_ref) is not None
                or session.scalar(
                    select(AtlasTurnGovernedAnswerDraftRow).where(
                        AtlasTurnGovernedAnswerDraftRow.execution_id == command.execution_id
                    )
                ) is not None
            ):
                raise ResultGovernanceStoreConflict("answer draft or execution identity already exists")
            draft = GovernedAnswerDraftV1(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                retrieval_status=command.retrieval_status,
                verification_status=turn_status,
                segments=governed_segments,
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnGovernedAnswerDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    retrieval_status=draft.retrieval_status,
                    verification_status=draft.verification_status,
                    digest=draft.digest,
                    payload=_draft_payload(draft),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def read(self, draft_ref: str) -> GovernedAnswerDraftV1 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnGovernedAnswerDraftRow, draft_ref)
            return _draft_from_row(row) if row is not None else None

    def materialize_v2(
        self, command: MaterializeGovernedAnswerDraftV2
    ) -> GovernedAnswerDraftV2:
        governed_segments, review_status, reason_codes = _governed_segments_v2(
            command
        )
        semantic_digest = _digest(_semantic_payload_v2(command))
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnGovernedAnswerDraftRow).where(
                    AtlasTurnGovernedAnswerDraftRow.execution_id
                    == command.execution_id,
                    AtlasTurnGovernedAnswerDraftRow.idempotency_key
                    == command.idempotency_key,
                )
            )
            if replay is not None:
                if (
                    replay.draft_ref != command.draft_ref
                    or replay.digest != semantic_digest
                    or replay.schema_version != "governed-answer-draft-v2"
                ):
                    raise ResultGovernanceStoreConflict(
                        "answer V2 draft replay payload changed"
                    )
                return _draft_v2_from_row(replay)
            if (
                session.get(AtlasTurnGovernedAnswerDraftRow, command.draft_ref)
                is not None
                or session.scalar(
                    select(AtlasTurnGovernedAnswerDraftRow).where(
                        AtlasTurnGovernedAnswerDraftRow.execution_id
                        == command.execution_id
                    )
                )
                is not None
            ):
                raise ResultGovernanceStoreConflict(
                    "answer draft or execution identity already exists"
                )
            draft = GovernedAnswerDraftV2(
                draft_ref=command.draft_ref,
                execution_id=command.execution_id,
                retrieval_status=command.retrieval_status,
                evidence_review_status=review_status,
                evidence_review_reason_codes=reason_codes,
                declared_evidence_mappings=command.declared_evidence_mappings,
                assessment_state=command.assessment_state,
                assessment_reason_code=command.assessment_reason_code,
                assessment_version=command.assessment_version,
                assessment_consistency=command.assessment_consistency,
                assessment_answer_digest=command.assessment_answer_digest,
                assessment_declared_subset_digest=command.assessment_declared_subset_digest,
                assessment_visual_image_digests=command.assessment_visual_image_digests,
                assessment_input_digest=command.assessment_input_digest,
                assessment_output_digest=command.assessment_output_digest,
                assessment_results=command.assessment_results,
                segments=governed_segments,
                digest=semantic_digest,
                created_at=_now(),
            )
            session.add(
                AtlasTurnGovernedAnswerDraftRow(
                    draft_ref=draft.draft_ref,
                    execution_id=draft.execution_id,
                    schema_version=draft.schema_version,
                    retrieval_status=draft.retrieval_status,
                    verification_status=draft.evidence_review_status,
                    digest=draft.digest,
                    payload=_draft_payload(draft),
                    idempotency_key=command.idempotency_key,
                    created_at=draft.created_at,
                )
            )
            session.flush()
            return draft

    def read_v2(self, draft_ref: str) -> GovernedAnswerDraftV2 | None:
        with self._session_factory() as session:
            row = session.get(AtlasTurnGovernedAnswerDraftRow, draft_ref)
            if row is None:
                return None
            if row.schema_version != "governed-answer-draft-v2":
                raise ResultGovernanceStoreConflict(
                    "governed answer draft is not V2"
                )
            return _draft_v2_from_row(row)

    def release(
        self, command: ReleaseGovernedAnswerDraftV1
    ) -> GovernedAnswerDraftReleaseV1:
        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(AtlasTurnGovernedAnswerDraftReleaseRow).where(
                    AtlasTurnGovernedAnswerDraftReleaseRow.execution_id == command.execution_id,
                    AtlasTurnGovernedAnswerDraftReleaseRow.idempotency_key == command.idempotency_key,
                )
            )
            if replay is not None:
                if replay.release_ref != command.release_ref or replay.draft_ref != command.draft_ref:
                    raise ResultGovernanceStoreConflict("answer draft release replay changed")
                return self._release_record(replay)
            if (
                session.get(AtlasTurnGovernedAnswerDraftReleaseRow, command.release_ref) is not None
                or session.scalar(
                    select(AtlasTurnGovernedAnswerDraftReleaseRow).where(
                        AtlasTurnGovernedAnswerDraftReleaseRow.execution_id == command.execution_id,
                        AtlasTurnGovernedAnswerDraftReleaseRow.draft_ref == command.draft_ref,
                    )
                ) is not None
            ):
                raise ResultGovernanceStoreConflict("answer draft release identity already exists")
            draft = session.get(AtlasTurnGovernedAnswerDraftRow, command.draft_ref)
            if draft is None or draft.execution_id != command.execution_id:
                raise ResultGovernanceStoreConflict("answer draft release binding is invalid")
            row = AtlasTurnGovernedAnswerDraftReleaseRow(
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
        row: AtlasTurnGovernedAnswerDraftReleaseRow,
    ) -> GovernedAnswerDraftReleaseV1:
        return GovernedAnswerDraftReleaseV1(
            release_ref=row.release_ref,
            execution_id=row.execution_id,
            draft_ref=row.draft_ref,
            released_at=row.released_at,
        )


__all__ = [
    "PostgresResultGovernanceV1Store",
    "ResultGovernanceStoreConflict",
    "_governed_segments_v2",
]
