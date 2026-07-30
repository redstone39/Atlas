from __future__ import annotations

import logging
import os
from socket import gethostname

from atlas_production.async_runtime.celery_app import celery_app
from atlas_production.async_runtime import workflows
from atlas_production.worker_composition import (
    DispatchWorkerComposition,
    IndexingWorkerComposition,
    JobTaskPort,
    MaintenanceWorkerComposition,
    ProcessingWorkerComposition,
    build_worker_composition,
)


logger = logging.getLogger(__name__)


def _worker_id(prefix: str) -> str:
    return f"{prefix}:{gethostname()}:{os.getpid()}"


def _composition(role: str):
    workflows.configure_postgres_worker_runtime()
    return build_worker_composition(role)


def _is_current_attempt(
    repository: JobTaskPort,
    task_name: str,
    identity: dict[str, str],
    attempt: int | None,
) -> bool:
    return repository.is_current_task_attempt(
        task_name=task_name, identity=identity, attempt=attempt
    )


def _mark_job_failure(
    repository: JobTaskPort,
    job_id: str,
    *,
    expected_attempt: int,
    worker_id: str,
    code: str,
    detail: str,
    transient: bool,
) -> None:
    if transient:
        raise ValueError("transient failure requires an explicit durable retry task")
    repository.fail_job(
        job_id, expected_attempt=expected_attempt, code=code, detail=detail
    )


def _failure_detail(exc: ValueError) -> str:
    cause = exc.__cause__
    return str(cause) if cause is not None else str(exc)


def _schedule_job_retry(
    repository: JobTaskPort,
    job_id: str,
    *,
    expected_attempt: int,
    worker_id: str,
    task_name: str,
    queue_name: str,
    payload: dict[str, str],
    code: str,
    detail: str,
) -> None:
    repository.schedule_retry(
        job_id,
        expected_attempt=expected_attempt,
        task_name=task_name,
        queue_name=queue_name,
        payload=payload,
        code=code,
        detail=detail,
    )


def _schedule_page_retry(
    repository: JobTaskPort,
    job_id: str,
    batch_id: str,
    *,
    expected_attempt: int,
    task_name: str,
    code: str,
) -> None:
    repository.schedule_page_batch_retry(
        job_id,
        batch_id,
        expected_attempt=expected_attempt,
        task_name=task_name,
        code=code,
    )


@celery_app.task(
    bind=True,
    name="atlas.dispatch.pending_outbox",
    acks_late=True,
    ignore_result=True,
)
def dispatch_pending_outbox(self) -> None:
    composition = _composition("dispatch")
    assert isinstance(composition, DispatchWorkerComposition)
    repository = composition.job
    owner = _worker_id("dispatch")
    for row in repository.claim_pending_outbox(owner):
        payload = dict(row["payload"])
        payload.pop("schema_version", None)
        try:
            celery_app.send_task(
                row["task_name"],
                kwargs=payload,
                queue=row["queue_name"],
                task_id=row["celery_task_id"],
            )
        except Exception as exc:
            repository.release_outbox(row["outbox_id"], owner, type(exc).__name__)
            continue
        repository.complete_outbox(row["outbox_id"], owner)


@celery_app.task(
    bind=True,
    name="atlas.processing.prepare_job",
    acks_late=True,
    ignore_result=True,
)
def prepare_job(self, job_id: str, attempt: int | None = None) -> None:
    composition = _composition("processing")
    assert isinstance(composition, ProcessingWorkerComposition)
    if not _is_current_attempt(
        composition.job,
        "atlas.processing.prepare_job", {"job_id": job_id}, attempt
    ):
        return
    try:
        composition.processing.prepare(job_id, attempt=int(attempt))
    except ValueError as exc:
        _mark_job_failure(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("prepare"),
            code=str(exc),
            detail=_failure_detail(exc),
            transient=False,
        )
    except Exception as exc:
        logger.exception(
            "processing prepare failed for job=%s attempt=%s",
            job_id,
            attempt,
        )
        _schedule_job_retry(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("prepare"),
            task_name="atlas.processing.prepare_job",
            queue_name="atlas.processing",
            payload={"job_id": job_id},
            code="processing_dependency_unavailable",
            detail=type(exc).__name__,
        )


@celery_app.task(
    bind=True,
    name="atlas.processing.process_batch",
    acks_late=True,
    ignore_result=True,
)
def process_batch(
    self, job_id: str, batch_id: str, attempt: int | None = None
) -> None:
    composition = _composition("processing")
    assert isinstance(composition, ProcessingWorkerComposition)
    if not _is_current_attempt(
        composition.job,
        "atlas.processing.process_batch",
        {"job_id": job_id, "batch_id": batch_id},
        attempt,
    ):
        return
    worker_id = _worker_id("processing")
    try:
        if not composition.processing.process_batch(
            job_id,
            batch_id,
            attempt=int(attempt),
        ):
            _schedule_page_retry(
                composition.job,
                job_id,
                batch_id,
                expected_attempt=int(attempt),
                task_name="atlas.processing.process_batch",
                code="processing_batch_not_committed",
            )
    except ValueError as exc:
        _mark_job_failure(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=worker_id,
            code=str(exc),
            detail=_failure_detail(exc),
            transient=False,
        )
    except Exception as exc:
        logger.exception(
            "processing batch failed for job=%s batch=%s attempt=%s",
            job_id,
            batch_id,
            attempt,
        )
        _schedule_job_retry(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=worker_id,
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload={"job_id": job_id, "batch_id": batch_id},
            code="processing_dependency_unavailable",
            detail=type(exc).__name__,
        )


