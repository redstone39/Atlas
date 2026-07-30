-- One-time additive upgrade for the Answer Model policy feature.
--
-- Operator contract:
--   * Run only after taking the normal database backup and stopping API/workers.
--   * Execute as one script with a role allowed to ALTER the Atlas schema.
--   * Safe to rerun after a successful commit.
--   * Do not use this artifact as an Alembic migration or destructive rollback.

BEGIN;

SELECT pg_advisory_xact_lock(
    hashtextextended('atlas:upgrade:answer-model-policy:v1', 0)
);

DO $$
BEGIN
    IF to_regclass('public.atlas_turn_conversations') IS NULL
       OR to_regclass('public.atlas_turn_executions') IS NULL
       OR to_regclass('public.atlas_turn_conversation_members') IS NULL
       OR to_regclass('public.atlas_audit_events') IS NULL THEN
        RAISE EXCEPTION
            'answer-model-policy upgrade requires the Atlas development baseline';
    END IF;
END
$$;

CREATE TEMPORARY TABLE answer_model_policy_upgrade_counts
ON COMMIT DROP
AS
SELECT
    (SELECT count(*) FROM atlas_turn_conversations) AS conversations,
    (SELECT count(*) FROM atlas_turn_conversation_members) AS turns,
    (SELECT count(*) FROM atlas_turn_executions) AS executions,
    (SELECT count(*) FROM atlas_audit_events) AS audits;

ALTER TABLE atlas_turn_conversations
    ADD COLUMN IF NOT EXISTS response_language varchar(10);

ALTER TABLE atlas_turn_executions
    ADD COLUMN IF NOT EXISTS response_language varchar(10),
    ADD COLUMN IF NOT EXISTS applied_guidance_revision bigint,
    ADD COLUMN IF NOT EXISTS applied_guidance_digest varchar(64);

UPDATE atlas_turn_conversations
SET response_language = 'zh-TW'
WHERE response_language IS NULL;

UPDATE atlas_turn_executions
SET
    response_language = COALESCE(response_language, 'zh-TW'),
    applied_guidance_revision = COALESCE(applied_guidance_revision, 0),
    applied_guidance_digest = CASE
        WHEN COALESCE(applied_guidance_revision, 0) = 0 THEN NULL
        ELSE applied_guidance_digest
    END
WHERE response_language IS NULL
   OR applied_guidance_revision IS NULL
   OR (
       COALESCE(applied_guidance_revision, 0) = 0
       AND applied_guidance_digest IS NOT NULL
   );

CREATE TABLE IF NOT EXISTS atlas_turn_answer_behavior_revisions (
    revision bigint PRIMARY KEY,
    custom_guidance text,
    guidance_digest varchar(64) NOT NULL,
    created_by varchar(200) NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    request_digest varchar(64) NOT NULL,
    audit_event_ref varchar(200) NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT ck_atlas_turn_answer_behavior_revision
        CHECK (revision >= 1),
    CONSTRAINT ck_atlas_turn_answer_behavior_guidance_length
        CHECK (
            custom_guidance IS NULL
            OR char_length(custom_guidance) BETWEEN 1 AND 2000
        ),
    CONSTRAINT ck_atlas_turn_answer_behavior_guidance_digest
        CHECK (guidance_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_atlas_turn_answer_behavior_request_digest
        CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uq_atlas_turn_answer_behavior_idempotency
        UNIQUE (idempotency_key)
);

ALTER TABLE atlas_turn_conversations
    ALTER COLUMN response_language SET NOT NULL;

ALTER TABLE atlas_turn_executions
    ALTER COLUMN response_language SET NOT NULL,
    ALTER COLUMN applied_guidance_revision SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'atlas_turn_conversations'::regclass
          AND conname = 'ck_atlas_turn_conversation_response_language'
    ) THEN
        ALTER TABLE atlas_turn_conversations
            ADD CONSTRAINT ck_atlas_turn_conversation_response_language
            CHECK (response_language IN ('zh-TW', 'en'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'atlas_turn_executions'::regclass
          AND conname = 'ck_atlas_turn_execution_response_language'
    ) THEN
        ALTER TABLE atlas_turn_executions
            ADD CONSTRAINT ck_atlas_turn_execution_response_language
            CHECK (response_language IN ('zh-TW', 'en'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'atlas_turn_executions'::regclass
          AND conname = 'ck_atlas_turn_execution_guidance_snapshot'
    ) THEN
        ALTER TABLE atlas_turn_executions
            ADD CONSTRAINT ck_atlas_turn_execution_guidance_snapshot
            CHECK (
                (
                    applied_guidance_revision = 0
                    AND applied_guidance_digest IS NULL
                )
                OR (
                    applied_guidance_revision >= 1
                    AND applied_guidance_digest ~ '^[0-9a-f]{64}$'
                )
            );
    END IF;
END
$$;

DO $$
DECLARE
    before_counts record;
BEGIN
    SELECT * INTO before_counts
    FROM answer_model_policy_upgrade_counts;

    IF before_counts.conversations <> (
           SELECT count(*) FROM atlas_turn_conversations
       )
       OR before_counts.turns <> (
           SELECT count(*) FROM atlas_turn_conversation_members
       )
       OR before_counts.executions <> (
           SELECT count(*) FROM atlas_turn_executions
       )
       OR before_counts.audits <> (
           SELECT count(*) FROM atlas_audit_events
       ) THEN
        RAISE EXCEPTION
            'answer-model-policy upgrade changed protected row counts';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM atlas_turn_conversation_members member
        LEFT JOIN atlas_turn_conversations conversation
          ON conversation.conversation_id = member.conversation_id
        WHERE conversation.conversation_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'answer-model-policy upgrade found an orphaned conversation turn';
    END IF;
END
$$;

COMMIT;
