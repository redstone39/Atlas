from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasTurnAuditDraftReleaseRow,
    AtlasTurnAuditDraftRow,
)
from atlas_production.infrastructure.postgres_owner.audit_v1 import (
    PostgresAuditV1Store,
    TurnAuditDraftStoreConflict,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV2,
    TurnAuditStepV1,
)
from atlas_production.modules.result_governance.public import EvidenceReviewStatusV2


@pytest.fixture(autouse=True)
def clean_audit_v2_rows(postgres_runtime: PostgresRuntime):
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(delete(AtlasTurnAuditDraftReleaseRow))
        session.execute(delete(AtlasTurnAuditDraftRow))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(delete(AtlasTurnAuditDraftReleaseRow))
        session.execute(delete(AtlasTurnAuditDraftRow))


def _command(
    evidence_review_status: EvidenceReviewStatusV2 = "evidence_aligned",
) -> MaterializeTurnAuditDraftV2:
    return MaterializeTurnAuditDraftV2(
        draft_ref="audit-v2-draft",
        execution_id="audit-v2-execution",
        claimed_evidence_handles=[
            "kh_evidence_first",
            "kh_evidence_first",
            "kh_evidence_second",
        ],
        evidence_pack_ref="evidence-pack-ref",
        evidence_pack_digest="1" * 64,
        governed_answer_draft_ref="governed-answer-v2-ref",
        governed_answer_digest="2" * 64,
        citation_binding_draft_ref="citation-binding-ref",
        citation_binding_digest="3" * 64,
        retrieval_status="evidence_found",
        evidence_review_status=evidence_review_status,
        terminal_status="terminal_completed",
        steps=[
            TurnAuditStepV1(
                ordinal=1,
                step_kind="governance",
                operation="soft_evidence_review",
                status="completed",
                safe_input_digest="4" * 64,
                result_ref="governed-answer-v2-ref",
                result_digest="2" * 64,
            ),
            TurnAuditStepV1(
                ordinal=2,
                step_kind="terminal",
                operation="prepare_terminal",
                status="completed",
                safe_input_digest="5" * 64,
            ),
        ],
        idempotency_key="audit-v2-key",
    )


@pytest.mark.parametrize(
    "evidence_review_status",
    ["evidence_aligned", "questionable"],
)
def test_v2_exact_replay_persists_review_status_and_safe_trace(
    postgres_runtime: PostgresRuntime,
    evidence_review_status: EvidenceReviewStatusV2,
) -> None:
    store = PostgresAuditV1Store(postgres_runtime.session_factory)
    command = _command(evidence_review_status)

    draft = store.materialize_v2(command)

    assert store.materialize_v2(command) == draft
    assert store.read_v2(draft.draft_ref) == draft
    assert draft.evidence_review_status == evidence_review_status
    assert draft.claimed_evidence_handles == command.claimed_evidence_handles
    raw_handles = store.read_raw_declared_evidence(command.execution_id)
    assert raw_handles == command.claimed_evidence_handles
    assert raw_handles is not None
    raw_handles.append("caller-local-change")
    assert store.read_raw_declared_evidence(command.execution_id) == (
        command.claimed_evidence_handles
    )
    with postgres_runtime.session_factory() as session:
        row = session.scalar(
            select(AtlasTurnAuditDraftRow).where(
                AtlasTurnAuditDraftRow.draft_ref == draft.draft_ref
            )
        )
        assert row is not None
        assert row.schema_version == "turn-audit-draft-v2"
        assert row.verification_status == evidence_review_status
        assert "evidence_content" not in row.payload
        assert "raw_evaluator_payload" not in row.payload


def test_v2_replay_conflicts_when_status_or_raw_declaration_changes(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresAuditV1Store(postgres_runtime.session_factory)
    command = _command()
    store.materialize_v2(command)

    with pytest.raises(TurnAuditDraftStoreConflict, match="replay payload changed"):
        store.materialize_v2(
            command.model_copy(update={"evidence_review_status": "questionable"})
        )
    with pytest.raises(TurnAuditDraftStoreConflict, match="replay payload changed"):
        store.materialize_v2(
            command.model_copy(
                update={
                    "claimed_evidence_handles": [
                        "kh_evidence_first",
                        "kh_evidence_second",
                        "kh_evidence_first",
                    ]
                }
            )
        )

    assert store.read_raw_declared_evidence(command.execution_id) == (
        command.claimed_evidence_handles
    )
    assert store.read_raw_declared_evidence("missing-execution") is None
