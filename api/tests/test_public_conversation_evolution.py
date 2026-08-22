from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure import learner_provider
from atlas_production.infrastructure.persistence.base import OrmBase
from atlas_production.infrastructure.persistence import schema as _schema  # noqa: F401
from atlas_production.infrastructure.persistence.payload_policy import (
    JSONB_PAYLOAD_REGISTRY,
)
from atlas_production.modules.conversation_review.public import (
    MAX_CASES,
    REVIEW_PROMPT_REVISION,
    SEMANTIC_QUIET_PERIOD,
    ConversationLearningCaseProposalV1,
    ConversationReviewProposalV1,
    ConversationReviewSnapshotTurnV1,
    ConversationReviewSnapshotV1,
    conversation_review_ref,
    conversation_review_snapshot_digest,
)
from atlas_production.modules.learner.public import (
    LearnerSourceIdentityV1,
    learner_case_digest,
    learner_experience_ref,
    learner_run_ref,
)


PUBLIC_NOW = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)
PUBLIC_DIGEST_A = "a" * 64
PUBLIC_DIGEST_B = "b" * 64


def _snapshot_turn(position: int) -> ConversationReviewSnapshotTurnV1:
    suffix = str(position)
    return ConversationReviewSnapshotTurnV1(
        position=position,
        turn_id=f"public-synthetic-turn-{suffix}",
        execution_id=f"public-synthetic-execution-{suffix}",
        input_projection_ref=f"public-synthetic-input-{suffix}",
        user_text_digest=PUBLIC_DIGEST_A if position == 1 else PUBLIC_DIGEST_B,
        terminal_status="completed",
        terminal_scan_sequence=position,
        terminal_commit_intent_ref=f"public-synthetic-intent-{suffix}",
        terminal_committed_at=PUBLIC_NOW + timedelta(minutes=position),
        governed_answer_draft_ref=f"public-synthetic-answer-{suffix}",
        governed_answer_digest=PUBLIC_DIGEST_B if position == 1 else PUBLIC_DIGEST_A,
    )


def _snapshot() -> ConversationReviewSnapshotV1:
    turns = [_snapshot_turn(1), _snapshot_turn(2)]
    latest_activity = PUBLIC_NOW + timedelta(minutes=2)
    digest = conversation_review_snapshot_digest(
        conversation_id="public-synthetic-conversation",
        conversation_updated_at=latest_activity,
        expected_next_ordinal=3,
        latest_semantic_activity_at=latest_activity,
        turns=turns,
    )
    return ConversationReviewSnapshotV1(
        review_ref=conversation_review_ref(
            conversation_id="public-synthetic-conversation",
            snapshot_digest=digest,
            review_prompt_revision=REVIEW_PROMPT_REVISION,
        ),
        conversation_id="public-synthetic-conversation",
        conversation_updated_at=latest_activity,
        expected_next_ordinal=3,
        latest_semantic_activity_at=latest_activity,
        eligible_at=latest_activity + SEMANTIC_QUIET_PERIOD,
        snapshot_digest=digest,
        turns=turns,
    )


def _case(ordinal: int) -> ConversationLearningCaseProposalV1:
    return ConversationLearningCaseProposalV1(
        case_ordinal=ordinal,
        title=f"public-synthetic-case-{ordinal}",
        learning_evidence="public-synthetic-evidence",
        generalization_hypothesis="public-synthetic-hypothesis",
        investigation_question="public-synthetic-question",
        selection_rationale="public-synthetic-rationale",
        involved_turn_ids=["public-synthetic-turn-1", "public-synthetic-turn-2"],
        primary_assistant_turn_id="public-synthetic-turn-2",
    )


def test_public_review_snapshot_is_quiet_period_bound_and_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()

    assert first == second
    assert first.review_ref == second.review_ref
    assert first.eligible_at - first.latest_semantic_activity_at == timedelta(hours=2)
    assert first.model_dump_json() == second.model_dump_json()


def test_public_review_proposal_accepts_at_most_three_contiguous_cases() -> None:
    proposal = ConversationReviewProposalV1(cases=[_case(index) for index in range(1, 4)])
    assert len(proposal.cases) == MAX_CASES

    with pytest.raises(ValidationError):
        ConversationReviewProposalV1(cases=[_case(index) for index in range(1, 5)])


def test_public_review_tables_are_part_of_the_resettable_baseline_schema() -> None:
    expected = {
        "atlas_conversation_reviews",
        "atlas_conversation_review_snapshot_turns",
        "atlas_conversation_learning_cases",
        "atlas_conversation_learning_case_turns",
    }
    assert expected <= set(OrmBase.metadata.tables)


def test_public_learner_identity_binds_review_case_and_experience() -> None:
    case = _case(1)
    case_digest = learner_case_digest(
        review_ref=_snapshot().review_ref,
        review_digest=PUBLIC_DIGEST_A,
        case_ordinal=1,
        case=case,
    )
    run_ref = learner_run_ref(
        review_ref=_snapshot().review_ref,
        review_digest=PUBLIC_DIGEST_A,
        case_ordinal=1,
        case_digest=case_digest,
    )

    source = LearnerSourceIdentityV1(
        run_ref=run_ref,
        experience_ref=learner_experience_ref(run_ref=run_ref),
        review_ref=_snapshot().review_ref,
        review_digest=PUBLIC_DIGEST_A,
        snapshot_digest=_snapshot().snapshot_digest,
        case_ordinal=1,
        case_digest=case_digest,
        case_title=case.title,
        involved_turn_ids=case.involved_turn_ids,
        primary_assistant_turn_id=case.primary_assistant_turn_id,
    )

    assert source.run_ref == learner_run_ref(
        review_ref=source.review_ref,
        review_digest=source.review_digest,
        case_ordinal=source.case_ordinal,
        case_digest=source.case_digest,
    )
    assert source.experience_ref == learner_experience_ref(run_ref=source.run_ref)


def test_public_learner_table_and_payload_policy_are_registered() -> None:
    assert "atlas_learner_runs" in OrmBase.metadata.tables
    assert "atlas_learner_runs.experience_payload" in JSONB_PAYLOAD_REGISTRY


def test_public_learner_normalizes_labeled_unicode_secret_boundaries() -> None:
    value = "public-synthetic-sensitive-value"
    candidates = learner_provider._secret_values(
        f"token=「{value}」; credential:『{value}-alternate』"
    )

    assert value in candidates
    assert f"{value}-alternate" in candidates
