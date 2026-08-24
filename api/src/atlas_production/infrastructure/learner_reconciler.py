from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from atlas_production.infrastructure.learner_provider import (
    LearnerProviderError,
    ProviderLearner,
)
from atlas_production.infrastructure.learner_source import (
    LearnerSource,
    LearnerSourceError,
)
from atlas_production.infrastructure.postgres_owner.learner import (
    LearnerClaimLost,
    LearnerConflict,
    PostgresLearnerOwner,
)
from atlas_production.modules.conversation_review.public import (
    ConversationReviewCursorV1,
    ConversationReviewOwner,
)
from atlas_production.modules.learner.public import (
    LearnerRunClaimV1,
    RegisterLearnerCaseV1,
)

logger = logging.getLogger(__name__)


class _ClaimHeartbeat:
    def __init__(
        self,
        *,
        owner: PostgresLearnerOwner,
        clock: Callable[[], datetime],
        lease_seconds: int,
        heartbeat_seconds: float,
    ) -> None:
        self._owner = owner
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._stop = Event()
        self._lost = Event()
        self._lock = Lock()
        self._claim: LearnerRunClaimV1 | None = None
        self._thread: Thread | None = None

    def start(self, claim: LearnerRunClaimV1) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("learner heartbeat already started")
            self._claim = claim
            self._thread = Thread(
                target=self._run,
                name="atlas-learner-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def latest_claim(self) -> LearnerRunClaimV1 | None:
        with self._lock:
            return self._claim

    def _run(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            with self._lock:
                claim = self._claim
            if claim is None:
                self._lost.set()
                return
            try:
                renewed = self._owner.renew_claim(
                    claim,
                    self._clock(),
                    lease_seconds=self._lease_seconds,
                )
            except Exception:
                self._lost.set()
                return
            with self._lock:
                self._claim = renewed


class LearnerReconciler:
    """Process-local bounded Review scan over durable Learner claim authority."""

    def __init__(
        self,
        *,
        reviews: ConversationReviewOwner,
        learners: PostgresLearnerOwner,
        source: LearnerSource,
        provider: ProviderLearner,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 5.0,
        batch_size: int = 10,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("learner interval must be positive")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("learner batch size must be between 1 and 100")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("learner lease must be between 1 and 3600 seconds")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("learner heartbeat must be positive and below lease")
        self._reviews = reviews
        self._learners = learners
        self._source = source
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._worker_id = worker_id or f"learner-{uuid4().hex}"
        self._review_cursor: ConversationReviewCursorV1 | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def review_cursor(self) -> ConversationReviewCursorV1 | None:
        return self._review_cursor

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._stop.is_set():
            return
        self.run_once()
        self._thread = Thread(
            target=self._run,
            name="atlas-learner-reconciler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                logger.warning(
                    "learner_reconciliation_failed failure_code=%s",
                    "learner_provider_unavailable",
                )
    def run_once(self) -> int:
        if self._stop.is_set():
            return 0
        try:
            settings = self._reviews.get_learning_settings()
        except Exception:
            logger.warning(
                "conversation_learning_admission_failed "
                "failure_code=conversation_learning_settings_unavailable"
            )
            return 0
        if not settings.enabled:
            return 0
        self._discover()
        observed_at = self._clock()
        claim = self._learners.claim_next(
            self._worker_id,
            observed_at,
            lease_seconds=self._lease_seconds,
        )
        if claim is None:
            return 0
        try:
            packet = self._source.assemble(claim)
        except LearnerSourceError as exc:
            return self._fail_claim(claim, exc.code, retryable=exc.retryable)
        except LearnerClaimLost:
            return 0
        except Exception:
            return self._fail_claim(
                claim, "learner_source_unavailable", retryable=True
            )

        heartbeat = _ClaimHeartbeat(
            owner=self._learners,
            clock=self._clock,
            lease_seconds=self._lease_seconds,
            heartbeat_seconds=self._heartbeat_seconds,
        )
        try:
            result = self._provider.learn(
                claim,
                packet,
                observed_at=self._clock(),
                on_claim_pinned=heartbeat.start,
            )
        except LearnerProviderError as exc:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_claim(current, exc.code, retryable=exc.retryable)
        except LearnerClaimLost:
            heartbeat.stop()
            return 0
        except Exception:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_claim(
                current, "learner_provider_unavailable", retryable=True
            )
        heartbeat.stop()
        if heartbeat.lost:
            return 0
        current = heartbeat.latest_claim or result.claim
        try:
            self._learners.complete(current, result.payload, self._clock())
            return 1
        except LearnerClaimLost:
            return 0
        except LearnerConflict as exc:
            code = str(exc)
            allowed = {
                "learner_experience_payload_invalid",
                "learner_source_identity_mismatch",
                "model_route_revision_conflict",
            }
            safe_code = code if code in allowed else "learner_experience_payload_invalid"
            return self._fail_claim(
                current,
                safe_code,
                retryable=safe_code == "model_route_revision_conflict",
            )
        except Exception:
            return self._fail_claim(
                current,
                "learner_provider_unavailable",
                retryable=True,
            )

    def _discover(self) -> None:
        reviews = self._reviews.list_after(self._review_cursor, self._batch_size)
        if not reviews:
            self._review_cursor = None
            return
        for review in reviews:
            if review.scan_sequence is None:
                logger.warning(
                    "learner_registration_failed review_ref=%s failure_code=%s",
                    review.snapshot.review_ref,
                    "learner_source_identity_mismatch",
                )
                continue
            self._review_cursor = ConversationReviewCursorV1(
                scan_sequence=review.scan_sequence,
                review_ref=review.snapshot.review_ref,
            )
            if review.status != "completed":
                continue
            if review.review_digest is None:
                logger.warning(
                    "learner_registration_failed review_ref=%s failure_code=%s",
                    review.snapshot.review_ref,
                    "learner_source_digest_mismatch",
                )
                continue
            for case in review.cases:
                try:
                    self._learners.register_case(
                        RegisterLearnerCaseV1(
                            review_ref=review.snapshot.review_ref,
                            review_digest=review.review_digest,
                            snapshot_digest=review.snapshot.snapshot_digest,
                            case=case,
                        )
                    )
                except Exception:
                    logger.warning(
                        "learner_registration_failed review_ref=%s failure_code=%s",
                        review.snapshot.review_ref,
                        "learner_source_unavailable",
                    )
        if len(reviews) < self._batch_size:
            self._review_cursor = None

    def _fail_claim(
        self, claim: LearnerRunClaimV1, code: str, *, retryable: bool
    ) -> int:
        try:
            self._learners.fail(claim, code, retryable, self._clock())
        except LearnerClaimLost:
            return 0
        except Exception:
            logger.warning(
                "learner_reconciliation_failed run_ref=%s experience_ref=%s failure_code=%s",
                claim.run_ref,
                claim.experience_ref,
                code,
            )
            return 0
        logger.warning(
            "learner_reconciliation_failed run_ref=%s experience_ref=%s failure_code=%s",
            claim.run_ref,
            claim.experience_ref,
            code,
        )
        return 1


__all__ = ["LearnerReconciler"]
