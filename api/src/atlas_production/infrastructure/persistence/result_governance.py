from typing import Any
from types import SimpleNamespace

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.result_governance.records import (
    ClaimEvidenceLink,
    ClaimRecord,
    ClaimSupportAssessment,
    ResponseSegmentRecord,
    validate_claim_graph,
)

from .base import OrmBase
from .payload_policy import serialize_typed_dataclass


_SEGMENT_FIELDS = {
    "segment_id", "assistant_turn_id", "kind", "segment_canonical_text",
    "canonical_text_digest", "normalization_version", "created_at", "artifact_id",
}
_CLAIM_FIELDS = {
    "claim_id", "provider_claim_id", "segment_id", "start_codepoint_offset",
    "end_codepoint_offset", "claim_text_digest", "created_at",
}
_CLAIM_EVIDENCE_FIELDS = {
    "link_id", "claim_id", "evidence_id", "relation", "support_scope",
    "evidence_stance_summary", "conflict_field", "confidence_score",
    "confidence_method", "calibration_reference", "created_at",
}
_ASSESSMENT_FIELDS = {
    "claim_id", "status", "supported_scope", "unsupported_scope",
    "conflict_summary", "assessment_method", "assessment_version", "created_at",
}


def _valid_externalized_claim_graph(store: Any) -> bool:
    """Validate metadata when canonical segment bytes live in artifact storage.

    Full text/digest/offset validation happens before publication and again when
    a protected consumer hydrates the segment. A restarted process only has the
    typed projection, so it can validate relationships and decision invariants
    without requiring PostgreSQL to retain the canonical answer text.
    """
    for claim_id, claim in store.claim_records.items():
        segment = store.response_segment_records.get(claim.segment_id)
        if segment is None or not segment.artifact_id:
            return False
        if not (
            0 <= claim.start_codepoint_offset < claim.end_codepoint_offset
            and len(claim.claim_text_digest) == 64
            and len(segment.canonical_text_digest) == 64
        ):
            return False
        assessment = store.claim_support_assessments.get(claim_id)
        links = [
            link
            for (linked_claim_id, _), link in store.claim_evidence_links.items()
            if linked_claim_id == claim_id
        ]
        if assessment is None or not links:
            return False
        if any(
            link.confidence_score is not None
            or link.confidence_method is not None
            or link.calibration_reference is not None
            for link in links
        ):
            return False
        if assessment.status == "supported" and any(
            link.relation != "supports" for link in links
        ):
            return False
        if assessment.status == "unverified" and not all(
            link.relation == "insufficient" for link in links
        ):
            return False
        if assessment.status == "conflict" and not all(
            link.relation == "contradicts" for link in links
        ):
            return False
    return set(store.claim_support_assessments).issubset(store.claim_records)


def _valid_persistable_claim_graph(store: Any) -> bool:
    """Validate fresh canonical claims alongside claims loaded from metadata.

    A restarted process loads older canonical text and support scopes from
    protected artifacts only on demand. A new turn is still canonical in
    memory until publication, so persistence must validate each claim in the
    representation it currently has instead of forcing one mode on the whole
    store.
    """
    claim_ids = set(store.claim_records)
    if not set(store.claim_support_assessments).issubset(claim_ids):
        return False
    if any(claim_id not in claim_ids for claim_id, _ in store.claim_evidence_links):
        return False

    canonical_ids: set[str] = set()
    externalized_ids: set[str] = set()
    for claim_id, claim in store.claim_records.items():
        segment = store.response_segment_records.get(claim.segment_id)
        assessment = store.claim_support_assessments.get(claim_id)
        links = [
            link
            for (linked_claim_id, _), link in store.claim_evidence_links.items()
            if linked_claim_id == claim_id
        ]
        if segment is None or assessment is None or not links:
            return False
        decision_projection_externalized = (
            all(
                link.support_scope is None
                and link.evidence_stance_summary == ""
                and link.conflict_field is None
                for link in links
            )
            and assessment.supported_scope is None
            and assessment.unsupported_scope is None
            and assessment.conflict_summary is None
        )
        if decision_projection_externalized and segment.artifact_id:
            externalized_ids.add(claim_id)
        elif segment.segment_canonical_text:
            canonical_ids.add(claim_id)
        else:
            return False

    def view(selected: set[str]) -> SimpleNamespace:
        claims = {
            claim_id: store.claim_records[claim_id] for claim_id in selected
        }
        segment_ids = {claim.segment_id for claim in claims.values()}
        return SimpleNamespace(
            response_segment_records={
                segment_id: store.response_segment_records[segment_id]
                for segment_id in segment_ids
            },
            claim_records=claims,
            claim_evidence_links={
                key: value
                for key, value in store.claim_evidence_links.items()
                if key[0] in selected
            },
            claim_support_assessments={
                claim_id: value
                for claim_id, value in store.claim_support_assessments.items()
                if claim_id in selected
            },
        )

    canonical = view(canonical_ids)
    externalized = view(externalized_ids)
    return validate_claim_graph(
        canonical.response_segment_records,
        canonical.claim_records,
        canonical.claim_evidence_links,
        canonical.claim_support_assessments,
    ) and _valid_externalized_claim_graph(externalized)


