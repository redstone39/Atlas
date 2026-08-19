from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasAuditEventRow,
)
from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationMemberRow,
    AtlasTurnConversationRow,
)
from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnExecutionRow,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime


ROOT = Path(__file__).resolve().parents[3]
UPGRADE = (
    ROOT / "infra/scripts/upgrade_answer_model_policy_once.sql"
).read_text(encoding="utf-8")
CONVERSATION_ID = "answer-policy-upgrade-conversation"
EXECUTION_ID = "answer-policy-upgrade-execution"
TURN_ID = "answer-policy-upgrade-turn"


def _plain_postgres_url(database_url: str) -> str:
    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _execute_script(database_url: str, script: str) -> None:
    with psycopg.connect(
        _plain_postgres_url(database_url),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(script)


def _protected_counts(runtime: PostgresRuntime) -> tuple[int, int, int, int]:
    with runtime.session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(AtlasTurnConversationRow))
            or 0,
            session.scalar(
                select(func.count()).select_from(
                    AtlasTurnConversationMemberRow
                )
            )
            or 0,
            session.scalar(select(func.count()).select_from(AtlasTurnExecutionRow))
            or 0,
            session.scalar(select(func.count()).select_from(AtlasAuditEventRow))
            or 0,
        )


def _insert_existing_rows(runtime: PostgresRuntime) -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    with runtime.session_factory() as session, session.begin():
        session.add(
            AtlasTurnConversationRow(
                conversation_id=CONVERSATION_ID,
                owner_actor_id="answer-policy-upgrade-actor",
                title="Existing conversation",
                status="active",
                response_language="zh-TW",
                reasoning_mode="standard",
                next_ordinal=2,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            AtlasTurnConversationMemberRow(
                turn_id=TURN_ID,
                conversation_id=CONVERSATION_ID,
                execution_id=EXECUTION_ID,
                role="user",
                ordinal=1,
                created_at=now,
            )
        )
        session.add(
            AtlasTurnExecutionRow(
                execution_id=EXECUTION_ID,
                turn_id=TURN_ID,
                conversation_id=CONVERSATION_ID,
                actor_id="answer-policy-upgrade-actor",
                input_digest="0" * 64,
                response_language="zh-TW",
                reasoning_mode="standard",
                prompt_skill_catalogs=[
                    {
                        "category": "understanding",
                        "catalog_revision": 1,
                        "catalog_digest": "1" * 64,
                    },
                    {
                        "category": "answer",
                        "catalog_revision": 1,
                        "catalog_digest": "2" * 64,
                    },
                ],
                reasoning_trace=None,
                applied_guidance_revision=0,
                applied_guidance_digest=None,
                state="allocated",
                version=1,
                route_id="answer-policy-upgrade-route",
                route_revision=1,
                runtime_policy_revision=1,
                tokenizer_profile="cl100k_base",
                context_window_tokens=16_000,
                max_input_tokens_per_invocation=8_000,
                max_output_tokens_per_invocation=2_000,
                max_tool_result_tokens_per_execution=4_000,
                max_total_tokens_per_conversation=20_000,
                max_tool_invocations=2,
                max_catalog_pages=2,
                max_search_rounds=2,
                max_model_visible_items_per_turn=2,
                max_retrieval_repairs=3,
                max_selected_anchor_pages_per_round=20,
                max_provider_invocations=11,
                max_reasoning_revision_cycles=0,
                max_schema_retries_per_turn=3,
                context_token_budget=100,
                tool_token_budget=100,
                tool_execution_timeout_seconds=45,
                deadline_seconds=120,
                heartbeat_interval_seconds=5,
                ttl_seconds=30,
                failure_sweep_interval_seconds=5,
                grant_ref=None,
                catalog_ref=None,
                context_pack_ref=None,
                terminal_commit_intent_ref=None,
                terminal_failure_code=None,
                deadline_at=now + timedelta(seconds=120),
                created_at=now,
                updated_at=now,
            )
        )


def _restore_old_schema(database_url: str) -> None:
    _execute_script(
        database_url,
        """
        BEGIN;
        DROP TABLE IF EXISTS atlas_turn_answer_behavior_revisions;
        ALTER TABLE atlas_turn_conversations
            DROP CONSTRAINT IF EXISTS ck_atlas_turn_conversation_response_language,
            DROP COLUMN IF EXISTS response_language;
        ALTER TABLE atlas_turn_executions
            DROP CONSTRAINT IF EXISTS ck_atlas_turn_execution_guidance_snapshot,
            DROP CONSTRAINT IF EXISTS ck_atlas_turn_execution_response_language,
            DROP COLUMN IF EXISTS applied_guidance_digest,
            DROP COLUMN IF EXISTS applied_guidance_revision,
            DROP COLUMN IF EXISTS response_language;
        COMMIT;
        """,
    )


def _cleanup(runtime: PostgresRuntime) -> None:
    with runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasTurnConversationMemberRow).where(
                AtlasTurnConversationMemberRow.turn_id == TURN_ID
            )
        )
        session.execute(
            delete(AtlasTurnExecutionRow).where(
                AtlasTurnExecutionRow.execution_id == EXECUTION_ID
            )
        )
        session.execute(
            delete(AtlasTurnConversationRow).where(
                AtlasTurnConversationRow.conversation_id == CONVERSATION_ID
            )
        )


def test_answer_model_policy_upgrade_backfills_preserves_and_reruns(
    postgres_runtime: PostgresRuntime,
    postgres_url: str,
) -> None:
    _cleanup(postgres_runtime)
    _insert_existing_rows(postgres_runtime)
    before = _protected_counts(postgres_runtime)
    _restore_old_schema(postgres_url)

    _execute_script(postgres_url, UPGRADE)

    with postgres_runtime.session_factory() as session:
        conversation = session.get(AtlasTurnConversationRow, CONVERSATION_ID)
        execution = session.get(AtlasTurnExecutionRow, EXECUTION_ID)
        assert conversation is not None
        assert conversation.response_language == "zh-TW"
        assert execution is not None
        assert execution.response_language == "zh-TW"
        assert execution.applied_guidance_revision == 0
        assert execution.applied_guidance_digest is None
        assert _protected_counts(postgres_runtime) == before

    _execute_script(postgres_url, UPGRADE)

    with psycopg.connect(_plain_postgres_url(postgres_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.relname,
                    con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                WHERE con.conname IN (
                    'ck_atlas_turn_conversation_response_language',
                    'ck_atlas_turn_execution_response_language',
                    'ck_atlas_turn_execution_guidance_snapshot',
                    'uq_atlas_turn_answer_behavior_idempotency'
                )
                ORDER BY con.conname
                """
            )
            constraints = {name for _table, name in cursor.fetchall()}
            cursor.execute(
                "SELECT to_regclass('public.atlas_turn_answer_behavior_revisions')"
            )
            revision_table = cursor.fetchone()[0]
    assert constraints == {
        "ck_atlas_turn_conversation_response_language",
        "ck_atlas_turn_execution_guidance_snapshot",
        "ck_atlas_turn_execution_response_language",
        "uq_atlas_turn_answer_behavior_idempotency",
    }
    assert revision_table == "atlas_turn_answer_behavior_revisions"
    assert _protected_counts(postgres_runtime) == before

    _cleanup(postgres_runtime)
