from __future__ import annotations
import hashlib
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.conversation_reviewer import _validate_domain
from atlas_production.infrastructure.conversation_review_reconciler import (
    ConversationReviewReconciler,
    _ClaimHeartbeat as ConversationReviewHeartbeat,
)
from atlas_production.infrastructure import learner_provider
from atlas_production.infrastructure.learner_reconciler import (
    _ClaimHeartbeat as LearnerHeartbeat,
)
from atlas_production.infrastructure.skill_candidate_pipeline_reconciler import (
    _ConsolidationHeartbeat as ConsolidationHeartbeat,
    _SkillDesignHeartbeat as SkillDesignHeartbeat,
)
from atlas_production.infrastructure.persistence.base import OrmBase
from atlas_production.infrastructure.persistence import schema as _schema  # noqa: F401
from atlas_production.infrastructure.persistence.payload_policy import (
    JSONB_PAYLOAD_REGISTRY,
)
from atlas_production.modules.consolidator.public import (
    ConsolidatorExperienceBindingV1,
    consolidation_run_ref,
)
from atlas_production.modules.conversation_review.public import (
    MAX_CASES,
    ConversationReviewProposalV1,
)
from atlas_production.modules.learner.public import (
    LearnerSourceIdentityV1,
    learner_case_digest,
    learner_experience_ref,
    learner_run_ref,
)
from atlas_production.modules.prompt_skills.public import PromptSkillApprovedPublishV1
from atlas_production.modules.skill_designer.public import (
    ApproveSkillCandidateV1,
    SkillCandidateError,
    add_draft_key,
)
from atlas_production.routes.prompt_skills import _candidate_command
from tests.public_synthetic_data import (
    PUBLIC_SECRET_VALUE,
    PUBLIC_DIGEST_A,
    synthetic_catalog_refs,
    synthetic_learning_case,
    synthetic_review_snapshot,
    synthetic_review_transcript,
)




def test_public_review_snapshot_is_quiet_period_bound_and_deterministic() -> None:
    first = synthetic_review_snapshot()
    second = synthetic_review_snapshot()

    assert first == second
    assert first.review_ref == second.review_ref
    assert first.eligible_at - first.latest_semantic_activity_at == timedelta(hours=2)
    assert first.model_dump_json() == second.model_dump_json()


def test_public_review_proposal_accepts_at_most_three_contiguous_cases() -> None:
    proposal = ConversationReviewProposalV1(
        cases=[synthetic_learning_case(index) for index in range(1, 4)]
    )
    assert len(proposal.cases) == MAX_CASES

    with pytest.raises(ValidationError):
        ConversationReviewProposalV1(
            cases=[synthetic_learning_case(index) for index in range(1, 5)]
        )


def test_public_review_rejects_verbatim_transcript_echo() -> None:
    transcript = synthetic_review_transcript()
    case = synthetic_learning_case(1).model_copy(
        update={"learning_evidence": transcript.turns[0].original_user_text}
    )
    proposal = ConversationReviewProposalV1(cases=[case])


    with pytest.raises(ValueError, match="repeats protected transcript"):
        _validate_domain(
            proposal,
            transcript,
            allowed_turn_ids=frozenset(turn.turn_id for turn in transcript.turns),
        )


@pytest.mark.parametrize("protected_text", ["user", "assistant"])
def test_public_review_rejects_long_protected_excerpt(protected_text) -> None:
    transcript = synthetic_review_transcript()
    source = (
        transcript.turns[0].original_user_text
        if protected_text == "user"
        else transcript.turns[0].final_governed_assistant_segments[0].text
    )
    case = synthetic_learning_case(1).model_copy(
        update={"learning_evidence": f"Derived note: {source[5:42]} only."}
    )
    proposal = ConversationReviewProposalV1(cases=[case])

    with pytest.raises(ValueError, match="repeats protected transcript"):
        _validate_domain(
            proposal,
            transcript,
            allowed_turn_ids=frozenset(turn.turn_id for turn in transcript.turns),
        )



def test_public_review_rejects_normalized_secret_echo() -> None:
    transcript = synthetic_review_transcript()
    case = synthetic_learning_case(1).model_copy(
        update={"learning_evidence": PUBLIC_SECRET_VALUE}
    )
    proposal = ConversationReviewProposalV1(cases=[case])

    with pytest.raises(ValueError, match="repeats protected transcript"):
        _validate_domain(
            proposal,
            transcript,
            allowed_turn_ids=frozenset(turn.turn_id for turn in transcript.turns),
        )



def test_public_reconciler_stop_waits_for_inflight_work(monkeypatch) -> None:
    reconciler = ConversationReviewReconciler(
        conversations=object(),
        source=object(),
        reviews=object(),
        reviewer=object(),
        publication=object(),
        interval_seconds=0.01,
        lease_seconds=2,
        heartbeat_seconds=0.5,
    )
    entered = Event()
    release = Event()
    calls = 0

    def run_once() -> int:
        nonlocal calls
        calls += 1
        if calls > 1:
            entered.set()
            release.wait()
        return 0

    monkeypatch.setattr(reconciler, "run_once", run_once)
    reconciler.start()
    assert entered.wait(1)
    stopper = Thread(target=reconciler.stop)
    stopper.start()
    stopper.join(0.05)
    assert stopper.is_alive()
    release.set()
    stopper.join(1)
    assert not stopper.is_alive()
    assert not reconciler.running


