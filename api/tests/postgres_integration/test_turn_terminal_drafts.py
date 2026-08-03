from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import delete, func, select

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasTurnAuditDraftReleaseRow,
    AtlasTurnAuditDraftRow,
)
from atlas_production.infrastructure.persistence.citation_preview import (
    AtlasTurnCitationBindingDraftReleaseRow,
    AtlasTurnCitationBindingDraftRow,
)
from atlas_production.infrastructure.persistence.result_governance import (
    AtlasTurnGovernedAnswerDraftReleaseRow,
    AtlasTurnGovernedAnswerDraftRow,
)
from atlas_production.infrastructure.postgres_owner.audit_v1 import (
    PostgresAuditV1Store,
    TurnAuditDraftStoreConflict,
)
from atlas_production.infrastructure.postgres_owner.citation_v1 import (
    CitationBindingStoreConflict,
    PostgresCitationV1Store,
)
from atlas_production.infrastructure.postgres_owner.result_governance_v1 import (
    PostgresResultGovernanceV1Store,
    ResultGovernanceStoreConflict,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV1,
    ReleaseTurnAuditDraftV1,
    TurnAuditStepV1,
)
from atlas_production.modules.citation_preview.public import (
    MaterializeCitationBindingDraftV1,
    ReleaseCitationBindingDraftV1,
)
from atlas_production.modules.result_governance.public import (
    ExecutionEvidenceLineageV1,
    FinalizedAnswerV1,
    MaterializeGovernedAnswerDraftV1,
    MaterializeGovernedAnswerDraftV2,
    PostHocAnswerAssessmentV2,
    PostHocClaimAssessmentV1,
    ReleaseGovernedAnswerDraftV1,
)
from atlas_production.modules.retrieval.public import DeclaredEvidenceMappingV1


PREFIX = "atr030-terminal-draft"


@pytest.fixture(autouse=True)
def clean_terminal_draft_rows(postgres_runtime: PostgresRuntime):
    tables = (
        AtlasTurnAuditDraftReleaseRow,
        AtlasTurnAuditDraftRow,
        AtlasTurnCitationBindingDraftReleaseRow,
        AtlasTurnCitationBindingDraftRow,
        AtlasTurnGovernedAnswerDraftReleaseRow,
        AtlasTurnGovernedAnswerDraftRow,
    )
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        for table in tables:
            session.execute(delete(table))


def _governance_command() -> MaterializeGovernedAnswerDraftV1:
    return MaterializeGovernedAnswerDraftV1(
        draft_ref=f"answer-{PREFIX}",
        execution_id=f"execution-{PREFIX}",
        finalized_answer=FinalizedAnswerV1(
            segments=[
                {"segment_id": "segment-verified", "text": "Verified text."},
                {"segment_id": "segment-unverified", "text": "Unverified text."},
            ],
        ),
        retrieval_status="evidence_found",
        evidence_lineage=[
            ExecutionEvidenceLineageV1(
                evidence_handle="evidence-handle-verified",
                evidence_ref="evidence-ref-verified",
                evidence_digest="a" * 64,
                result_ref="retrieval-result-verified",
                invocation_ordinal=1,
            )
        ],
        assessment_succeeded=True,
        assessments=[
            PostHocClaimAssessmentV1(
                segment_id="segment-verified",
                start=0,
                end=8,
                decision="supported",
                supporting_evidence_handles=["evidence-handle-verified"],
            ),
            PostHocClaimAssessmentV1(
                segment_id="segment-unverified",
                start=0,
                end=10,
                decision="unsupported",
                supporting_evidence_handles=[],
            ),
        ],
        idempotency_key="governance-key",
    )


