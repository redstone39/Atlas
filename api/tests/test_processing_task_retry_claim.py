from __future__ import annotations

from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from atlas_production.async_runtime import tasks, workflows


@pytest.mark.parametrize("status, expected_claims", [("retry_wait", 1), ("queued", 1), ("running", 0)])
def test_finalize_generation_reclaims_a_durable_retry_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_claims: int,
) -> None:
    calls: list[tuple[str, str]] = []

    class JobPort:
        def get_job(self, job_id: str):
            calls.append(("get", job_id))
            return SimpleNamespace(status=status, attempt=3)

        def claim_job(self, job_id: str, worker_id: str):
            calls.append(("claim", worker_id))
            return SimpleNamespace(status="running", attempt=3), 7

    class ProcessingPort:
        def finalize_generation(self, job_id: str, *, attempt: int):
            calls.append(("finalize", job_id))
            return "published"

    class FakeComposition:
        job = JobPort()
        processing = ProcessingPort()

    monkeypatch.setattr(tasks, "ProcessingWorkerComposition", FakeComposition)
    monkeypatch.setattr(tasks, "_composition", lambda _kind: FakeComposition())
    monkeypatch.setattr(tasks, "_is_current_attempt", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "_worker_id", lambda _kind: "publication-worker")

    tasks.finalize_generation.run("job-1", attempt=3)

    assert sum(name == "claim" for name, _value in calls) == expected_claims
    assert calls[-1] == ("finalize", "job-1")


@pytest.mark.parametrize(
    "task_name,composition_name,port_name",
    (
        ("process_batch", "ProcessingWorkerComposition", "processing"),
        ("index_batch", "IndexingWorkerComposition", "indexing"),
    ),
)
def test_page_currentness_candidate_schedules_only_the_page_successor(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    composition_name: str,
    port_name: str,
) -> None:
    calls: list[tuple[str, object]] = []

    class JobPort:
        def schedule_page_batch_retry(self, job_id, batch_id, **values):
            calls.append(("page_retry", (job_id, batch_id, values)))
            return True

        def schedule_retry(self, *_args, **_kwargs):
            pytest.fail("page currentness must not schedule a job retry")

    class PagePort:
        def process_batch(self, *_args, **_kwargs):
            return False

        def index_batch(self, *_args, **_kwargs):
            return False

    class FakeComposition:
        job = JobPort()
        processing = PagePort()
        indexing = PagePort()

    monkeypatch.setattr(tasks, composition_name, FakeComposition)
    monkeypatch.setattr(tasks, "_composition", lambda _kind: FakeComposition())
    monkeypatch.setattr(tasks, "_is_current_attempt", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "_worker_id", lambda _kind: "worker")

    getattr(tasks, task_name).run("job-1", "job-1:page:20", attempt=3)

    assert calls == [
        (
            "page_retry",
            (
                "job-1",
                "job-1:page:20",
                {
                    "expected_attempt": 3,
                    "task_name": (
                        "atlas.processing.process_batch"
                        if port_name == "processing"
                        else "atlas.indexing.index_batch"
                    ),
                    "code": (
                        "processing_batch_not_committed"
                        if port_name == "processing"
                        else "index_batch_not_committed"
                    ),
                },
            ),
        )
    ]


def test_checkpointed_page_with_index_enqueue_conflict_retries_index_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    claimed = SimpleNamespace(attempt=3, status="running")

    class Repository:
        @contextmanager
        def batch_execution(self, _job_id, _batch_id):
            yield claimed

        def checkpoint_for_batch(self, _job_id, _batch_id):
            return {"output_digest": "digest"}

        def enqueue_index_batch(self, *_args, **_kwargs):
            return False

        def schedule_page_batch_retry(self, job_id, batch_id, **values):
            calls.append(("page_retry", (job_id, batch_id, values)))
            return True

    repository = Repository()
    monkeypatch.setattr(workflows, "worker_repository", lambda: repository)
    monkeypatch.setattr(
        workflows,
        "_processing_document",
        lambda *_args, **_kwargs: (
            repository,
            claimed,
            SimpleNamespace(),
            "application/pdf",
        ),
    )

    assert workflows.process_batch("job-1", "job-1:page:20", 3)
    assert calls == [
        (
            "page_retry",
            (
                "job-1",
                "job-1:page:20",
                {
                    "expected_attempt": 3,
                    "task_name": "atlas.indexing.index_batch",
                    "code": "index_batch_not_queued",
                },
            ),
        )
    ]