@pytest.mark.parametrize(
    "heartbeat_type",
    [
        ConversationReviewHeartbeat,
        LearnerHeartbeat,
        ConsolidationHeartbeat,
        SkillDesignHeartbeat,
    ],
)
def test_public_heartbeat_stop_waits_for_inflight_renewal(heartbeat_type) -> None:
    entered = Event()
    release = Event()

    class BlockingOwner:
        def renew_claim(self, claim, observed_at, *, lease_seconds):
            entered.set()
            release.wait()
            return claim

    heartbeat = heartbeat_type(
        owner=BlockingOwner(),
        clock=lambda: datetime.now(timezone.utc),
        lease_seconds=2,
        heartbeat_seconds=0.01,
    )
    heartbeat.start(object())
    assert entered.wait(1)
    stopper = Thread(target=heartbeat.stop)
    stopper.start()
    stopper.join(0.05)
    assert stopper.is_alive()
    release.set()
    stopper.join(1)
    assert not stopper.is_alive()

def test_public_review_tables_are_part_of_the_resettable_baseline_schema() -> None:
    expected = {
        "atlas_conversation_reviews",
        "atlas_conversation_review_snapshot_turns",
        "atlas_conversation_learning_cases",
        "atlas_conversation_learning_case_turns",
    }
    assert expected <= set(OrmBase.metadata.tables)


def test_public_learner_identity_binds_review_case_and_experience() -> None:
    case = synthetic_learning_case(1)
    snapshot = synthetic_review_snapshot()
    case_digest = learner_case_digest(
        review_ref=snapshot.review_ref,
        review_digest=PUBLIC_DIGEST_A,
        case_ordinal=1,
        case=case,
    )
    run_ref = learner_run_ref(
        review_ref=snapshot.review_ref,
        review_digest=PUBLIC_DIGEST_A,
        case_ordinal=1,
        case_digest=case_digest,
    )

    source = LearnerSourceIdentityV1(
        run_ref=run_ref,
        experience_ref=learner_experience_ref(run_ref=run_ref),
        review_ref=snapshot.review_ref,
        review_digest=PUBLIC_DIGEST_A,
        snapshot_digest=snapshot.snapshot_digest,
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




def test_public_consolidation_identity_requires_exactly_ten_experiences() -> None:
    bindings = [
        ConsolidatorExperienceBindingV1(
            experience_ref=f"public-synthetic-experience-{index}",
            experience_digest=f"{index:x}" * 64,
            scan_sequence=index,
        )
        for index in range(1, 11)
    ]

    assert consolidation_run_ref(source_bindings=bindings) == consolidation_run_ref(
        source_bindings=list(bindings)
    )
    with pytest.raises(ValueError, match="exactly ten"):
        consolidation_run_ref(source_bindings=bindings[:9])


def test_public_candidate_semantic_identity_normalizes_topic_and_goal() -> None:
    first = add_draft_key(
        category="planner",
        topic=" Public Synthetic Planning ",
        goal="Compare   public synthetic options",
    )
    replay = add_draft_key(
        category="planner",
        topic="public synthetic planning",
        goal="compare public synthetic options",
    )

    assert first == replay
    assert first != add_draft_key(
        category="answer",
        topic="public synthetic planning",
        goal="compare public synthetic options",
    )


def test_public_approved_publication_pins_all_three_catalogs() -> None:
    source = (
        "---\n"
        "name: public-synthetic-skill\n"
        "description: Public synthetic skill.\n"
        "---\n"
        "Apply the public synthetic behavior."
    )
    request = PromptSkillApprovedPublishV1(
        disposition="add",
        category="planner",
        name="public-synthetic-skill",
        source=source,
        source_digest=hashlib.sha256(source.encode()).hexdigest(),
        expected_catalogs=synthetic_catalog_refs(),
        idempotency_key="public-synthetic-publication",
    )
    assert [ref.category for ref in request.expected_catalogs] == [
        "understanding",
        "planner",
        "answer",
    ]
    with pytest.raises(ValidationError):
        PromptSkillApprovedPublishV1(
            **{
                **request.model_dump(),
                "expected_catalogs": list(reversed(synthetic_catalog_refs())),
            }
        )


def test_public_candidate_pipeline_tables_and_payloads_are_registered() -> None:
    assert {
        "atlas_consolidator_checkpoint",
        "atlas_consolidation_runs",
        "atlas_skill_designer_checkpoint",
        "atlas_skill_design_runs",
        "atlas_skill_candidates",
        "atlas_skill_candidate_idempotency",
    } <= set(OrmBase.metadata.tables)
    assert {
        "atlas_consolidation_runs.result_payload",
        "atlas_skill_candidates.draft_payload",
        "atlas_skill_candidates.approved_skill_ref",
        "atlas_skill_candidate_idempotency.response_payload",
    } <= set(JSONB_PAYLOAD_REGISTRY)


def test_public_candidate_mutation_requires_matching_header_and_body_identity() -> None:
    command = ApproveSkillCandidateV1(
        expected_draft_revision=3,
        idempotency_key="public-synthetic-candidate-approval",
    )

    assert (
        _candidate_command(
            command,
            idempotency_header="public-synthetic-candidate-approval",
            if_match="3",
        )
        is command
    )
    with pytest.raises(SkillCandidateError) as mismatch:
        _candidate_command(
            command,
            idempotency_header="public-synthetic-different-request",
            if_match="3",
        )
    assert mismatch.value.status_code == 422
    assert (
        mismatch.value.message_code
        == "prompt_skills.candidate_headers_and_body_must_match"
    )