@celery_app.task(
    bind=True,
    name="atlas.indexing.index_batch",
    acks_late=True,
    ignore_result=True,
    max_retries=None,
)
def index_batch(
    self, job_id: str, batch_id: str, attempt: int | None = None
) -> None:
    composition = _composition("indexing")
    assert isinstance(composition, IndexingWorkerComposition)
    if not _is_current_attempt(
        composition.job,
        "atlas.indexing.index_batch",
        {"job_id": job_id, "batch_id": batch_id},
        attempt,
    ):
        return
    try:
        if not composition.indexing.index_batch(
            job_id,
            batch_id,
            attempt=int(attempt),
        ):
            _schedule_page_retry(
                composition.job,
                job_id,
                batch_id,
                expected_attempt=int(attempt),
                task_name="atlas.indexing.index_batch",
                code="index_batch_not_committed",
            )
    except ValueError as exc:
        _mark_job_failure(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("indexing"),
            code=str(exc),
            detail=str(exc),
            transient=False,
        )
    except Exception as exc:
        logger.exception(
            "processing indexing failed for job=%s attempt=%s",
            job_id,
            attempt,
        )
        _schedule_job_retry(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("indexing"),
            task_name="atlas.indexing.index_batch",
            queue_name="atlas.indexing",
            payload={"job_id": job_id, "batch_id": batch_id},
            code="index_dependency_unavailable",
            detail=type(exc).__name__,
        )


@celery_app.task(
    bind=True,
    name="atlas.indexing.reindex_generation",
    acks_late=True,
    ignore_result=True,
    max_retries=None,
)
def reindex_generation(
    self, job_id: str, batch_id: str, attempt: int | None = None
) -> None:
    composition = _composition("indexing")
    assert isinstance(composition, IndexingWorkerComposition)
    if not _is_current_attempt(
        composition.job,
        "atlas.indexing.reindex_generation",
        {"job_id": job_id, "batch_id": batch_id},
        attempt,
    ):
        return
    try:
        composition.indexing.reindex_generation(
            job_id,
            batch_id,
            attempt=int(attempt),
        )
    except ValueError as exc:
        _mark_job_failure(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("reindex"),
            code=str(exc),
            detail=str(exc),
            transient=False,
        )
    except Exception as exc:
        _schedule_job_retry(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=_worker_id("reindex"),
            task_name="atlas.indexing.reindex_generation",
            queue_name="atlas.indexing",
            payload={"job_id": job_id, "batch_id": batch_id},
            code="index_dependency_unavailable",
            detail=type(exc).__name__,
        )


@celery_app.task(
    bind=True,
    name="atlas.processing.finalize_generation",
    acks_late=True,
    ignore_result=True,
)
def finalize_generation(self, job_id: str, attempt: int | None = None) -> None:
    composition = _composition("processing")
    assert isinstance(composition, ProcessingWorkerComposition)
    if not _is_current_attempt(
        composition.job,
        "atlas.processing.finalize_generation", {"job_id": job_id}, attempt
    ):
        return
    worker_id = _worker_id("publication")
    try:
        current = composition.job.get_job(job_id)
        if current is None or current.attempt != int(attempt):
            return
        if current.status in {"queued", "retry_wait"}:
            claimed = composition.job.claim_job(job_id, worker_id)
            if claimed is None or claimed[0].attempt != int(attempt):
                return
        outcome = composition.processing.finalize_generation(
            job_id,
            attempt=int(attempt),
        )
        if outcome != "published":
            _schedule_job_retry(
                composition.job,
                job_id,
                expected_attempt=int(attempt),
                worker_id=worker_id,
                task_name="atlas.processing.finalize_generation",
                queue_name="atlas.processing",
                payload={"job_id": job_id},
                code="publication_not_ready",
                detail=outcome,
            )
    except ValueError as exc:
        _mark_job_failure(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=worker_id,
            code=str(exc),
            detail=str(exc),
            transient=False,
        )
    except Exception as exc:
        logger.exception(
            "processing publication failed for job=%s attempt=%s",
            job_id,
            attempt,
        )
        _schedule_job_retry(
            composition.job,
            job_id,
            expected_attempt=int(attempt),
            worker_id=worker_id,
            task_name="atlas.processing.finalize_generation",
            queue_name="atlas.processing",
            payload={"job_id": job_id},
            code="publication_dependency_unavailable",
            detail=type(exc).__name__,
        )


@celery_app.task(name="atlas.maintenance.reconcile_jobs", ignore_result=True)
def reconcile_jobs() -> None:
    composition = _composition("maintenance")
    assert isinstance(composition, MaintenanceWorkerComposition)
    composition.job.reconcile_expired_claims()
    composition.job.reconcile_incomplete_page_batches(limit=100)
    dispatch_pending_outbox.apply_async(queue="atlas.dispatch")


@celery_app.task(name="atlas.maintenance.reconcile_storage", ignore_result=True)
def reconcile_storage() -> None:
    composition = _composition("maintenance")
    assert isinstance(composition, MaintenanceWorkerComposition)
    composition.artifact.reconcile_incomplete(
        worker_id=_worker_id("maintenance"), limit=100
    )


@celery_app.task(name="atlas.maintenance.cleanup_staging", ignore_result=True)
def cleanup_staging() -> None:
    composition = _composition("maintenance")
    assert isinstance(composition, MaintenanceWorkerComposition)
    composition.job.cleanup_staging()


@celery_app.task(name="atlas.maintenance.cleanup_old_index", ignore_result=True)
def cleanup_old_index() -> None:
    composition = _composition("maintenance")
    assert isinstance(composition, MaintenanceWorkerComposition)
    composition.indexing.cleanup_old_index(limit=100)
