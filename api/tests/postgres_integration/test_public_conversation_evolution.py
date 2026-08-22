from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from atlas_production.infrastructure.persistence.consolidator import (
    AtlasConsolidationRunRow,
    AtlasConsolidatorCheckpointRow,
)
from atlas_production.infrastructure.persistence.conversation_review import (
    AtlasConversationLearningCaseRow,
    AtlasConversationLearningCaseTurnRow,
    AtlasConversationReviewRow,
    AtlasConversationReviewSnapshotTurnRow,
)
from atlas_production.infrastructure.persistence.learner import AtlasLearnerRunRow
from atlas_production.infrastructure.persistence.prompt_skills import (
    AtlasPromptSkillCatalogRevisionRow,
    AtlasPromptSkillControlRow,
    AtlasPromptSkillIdempotencyRow,
    AtlasPromptSkillRevisionRow,
)
from atlas_production.infrastructure.persistence.skill_designer import (
    AtlasSkillCandidateIdempotencyRow,
    AtlasSkillCandidateRow,
    AtlasSkillDesignerCheckpointRow,
    AtlasSkillDesignRunRow,
)
from atlas_production.infrastructure.postgres_owner.consolidator import (
    PostgresConsolidatorOwner,
)
from atlas_production.infrastructure.postgres_owner.conversation_review import (
    ConversationReviewClaimLost,
    PostgresConversationReviewOwner,
)
from atlas_production.infrastructure.postgres_owner.learner import PostgresLearnerOwner
from atlas_production.infrastructure.postgres_owner.prompt_skills import (
    PostgresPromptSkillOwner,
)
from atlas_production.infrastructure.postgres_owner.skill_designer import (
    PostgresSkillDesignerOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.learner.public import RegisterLearnerCaseV1
from atlas_production.modules.skill_designer.public import (
    ApproveSkillCandidateV1,
    SkillCandidateError,
    SkillDesignRunV1,
)
from atlas_production.modules.skill_designer.service import SkillCandidateAdminService
from tests.public_synthetic_data import (
    synthetic_candidate_draft,
    synthetic_consolidated_experience,
    synthetic_learner_payload,
    synthetic_review_proposal,
    synthetic_review_snapshot,
)


@pytest.fixture(autouse=True)
def clean_public_evolution_rows(postgres_runtime: PostgresRuntime):
    def clean() -> None:
        with postgres_runtime.session_factory() as session, session.begin():
            session.execute(delete(AtlasSkillCandidateIdempotencyRow))
            session.execute(delete(AtlasSkillCandidateRow))
            session.execute(delete(AtlasSkillDesignRunRow))
            session.execute(delete(AtlasSkillDesignerCheckpointRow))
            session.execute(delete(AtlasConsolidationRunRow))
            session.execute(delete(AtlasConsolidatorCheckpointRow))
            session.execute(delete(AtlasLearnerRunRow))
            session.execute(delete(AtlasConversationLearningCaseTurnRow))
            session.execute(delete(AtlasConversationLearningCaseRow))
            session.execute(delete(AtlasConversationReviewSnapshotTurnRow))
            session.execute(delete(AtlasConversationReviewRow))
            session.execute(delete(AtlasPromptSkillIdempotencyRow))
            session.execute(delete(AtlasPromptSkillControlRow))
            session.execute(delete(AtlasPromptSkillRevisionRow))
            session.execute(
                delete(AtlasPromptSkillCatalogRevisionRow).where(
                    AtlasPromptSkillCatalogRevisionRow.catalog_revision > 1
                )
            )

    clean()
    yield
    clean()


class _ApplyingPublisher:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.observed = False

    def publish_enabled_in_session(self, session, *, actor_id, request):
        statuses = list(
            session.scalars(select(AtlasSkillCandidateRow.status))
        )
        if statuses != ["applying"]:
            raise AssertionError("candidate was not fenced in applying state")
        self.observed = True
        return self.delegate.publish_enabled_in_session(
            session,
            actor_id=actor_id,
            request=request,
        )


def test_public_completed_conversations_reach_one_atomic_candidate(
    postgres_runtime: PostgresRuntime,
) -> None:
    reviews = PostgresConversationReviewOwner(postgres_runtime.session_factory)
    learners = PostgresLearnerOwner(postgres_runtime.session_factory)
    consolidations = PostgresConsolidatorOwner(postgres_runtime.session_factory)
    prompt_skills = PostgresPromptSkillOwner(postgres_runtime.session_factory)
    applying_publisher = _ApplyingPublisher(prompt_skills)
    designs = PostgresSkillDesignerOwner(
        postgres_runtime.session_factory,
        publisher=applying_publisher,
    )
    admin = SkillCandidateAdminService(designs)
    experiences = []

    for ordinal in range(1, 11):
        snapshot = synthetic_review_snapshot(conversation_suffix=str(ordinal))
        assert reviews.register_snapshot(snapshot).status == "pending"
        claimed_at = max(
            snapshot.eligible_at + timedelta(seconds=1),
            datetime.now(timezone.utc) + timedelta(seconds=ordinal),
        )
        claim = reviews.claim_next(
            f"public-synthetic-review-worker-{ordinal}",
            claimed_at,
            lease_seconds=1 if ordinal == 1 else 300,
        )
        assert claim is not None
        if ordinal == 1:
            replacement = reviews.claim_next(
                "public-synthetic-review-worker-replacement",
                claimed_at + timedelta(seconds=2),
            )
            assert replacement is not None
            with pytest.raises(ConversationReviewClaimLost):
                reviews.complete(
                    claim,
                    synthetic_review_proposal(),
                    ["public-synthetic-stale-review-invocation"],
                    claimed_at + timedelta(seconds=2),
                )
            claim = replacement
            claimed_at += timedelta(seconds=2)
        claim = reviews.pin_route(
            claim,
            "public-synthetic-review-route",
            1,
            1,
            claimed_at,
        )
        review = reviews.complete(
            claim,
            synthetic_review_proposal(),
            [f"public-synthetic-review-invocation-{ordinal}"],
            claimed_at,
        )
        assert review.status == "completed"
        assert review.review_digest is not None

        run = learners.register_case(
            RegisterLearnerCaseV1(
                review_ref=review.snapshot.review_ref,
                review_digest=review.review_digest,
                snapshot_digest=review.snapshot.snapshot_digest,
                case=review.cases[0],
            )
        )
        learner_claim = learners.claim_next(
            f"public-synthetic-learner-worker-{ordinal}",
            claimed_at,
        )
        assert learner_claim is not None
        if ordinal == 1:
            failed = learners.fail(
                learner_claim,
                "public_synthetic_retryable_provider_failure",
                True,
                claimed_at,
            )
            assert failed.status == "retryable_failed"
            assert learners.read_experience(run.source.experience_ref) is None
            claimed_at += timedelta(seconds=31)
            learner_claim = learners.claim_next(
                "public-synthetic-learner-worker-retry",
                claimed_at,
            )
            assert learner_claim is not None
        learner_claim = learners.pin_route(
            learner_claim,
            "public-synthetic-learner-route",
            1,
            1,
            claimed_at,
        )
        experience = learners.complete(
            learner_claim,
            synthetic_learner_payload(
                run.source,
                invocation_suffix=str(ordinal),
            ),
            claimed_at,
        )
        experiences.append(experience)

    assert len(learners.list_experiences_after(None, 100)) == 10
    consolidation_run = consolidations.reserve_next(learners, experiences[-1].completed_at)
    assert consolidation_run is not None
    assert len(consolidation_run.source_bindings) == 10
    assert consolidations.reserve_next(learners, experiences[-1].completed_at) is None

    consolidation_claim = consolidations.claim_next(
        "public-synthetic-consolidator-worker",
        experiences[-1].completed_at,
    )
    assert consolidation_claim is not None
    failed_consolidation = consolidations.fail(
        consolidation_claim,
        "public_synthetic_retryable_consolidator_failure",
        True,
        experiences[-1].completed_at,
    )
    assert failed_consolidation.status == "retryable_failed"
    retry_at = experiences[-1].completed_at + timedelta(seconds=31)
    consolidation_claim = consolidations.claim_next(
        "public-synthetic-consolidator-worker-retry",
        retry_at,
    )
    assert consolidation_claim is not None
    consolidation_claim = consolidations.pin_route(
        consolidation_claim,
        "public-synthetic-consolidator-route",
        1,
        1,
        retry_at,
    )
    consolidation = consolidations.complete(
        consolidation_claim,
        [
            synthetic_consolidated_experience(
                [binding.experience_ref for binding in consolidation_claim.source_bindings]
            )
        ],
        ["public-synthetic-consolidator-invocation"],
        retry_at,
    )
    assert consolidation.payload.source_bindings == consolidation_claim.source_bindings

    design_run = designs.register_consolidation(consolidation)
    design_claim = designs.claim_next("public-synthetic-designer-worker", retry_at)
    assert design_claim is not None
    design_claim = designs.pin_route(
        design_claim,
        "public-synthetic-designer-route",
        1,
        1,
        retry_at,
    )
    catalogs = [
        prompt_skills.current_catalog(category)
        for category in ("understanding", "planner", "answer")
    ]
    completed_design = designs.complete(
        design_claim,
        [synthetic_candidate_draft(consolidation, catalog_refs=catalogs)],
        [
            "public-synthetic-designer-invocation-1",
            "public-synthetic-designer-invocation-2",
        ],
        retry_at,
    )
    assert completed_design.status == "completed"
    assert completed_design.model_invocation_refs == [
        "public-synthetic-designer-invocation-1",
        "public-synthetic-designer-invocation-2",
    ]
    assert completed_design.result_digest is not None
    with pytest.raises(ValueError, match="result digest does not bind provenance"):
        SkillDesignRunV1.model_validate(
            {
                **completed_design.model_dump(mode="json"),
                "model_invocation_refs": list(
                    reversed(completed_design.model_invocation_refs)
                ),
            }
        )
    assert len(completed_design.candidate_refs) == 1
    assert len(completed_design.candidate_material_digests) == 1
    candidate_ref = completed_design.candidate_refs[0]
    candidate = admin.get_candidate("public-synthetic-admin", candidate_ref)
    assert candidate.draft_revision == 1
    assert candidate.status == "draft"

    command = ApproveSkillCandidateV1(
        expected_draft_revision=1,
        idempotency_key="public-synthetic-candidate-approval",
    )
    approved = admin.approve_candidate(
        "public-synthetic-admin",
        candidate_ref,
        command,
    )
    replayed = admin.approve_candidate(
        "public-synthetic-admin",
        candidate_ref,
        command,
    )
    assert approved.status == "approved"
    assert applying_publisher.observed
    assert approved.approved_skill_ref is not None
    assert replayed.outcome == "replayed"
    assert replayed.approved_skill_ref == approved.approved_skill_ref
    conflict = admin.approve_candidate(
        "public-synthetic-admin",
        candidate_ref,
        ApproveSkillCandidateV1(
            expected_draft_revision=2,
            idempotency_key="public-synthetic-candidate-approval",
        ),
    )
    assert conflict.status == "approved"
    assert conflict.outcome == "conflict"
    assert conflict.approved_skill_ref == approved.approved_skill_ref

    with pytest.raises(SkillCandidateError) as stale:
        admin.approve_candidate(
            "public-synthetic-admin",
            candidate_ref,
            ApproveSkillCandidateV1(
                expected_draft_revision=1,
                idempotency_key="public-synthetic-stale-candidate-approval",
            ),
        )
    assert stale.value.status_code == 412
    replayed_design = designs.register_consolidation(consolidation)
    assert replayed_design.run_ref == design_run.run_ref
    assert replayed_design.result_digest == completed_design.result_digest
    assert replayed_design.model_invocation_refs == completed_design.model_invocation_refs
    assert (
        replayed_design.candidate_material_digests
        == completed_design.candidate_material_digests
    )
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasSkillCandidateRow)) == 1
        persisted_candidate = session.scalar(
            select(AtlasSkillCandidateRow).where(
                AtlasSkillCandidateRow.candidate_ref == candidate_ref
            )
        )
        assert persisted_candidate is not None
        assert completed_design.candidate_material_digests == [
            persisted_candidate.material_digest
        ]
        assert session.scalar(select(func.count()).select_from(AtlasPromptSkillRevisionRow)) == 1
