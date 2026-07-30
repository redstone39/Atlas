from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV2,
    TurnAuditStepV1,
)


def _command_payload() -> dict[str, object]:
    return {
        "draft_ref": "audit-v2-draft",
        "execution_id": "audit-v2-execution",
        "claimed_evidence_handles": [
            "kh_evidence_first",
            "kh_evidence_first",
            "kh_evidence_second",
        ],
        "evidence_pack_ref": "evidence-pack-ref",
        "evidence_pack_digest": "1" * 64,
        "governed_answer_draft_ref": "governed-answer-v2-ref",
        "governed_answer_digest": "2" * 64,
        "citation_binding_draft_ref": "citation-binding-ref",
        "citation_binding_digest": "3" * 64,
        "retrieval_status": "evidence_found",
        "evidence_review_status": "evidence_aligned",
        "terminal_status": "terminal_completed",
        "steps": [
            TurnAuditStepV1(
                ordinal=1,
                step_kind="governance",
                operation="soft_evidence_review",
                status="completed",
                safe_input_digest="4" * 64,
                result_ref="governed-answer-v2-ref",
                result_digest="2" * 64,
            )
        ],
        "idempotency_key": "audit-v2-key",
    }


def test_v2_contract_preserves_raw_claim_order_and_duplicates() -> None:
    command = MaterializeTurnAuditDraftV2.model_validate(_command_payload())

    assert command.claimed_evidence_handles == [
        "kh_evidence_first",
        "kh_evidence_first",
        "kh_evidence_second",
    ]
    assert command.evidence_review_status == "evidence_aligned"


@pytest.mark.parametrize(
    "unsupported_status",
    ["verified", "partially_verified", "unverified"],
)
def test_v2_contract_rejects_v1_verification_statuses(
    unsupported_status: str,
) -> None:
    payload = _command_payload()
    payload["evidence_review_status"] = unsupported_status

    with pytest.raises(ValidationError):
        MaterializeTurnAuditDraftV2.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_field",
    ["evidence_content", "raw_evaluator_payload", "raw_prompt", "tool_payload"],
)
def test_v2_contract_rejects_evidence_content_and_raw_runtime_payloads(
    unsafe_field: str,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MaterializeTurnAuditDraftV2.model_validate(
            {**_command_payload(), unsafe_field: {"unsafe": "content"}}
        )
