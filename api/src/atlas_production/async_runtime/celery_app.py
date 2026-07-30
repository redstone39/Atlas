from __future__ import annotations

import os

from celery import Celery
from celery.signals import beat_init
from kombu import Queue

from atlas_production.worker_composition import (
    BeatWorkerComposition,
    build_worker_composition,
)

BROKER_URL = os.getenv("ATLAS_REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "atlas",
    broker=BROKER_URL,
    backend=None,
    include=["atlas_production.async_runtime.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_default_exchange="atlas",
    task_default_exchange_type="direct",
    task_default_routing_key="atlas.processing",
    task_queues=tuple(
        Queue(name, routing_key=name)
        for name in (
            "atlas.dispatch",
            "atlas.processing",
            "atlas.indexing",
            "atlas.maintenance",
        )
    ),
    task_routes={
        "atlas.dispatch.*": {"queue": "atlas.dispatch"},
        "atlas.processing.*": {"queue": "atlas.processing"},
        "atlas.indexing.*": {"queue": "atlas.indexing"},
        "atlas.maintenance.*": {"queue": "atlas.maintenance"},
    },
    beat_schedule={
        "dispatch-pending-outbox": {
            "task": "atlas.dispatch.pending_outbox",
            "schedule": 5.0,
        },
        "reconcile-processing": {
            "task": "atlas.maintenance.reconcile_jobs",
            "schedule": 30.0,
        },
        "cleanup-orphans": {
            "task": "atlas.maintenance.reconcile_storage",
            "schedule": 300.0,
        },
        "cleanup-staging": {
            "task": "atlas.maintenance.cleanup_staging",
            "schedule": 600.0,
        },
        "cleanup-old-index": {
            "task": "atlas.maintenance.cleanup_old_index",
            "schedule": 600.0,
        },
    },
)


@beat_init.connect
def _build_beat_composition(**_kwargs: object) -> None:
    """Validate the scheduler role without constructing database task ports."""

    composition = build_worker_composition("beat")
    if not isinstance(composition, BeatWorkerComposition):
        raise RuntimeError("beat composition is cross-wired")