def test_v2_persists_ordered_answer_results_and_digests(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresResultGovernanceV1Store(postgres_runtime.session_factory)
    finalized_answer = FinalizedAnswerV1(
        segments=[
            {"segment_id": "answer-1", "text": "First answer."},
            {"segment_id": "answer-2", "text": "Second answer."},
        ],
    )
    command = MaterializeGovernedAnswerDraftV2(
        draft_ref=f"answer-v2-{PREFIX}",
        execution_id=f"execution-v2-{PREFIX}",
        finalized_answer=finalized_answer,
        retrieval_status="evidence_found",
        declared_evidence_mappings=[
            DeclaredEvidenceMappingV1(
                position=1,
                handle="kh_evidence_v2",
                resolution_status="resolved",
                subset_position=1,
                reason_code="resolved",
            )
        ],
        evidence_lineage=[
            ExecutionEvidenceLineageV1(
                evidence_handle="kh_evidence_v2",
                evidence_ref="evidence-ref-v2",
                evidence_digest="a" * 64,
                result_ref="retrieval-result-v2",
                invocation_ordinal=1,
            )
        ],
        assessment_state="completed",
        assessment_reason_code="completed",
        assessment_version="provisional-declared-evidence-v1",
        assessment_consistency="insufficient",
        assessment_answer_digest=hashlib.sha256(
            json.dumps(
                finalized_answer.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        assessment_declared_subset_digest="d" * 64,
        assessment_visual_image_digests=[],
        assessment_input_digest="b" * 64,
        assessment_output_digest="c" * 64,
        assessment_results=[
            PostHocAnswerAssessmentV2(id="answer-1", status="success"),
            PostHocAnswerAssessmentV2(id="answer-2", status="failure"),
        ],
        idempotency_key="governance-v2-key",
    )

    answer = store.materialize_v2(command)
    assert answer.evidence_review_status == "questionable"
    assert answer.evidence_review_reason_codes == [
        "declared_evidence_not_aligned",
        "answer_item_failed",
    ]
    assert answer.assessment_results == command.assessment_results
    assert store.materialize_v2(command) == answer
    assert store.read_v2(answer.draft_ref) == answer

    with postgres_runtime.session_factory() as session:
        row = session.get(AtlasTurnGovernedAnswerDraftRow, answer.draft_ref)
        assert row is not None
        assert row.payload["assessment_results"] == [
            {"id": "answer-1", "status": "success"},
            {"id": "answer-2", "status": "failure"},
        ]
        assert row.payload["assessment_input_digest"] == "b" * 64
        assert row.payload["assessment_output_digest"] == "c" * 64
        assert row.payload["assessment_consistency"] == "insufficient"


def test_terminal_drafts_exact_replay_conflict_release_and_safe_lineage(
    postgres_runtime: PostgresRuntime,
) -> None:
    governance_store = PostgresResultGovernanceV1Store(postgres_runtime.session_factory)
    citation_store = PostgresCitationV1Store(postgres_runtime.session_factory)
    audit_store = PostgresAuditV1Store(postgres_runtime.session_factory)

    governance_command = _governance_command()
    answer = governance_store.materialize(governance_command)
    assert governance_store.materialize(governance_command) == answer
    assert governance_store.read(answer.draft_ref) == answer
    assert answer.verification_status == "partially_verified"
    assert answer.segments[1].claims[0].evidence_refs == []
    changed_answer = governance_command.finalized_answer.model_copy(
        update={"segments": [governance_command.finalized_answer.segments[0]]}
    )
    with pytest.raises(ResultGovernanceStoreConflict):
        governance_store.materialize(
            governance_command.model_copy(update={"finalized_answer": changed_answer})
        )
    changed_assessment = governance_command.assessments[0].model_copy(
        update={"decision": "unsupported", "supporting_evidence_handles": []}
    )
    with pytest.raises(ResultGovernanceStoreConflict):
        governance_store.materialize(
            governance_command.model_copy(
                update={
                    "assessments": [
                        changed_assessment,
                        governance_command.assessments[1],
                    ]
                }
            )
        )
    changed_lineage = governance_command.evidence_lineage[0].model_copy(
        update={"evidence_digest": "9" * 64}
    )
    with pytest.raises(ResultGovernanceStoreConflict):
        governance_store.materialize(
            governance_command.model_copy(update={"evidence_lineage": [changed_lineage]})
        )

    citation_command = MaterializeCitationBindingDraftV1(
        draft_ref=f"citation-{PREFIX}",
        execution_id=answer.execution_id,
        governed_answer=answer,
        idempotency_key="citation-key",
    )
    citation = citation_store.materialize(citation_command)
    assert citation_store.materialize(citation_command) == citation
    assert citation_store.read(citation.draft_ref) == citation
    assert [(binding.claim_id, binding.evidence_ref) for binding in citation.bindings] == [
        (answer.segments[0].claims[0].claim_id, "evidence-ref-verified")
    ]
    with pytest.raises(CitationBindingStoreConflict):
        citation_store.materialize(citation_command.model_copy(update={"draft_ref": "changed-ref"}))

    audit_command = MaterializeTurnAuditDraftV1(
        draft_ref=f"audit-{PREFIX}",
        execution_id=answer.execution_id,
        claimed_evidence_handles=[
            "kh_evidence_declared",
            "kh_evidence_declared",
            "unknown-handle",
        ],
        evidence_pack_ref="evidence-pack-ref",
        evidence_pack_digest="b" * 64,
        governed_answer_draft_ref=answer.draft_ref,
        governed_answer_digest=answer.digest,
        citation_binding_draft_ref=citation.draft_ref,
        citation_binding_digest=citation.digest,
        retrieval_status=answer.retrieval_status,
        verification_status=answer.verification_status,
        terminal_status="terminal_completed",
        steps=[
            TurnAuditStepV1(
                ordinal=1,
                step_kind="tool",
                operation="search_knowledge",
                status="completed",
                safe_input_digest="c" * 64,
                result_ref="retrieval-result-verified",
                result_digest="d" * 64,
                input_tokens=11,
                output_tokens=22,
                evidence_count=1,
            ),
            TurnAuditStepV1(
                ordinal=2,
                step_kind="terminal",
                operation="prepare_terminal",
                status="completed",
                safe_input_digest="e" * 64,
            ),
        ],
        idempotency_key="audit-key",
    )
    audit = audit_store.materialize(audit_command)
    assert audit_store.materialize(audit_command) == audit
    assert audit_store.read(audit.draft_ref) == audit
    assert audit.claimed_evidence_handles == [
        "kh_evidence_declared",
        "kh_evidence_declared",
        "unknown-handle",
    ]
    assert audit.steps[0].input_tokens == 11
    with pytest.raises(TurnAuditDraftStoreConflict):
        audit_store.materialize(
            audit_command.model_copy(update={"verification_status": "unverified"})
        )

    answer_release = ReleaseGovernedAnswerDraftV1(
        release_ref="answer-release", execution_id=answer.execution_id,
        draft_ref=answer.draft_ref, idempotency_key="answer-release-key",
    )
    citation_release = ReleaseCitationBindingDraftV1(
        release_ref="citation-release", execution_id=answer.execution_id,
        draft_ref=citation.draft_ref, idempotency_key="citation-release-key",
    )
    audit_release = ReleaseTurnAuditDraftV1(
        release_ref="audit-release", execution_id=answer.execution_id,
        draft_ref=audit.draft_ref, idempotency_key="audit-release-key",
    )
    assert governance_store.release(answer_release) == governance_store.release(answer_release)
    assert citation_store.release(citation_release) == citation_store.release(citation_release)
    assert audit_store.release(audit_release) == audit_store.release(audit_release)
    with pytest.raises(ResultGovernanceStoreConflict):
        governance_store.release(answer_release.model_copy(update={"release_ref": "changed-answer-release"}))
    with pytest.raises(CitationBindingStoreConflict):
        citation_store.release(citation_release.model_copy(update={"release_ref": "changed-citation-release"}))
    with pytest.raises(TurnAuditDraftStoreConflict):
        audit_store.release(audit_release.model_copy(update={"release_ref": "changed-audit-release"}))


def test_conflicts_and_invalid_ordinals_leave_owner_tables_unchanged(
    postgres_runtime: PostgresRuntime,
) -> None:
    governance_store = PostgresResultGovernanceV1Store(postgres_runtime.session_factory)
    command = _governance_command()
    governance_store.materialize(command)
    with pytest.raises(ResultGovernanceStoreConflict):
        governance_store.materialize(command.model_copy(update={"draft_ref": "other-draft"}))

    audit_store = PostgresAuditV1Store(postgres_runtime.session_factory)
    invalid_audit = MaterializeTurnAuditDraftV1(
        draft_ref="invalid-audit-draft",
        execution_id="invalid-audit-execution",
        claimed_evidence_handles=[],
        evidence_pack_ref="evidence-pack-ref",
        evidence_pack_digest="1" * 64,
        governed_answer_draft_ref="answer-ref",
        governed_answer_digest="2" * 64,
        citation_binding_draft_ref="citation-ref",
        citation_binding_digest="3" * 64,
        retrieval_status="not_used",
        verification_status="unverified",
        terminal_status="terminal_completed",
        steps=[
            TurnAuditStepV1(
                ordinal=2, step_kind="terminal", operation="prepare_terminal",
                status="completed", safe_input_digest="4" * 64,
            )
        ],
        idempotency_key="invalid-audit-key",
    )
    with pytest.raises(ValueError, match="contiguous"):
        audit_store.materialize(invalid_audit)

    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasTurnGovernedAnswerDraftRow)) == 1
        assert session.scalar(select(func.count()).select_from(AtlasTurnAuditDraftRow)) == 0