def _segment_payload(value: ResponseSegmentRecord) -> dict[str, Any]:
    return serialize_typed_dataclass(
        value, family="response segment metadata", allowed_fields=_SEGMENT_FIELDS,
        overrides={"segment_canonical_text": ""},
    )


def _claim_payload(value: ClaimRecord) -> dict[str, Any]:
    return serialize_typed_dataclass(
        value, family="claim metadata", allowed_fields=_CLAIM_FIELDS,
    )


def _claim_evidence_payload(value: ClaimEvidenceLink) -> dict[str, Any]:
    return serialize_typed_dataclass(
        value, family="claim evidence metadata", allowed_fields=_CLAIM_EVIDENCE_FIELDS,
        overrides={
            "support_scope": None,
            "evidence_stance_summary": "",
            "conflict_field": None,
        },
    )


def _assessment_payload(value: ClaimSupportAssessment) -> dict[str, Any]:
    return serialize_typed_dataclass(
        value, family="claim assessment metadata", allowed_fields=_ASSESSMENT_FIELDS,
        overrides={
            "supported_scope": None,
            "unsupported_scope": None,
            "conflict_summary": None,
        },
    )


class AtlasTurnGovernedAnswerDraftRow(OrmBase):
    """Result-governance-owned immutable strict-turn draft."""

    __tablename__ = "atlas_turn_governed_answer_drafts"

    draft_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    retrieval_status: Mapped[str] = mapped_column(String(30), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "schema_version IN ('governed-answer-draft-v1','governed-answer-draft-v2')",
            name="ck_atlas_turn_governed_answer_schema",
        ),
        CheckConstraint(
            "retrieval_status IN ('not_used','evidence_found','no_evidence','access_denied','tool_failed','budget_exhausted')",
            name="ck_atlas_turn_governed_answer_retrieval",
        ),
        CheckConstraint(
            "verification_status IN ('verified','partially_verified','unverified','evidence_aligned','questionable')",
            name="ck_atlas_turn_governed_answer_verification",
        ),
        CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_governed_answer_digest",
        ),
        CheckConstraint(
            "octet_length(payload::text) <= 2097152",
            name="ck_atlas_turn_governed_answer_payload_bytes",
        ),
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_governed_answer_idempotency",
        ),
    )


class AtlasTurnGovernedAnswerDraftReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_governed_answer_draft_releases"

    release_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    draft_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_governed_answer_drafts.draft_ref", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_governed_answer_release_idempotency",
        ),
        UniqueConstraint(
            "execution_id", "draft_ref",
            name="uq_atlas_turn_governed_answer_release_binding",
        ),
    )


TURN_RESULT_GOVERNANCE_OWNER_TABLES = frozenset(
    {
        AtlasTurnGovernedAnswerDraftRow.__tablename__,
        AtlasTurnGovernedAnswerDraftReleaseRow.__tablename__,
    }
)
