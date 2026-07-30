from dataclasses import dataclass
import hashlib
from typing import Literal


@dataclass(frozen=True)
class ResponseSegmentRecord:
    segment_id: str
    assistant_turn_id: str
    kind: str
    segment_canonical_text: str
    canonical_text_digest: str
    normalization_version: str
    created_at: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    provider_claim_id: str
    segment_id: str
    start_codepoint_offset: int
    end_codepoint_offset: int
    claim_text_digest: str
    created_at: str


@dataclass(frozen=True)
class ClaimEvidenceLink:
    link_id: str
    claim_id: str
    evidence_id: str
    relation: Literal["supports", "contradicts", "insufficient"]
    support_scope: str | None
    evidence_stance_summary: str
    conflict_field: str | None
    confidence_score: float | None
    confidence_method: str | None
    calibration_reference: str | None
    created_at: str


@dataclass(frozen=True)
class ClaimSupportAssessment:
    claim_id: str
    status: Literal["supported", "unverified", "conflict"]
    supported_scope: str | None
    unsupported_scope: str | None
    conflict_summary: str | None
    assessment_method: str
    assessment_version: str
    created_at: str


def validate_claim_graph(
    segments: dict[str, ResponseSegmentRecord],
    claims: dict[str, ClaimRecord],
    links: dict[tuple[str, str], ClaimEvidenceLink],
    assessments: dict[str, ClaimSupportAssessment],
) -> bool:
    for claim_id, claim in claims.items():
        segment = segments.get(claim.segment_id)
        if segment is None:
            return False
        if not (
            0 <= claim.start_codepoint_offset < claim.end_codepoint_offset
            <= len(segment.segment_canonical_text)
        ):
            return False
        claim_text = segment.segment_canonical_text[
            claim.start_codepoint_offset:claim.end_codepoint_offset
        ]
        if hashlib.sha256(claim_text.encode("utf-8")).hexdigest() != claim.claim_text_digest:
            return False
        assessment = assessments.get(claim_id)
        claim_links = [link for (linked_claim_id, _), link in links.items() if linked_claim_id == claim_id]
        if assessment is None:
            return False
        if any(
            link.confidence_score is not None
            or link.confidence_method is not None
            or link.calibration_reference is not None
            for link in claim_links
        ):
            return False
        if assessment.status == "supported":
            if (
                not claim_links
                or
                not assessment.supported_scope
                or assessment.unsupported_scope is not None
                or assessment.conflict_summary is not None
                or any(link.relation != "supports" for link in claim_links)
                or any(
                    not link.support_scope or link.support_scope not in claim_text
                    for link in claim_links
                )
            ):
                return False
        if assessment.status == "unverified":
            if not (
                assessment.supported_scope is None
                and assessment.unsupported_scope == claim_text
                and assessment.conflict_summary is None
                and all(link.relation == "insufficient" for link in claim_links)
                and all(link.support_scope is None for link in claim_links)
            ):
                return False
        if assessment.status == "conflict":
            if not (
                claim_links
                and
                assessment.conflict_summary == claim_text
                and assessment.supported_scope is None
                and assessment.unsupported_scope is None
                and all(link.relation == "contradicts" for link in claim_links)
                and all(link.support_scope is None for link in claim_links)
            ):
                return False
    return set(assessments).issubset(claims)


def validate_published_claim_graph(
    segments: dict[str, ResponseSegmentRecord],
    claims: dict[str, ClaimRecord],
    links: dict[tuple[str, str], ClaimEvidenceLink],
    assessments: dict[str, ClaimSupportAssessment],
) -> bool:
    """Validate a claim after canonical decision scopes were externalized.

    The caller must hydrate canonical segment text from its protected artifact.
    Publication-time validation remains stricter and is still owned by
    ``validate_claim_graph``.
    """
    if validate_claim_graph(segments, claims, links, assessments):
        return True
    if not set(assessments).issubset(claims):
        return False
    if any(claim_id not in claims for claim_id, _ in links):
        return False
    expected_relation = {
        "supported": "supports",
        "unverified": "insufficient",
        "conflict": "contradicts",
    }
    for claim_id, claim in claims.items():
        segment = segments.get(claim.segment_id)
        assessment = assessments.get(claim_id)
        claim_links = [
            link
            for (linked_claim_id, _), link in links.items()
            if linked_claim_id == claim_id
        ]
        if (
            segment is None
            or not segment.artifact_id
            or not segment.segment_canonical_text
            or assessment is None
        ):
            return False
        if not (
            0 <= claim.start_codepoint_offset < claim.end_codepoint_offset
            <= len(segment.segment_canonical_text)
        ):
            return False
        claim_text = segment.segment_canonical_text[
            claim.start_codepoint_offset:claim.end_codepoint_offset
        ]
        if hashlib.sha256(claim_text.encode("utf-8")).hexdigest() != claim.claim_text_digest:
            return False
        if (
            assessment.supported_scope is not None
            or assessment.unsupported_scope is not None
            or assessment.conflict_summary is not None
            or any(
                link.support_scope is not None
                or link.evidence_stance_summary != ""
                or link.conflict_field is not None
                or link.confidence_score is not None
                or link.confidence_method is not None
                or link.calibration_reference is not None
                or link.relation != expected_relation[assessment.status]
                for link in claim_links
            )
            or (assessment.status in {"supported", "conflict"} and not claim_links)
        ):
            return False
    return True
