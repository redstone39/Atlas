from __future__ import annotations

from datetime import datetime, timezone

from atlas_production.infrastructure.turn_release_reconciler import (
    TurnResourceReleaseReconciler,
)
from atlas_production.modules.turn_runtime.public import ReleaseIntentV1


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class Runtime:
    def __init__(self, intents):
        self.intents = intents
        self.completed = []

    def pending_release_intents(self, *, limit):
        assert limit == 100
        values, self.intents = self.intents, []
        return values

    def complete_release_intent(self, command):
        self.completed.append(command)


class Authorization:
    def __init__(self):
        self.commands = []

    def release_grant(self, command):
        self.commands.append(command)

    def release_execution_grant(self, **command):
        self.commands.append(command)


class Retrieval:
    def __init__(self):
        self.commands = []

    def release_catalog(self, **command):
        self.commands.append(command)

    def release_execution_catalog(self, **command):
        self.commands.append(command)


class Contexts:
    def __init__(self):
        self.commands = []

    def release(self, command):
        self.commands.append(command)

    def release_execution_context(self, **command):
        self.commands.append(command)


class GenerationRetention:
    def __init__(self):
        self.commands = []

    def release_generation_retention(self, command):
        self.commands.append(command)

    def release_execution_generation_retention(self, **command):
        self.commands.append(command)



def _intent(owner, kind, ref, ordinal):
    return ReleaseIntentV1(
        release_intent_id=f"release-intent-{ordinal}",
        execution_id="execution-1",
        resource_owner=owner,
        resource_ref=ref,
        release_kind=kind,
        status="releasing",
        attempt_count=1,
    )


def test_reconciler_releases_each_owner_after_claim_transaction_closed() -> None:
    runtime = Runtime(
        [
            _intent("authorization", "release_turn_grant", "grant-1", 1),
            _intent(
                "processing_pipeline",
                "release_generation_retention",
                "retention-1",
                2,
            ),
            _intent("retrieval", "release_knowledge_catalog", "catalog-1", 3),
            _intent("context_engineering", "release_context_pack", "context-1", 4),
        ]
    )
    authorization, retrieval, contexts = Authorization(), Retrieval(), Contexts()
    generation_retention = GenerationRetention()
    reconciler = TurnResourceReleaseReconciler(
        runtime=runtime,
        authorization=authorization,
        retrieval=retrieval,
        generation_retention=generation_retention,
        contexts=contexts,
    )

    assert reconciler.run_once() == 4
    assert authorization.commands[0].grant_ref == "grant-1"
    assert retrieval.commands[0]["catalog_ref"] == "catalog-1"
    assert generation_retention.commands[0].retention_ref == "retention-1"
    assert contexts.commands[0].context_pack_ref == "context-1"
    assert [item.outcome for item in runtime.completed] == ["released"] * 4


def test_unknown_release_is_failed_and_remains_retryable() -> None:
    runtime = Runtime([_intent("audit", "unknown", "audit-1", 1)])
    reconciler = TurnResourceReleaseReconciler(
        runtime=runtime,
        authorization=Authorization(),
        retrieval=Retrieval(),
        generation_retention=GenerationRetention(),
        contexts=Contexts(),
    )

    assert reconciler.run_once() == 1
    assert runtime.completed[0].outcome == "failed"
    assert runtime.completed[0].failure_code == "ValueError"


def test_reconciler_resolves_pre_acceptance_resources_by_execution() -> None:
    runtime = Runtime(
        [
            _intent(
                "authorization",
                "release_turn_grant",
                "execution-resource:authorization:execution-1",
                1,
            ),
            _intent(
                "processing_pipeline",
                "release_generation_retention",
                "execution-resource:processing_pipeline:execution-1",
                2,
            ),
            _intent(
                "retrieval",
                "release_knowledge_catalog",
                "execution-resource:retrieval:execution-1",
                3,
            ),
            _intent(
                "context_engineering",
                "release_context_pack",
                "execution-resource:context_engineering:execution-1",
                4,
            ),
        ]
    )
    authorization, retrieval, contexts = Authorization(), Retrieval(), Contexts()
    generation_retention = GenerationRetention()
    reconciler = TurnResourceReleaseReconciler(
        runtime=runtime,
        authorization=authorization,
        retrieval=retrieval,
        generation_retention=generation_retention,
        contexts=contexts,
    )

    assert reconciler.run_once() == 4
    assert authorization.commands[0]["execution_id"] == "execution-1"
    assert retrieval.commands[0]["execution_id"] == "execution-1"
    assert generation_retention.commands[0]["execution_id"] == "execution-1"
    assert contexts.commands[0]["execution_id"] == "execution-1"
    assert [item.outcome for item in runtime.completed] == ["released"] * 4
