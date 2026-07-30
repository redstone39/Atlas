"""Fail-safe idempotent release saga for strict-turn leased resources."""

from __future__ import annotations

from threading import Event, Thread

from atlas_production.modules.authorization.public import (
    AuthorizationOwner,
    ReleaseTurnAccessGrantV1,
)
from atlas_production.modules.context_engineering.public import (
    ContextEngineeringOwner,
    ReleaseContextPackV3,
)
from atlas_production.modules.retrieval.public import RetrievalOwner
from atlas_production.modules.processing_pipeline.public import (
    GenerationRetentionOwner,
    ReleaseGenerationRetentionV1,
)
from atlas_production.modules.turn_runtime.public import (
    CompleteReleaseIntentV1,
    ReleaseIntentV1,
    TurnRuntimeOwner,
)


class TurnResourceReleaseReconciler:
    """Claims runtime-owned intents, then calls exactly one owner without locks."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeOwner,
        authorization: AuthorizationOwner,
        retrieval: RetrievalOwner,
        generation_retention: GenerationRetentionOwner,
        contexts: ContextEngineeringOwner,
        interval_seconds: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        self._runtime = runtime
        self._authorization = authorization
        self._retrieval = retrieval
        self._generation_retention = generation_retention
        self._contexts = contexts
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._stop = Event()

    def start(self) -> None:
        self.run_once()
        Thread(
            target=self._run,
            name="atlas-turn-resource-release-reconciler",
            daemon=True,
        ).start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.run_once()

    def run_once(self) -> int:
        intents = self._runtime.pending_release_intents(limit=self._batch_size)
        for intent in intents:
            try:
                self._release(intent)
            except Exception as error:
                self._runtime.complete_release_intent(
                    CompleteReleaseIntentV1(
                        release_intent_id=intent.release_intent_id,
                        expected_status="releasing",
                        outcome="failed",
                        failure_code=type(error).__name__[:100] or "release_failed",
                    )
                )
            else:
                self._runtime.complete_release_intent(
                    CompleteReleaseIntentV1(
                        release_intent_id=intent.release_intent_id,
                        expected_status="releasing",
                        outcome="released",
                    )
                )
        return len(intents)

    def _release(self, intent: ReleaseIntentV1) -> None:
        key = intent.release_intent_id
        staged = intent.resource_ref == (
            f"execution-resource:{intent.resource_owner}:{intent.execution_id}"
        )
        if intent.resource_owner == "authorization" and intent.release_kind == "release_turn_grant":
            if staged:
                self._authorization.release_execution_grant(
                    execution_id=intent.execution_id,
                    idempotency_key=key,
                )
                return
            self._authorization.release_grant(
                ReleaseTurnAccessGrantV1(
                    execution_id=intent.execution_id,
                    grant_ref=intent.resource_ref,
                    idempotency_key=key,
                )
            )
            return
        if intent.resource_owner == "retrieval" and intent.release_kind == "release_knowledge_catalog":
            if staged:
                self._retrieval.release_execution_catalog(
                    execution_id=intent.execution_id,
                    idempotency_key=key,
                )
                return
            self._retrieval.release_catalog(
                execution_id=intent.execution_id,
                catalog_ref=intent.resource_ref,
                idempotency_key=key,
            )
            return
        if (
            intent.resource_owner == "processing_pipeline"
            and intent.release_kind == "release_generation_retention"
        ):
            if staged:
                self._generation_retention.release_execution_generation_retention(
                    execution_id=intent.execution_id,
                    idempotency_key=key,
                )
                return
            self._generation_retention.release_generation_retention(
                ReleaseGenerationRetentionV1(
                    execution_id=intent.execution_id,
                    retention_ref=intent.resource_ref,
                    idempotency_key=key,
                )
            )
            return
        if intent.resource_owner == "context_engineering" and intent.release_kind == "release_context_pack":
            if staged:
                self._contexts.release_execution_context(
                    execution_id=intent.execution_id,
                    idempotency_key=key,
                )
                return
            self._contexts.release(
                ReleaseContextPackV3(
                    release_ref=f"context-release-{key}",
                    execution_id=intent.execution_id,
                    context_pack_ref=intent.resource_ref,
                    idempotency_key=key,
                )
            )
            return
        raise ValueError("unsupported turn release intent")


__all__ = ["TurnResourceReleaseReconciler"]
