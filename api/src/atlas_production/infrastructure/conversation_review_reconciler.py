from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from atlas_production.infrastructure.conversation_review_source import (
    ConversationReviewPublicationCoordinator,
    ConversationReviewSource,
    ConversationReviewSourceError,
)
from atlas_production.infrastructure.conversation_reviewer import (
    ConversationReviewerError,
    ProviderConversationReviewer,
)
from atlas_production.infrastructure.postgres_owner.conversation_review import (
    ConversationReviewClaimLost,
    PostgresConversationReviewOwner,
)
from atlas_production.modules.conversation.public import (
    ConversationOwner,
    ConversationScanCursorV1,
)
from atlas_production.modules.conversation_review.public import (
    ConversationReviewClaimV1,
)


logger = logging.getLogger(__name__)


class _ClaimHeartbeat:
    def __init__(
        self,
        *,
        owner: PostgresConversationReviewOwner,
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
        self._claim: ConversationReviewClaimV1 | None = None
        self._thread: Thread | None = None

    def start(self, claim: ConversationReviewClaimV1) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("conversation review heartbeat already started")
            self._claim = claim
            self._thread = Thread(
                target=self._run,
                name="atlas-conversation-review-heartbeat",
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
    def latest_claim(self) -> ConversationReviewClaimV1 | None:
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


class ConversationReviewReconciler:
    """Process-local bounded discovery over durable Review claim authority."""

    def __init__(
        self,
        *,
        conversations: ConversationOwner,
        source: ConversationReviewSource,
        reviews: PostgresConversationReviewOwner,
        reviewer: ProviderConversationReviewer,
        publication: ConversationReviewPublicationCoordinator,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 5.0,
        batch_size: int = 1,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("conversation review interval must be positive")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("conversation review batch size must be between 1 and 100")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("conversation review lease must be between 1 and 3600 seconds")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("conversation review heartbeat must be positive and below lease")
        self._conversations = conversations
        self._source = source
        self._reviews = reviews
        self._reviewer = reviewer
        self._publication = publication
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._worker_id = worker_id or f"conversation-review-{uuid4().hex}"
        self._candidate_cursor: ConversationScanCursorV1 | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def candidate_cursor(self) -> ConversationScanCursorV1 | None:
        return self._candidate_cursor

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self.run_once()
        self._thread = Thread(
            target=self._run,
            name="atlas-conversation-review-reconciler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.run_once()

    def run_once(self) -> int:
        observed_at = self._clock()
        self._discover(observed_at)
        claim = self._reviews.claim_next(
            self._worker_id,
            observed_at,
            lease_seconds=self._lease_seconds,
        )
        if claim is None:
            return 0
        review = self._reviews.read(claim.review_ref)
        if review is None:
            return self._fail_claim(
                claim,
                "conversation_review_snapshot_missing",
                retryable=False,
            )
        try:
            transcript = self._source.rehydrate(review.snapshot)
        except ConversationReviewSourceError as exc:
            return self._fail_claim(claim, exc.code, retryable=False)
        heartbeat = _ClaimHeartbeat(
            owner=self._reviews,
            clock=self._clock,
            lease_seconds=self._lease_seconds,
            heartbeat_seconds=self._heartbeat_seconds,
        )
        try:
            result = self._reviewer.review(
                claim,
                transcript,
                observed_at=self._clock(),
                on_claim_pinned=heartbeat.start,
            )
        except ConversationReviewerError as exc:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_claim(current, exc.code, retryable=exc.retryable)
        except ConversationReviewClaimLost:
            heartbeat.stop()
            return 0
        except Exception:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_claim(
                current,
                "conversation_review_provider_unavailable",
                retryable=True,
            )
        heartbeat.stop()
        if heartbeat.lost:
            return 0
        current = heartbeat.latest_claim or result.claim
        try:
            self._publication.finalize(
                current,
                result.proposal,
                list(result.model_invocation_refs),
                observed_at=self._clock(),
            )
            return 1
        except ConversationReviewClaimLost:
            return 0

    def _discover(self, observed_at: datetime) -> None:
        candidates = self._conversations.list_active_updated_before(
            cutoff=observed_at - timedelta(hours=2),
            after=self._candidate_cursor,
            limit=self._batch_size,
        )
        if not candidates:
            self._candidate_cursor = None
            return
        for candidate in candidates:
            try:
                self._source.assemble_and_register(
                    candidate.conversation_id, observed_at=observed_at
                )
            except ConversationReviewSourceError as exc:
                logger.warning(
                    "conversation_review_discovery_failed failure_code=%s",
                    exc.code,
                )
            except Exception:
                logger.warning(
                    "conversation_review_discovery_failed failure_code=%s",
                    "conversation_review_source_unavailable",
                )
            self._candidate_cursor = ConversationScanCursorV1(
                updated_at=candidate.updated_at,
                conversation_id=candidate.conversation_id,
            )
        if len(candidates) < self._batch_size:
            self._candidate_cursor = None

    def _fail_claim(
        self, claim: ConversationReviewClaimV1, code: str, *, retryable: bool
    ) -> int:
        try:
            self._reviews.fail(
                claim,
                code,
                retryable,
                self._clock(),
            )
        except ConversationReviewClaimLost:
            return 0
        logger.warning(
            "conversation_review_reconciliation_failed review_ref=%s failure_code=%s",
            claim.review_ref,
            code,
        )
        return 1


__all__ = ["ConversationReviewReconciler"]
