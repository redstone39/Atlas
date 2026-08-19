from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, update

from atlas_production.infrastructure.persistence.turn_experience import AtlasTurnExperienceRow
from atlas_production.infrastructure.postgres_owner.turn_experience import (
    PostgresTurnExperienceStore,
    TurnExperienceStoreConflict,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.turn_experience.public import (
    MaterializeTurnExperienceV1,
    TurnExperienceCursorV1,
    TurnExperienceExecutionSkillSelectionV1,
    TurnExperienceGovernanceV1,
    TurnExperienceRouteRefV1,
    TurnExperienceTerminalV1,
    TurnExperienceUsageV1,
)


NOW = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_experiences(postgres_runtime: PostgresRuntime):
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(delete(AtlasTurnExperienceRow))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(delete(AtlasTurnExperienceRow))


def _command(
    execution_id: str = "experience-execution-1",
    *,
    committed_at: datetime = NOW,
) -> MaterializeTurnExperienceV1:
    return MaterializeTurnExperienceV1(
        experience_ref=f"turn-experience:{execution_id}:v1",
        execution_id=execution_id,
        turn_id=f"turn-{execution_id}",
        input_digest="1" * 64,
        applied_guidance_revision=0,
        response_language="en",
        reasoning_mode="standard",
        route=TurnExperienceRouteRefV1(
            route_id="route-1", route_revision=1, runtime_policy_revision=1
        ),
        usage=TurnExperienceUsageV1(
            tool_invocations=0,
            catalog_pages=0,
            document_candidates=0,
            search_rounds=0,
            model_visible_items=0,
            provider_invocations=1,
            context_tokens=100,
            tool_tokens=0,
            retrieval_repairs=0,
            schema_retries=0,
        ),
        prompt_skill_selections=[
            TurnExperienceExecutionSkillSelectionV1(
                category="understanding",
                node="resolver",
                status="not_applicable",
            ),
            TurnExperienceExecutionSkillSelectionV1(
                category="answer",
                node="answer_candidate",
                candidate_ordinal=1,
                candidate_kind="normal",
                status="not_applicable",
            ),
        ],
        terminal=TurnExperienceTerminalV1(
            terminal_commit_intent_ref=f"terminal-{execution_id}",
            evidence_pack_ref=f"evidence-{execution_id}",
            governed_answer_draft_ref=f"governed-{execution_id}",
            citation_binding_draft_ref=f"citation-{execution_id}",
            audit_draft_ref=f"audit-{execution_id}",
            committed_at=committed_at,
        ),
        governance=TurnExperienceGovernanceV1(
            governed_answer_draft_ref=f"governed-{execution_id}",
            governed_answer_digest="2" * 64,
            retrieval_status="not_used",
            evidence_review_status="questionable",
            evidence_review_reason_codes=["empty_declaration"],
            declared_evidence_count=0,
            resolved_evidence_count=0,
            unresolved_evidence_count=0,
            assessment_state="not_attempted",
            assessment_reason_code="empty_declaration",
            assessment_version="provisional-declared-evidence-v1",
            assessment_consistency="not_applicable",
            assessment_answer_digest="3" * 64,
            assessment_declared_subset_digest="4" * 64,
            assessment_success_count=0,
            assessment_failure_count=0,
        ),
        idempotency_key=f"{execution_id}:turn-experience-v1",
    )


def test_materialize_exact_replay_read_and_conflict(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresTurnExperienceStore(postgres_runtime.session_factory)
    command = _command()

    first = store.materialize(command)
    replay = store.materialize(command)

    assert replay == first
    assert store.read_for_execution(command.execution_id, command.schema_version) == first
    assert store.read_for_execution(command.execution_id, "turn-experience-v2") is None
    with pytest.raises(TurnExperienceStoreConflict, match="conflicts"):
        store.materialize(command.model_copy(update={"input_digest": "f" * 64}))


def test_concurrent_duplicate_materialization_converges_on_one_row(
    postgres_runtime: PostgresRuntime,
) -> None:
    command = _command()

    def materialize():
        return PostgresTurnExperienceStore(postgres_runtime.session_factory).materialize(command)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: materialize(), range(2)))

    assert results[0] == results[1]
    with postgres_runtime.session_factory() as session, session.begin():
        assert session.scalar(select(func.count()).select_from(AtlasTurnExperienceRow)) == 1


def test_bounded_scan_uses_strict_sequence_execution_cursor(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresTurnExperienceStore(postgres_runtime.session_factory)
    first = store.materialize(_command("experience-execution-a"))
    second = store.materialize(_command("experience-execution-b"))
    third = store.materialize(
        _command("experience-execution-c", committed_at=NOW + timedelta(seconds=1))
    )

    page_one = store.list_after(None, 2)
    assert [item.execution_id for item in page_one] == [first.execution_id, second.execution_id]
    page_two = store.list_after(
        TurnExperienceCursorV1(
            scan_sequence=page_one[-1].scan_sequence,
            execution_id=page_one[-1].execution_id,
        ),
        2,
    )
    assert page_two == [third]
    assert store.list_after(
        TurnExperienceCursorV1(
            scan_sequence=third.scan_sequence,
            execution_id=third.execution_id,
        ),
        100,
    ) == []


def test_late_backfill_remains_visible_after_newer_terminal_was_scanned(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresTurnExperienceStore(postgres_runtime.session_factory)
    newer_terminal = store.materialize(
        _command("experience-late-newer", committed_at=NOW + timedelta(seconds=1))
    )
    cursor = TurnExperienceCursorV1(
        scan_sequence=newer_terminal.scan_sequence,
        execution_id=newer_terminal.execution_id,
    )

    older_terminal_backfill = store.materialize(
        _command("experience-late-older", committed_at=NOW)
    )
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            update(AtlasTurnExperienceRow)
            .where(
                AtlasTurnExperienceRow.execution_id
                == older_terminal_backfill.execution_id
            )
            .values(created_at=NOW - timedelta(days=1))
        )

    assert [
        item.execution_id for item in store.list_after(cursor, 100)
    ] == [older_terminal_backfill.execution_id]


def test_scan_waits_for_inflight_materialization_commit(
    postgres_runtime: PostgresRuntime,
) -> None:
    store = PostgresTurnExperienceStore(postgres_runtime.session_factory)
    materialized = store.materialize(_command("experience-inflight"))
    with postgres_runtime.session_factory() as session, session.begin():
        row = session.get(AtlasTurnExperienceRow, materialized.experience_ref)
        assert row is not None
        values = {
            column.name: getattr(row, column.name)
            for column in AtlasTurnExperienceRow.__table__.columns
        }
        session.delete(row)

    with postgres_runtime.session_factory() as writer:
        transaction = writer.begin()
        writer.add(AtlasTurnExperienceRow(**values))
        writer.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_scan = pool.submit(store.list_after, None, 100)
            with pytest.raises(FutureTimeoutError):
                pending_scan.result(timeout=0.2)
            transaction.commit()
            scanned = pending_scan.result(timeout=3)

    assert [item.execution_id for item in scanned] == [materialized.execution_id]
