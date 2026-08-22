from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from atlas_production.infrastructure.consolidator_provider import (
    ConsolidatorProviderError,
    ProviderConsolidator,
)
from atlas_production.infrastructure.postgres_owner.consolidator import (
    ConsolidatorClaimLost,
    ConsolidatorConflict,
    PostgresConsolidatorOwner,
)
from atlas_production.infrastructure.postgres_owner.skill_designer import (
    PostgresSkillDesignerOwner,
    SkillDesignerClaimLost,
    SkillDesignerConflict,
)
from atlas_production.infrastructure.skill_designer_provider import (
    ProviderSkillDesigner,
    SkillDesignerProviderError,
    load_skill_designer_catalog_context,
)
from atlas_production.modules.consolidator.public import ConsolidationRunClaimV1
from atlas_production.modules.learner.public import LearnerExperienceReader
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalog,
    PromptSkillExactReader,
)
from atlas_production.modules.skill_designer.public import SkillDesignRunClaimV1

logger = logging.getLogger(__name__)


class _ConsolidationHeartbeat:
    def __init__(
        self,
        *,
        owner: PostgresConsolidatorOwner,
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
        self._claim: ConsolidationRunClaimV1 | None = None
        self._thread: Thread | None = None

    def start(self, claim: ConsolidationRunClaimV1) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("consolidation heartbeat already started")
            self._claim = claim
            self._thread = Thread(
                target=self._run,
                name="atlas-consolidation-heartbeat",
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
    def latest_claim(self) -> ConsolidationRunClaimV1 | None:
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


class _SkillDesignHeartbeat:
    def __init__(
        self,
        *,
        owner: PostgresSkillDesignerOwner,
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
        self._claim: SkillDesignRunClaimV1 | None = None
        self._thread: Thread | None = None

    def start(self, claim: SkillDesignRunClaimV1) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("Skill Designer heartbeat already started")
            self._claim = claim
            self._thread = Thread(
                target=self._run,
                name="atlas-skill-designer-heartbeat",
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
    def latest_claim(self) -> SkillDesignRunClaimV1 | None:
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


class SkillCandidatePipelineReconciler:
    """Single process-local carrier over durable candidate-pipeline owners."""

    def __init__(
        self,
        *,
        learner_experiences: LearnerExperienceReader,
        consolidations: PostgresConsolidatorOwner,
        consolidator: ProviderConsolidator,
        designs: PostgresSkillDesignerOwner | None = None,
        designer: ProviderSkillDesigner | None = None,
        skill_catalogs: PromptSkillCatalog | None = None,
        skill_exact_reader: PromptSkillExactReader | None = None,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 5.0,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("candidate pipeline interval must be positive")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("candidate pipeline lease must be between 1 and 3600 seconds")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("candidate pipeline heartbeat must be positive and below lease")
        designer_dependencies = (
            designs,
            designer,
            skill_catalogs,
            skill_exact_reader,
        )
        if any(value is None for value in designer_dependencies) and not all(
            value is None for value in designer_dependencies
        ):
            raise ValueError("Skill Designer dependencies must be wholly present or absent")
        self._learner_experiences = learner_experiences
        self._consolidations = consolidations
        self._consolidator = consolidator
        self._designs = designs
        self._designer = designer
        self._skill_catalogs = skill_catalogs
        self._skill_exact_reader = skill_exact_reader
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._interval_seconds = interval_seconds
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._worker_id = worker_id or f"skill-candidate-pipeline-{uuid4().hex}"
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._stop.is_set():
            return
        self.run_once()
        self._thread = Thread(
            target=self._run,
            name="atlas-skill-candidate-pipeline",
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
                    "skill_candidate_pipeline_failed phase=%s failure_code=%s",
                    "carrier",
                    "skill_candidate_pipeline_unavailable",
                )

    def run_once(self) -> int:
        if self._stop.is_set():
            return 0
        completed = self._run_consolidator_once()
        if self._designs is not None:
            completed += self._run_designer_once()
        return completed

    def _run_consolidator_once(self) -> int:
        observed_at = self._clock()
        try:
            self._consolidations.reserve_next(
                self._learner_experiences, observed_at
            )
        except Exception:
            logger.warning(
                "skill_candidate_pipeline_failed phase=%s failure_code=%s",
                "consolidator",
                "consolidation_source_unavailable",
            )
            return 0

        claim = self._consolidations.claim_next(
            self._worker_id,
            self._clock(),
            lease_seconds=self._lease_seconds,
        )
        if claim is None:
            return 0
        try:
            source_experiences = self._consolidations.read_source_experiences(
                claim, self._learner_experiences
            )
        except ConsolidatorClaimLost:
            return 0
        except ConsolidatorConflict as exc:
            code = str(exc)
            retryable = code == "consolidation_source_unavailable"
            return self._fail_consolidation(claim, code, retryable=retryable)
        except Exception:
            return self._fail_consolidation(
                claim, "consolidation_source_unavailable", retryable=True
            )

        heartbeat = _ConsolidationHeartbeat(
            owner=self._consolidations,
            clock=self._clock,
            lease_seconds=self._lease_seconds,
            heartbeat_seconds=self._heartbeat_seconds,
        )
        try:
            result = self._consolidator.consolidate(
                claim,
                source_experiences,
                observed_at=self._clock(),
                on_claim_pinned=heartbeat.start,
            )
        except ConsolidatorProviderError as exc:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_consolidation(
                current, exc.code, retryable=exc.retryable
            )
        except ConsolidatorClaimLost:
            heartbeat.stop()
            return 0
        except Exception:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_consolidation(
                current, "consolidator_provider_unavailable", retryable=True
            )
        heartbeat.stop()
        if heartbeat.lost:
            return 0
        current = heartbeat.latest_claim or result.claim
        try:
            self._consolidations.complete(
                current,
                result.experiences,
                result.model_invocation_refs,
                self._clock(),
            )
            return 1
        except ConsolidatorClaimLost:
            return 0
        except ConsolidatorConflict as exc:
            code = str(exc)
            retryable = code == "model_route_revision_conflict"
            safe = (
                code
                if code
                in {
                    "consolidation_payload_invalid",
                    "consolidation_invocation_provenance_invalid",
                    "model_route_revision_conflict",
                }
                else "consolidation_payload_invalid"
            )
            return self._fail_consolidation(current, safe, retryable=retryable)
        except Exception:
            return self._fail_consolidation(
                current, "consolidator_provider_unavailable", retryable=True
            )

    def _run_designer_once(self) -> int:
        assert self._designs is not None
        assert self._designer is not None
        assert self._skill_catalogs is not None
        assert self._skill_exact_reader is not None
        try:
            self._designs.register_completed_after(self._consolidations, None, 100)
        except Exception:
            logger.warning(
                "skill_candidate_pipeline_failed phase=%s failure_code=%s",
                "skill_designer",
                "skill_design_source_unavailable",
            )
            return 0
        claim = self._designs.claim_next(
            self._worker_id,
            self._clock(),
            lease_seconds=self._lease_seconds,
        )
        if claim is None:
            return 0
        try:
            consolidation = self._consolidations.read_consolidation(
                claim.source.consolidation_ref
            )
            if (
                consolidation is None
                or consolidation.digest != claim.source.consolidation_digest
                or consolidation.scan_sequence
                != claim.source.consolidation_scan_sequence
            ):
                return self._fail_design(
                    claim, "skill_design_source_integrity_conflict", retryable=False
                )
            context = load_skill_designer_catalog_context(
                self._skill_catalogs, self._skill_exact_reader
            )
        except SkillDesignerProviderError as exc:
            return self._fail_design(claim, exc.code, retryable=exc.retryable)
        except Exception:
            return self._fail_design(
                claim, "skill_design_source_unavailable", retryable=True
            )

        heartbeat = _SkillDesignHeartbeat(
            owner=self._designs,
            clock=self._clock,
            lease_seconds=self._lease_seconds,
            heartbeat_seconds=self._heartbeat_seconds,
        )
        try:
            result = self._designer.design(
                claim,
                consolidation,
                context,
                observed_at=self._clock(),
                on_claim_pinned=heartbeat.start,
            )
        except SkillDesignerProviderError as exc:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_design(current, exc.code, retryable=exc.retryable)
        except SkillDesignerClaimLost:
            heartbeat.stop()
            return 0
        except Exception:
            heartbeat.stop()
            if heartbeat.lost:
                return 0
            current = heartbeat.latest_claim or claim
            return self._fail_design(
                current, "skill_designer_provider_unavailable", retryable=True
            )
        heartbeat.stop()
        if heartbeat.lost:
            return 0
        current = heartbeat.latest_claim or result.claim
        try:
            self._designs.complete(
                current,
                result.drafts,
                result.model_invocation_refs,
                self._clock(),
            )
            return 1
        except SkillDesignerClaimLost:
            return 0
        except SkillDesignerConflict as exc:
            code = str(exc)
            retryable = code in {
                "model_route_revision_conflict",
                "skill_candidate_apply_in_progress",
            }
            safe = (
                code
                if code
                in {
                    "model_route_revision_conflict",
                    "skill_candidate_apply_in_progress",
                    "skill_candidate_duplicate_draft_key",
                    "skill_design_invocation_provenance_invalid",
                    "skill_candidate_identity_conflict",
                    "skill_candidate_payload_invalid",
                }
                else "skill_candidate_payload_invalid"
            )
            return self._fail_design(current, safe, retryable=retryable)
        except Exception:
            return self._fail_design(
                current, "skill_designer_provider_unavailable", retryable=True
            )

    def _fail_consolidation(
        self,
        claim: ConsolidationRunClaimV1,
        code: str,
        *,
        retryable: bool,
    ) -> int:
        safe_code = code if 0 < len(code) <= 100 else "consolidation_failure"
        try:
            self._consolidations.fail(
                claim,
                safe_code,
                retryable,
                self._clock(),
            )
        except ConsolidatorClaimLost:
            return 0
        except Exception:
            logger.warning(
                "skill_candidate_pipeline_failed phase=%s consolidation_ref=%s failure_code=%s",
                "consolidator",
                claim.consolidation_ref,
                safe_code,
            )
            return 0
        logger.warning(
            "skill_candidate_pipeline_failed phase=%s consolidation_ref=%s failure_code=%s",
            "consolidator",
            claim.consolidation_ref,
            safe_code,
        )
        return 1

    def _fail_design(
        self,
        claim: SkillDesignRunClaimV1,
        code: str,
        *,
        retryable: bool,
    ) -> int:
        safe_code = code if 0 < len(code) <= 100 else "skill_design_failure"
        assert self._designs is not None
        try:
            self._designs.fail(
                claim,
                safe_code,
                retryable,
                self._clock(),
            )
        except SkillDesignerClaimLost:
            return 0
        except Exception:
            logger.warning(
                "skill_candidate_pipeline_failed phase=%s run_ref=%s failure_code=%s",
                "skill_designer",
                claim.run_ref,
                safe_code,
            )
            return 0
        logger.warning(
            "skill_candidate_pipeline_failed phase=%s run_ref=%s failure_code=%s",
            "skill_designer",
            claim.run_ref,
            safe_code,
        )
        return 1


__all__ = ["SkillCandidatePipelineReconciler"]
