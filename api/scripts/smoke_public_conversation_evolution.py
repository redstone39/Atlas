#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from threading import Event

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from atlas_production import app as app_module  # noqa: E402
from atlas_production.infrastructure import composition  # noqa: E402
from atlas_production.infrastructure.consolidator_provider import (  # noqa: E402
    ConsolidatorRunResult,
)
from atlas_production.infrastructure.conversation_reviewer import (  # noqa: E402
    ConversationReviewRunResult,
)
from atlas_production.infrastructure.learner_provider import LearnerRunResult  # noqa: E402
from atlas_production.infrastructure.persistence.consolidator import (  # noqa: E402
    AtlasConsolidationRunRow,
    AtlasConsolidatorCheckpointRow,
)
from atlas_production.infrastructure.persistence.conversation import (  # noqa: E402
    AtlasTurnConversationRow,
)
from atlas_production.infrastructure.persistence.conversation_review import (  # noqa: E402
    AtlasConversationLearningCaseRow,
    AtlasConversationLearningCaseTurnRow,
    AtlasConversationReviewRow,
    AtlasConversationReviewSnapshotTurnRow,
)
from atlas_production.infrastructure.persistence.learner import AtlasLearnerRunRow  # noqa: E402
from atlas_production.infrastructure.persistence.prompt_skills import (  # noqa: E402
    AtlasPromptSkillCatalogRevisionRow,
    AtlasPromptSkillControlRow,
    AtlasPromptSkillIdempotencyRow,
    AtlasPromptSkillRevisionRow,
)
from atlas_production.infrastructure.persistence.skill_designer import (  # noqa: E402
    AtlasSkillCandidateIdempotencyRow,
    AtlasSkillCandidateRow,
    AtlasSkillDesignerCheckpointRow,
    AtlasSkillDesignRunRow,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime  # noqa: E402
from atlas_production.infrastructure.skill_designer_provider import (  # noqa: E402
    SkillDesignerRunResult,
)
from atlas_production.modules.conversation.public import (  # noqa: E402
    ConversationTurnMemberV1,
    ConversationV1,
)
from atlas_production.modules.identity_access.records import UserRecord  # noqa: E402
from tests.public_synthetic_data import (  # noqa: E402
    synthetic_candidate_draft,
    synthetic_consolidated_experience,
    synthetic_learner_payload,
    synthetic_review_proposal,
    synthetic_review_transcript,
)



def _validated_url() -> str:
    database_url = os.environ.get("ATLAS_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        raise RuntimeError("ATLAS_TEST_POSTGRES_URL is required")
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise RuntimeError(
            "ATLAS_TEST_POSTGRES_URL must be a valid PostgreSQL URL"
        ) from exc
    if not parsed.drivername.startswith("postgresql"):
        raise RuntimeError("ATLAS_TEST_POSTGRES_URL must use PostgreSQL")
    if "database" in parsed.query or "dbname" in parsed.query:
        raise RuntimeError(
            "PostgreSQL checks require a dedicated atlas_baseline_test_* database"
        )
    database_name = parsed.database or ""
    if database_name == "atlas_production" or not database_name.startswith(
        "atlas_baseline_test_"
    ):
        raise RuntimeError(
            "PostgreSQL checks require a dedicated atlas_baseline_test_* database"
        )
    return database_url


class _SyntheticConversationState:
    def __init__(self, observed_at: datetime) -> None:
        self._conversations: dict[str, ConversationV1] = {}
        self._members: dict[str, list[ConversationTurnMemberV1]] = {}
        self._runtime: dict[str, object] = {}
        self._projections: dict[str, object] = {}
        self._outcomes: dict[str, object] = {}
        self._drafts: dict[str, object] = {}
        updated_at = observed_at - timedelta(hours=3)
        for ordinal in range(1, 11):
            suffix = f"smoke-{ordinal}"
            transcript = synthetic_review_transcript(conversation_suffix=suffix)
            conversation_id = transcript.conversation_id
            self._conversations[conversation_id] = ConversationV1(
                conversation_id=conversation_id,
                owner_actor_id="public-synthetic-admin",
                title=f"public-synthetic-conversation-title-{ordinal}",
                status="active",
                response_language="en",
                created_at=updated_at,
                updated_at=updated_at,
            )
            members: list[ConversationTurnMemberV1] = []
            for turn in transcript.turns:
                members.append(
                    ConversationTurnMemberV1(
                        turn_id=turn.turn_id,
                        conversation_id=conversation_id,
                        execution_id=turn.execution_id,
                        role="user",
                        ordinal=turn.position,
                        created_at=updated_at,
                    )
                )
                projection_ref = f"public-synthetic-input-{suffix}-{turn.position}"
                draft_ref = f"public-synthetic-answer-{suffix}-{turn.position}"
                draft_digest = (
                    "a" * 64 if turn.position == 1 else "b" * 64
                )
                self._runtime[turn.execution_id] = SimpleNamespace(
                    execution_id=turn.execution_id,
                    turn_id=turn.turn_id,
                    conversation_id=conversation_id,
                )
                self._projections[turn.execution_id] = SimpleNamespace(
                    execution_id=turn.execution_id,
                    projection_ref=projection_ref,
                    original_user_input=turn.original_user_text,
                )
                self._outcomes[turn.execution_id] = SimpleNamespace(
                    outcome="completed",
                    scan_sequence=(ordinal - 1) * 2 + turn.position,
                    terminal_commit_intent_ref=(
                        f"public-synthetic-intent-{suffix}-{turn.position}"
                    ),
                    committed_at=updated_at,
                    governed_answer_draft_ref=draft_ref,
                )
                self._drafts[draft_ref] = SimpleNamespace(
                    execution_id=turn.execution_id,
                    digest=draft_digest,
                    segments=turn.final_governed_assistant_segments,
                )
            self._members[conversation_id] = members

    def list_active_updated_before(self, *, cutoff, after, limit):
        records = sorted(
            (
                conversation
                for conversation in self._conversations.values()
                if conversation.updated_at <= cutoff
            ),
            key=lambda value: (value.updated_at, value.conversation_id),
        )
        if after is not None:
            records = [
                value
                for value in records
                if (value.updated_at, value.conversation_id)
                > (after.updated_at, after.conversation_id)
            ]
        return records[:limit]

    def get(self, conversation_id):
        return self._conversations.get(conversation_id)

    def candidate_turns_after(self, conversation_id, *, after, limit):
        members = self._members[conversation_id]
        if after is not None:
            members = [
                value
                for value in members
                if (value.ordinal, value.turn_id) > (after.ordinal, after.turn_id)
            ]
        return members[:limit]

    def retry_sources(self, conversation_id):
        return {}

    def snapshot(self, execution_id):
        return self._runtime[execution_id]

    def get_input_projection(self, execution_id):
        return self._projections.get(execution_id)

    def terminal_outcome(self, execution_id):
        return self._outcomes.get(execution_id)

    def read_v2(self, draft_ref):
        return self._drafts.get(draft_ref)

    def persist_conversations(self, runtime: PostgresRuntime) -> None:
        with runtime.session_factory() as session, session.begin():
            for conversation in self._conversations.values():
                session.add(
                    AtlasTurnConversationRow(
                        conversation_id=conversation.conversation_id,
                        owner_actor_id=conversation.owner_actor_id,
                        title=conversation.title,
                        status=conversation.status,
                        response_language=conversation.response_language,
                        reasoning_mode=conversation.reasoning_mode,
                        next_ordinal=len(self._members[conversation.conversation_id]) + 1,
                        created_at=conversation.created_at,
                        updated_at=conversation.updated_at,
                    )
                )


class _SyntheticReviewer:
    def __init__(self, owner) -> None:
        self._owner = owner
        self._ordinal = 0

    def review(self, claim, transcript, *, observed_at, on_claim_pinned=None):
        self._ordinal += 1
        pinned = self._owner.pin_route(
            claim,
            "public-synthetic-review-route",
            1,
            1,
            observed_at,
        )
        if on_claim_pinned is not None:
            on_claim_pinned(pinned)
        turn_ids = [turn.turn_id for turn in transcript.turns]
        proposal = synthetic_review_proposal()
        case = proposal.cases[0].model_copy(
            update={
                "involved_turn_ids": turn_ids,
                "primary_assistant_turn_id": turn_ids[0],
            }
        )
        proposal = proposal.model_copy(update={"cases": [case]})
        return ConversationReviewRunResult(
            claim=pinned,
            proposal=proposal,
            model_invocation_refs=(
                f"public-synthetic-review-invocation-{self._ordinal}",
            ),
            core_window_turn_ids=(tuple(turn_ids),),
        )




class _SyntheticLearnerSource:
    def assemble(self, claim):
        return claim



class _SyntheticLearner:
    def __init__(self, owner) -> None:
        self._owner = owner
        self._ordinal = 0

    def learn(self, claim, packet, *, observed_at, on_claim_pinned=None):
        self._ordinal += 1
        pinned = self._owner.pin_route(
            claim,
            "public-synthetic-learner-route",
            1,
            1,
            observed_at,
        )
        if on_claim_pinned is not None:
            on_claim_pinned(pinned)
        run = self._owner.read_run(pinned.run_ref)
        if run is None:
            raise RuntimeError("public synthetic learner run disappeared")
        return LearnerRunResult(
            claim=pinned,
            payload=synthetic_learner_payload(
                run.source,
                invocation_suffix=str(self._ordinal),
            ),
        )


class _SyntheticConsolidator:
    def __init__(self, owner) -> None:
        self._owner = owner

    def consolidate(
        self,
        claim,
        source_experiences,
        *,
        observed_at,
        on_claim_pinned=None,
    ):
        pinned = self._owner.pin_route(
            claim,
            "public-synthetic-consolidator-route",
            1,
            1,
            observed_at,
        )
        if on_claim_pinned is not None:
            on_claim_pinned(pinned)
        return ConsolidatorRunResult(
            claim=pinned,
            experiences=[
                synthetic_consolidated_experience(
                    [experience.payload.source.experience_ref for experience in source_experiences]
                )
            ],
            model_invocation_refs=["public-synthetic-consolidator-invocation"],
        )


class _SyntheticDesigner:
    def __init__(self, owner) -> None:
        self._owner = owner

    def design(
        self,
        claim,
        consolidation,
        context,
        *,
        observed_at,
        on_claim_pinned=None,
    ):
        pinned = self._owner.pin_route(
            claim,
            "public-synthetic-designer-route",
            1,
            1,
            observed_at,
        )
        if on_claim_pinned is not None:
            on_claim_pinned(pinned)
        return SkillDesignerRunResult(
            claim=pinned,
            drafts=[
                synthetic_candidate_draft(
                    consolidation,
                    catalog_refs=context.catalog_refs,
                )
            ],
            model_invocation_refs=[
                "public-synthetic-designer-invocation-1",
                "public-synthetic-designer-invocation-2",
            ],
        )


class _SyntheticAdminPrincipal:
    def current_user(self, _token):
        return UserRecord(
            actor_id="public-synthetic-admin",
            display_name="Public Synthetic Admin",
            email="public-synthetic-admin@example.test",
            system_role="admin",
            password_digest=None,
            active=True,
        )


def _stop_composition(selected) -> None:
    selected.skill_candidate_pipeline_reconciler.stop()
    selected.turn_execution_carrier.shutdown()
    selected.conversation_review_reconciler.stop()
    selected.learner_reconciler.stop()
    selected.turn_experience_reconciler.stop()
    selected.turn_resource_release_reconciler.stop()
    selected.turn_lease_failure_sweeper.stop()


def _clean(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session, session.begin():
        for row in (
            AtlasSkillCandidateIdempotencyRow,
            AtlasSkillCandidateRow,
            AtlasSkillDesignRunRow,
            AtlasSkillDesignerCheckpointRow,
            AtlasConsolidationRunRow,
            AtlasConsolidatorCheckpointRow,
            AtlasLearnerRunRow,
            AtlasConversationLearningCaseTurnRow,
            AtlasConversationLearningCaseRow,
            AtlasConversationReviewSnapshotTurnRow,
            AtlasConversationReviewRow,
            AtlasPromptSkillIdempotencyRow,
            AtlasPromptSkillControlRow,
            AtlasPromptSkillRevisionRow,
            AtlasTurnConversationRow,
        ):
            session.execute(delete(row))
        session.execute(
            delete(AtlasPromptSkillCatalogRevisionRow).where(
                AtlasPromptSkillCatalogRevisionRow.catalog_revision > 1
            )
        )


def _build_composition(runtime: PostgresRuntime):
    original_filesystem = composition._active_artifact_filesystem
    composition._active_artifact_filesystem = lambda _runtime: object()
    try:
        return composition.build_api_composition(runtime)
    finally:
        composition._active_artifact_filesystem = original_filesystem


def main() -> None:
    database_url = _validated_url()
    runtime = PostgresRuntime.from_url(database_url)
    runtime.bootstrap_schema()
    _clean(runtime)

    selected = _build_composition(runtime)
    _stop_composition(selected)

    if (
        selected.learner_owner is None
        or selected.skill_candidates is None
        or selected.skill_candidate_pipeline_reconciler is None
    ):
        raise RuntimeError("public evolution owners are unavailable")
    fixed_now = datetime.now(timezone.utc) + timedelta(minutes=10)
    review_owner = selected.conversation_review_owner
    learner_owner = selected.learner_owner
    selected.conversation_review_reconciler._stop = Event()
    selected.learner_reconciler._stop = Event()
    selected.skill_candidate_pipeline_reconciler._stop = Event()
    pipeline_owner = selected.skill_candidate_pipeline_reconciler
    consolidation_owner = pipeline_owner._consolidations
    design_owner = pipeline_owner._designs
    if design_owner is None:
        raise RuntimeError("public Skill Designer owner is unavailable")

    conversation_state = _SyntheticConversationState(fixed_now)
    review_source = selected.conversation_review_source
    conversation_state.persist_conversations(runtime)
    review_source._conversations = conversation_state
    review_source._retry_lineage = conversation_state
    review_source._input_reader = conversation_state
    review_source._runtime = conversation_state
    review_source._governance = conversation_state
    review_reconciler = selected.conversation_review_reconciler
    review_reconciler._conversations = conversation_state
    review_reconciler._reviewer = _SyntheticReviewer(review_owner)
    review_reconciler._clock = lambda: fixed_now
    review_reconciler._batch_size = 10
    review_reconciler._worker_id = "public-synthetic-review-smoke"
    for _ in range(10):
        if review_reconciler.run_once() != 1:
            raise RuntimeError("public synthetic review did not complete")

    learner_reconciler = selected.learner_reconciler
    learner_reconciler._source = _SyntheticLearnerSource()
    learner_reconciler._provider = _SyntheticLearner(learner_owner)
    learner_reconciler._clock = lambda: fixed_now
    learner_reconciler._batch_size = 10
    learner_reconciler._worker_id = "public-synthetic-learner-smoke"
    for _ in range(10):
        if learner_reconciler.run_once() != 1:
            raise RuntimeError("public synthetic learner did not complete")

    candidate_reconciler = selected.skill_candidate_pipeline_reconciler
    candidate_reconciler._consolidator = _SyntheticConsolidator(consolidation_owner)
    candidate_reconciler._designer = _SyntheticDesigner(design_owner)
    candidate_reconciler._clock = lambda: fixed_now
    candidate_reconciler._worker_id = "public-synthetic-candidate-smoke"
    if candidate_reconciler.run_once() != 2:
        raise RuntimeError("public synthetic candidate pipeline did not complete")
    candidate = selected.skill_candidates.list_candidates(
        "public-synthetic-admin",
        "planner",
    ).items[0]
    candidate_ref = candidate.candidate_ref
    _stop_composition(selected)

    lifecycle = _build_composition(runtime)
    if lifecycle.skill_candidates is None:
        raise RuntimeError("public candidate Admin service is unavailable")
    object.__setattr__(
        lifecycle, "current_principal", _SyntheticAdminPrincipal()
    )
    with TestClient(app_module.create_app(lifecycle)) as client:
        listed = client.get(
            "/api/v1/admin/prompt-skill-candidates",
            params={"category": "planner"},
        )
        if listed.status_code != 200:
            raise RuntimeError(
                f"public candidate Admin list route failed: {listed.status_code}"
            )
        detail = client.get(
            f"/api/v1/admin/prompt-skill-candidates/{candidate_ref}"
        )
        if detail.status_code != 200:
            raise RuntimeError("public candidate Admin detail route failed")
        approval_body = {
            "expected_draft_revision": candidate.draft_revision,
            "idempotency_key": "public-synthetic-smoke-approval",
        }
        approval_headers = {
            "Idempotency-Key": "public-synthetic-smoke-approval",
            "If-Match": str(candidate.draft_revision),
        }
        approved = client.post(
            f"/api/v1/admin/prompt-skill-candidates/{candidate_ref}/approve",
            headers=approval_headers,
            json=approval_body,
        )
        replayed = client.post(
            f"/api/v1/admin/prompt-skill-candidates/{candidate_ref}/approve",
            headers=approval_headers,
            json=approval_body,
        )
        conflict = client.post(
            f"/api/v1/admin/prompt-skill-candidates/{candidate_ref}/approve",
            headers={
                **approval_headers,
                "If-Match": str(candidate.draft_revision + 1),
            },
            json={
                **approval_body,
                "expected_draft_revision": candidate.draft_revision + 1,
            },
        )
        if (
            approved.status_code != 200
            or replayed.status_code != 200
            or conflict.status_code != 200
        ):
            raise RuntimeError("public candidate Admin approval route failed")
        if (
            approved.json()["status"] != "approved"
            or replayed.json()["outcome"] != "replayed"
            or conflict.json()["outcome"] != "conflict"
        ):
            raise RuntimeError("public candidate Admin approval outcomes are incomplete")
    for reconciler in (
        lifecycle.conversation_review_reconciler,
        lifecycle.learner_reconciler,
        lifecycle.skill_candidate_pipeline_reconciler,
    ):
        if reconciler.running:
            raise RuntimeError("public API lifecycle left a reconciler thread running")
    if candidate_reconciler.run_once() != 0:
        raise RuntimeError("public synthetic reconciliation duplicated work")
    with runtime.session_factory() as session:
        candidate_count = session.scalar(
            select(func.count()).select_from(AtlasSkillCandidateRow)
        )
        revision_count = session.scalar(
            select(func.count()).select_from(AtlasPromptSkillRevisionRow)
        )
        persisted_design = session.scalar(select(AtlasSkillDesignRunRow))
        persisted_candidate = session.scalar(select(AtlasSkillCandidateRow))
    if (candidate_count, revision_count) != (1, 1):
        raise RuntimeError("public synthetic replay duplicated candidate publication")
    if (
        persisted_design is None
        or persisted_design.model_invocation_refs
        != [
            "public-synthetic-designer-invocation-1",
            "public-synthetic-designer-invocation-2",
        ]
        or persisted_design.result_digest is None
        or persisted_candidate is None
        or persisted_design.candidate_material_digests
        != [persisted_candidate.material_digest]
    ):
        raise RuntimeError("public Skill Designer provenance was not retained")

    runtime.engine.dispose()
    print("PUBLIC_CONVERSATION_EVOLUTION_ACCEPTED")


if __name__ == "__main__":
    main()
