from __future__ import annotations

import pytest

from atlas_production.infrastructure.postgres_owner import document_processing


def test_processing_acceptance_relies_on_session_exit_for_failure_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class JoinedSession:
        def __enter__(self) -> "JoinedSession":
            return self

        def __exit__(self, exc_type, exc, traceback) -> bool:
            self.rollback()
            return False

        def scalar(self, _statement):
            raise RuntimeError("public-synthetic-acceptance-failure")

        def rollback(self) -> None:
            events.append("rollback")

    def joined_session(*, bind, join_transaction_mode):
        assert bind is external_connection
        assert join_transaction_mode == "rollback_only"
        return JoinedSession()

    external_connection = object()
    monkeypatch.setattr(document_processing, "Session", joined_session)
    monkeypatch.setattr(
        document_processing,
        "acquire_mixed_owner_locks",
        lambda *_args, **_kwargs: None,
    )

    command = document_processing.AcceptProcessingExecutionCommand(
        lambda: pytest.fail("opened an owner session")
    )
    with pytest.raises(RuntimeError, match="public-synthetic-acceptance-failure"):
        command.accept_job(
            media_type="application/pdf",
            document_id="public-synthetic-document",
            document_version_id="public-synthetic-version",
            job_kind="ingest",
            idempotency_scope="public-synthetic-scope",
            idempotency_key="public-synthetic-key",
            created_by="public-synthetic-user",
            connection=external_connection,
            execution_snapshot=object(),  # type: ignore[arg-type]
        )

    assert events == ["rollback"]
