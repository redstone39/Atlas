from __future__ import annotations

import ast
from dataclasses import asdict, replace
from datetime import datetime, timezone
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.postgres_owner import document_processing as owner


def test_public_surface_is_five_named_commands_without_generic_escape_hatch() -> None:
    assert set(owner.__all__) >= {
        "DocumentMutationCommand",
        "JobTransitionCommand",
        "OutboxDeliveryCommand",
        "BatchCheckpointCommand",
        "FinalGenerationPublicationCommand",
    }
    source = inspect.getsource(owner)
    tree = ast.parse(source)
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert "DocumentProcessingRepository" not in source
    assert "DocumentProcessingChangeSet" not in source
    assert "_DocumentProcessingSqlPrimitives" not in source
    assert "_DocumentMutationSpec" not in source
    assert "_publish_document_processing_graph" not in source
    assert "publish_graph" not in {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "DocumentMutationCommand",
        "JobTransitionCommand",
        "OutboxDeliveryCommand",
        "BatchCheckpointCommand",
        "FinalGenerationPublicationCommand",
    }.issubset(public_classes)
    for command_type in (
        owner.DocumentMutationCommand,
        owner.JobTransitionCommand,
        owner.OutboxDeliveryCommand,
        owner.BatchCheckpointCommand,
        owner.FinalGenerationPublicationCommand,
    ):
        assert not hasattr(command_type, "_implementation")
    assert not hasattr(owner.JobTransitionCommand, "claim_pending_outbox")
    assert not hasattr(owner.OutboxDeliveryCommand, "claim_job")
    assert not hasattr(owner.BatchCheckpointCommand, "publish_job")
    assert not hasattr(owner.FinalGenerationPublicationCommand, "commit_checkpoint")


def test_stable_artifact_provider_has_no_legacy_writer_dependency() -> None:
    source = inspect.getsource(owner)
    assert "ArtifactMetadataWriter" not in source
    assert "GenerationArtifactPublicationGraphReader" not in source
    assert "validate_artifact_metadata_graph" not in source
    assert "FinalizeArtifactWriteCommand" in source


def test_product_bounds_preserve_3000_pages_and_6002_outboxes() -> None:
    assert owner._MAX_PROCESSING_PAGE_COUNT == 3_000
    assert owner._MAX_RETRY_CHECKPOINT_ROWS == 3_000
    assert owner._MAX_CURRENT_ATTEMPT_OUTBOX_ROWS == 6_002
    owner._validate_progress_total_bound(3_000)
    with pytest.raises(ValueError, match="supported page limit"):
        owner._validate_progress_total_bound(3_001)


def _processing_execution_fixture():
    ref = {
        "plugin_id": "parser-1",
        "plugin_version": "1.0.0",
        "package_digest": "platform-builtin:parser-1:1.0.0",
        "runtime_profile": "runtime-1",
    }
    profile = {
        "profile_id": "profile-1",
        "revision": 3,
        "status": "active",
        "accepted_media_types": ["application/pdf"],
        "base_parser_plugin_ref": ref,
        "mandatory_processor_plugin_refs": [],
        "eligible_processor_plugin_refs": [],
        "plugin_priority": [],
    }
    version = {
        **ref,
        "plugin_kind": "base_parser",
        "status": "verified",
        "trust_provenance": "platform_builtin",
        "descriptor": {"entrypoint": "plugins:Parser"},
    }
    runtime = {
        "runtime_profile_id": "runtime-1",
        "description": "runtime",
        "enabled": True,
        "available_packages": {},
    }
    return profile, version, runtime


def test_request_boundary_pins_complete_processing_execution(monkeypatch) -> None:
    profile, version, runtime = _processing_execution_fixture()
    locks = []

    class Results:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class CaptureSession:
        rows = iter(
            (
                [SimpleNamespace(payload=profile)],
                [SimpleNamespace(payload=version)],
                [],
                [SimpleNamespace(payload=runtime)],
            )
        )

        def scalars(self, _statement):
            return Results(next(self.rows))

    monkeypatch.setattr(
        owner,
        "acquire_mixed_owner_locks",
        lambda _session, **kwargs: locks.append(kwargs),
    )
    snapshot = owner._capture_processing_execution_snapshot(
        CaptureSession(),
        media_type="application/pdf",
        acceptance_request_digest="a" * 64,
    )
    payload = owner._processing_execution_payload(snapshot)
    restored = owner._processing_execution_snapshot(payload)

    assert locks == [
        {
            "shared_domain_keys": (
                "model-routing:configuration-control",
                "processing-registry:configuration-control",
            )
        }
    ]
    assert restored.profile_id == "profile-1"
    assert restored.profile_revision == 3
    assert restored.plugin_versions == (version,)
    assert restored.runtime_profiles == (runtime,)
    profile["revision"] = 99
    version["status"] = "disabled"
    assert restored.profile_revision == 3
    assert restored.plugin_versions[0]["status"] == "verified"


def test_processing_execution_snapshot_rejects_tampering() -> None:
    profile, version, runtime = _processing_execution_fixture()
    snapshot = owner.ProcessingExecutionSnapshot(
        profile_id="profile-1",
        profile_revision=3,
        profile_snapshot=profile,
        plugin_versions=(version,),
        plugin_packages=(),
        runtime_profiles=(runtime,),
        acceptance_request_digest="a" * 64,
    )
    payload = owner._processing_execution_payload(snapshot)
    payload["profile_revision"] = 4
    with pytest.raises(ValueError, match="digest"):
        owner._processing_execution_snapshot(payload)


def test_processing_job_acceptance_persists_request_owned_snapshot() -> None:
    source = inspect.getsource(owner._JobTransitionSql.create_processing_job)
    command_fingerprint = source.index('"execution_snapshot"')
    request_snapshot = source.index("AtlasProcessingRequestSnapshotRow(")
    mutation = source.index("_apply_sealed_family_mutation")
    commit = source.index("session.commit()", mutation)
    prepare_task = source.index('task_name="atlas.processing.prepare_job"')
    assert command_fingerprint < prepare_task < request_snapshot
    assert mutation < request_snapshot < commit
    assert "_processing_execution_payload(execution_snapshot)" in source
    assert "visual_route" not in source


def test_reprocess_accepts_generation_preallocated_identity() -> None:
    profile, version, runtime = _processing_execution_fixture()
    request = {
        "media_type": "application/pdf",
        "document_id": "document-1",
        "document_version_id": "version-1",
        "job_kind": "reprocess",
        "created_by": "user-1",
        "progress_total": None,
    }
    snapshot = owner.ProcessingExecutionSnapshot(
        profile_id="profile-1",
        profile_revision=3,
        profile_snapshot=profile,
        plugin_versions=(version,),
        plugin_packages=(),
        runtime_profiles=(runtime,),
        acceptance_request_digest=owner._processing_acceptance_request_digest(
            **request
        ),
    )
    identity = owner.document_processing_acceptance_identity(
        document_id="document-1",
        idempotency_scope="document-refresh-processing",
        idempotency_key="refresh-1",
        processing_generation=2,
    )

    class ReachedSessionFactory(RuntimeError):
        pass

    def reached_session_factory():
        raise ReachedSessionFactory

    with pytest.raises(ReachedSessionFactory):
        owner._JobTransitionSql(reached_session_factory).create_processing_job(
            document_id=request["document_id"],
            document_version_id=request["document_version_id"],
            job_kind=request["job_kind"],
            idempotency_scope="document-refresh-processing",
            idempotency_key="refresh-1",
            created_by=request["created_by"],
            execution_snapshot=snapshot,
            acceptance_identity=identity,
        )


def test_processing_acceptance_serializes_idempotency_before_config_capture() -> None:
    source = inspect.getsource(owner.AcceptProcessingExecutionCommand.accept_job)
    lock = source.index("acquire_mixed_owner_locks")
    existing = source.index("existing = session.scalar")
    capture = source.index("_capture_processing_execution_snapshot")
    assert lock < existing < capture
    assert "shared_domain_keys" in source[lock:existing]
    assert "exclusive_identity_keys" in source[lock:existing]


def test_load_processing_execution_is_attempt_and_fence_scoped() -> None:
    profile, version, runtime = _processing_execution_fixture()
    payload = owner._processing_execution_payload(
        owner.ProcessingExecutionSnapshot(
            profile_id="profile-1",
            profile_revision=3,
            profile_snapshot=profile,
            plugin_versions=(version,),
            plugin_packages=(),
            runtime_profiles=(runtime,),
            acceptance_request_digest="a" * 64,
        )
    )
    job = SimpleNamespace(
        job_id="job-1",
        attempt=2,
        fence=9,
        status="running",
        document_id="document-1",
        processing_generation=4,
    )
    request_snapshot = SimpleNamespace(
        document_id="document-1",
        processing_generation=4,
        accepted_attempt=1,
        payload=payload,
    )

    class Session:
        def __init__(self, rows):
            self.rows = iter(rows)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _statement):
            return next(self.rows)

        def get(self, _row_type, identity):
            assert identity == "job-1"
            return request_snapshot

    command = owner.LoadProcessingExecutionCommand(
        lambda: Session((job,))
    )
    assert command.execute(
        job_id="job-1", expected_attempt=2, expected_fence=9
    ).profile_revision == 3
    stale = owner.LoadProcessingExecutionCommand(lambda: Session((job,)))
    with pytest.raises(owner.DocumentProcessingCurrentnessConflict):
        stale.execute(job_id="job-1", expected_attempt=1, expected_fence=9)


def test_work_identity_covers_task_queue_normalized_payload_and_attempt() -> None:
    first = owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={"job_id": "job-1", "attempt": 2, "schema_version": 1},
    )
    reordered = owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={"schema_version": 1, "attempt": 2, "job_id": "job-1"},
    )
    assert first == reordered
    assert first != owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={"job_id": "job-1", "attempt": 3, "schema_version": 1},
    )
    assert first != owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.finalize_job",
        queue_name="atlas.processing",
        payload={"job_id": "job-1", "attempt": 2, "schema_version": 1},
    )


def test_physical_retry_lineage_does_not_change_work_identity() -> None:
    payload = {"job_id": "job-1", "attempt": 2, "schema_version": 1}
    assert owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload=payload,
    ) == owner._outbox_work_identity_owner_key(
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload=dict(payload),
    )


def test_outbox_creation_rejects_a_second_active_identity_under_lock() -> None:
    source = inspect.getsource(owner._publish_outbox_cas)
    advisory = source.index("_outbox_work_identity_owner_key")
    active_query = source.index("status.in_", advisory)
    merge = source.index("session.merge", active_query)
    assert advisory < active_query < merge
    assert '("pending", "dispatching")' in source
    assert "active outbox work identity already has a delivery" in source


def test_outbox_duplicate_probe_observes_advisory_before_active_row_read() -> None:
    now = datetime.now(timezone.utc)
    desired = owner.TaskOutboxRecord(
        outbox_id="outbox-new-delivery",
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload_schema_version=1,
        payload={"job_id": "job-1", "attempt": 1, "schema_version": 1},
        celery_task_id="task-new-delivery",
        status="pending",
        claim_owner=None,
        claim_expires_at=None,
        attempts=0,
        available_at=now,
        last_error_code=None,
        created_at=now,
        dispatched_at=None,
    )
    duplicate = SimpleNamespace(
        outbox_id="outbox-other-active-delivery",
    )

    class _Rows:
        def all(self):
            return [duplicate]

    class _Session:
        def __init__(self):
            self.events: list[str] = []

        def execute(self, statement, parameters=None):
            self.events.append("advisory")

        def scalars(self, statement):
            self.events.append("active-row-read")
            return _Rows()

    session = _Session()
    with pytest.raises(
        owner.DocumentProcessingCurrentnessConflict,
        match="already has a delivery",
    ):
        owner._publish_outbox_cas(
            session,
            owner.TaskOutboxTransition(
                desired,
                owner.CurrentRowExpectation.absent(),
            ),
            current=None,
        )
    assert session.events[:-1] and set(session.events[:-1]) == {"advisory"}
    assert session.events[-1] == "active-row-read"


def _job_record(*, status: str, attempt: int = 1, fence: int = 0):
    now = datetime.now(timezone.utc)
    return owner.ProcessingJobRecord(
        job_id="job-1",
        job_kind="ingest",
        document_id="document-1",
        document_version_id="version-1",
        processing_generation=1,
        index_generation_id="index-1",
        stage="processing",
        status=status,
        progress_current=0,
        progress_total=1,
        progress_unit="page",
        attempt=attempt,
        lease_owner=None,
        lease_expires_at=None,
        fence=fence,
        failure_code=None,
        failure_detail=None,
        idempotency_scope="document-1",
        idempotency_key="ingest-1",
        request_fingerprint="a" * 64,
        created_by="user-1",
        attempt_started_at=now,
        created_at=now,
        updated_at=now,
    )


def test_job_request_fingerprint_is_validated_then_immutable() -> None:
    with pytest.raises(ValueError, match="request fingerprint"):
        owner._validate_job_transition(
            None,
            replace(_job_record(status="queued"), request_fingerprint="not-a-digest"),
        )

    current_record = _job_record(status="running")
    with pytest.raises(ValueError, match="identity/provenance is immutable"):
        owner._validate_job_transition(
            SimpleNamespace(**asdict(current_record)),
            replace(current_record, request_fingerprint="b" * 64),
        )


def test_terminal_job_cannot_be_revived_by_named_transition() -> None:
    current_record = _job_record(status="failed")
    current = SimpleNamespace(**asdict(current_record))
    with pytest.raises(ValueError, match="transition is not monotonic"):
        owner._validate_job_transition(
            current,
            replace(current_record, status="running", attempt=2, fence=1),
            allow_operator_retry=False,
        )


def test_operator_retry_is_the_only_terminal_reactivation_path() -> None:
    current_record = _job_record(status="failed")
    current = SimpleNamespace(**asdict(current_record))
    desired = replace(current_record, status="queued", attempt=2, fence=1)
    owner._validate_job_transition(
        current,
        desired,
        allow_operator_retry=True,
    )
    with pytest.raises(ValueError, match="fence"):
        owner._validate_job_transition(
            current,
            replace(current_record, status="queued", attempt=2, fence=0),
            allow_operator_retry=True,
        )


def test_named_mutation_rolls_back_when_audit_publication_fails(monkeypatch) -> None:
    class _Session:
        committed = False
        rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    session = _Session()
    command = owner.DocumentMutationCommand(lambda: session)
    document = owner.DocumentRecord(
        document_id="document-1",
        title="Document",
        source_digest="a" * 64,
    )

    def _audit_failure(*args, **kwargs):
        raise RuntimeError("audit append failed")

    monkeypatch.setattr(owner, "_apply_sealed_family_mutation", _audit_failure)
    with pytest.raises(RuntimeError, match="audit append failed"):
        command.execute(
            document=document,
            document_version_id="version-1",
            expected_document_lifecycle_epoch=None,
            audit_events=(
                owner._internal_event(
                    operation="document.test",
                    job_id=None,
                    document_id="document-1",
                ),
            ),
        )
    assert session.rolled_back is True
    assert session.committed is False


def test_adapter_is_unwired_and_delegates_only_to_named_owners() -> None:
    source = inspect.getsource(PostgresDocumentProcessingAdapter)
    assert "create_app" not in source
    assert "DocumentProcessingRepository" not in source
    assert "JobTransitionCommand" in source
    assert "OutboxDeliveryCommand" in source
    assert "BatchCheckpointCommand" in source
    assert "FinalGenerationPublicationCommand" in source
    assert "__getattr__" not in source
    public_methods = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert {
        "create_processing_job",
        "claim_pending_outbox",
        "commit_checkpoint",
        "publish_job",
    } <= public_methods


CONSUMER_PARITY_METHODS = {
    "transaction",
    "create_processing_job",
    "prepare_job",
    "prepare_reindex",
    "finalize_document_page_preparation",
    "prepared_page_artifact",
    "get_processing_profile_pin",
    "chunks_for_batch",
    "set_embedding_profile",
    "stage_reindex_batch",
    "cleanup_retired_generations",
    "list_job_projection_batch",
    "batch_execution",
    "preparation_execution",
    "mark_failure",
}


def _real_repository_caller_inventory() -> dict[str, set[str]]:
    root = Path(__file__).parents[1] / "src" / "atlas_production"
    callers = {
        "workflows.py": root / "async_runtime" / "workflows.py",
        "tasks.py": root / "async_runtime" / "tasks.py",
        "processing_jobs.py": root / "routes" / "processing_jobs.py",
    }

    def repository_receiver(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id == "repository"
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr in {"async_jobs", "async_jobs_repository"}
        )

    inventory: dict[str, set[str]] = {}
    for label, path in callers.items():
        tree = ast.parse(path.read_text())
        inventory[label] = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and repository_receiver(node.func.value)
        }
    return inventory


def _method_arguments(source: str, class_name: str) -> dict[str, tuple]:
    tree = ast.parse(source)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    result = {}
    for node in class_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = tuple(arg.arg for arg in (*node.args.posonlyargs, *node.args.args))
        keyword_only = tuple(arg.arg for arg in node.args.kwonlyargs)
        result[node.name] = (
            positional,
            keyword_only,
            len(node.args.defaults),
            tuple(default is not None for default in node.args.kw_defaults),
        )
    return result


def test_adapter_has_full_live_consumer_signature_parity() -> None:
    successor_methods = _method_arguments(
        inspect.getsource(PostgresDocumentProcessingAdapter),
        "PostgresDocumentProcessingAdapter",
    )
    derived_inventory = set().union(*_real_repository_caller_inventory().values())
    parity_methods = derived_inventory | {"mark_failure"}
    assert parity_methods <= successor_methods.keys()


def test_signature_inventory_is_derived_from_real_callers() -> None:
    inventory = _real_repository_caller_inventory()
    derived = set().union(*inventory.values())
    adapter_methods = _method_arguments(
        inspect.getsource(PostgresDocumentProcessingAdapter),
        "PostgresDocumentProcessingAdapter",
    )
    assert derived
    assert derived <= adapter_methods.keys()
    # A typed composition may remove direct repository calls from an entrypoint;
    # the inventory is authoritative for the calls that remain, not a quota that
    # forces every transport/carrier to keep a repository dependency.


def test_external_connection_is_joined_without_nested_commit() -> None:
    source = inspect.getsource(owner._JobTransitionSql.create_processing_job)
    assert "bind=connection" in source
    assert 'join_transaction_mode="rollback_only"' in source
    assert "connection=connection" not in source


def test_create_processing_job_executes_on_passed_connection(monkeypatch) -> None:
    external_connection = object()
    captured = {}

    class ProbeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def scalar(self, _statement):
            raise RuntimeError("joined-session-probe")

        def execute(self, *_args, **_kwargs):
            return None

        def rollback(self):
            captured["rolled_back"] = True

    def joined_session(*, bind, join_transaction_mode):
        captured.update(
            bind=bind,
            join_transaction_mode=join_transaction_mode,
        )
        return ProbeSession()

    monkeypatch.setattr(owner, "Session", joined_session)
    command = owner.JobTransitionCommand(lambda: pytest.fail("opened own session"))
    with pytest.raises(RuntimeError, match="joined-session-probe"):
        command.create_processing_job(
            document_id="document-1",
            document_version_id="version-1",
            job_kind="ingest",
            idempotency_scope="scope",
            idempotency_key="key",
            created_by="user-1",
            connection=external_connection,
        )
    assert captured == {
        "bind": external_connection,
        "join_transaction_mode": "rollback_only",
        "rolled_back": True,
    }


def test_transaction_yields_the_owner_connection() -> None:
    connection = object()
    events = []

    class Begin:
        def __enter__(self):
            events.append("begin")
            return connection

        def __exit__(self, exc_type, exc, traceback):
            events.append("end")
            return False

    class Bind:
        def begin(self):
            return Begin()

    class ProbeSession:
        def get_bind(self):
            return Bind()

        def close(self):
            events.append("session-closed")

    adapter = PostgresDocumentProcessingAdapter(ProbeSession)
    with adapter.transaction() as yielded:
        assert yielded is connection
        events.append("body")
    assert events == ["session-closed", "begin", "body", "end"]


def test_retry_acquires_complete_graph_lock_once_without_partial_work_lock() -> None:
    source = inspect.getsource(owner._JobTransitionSql.schedule_retry)
    assert "acquire_owner_locks(" not in source
    assert "coordination_identity_keys=(work_identity_key,)" in source
    graph_source = inspect.getsource(owner._apply_sealed_family_mutation)
    assert "_acquire_document_processing_mutation" in graph_source


def test_existing_index_status_transition_does_not_revalidate_unchanged_ancestor() -> None:
    now = datetime.now(timezone.utc)
    active = owner.IndexGenerationProjection(
        "index-current", "document-1", "version-1", 2, "profile-1", {},
        "atlas_evidence_v1", "active", 1, 1, 1, 1, "a" * 64,
        "index-cleaned-ancestor", now, now,
    )
    retired = replace(active, status="retired")
    transition = owner.IndexGenerationTransition(
        retired,
        owner.CurrentRowExpectation(
            True, "active", None, None, None, active,
        ),
    )

    assert not owner._requires_index_supersedes_parent(transition)
    assert owner._requires_index_supersedes_parent(
        replace(transition, record=replace(retired, supersedes_index_generation_id="index-new-parent"))
    )
    assert owner._requires_index_supersedes_parent(
        owner.IndexGenerationTransition(retired, owner.CurrentRowExpectation.absent())
    )


def test_consumer_methods_preserve_five_family_capability_split() -> None:
    expected = {
        owner.JobTransitionCommand: {
            "transaction",
            "create_processing_job",
            "prepare_job",
            "prepare_reindex",
            "list_job_projection_batch",
            "mark_failure",
        },
        owner.BatchCheckpointCommand: {
            "finalize_document_page_preparation",
            "prepared_page_artifact",
            "get_processing_profile_pin",
            "chunks_for_batch",
            "set_embedding_profile",
            "stage_reindex_batch",
            "batch_execution",
            "preparation_execution",
        },
        owner.FinalGenerationPublicationCommand: {
            "cleanup_retired_generations",
        },
    }
    for command, methods in expected.items():
        assert all(hasattr(command, method) for method in methods)
    assert not hasattr(owner.DocumentMutationCommand, "prepare_job")
    assert not hasattr(owner.OutboxDeliveryCommand, "prepare_job")
    assert not hasattr(owner.JobTransitionCommand, "stage_reindex_batch")
    assert not hasattr(owner.BatchCheckpointCommand, "mark_failure")
    assert not hasattr(
        owner.FinalGenerationPublicationCommand,
        "finalize_document_page_preparation",
    )


def test_ported_methods_reject_invalid_consumer_inputs_before_sql() -> None:
    def no_session():
        raise AssertionError("invalid inputs must fail before opening SQL")

    jobs = owner.JobTransitionCommand(no_session)
    batches = owner.BatchCheckpointCommand(no_session)
    publication = owner.FinalGenerationPublicationCommand(no_session)
    with pytest.raises(ValueError, match="supported page limit"):
        jobs.prepare_job(
            "job-1",
            total_units=3_001,
            profile_id="profile-1",
            profile_revision=1,
            expected_attempt=1,
        )
    with pytest.raises(ValueError, match="invalid_batch_id"):
        batches.prepared_page_artifact("job-1", "job-2:page:1")
    with pytest.raises(ValueError, match="invalid_reindex_batch_id"):
        batches.stage_reindex_batch(
            "job-1",
            "job-1:page:1",
            expected_attempt=1,
        )
    with pytest.raises(ValueError, match="retired generation limit"):
        publication.cleanup_retired_generations(limit=0)


def test_adapter_routes_all_consumer_methods_to_explicit_family_owners(
    monkeypatch,
) -> None:
    adapter = PostgresDocumentProcessingAdapter(lambda: None)
    calls: list[tuple[str, tuple, dict]] = []

    def recording(name, result=None):
        def invoke(_self, *args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return invoke

    family_methods = {
        owner.JobTransitionCommand: {
            "transaction": "transaction-context",
            "create_processing_job": "created-job",
            "prepare_job": ["batch-1"],
            "prepare_reindex": 1,
            "list_job_projection_batch": "projection",
            "mark_failure": None,
        },
        owner.BatchCheckpointCommand: {
            "finalize_document_page_preparation": "outbox-1",
            "prepared_page_artifact": {"artifact_kind": "pdf_single_page"},
            "get_processing_profile_pin": "pin",
            "chunks_for_batch": ("job", []),
            "set_embedding_profile": True,
            "stage_reindex_batch": True,
            "batch_execution": "batch-context",
            "preparation_execution": "prepare-context",
        },
        owner.FinalGenerationPublicationCommand: {
            "cleanup_retired_generations": None,
        },
    }
    for command, methods in family_methods.items():
        for name, result in methods.items():
            monkeypatch.setattr(command, name, recording(name, result))

    assert adapter.transaction() == "transaction-context"
    assert adapter.create_processing_job(
        document_id="document",
        document_version_id="version",
        job_kind="ingest",
        idempotency_scope="scope",
        idempotency_key="key",
        created_by="user",
        connection=object(),
    ) == "created-job"
    assert adapter.prepare_job("job", total_units=1, profile_id="p", profile_revision=1, expected_attempt=1) == ["batch-1"]
    assert adapter.prepare_reindex("job", expected_attempt=1) == 1
    assert adapter.list_job_projection_batch(actor_type="user", actor_id="u") == "projection"
    adapter.mark_failure("job", fence=1, code="x", detail="y", transient=False)
    assert adapter.finalize_document_page_preparation(object(), job_id="job", expected_attempt=1, claim_fence=1, claim_token="claim", page_record={}) == "outbox-1"
    assert adapter.prepared_page_artifact("job", "job:page:1")["artifact_kind"] == "pdf_single_page"
    assert adapter.get_processing_profile_pin(document_id="doc", processing_generation=1) == "pin"
    assert adapter.chunks_for_batch("job", "batch") == ("job", [])
    assert adapter.set_embedding_profile("job", "index", {}, expected_attempt=1)
    assert adapter.stage_reindex_batch("job", "job:reindex:0", expected_attempt=1)
    assert adapter.batch_execution("job", "job:page:1") == "batch-context"
    assert adapter.preparation_execution("job", expected_attempt=1) == "prepare-context"
    adapter.cleanup_retired_generations(limit=1)
    assert {name for name, _args, _kwargs in calls} == CONSUMER_PARITY_METHODS


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _SequencedSession:
    def __init__(self, *, scalars=(), collections=(), gets=(), executes=()):
        self.scalar_values = list(scalars)
        self.collection_values = list(collections)
        self.get_values = list(gets)
        self.execute_values = list(executes)
        self.scalar_calls = 0
        self.scalars_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.deleted = []
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.scalar_values.pop(0)

    def scalars(self, _statement):
        self.scalars_calls += 1
        return _Rows(self.collection_values.pop(0))

    def get(self, _row_type, _identity):
        return self.get_values.pop(0)

    def execute(self, _statement, _parameters=None):
        return _Rows(self.execute_values.pop(0))

    def expire_all(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def delete(self, value):
        self.deleted.append(value)

    def add(self, value):
        self.added.append(value)


def _document_parent(job_id: str):
    document = replace(
        owner.DocumentRecord(
            document_id="document-1",
            title="Document",
            source_digest="a" * 64,
        ),
        processing_job_id=job_id,
    )
    return SimpleNamespace(**asdict(document))


def _generation(*, status: str, expected_page_count: int):
    projection = owner.ProcessingGenerationProjection(
        document_id="document-1",
        processing_generation=1,
        document_version_id="version-1",
        profile_id="profile-1",
        profile_revision=1,
        status=status,
        expected_page_count=expected_page_count,
        actual_page_count=0,
        expected_evidence_count=None,
        actual_evidence_count=0,
        expected_chunk_count=None,
        actual_chunk_count=0,
        manifest_digest=None,
        created_at=datetime.now(timezone.utc),
        published_at=None,
    )
    return SimpleNamespace(**asdict(projection))


def _publication_snapshot(
    *,
    job_kind: str = "ingest",
    job_status: str = "running",
    lifecycle_status: str = "active",
    warning_codes: list[str] | None = None,
    active_index: str | None = "index-prior",
    active_processing: int = 0,
):
    now = datetime.now(timezone.utc)
    document = replace(
        owner.DocumentRecord(
            document_id="document-1",
            title="Document",
            source_digest="a" * 64,
        ),
        original_artifact_id="artifact-source",
        lifecycle_status=lifecycle_status,
        warning_codes=list(warning_codes or []),
        active_index_generation_id=active_index,
        active_processing_generation=active_processing,
    )
    version = owner.DocumentVersionRecord(
        document_version_id="version-1",
        document_id=document.document_id,
        title=document.title,
        source_kind=document.source_kind,
        document_format=document.document_format,
        source_digest=document.raw_sha256,
        content_digest="b" * 64,
        created_at=now.isoformat(),
        status="active",
        original_artifact_id=document.original_artifact_id,
        content_type=document.content_type,
    )
    processing_generation = None if job_kind == "reindex" else 1
    generation_status = "active" if job_kind == "reindex" or job_status == "succeeded" else "building"
    generation = owner.ProcessingGenerationProjection(
        document_id=document.document_id,
        processing_generation=1,
        document_version_id=version.document_version_id,
        profile_id="profile-1",
        profile_revision=1,
        status=generation_status,
        expected_page_count=1,
        actual_page_count=1,
        expected_evidence_count=1,
        actual_evidence_count=1,
        expected_chunk_count=1,
        actual_chunk_count=1,
        manifest_digest="m" * 64,
        created_at=now,
        published_at=(now if generation_status == "active" else None),
    )
    index_status = "active" if job_status == "succeeded" else "building"
    index = owner.IndexGenerationProjection(
        index_generation_id="index-new",
        document_id=document.document_id,
        document_version_id=version.document_version_id,
        source_processing_generation=1,
        embedding_profile_id="profile-1",
        embedding_profile={},
        qdrant_collection="atlas_evidence_v1",
        status=index_status,
        expected_point_count=1,
        actual_point_count=1,
        expected_fts_count=1,
        actual_fts_count=1,
        manifest_digest="m" * 64,
        supersedes_index_generation_id="index-prior",
        created_at=now,
        published_at=(now if index_status == "active" else None),
    )
    request_fingerprint = owner._request_digest(
        {
            "document_id": document.document_id,
            "document_version_id": version.document_version_id,
            "job_kind": job_kind,
            "created_by": "user-1",
            "progress_total": 1,
            "parent_lifecycle_epoch": document.resource_lifecycle_epoch,
        }
    )
    job = replace(
        _job_record(status=job_status),
        job_kind=job_kind,
        processing_generation=processing_generation,
        index_generation_id=index.index_generation_id,
        progress_current=1,
        progress_total=1,
        request_fingerprint=request_fingerprint,
    )
    evidence = owner.EvidenceRecord(
        evidence_id="evidence-1",
        document_id=document.document_id,
        document_title=document.title,
        locator_label="page 1",
        snippet="snippet",
        content="content",
        document_version_id=version.document_version_id,
        processing_generation=1,
        status="ready" if job_status == "succeeded" else "staged",
    )
    chunk = owner.SearchChunkProjection(
        chunk_id="chunk-1",
        batch_id=f"{job.job_id}:page:1",
        document_id=document.document_id,
        document_version_id=version.document_version_id,
        processing_generation=1,
        index_generation_id=index.index_generation_id,
        evidence_id=evidence.evidence_id,
        segment_id="segment-1",
        window_ordinal=0,
        normalized_text="content",
        locator={},
        content_fingerprint="c" * 64,
        processing_fingerprint="d" * 64,
        search_vector=None,
        status="active" if job_status == "succeeded" else "staged",
        created_at=now,
    )
    vector = owner.VectorPointMappingRecord(
        index_generation_id=index.index_generation_id,
        point_id="point-1",
        chunk_id=chunk.chunk_id,
        payload_digest="e" * 64,
        vector_digest="f" * 64,
        created_at=now,
    )
    checkpoint = owner.ProcessingCheckpointRecord(
        job_id=job.job_id,
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        batch_id=chunk.batch_id,
        claim_token="claim-1",
        fence=0,
        input_fingerprint="a" * 64,
        output_digest="b" * 64,
        evidence_count=1,
        chunk_count=1,
        preview_count=0,
        committed_at=now,
    )
    prior_index = replace(index, index_generation_id="index-prior", status="active")
    return owner._GenerationPublicationSnapshot(
        document=document,
        version=version,
        superseded_version=None,
        job=job,
        generation=generation,
        index=index,
        checkpoints=(() if job_kind == "reindex" else (checkpoint,)),
        evidence=(evidence,),
        pages=(),
        chunks=(chunk,),
        vectors=(vector,),
        prior_generations=(),
        prior_indexes=((prior_index,) if active_index is not None else ()),
    )


def test_retry_accepts_exact_3000_page_envelope(monkeypatch) -> None:
    record = replace(
        _job_record(status="failed", fence=3),
        stage="indexing",
        progress_total=3_000,
        failure_code="parse_failed",
    )
    session = _SequencedSession(
        scalars=(
            SimpleNamespace(**asdict(record)),
            _generation(status="failed", expected_page_count=3_000),
            _document_parent(record.job_id),
        ),
        collections=((), (), ()),
    )
    captured = {}

    def capture(_self, _session, **values):
        captured.update(values)

    monkeypatch.setattr(owner._JobTransitionSql, "_publish_job_state_graph", capture)
    result = owner.JobTransitionCommand(lambda: session).retry_terminal_job(record.job_id)
    assert result.attempt == 2
    assert len(captured["outbox"]) == 3_000
    assert captured["outbox"][0].record.payload["batch_id"].endswith(":page:1")
    assert captured["outbox"][-1].record.payload["batch_id"].endswith(":page:3000")


def test_retry_skips_batches_with_complete_immutable_vector_mappings(monkeypatch) -> None:
    record = replace(
        _job_record(status="failed", fence=3),
        stage="indexing",
        progress_current=1,
        progress_total=2,
        failure_code="index_failed",
    )
    checkpoints = tuple(
        owner.ProcessingCheckpointRecord(
            job_id=record.job_id,
            unit_kind="page",
            unit_start=page,
            unit_end=page,
            batch_id=f"{record.job_id}:page:{page}",
            claim_token=f"claim-{page}",
            fence=record.fence,
            input_fingerprint="a" * 64,
            output_digest="b" * 64,
            evidence_count=1,
            chunk_count=1,
            preview_count=0,
            committed_at=datetime.now(timezone.utc),
        )
        for page in (1, 2)
    )
    chunks = tuple(
        SimpleNamespace(
            batch_id=checkpoint.batch_id,
            chunk_id=f"chunk-{checkpoint.unit_start}",
        )
        for checkpoint in checkpoints
    )
    session = _SequencedSession(
        scalars=(
            SimpleNamespace(**asdict(record)),
            _generation(status="failed", expected_page_count=2),
            _document_parent(record.job_id),
        ),
        collections=(checkpoints, chunks, ("chunk-1",)),
    )
    captured = {}

    def capture(_self, _session, **values):
        captured.update(values)

    monkeypatch.setattr(owner._JobTransitionSql, "_publish_job_state_graph", capture)
    owner.JobTransitionCommand(lambda: session).retry_terminal_job(record.job_id)

    tasks = tuple(
        (item.record.task_name, item.record.payload["batch_id"])
        for item in captured["outbox"]
    )
    assert tasks == (("atlas.indexing.index_batch", f"{record.job_id}:page:2"),)


def test_retry_rejects_3001_before_checkpoint_scan() -> None:
    record = replace(
        _job_record(status="failed"),
        stage="indexing",
        progress_total=3_001,
    )
    session = _SequencedSession(scalars=(SimpleNamespace(**asdict(record)),))
    with pytest.raises(ValueError, match="supported page limit"):
        owner.JobTransitionCommand(lambda: session).retry_terminal_job(record.job_id)
    assert session.scalar_calls == 1
    assert session.scalars_calls == 0
    assert session.commits == 0
    assert session.rollbacks == 1


def test_cancel_covers_exact_6002_current_attempt_outboxes(monkeypatch) -> None:
    record = replace(_job_record(status="running", fence=1), progress_total=3_000)
    now = datetime.now(timezone.utc)
    outboxes = tuple(
        SimpleNamespace(
            **asdict(
                owner.TaskOutboxRecord(
                    outbox_id=f"outbox-{ordinal}",
                    task_name="atlas.processing.process_batch",
                    queue_name="atlas.processing",
                    payload={"job_id": record.job_id, "attempt": 1},
                    payload_schema_version=1,
                    celery_task_id=f"task-{ordinal}",
                    status="pending",
                    claim_owner=None,
                    claim_expires_at=None,
                    attempts=0,
                    available_at=now,
                    last_error_code=None,
                    created_at=now,
                    dispatched_at=None,
                )
            )
        )
        for ordinal in range(6_002)
    )
    session = _SequencedSession(
        scalars=(SimpleNamespace(**asdict(record)), _document_parent(record.job_id)),
        collections=(outboxes,),
    )
    captured = {}

    def capture(_self, _session, **values):
        captured.update(values)

    monkeypatch.setattr(owner._JobTransitionSql, "_publish_job_state_graph", capture)
    result = owner.JobTransitionCommand(lambda: session).cancel_processing_job(record.job_id)
    assert result.status == "cancelled"
    assert len(captured["outbox"]) == 6_002


def test_exact_preimage_accepts_replay_and_rejects_stale_currentness() -> None:
    current = _job_record(status="running")
    row = SimpleNamespace(**asdict(current))
    expectation = owner.CurrentRowExpectation(
        exists=True,
        status="running",
        attempt=1,
        fence=0,
        claim_owner=None,
        preimage=current,
    )
    assert owner._expect_current_or_exact_replay(
        row,
        current,
        current,
        expectation,
        family="processing job",
        status_attr="status",
        attempt_attr="attempt",
        fence_attr="fence",
        claim_attr="lease_owner",
    )
    with pytest.raises(owner.DocumentProcessingCurrentnessConflict, match="preimage"):
        owner._expect_current_or_exact_replay(
            row,
            replace(current, progress_current=1),
            replace(current, progress_current=2),
            expectation,
            family="processing job",
            status_attr="status",
            attempt_attr="attempt",
            fence_attr="fence",
            claim_attr="lease_owner",
        )


def test_foreign_owner_identity_is_rejected_across_job_batch_outbox_checkpoint() -> None:
    job = _job_record(status="running")
    with pytest.raises(ValueError, match="identity/provenance"):
        owner._validate_job_transition(
            SimpleNamespace(**asdict(job)),
            replace(job, document_id="foreign-document"),
        )
    now = datetime.now(timezone.utc)
    batch = owner.ProcessingBatchClaimRecord(
        batch_id="job-1:page:1",
        job_id="job-1",
        attempt=1,
        claim_token="claim-1",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        lease_expires_at=now,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValueError, match="identity/provenance"):
        owner._validate_batch_transition(
            SimpleNamespace(**asdict(batch)),
            replace(batch, job_id="foreign-job"),
        )
    outbox = owner.TaskOutboxRecord(
        outbox_id="outbox-1",
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={"job_id": "job-1", "attempt": 1, "schema_version": 1},
        payload_schema_version=1,
        celery_task_id="task-1",
        status="pending",
        claim_owner=None,
        claim_expires_at=None,
        attempts=0,
        available_at=now,
        last_error_code=None,
        created_at=now,
        dispatched_at=None,
    )
    with pytest.raises(ValueError, match="identity/provenance"):
        owner._validate_outbox_transition(
            SimpleNamespace(**asdict(outbox)),
            replace(outbox, payload={"job_id": "foreign-job", "attempt": 1, "schema_version": 1}),
        )
    checkpoint = owner.ProcessingCheckpointRecord(
        job_id="job-1",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        batch_id="job-1:page:1",
        claim_token="claim-1",
        fence=0,
        input_fingerprint="a" * 64,
        output_digest="b" * 64,
        evidence_count=1,
        chunk_count=1,
        preview_count=0,
        committed_at=now,
    )
    with pytest.raises(ValueError, match="identity/provenance"):
        owner._validate_checkpoint_preimage(
            SimpleNamespace(**asdict(checkpoint)),
            replace(checkpoint, job_id="foreign-job"),
        )


def test_existing_checkpoint_allows_index_claim_token_takeover_only() -> None:
    now = datetime.now(timezone.utc)
    checkpoint = owner.ProcessingCheckpointRecord(
        job_id="job-1",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        batch_id="job-1:page:1",
        claim_token="processing-claim",
        fence=0,
        input_fingerprint="a" * 64,
        output_digest="b" * 64,
        evidence_count=1,
        chunk_count=1,
        preview_count=0,
        committed_at=now,
    )
    indexing_claim = owner.ProcessingBatchClaimRecord(
        batch_id=checkpoint.batch_id,
        job_id=checkpoint.job_id,
        attempt=1,
        claim_token="indexing-claim",
        unit_kind=checkpoint.unit_kind,
        unit_start=checkpoint.unit_start,
        unit_end=checkpoint.unit_end,
        lease_expires_at=now,
        created_at=now,
        updated_at=now,
    )
    existing = owner.ProcessingCheckpointTransition(
        checkpoint,
        owner.CurrentRowExpectation(
            exists=True,
            status=None,
            attempt=None,
            fence=None,
            claim_owner=None,
            preimage=checkpoint,
        ),
    )
    creating = replace(existing, expected=owner.CurrentRowExpectation.absent())

    assert owner._checkpoint_has_matching_batch_owner(existing, indexing_claim)
    assert not owner._checkpoint_has_matching_batch_owner(creating, indexing_claim)
    assert not owner._checkpoint_has_matching_batch_owner(
        existing,
        replace(indexing_claim, unit_start=2, unit_end=2),
    )


def test_batch_claim_token_takeover_requires_expired_authority() -> None:
    now = datetime.now(timezone.utc)
    current = owner.ProcessingBatchClaimRecord(
        batch_id="job-1:page:1",
        job_id="job-1",
        attempt=1,
        claim_token="claim-old",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        lease_expires_at=now,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValueError, match="cannot be taken over"):
        owner._validate_batch_transition(
            SimpleNamespace(**asdict(current)),
            replace(current, claim_token="claim-new"),
        )
    owner._validate_batch_transition(
        SimpleNamespace(**asdict(current)),
        replace(current, claim_token="claim-new"),
        allow_claim_token_takeover=True,
    )


def test_retired_generation_cleanup_audit_failure_rolls_back(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    retired = owner.async_rows.AtlasIndexGenerationRow(
        index_generation_id="index-retired",
        document_id="document-1",
        document_version_id="version-1",
        source_processing_generation=1,
        embedding_profile_id="profile-1",
        embedding_profile={},
        qdrant_collection="atlas_evidence_v1",
        status="retired",
        expected_point_count=0,
        actual_point_count=0,
        expected_fts_count=0,
        actual_fts_count=0,
        manifest_digest="a" * 64,
        supersedes_index_generation_id=None,
        created_at=now,
        published_at=now,
    )
    session = _SequencedSession(collections=((retired,),))

    def fail_audit(_self, _event):
        raise RuntimeError("audit append failed")

    monkeypatch.setattr(owner.AuditEventWriter, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit append failed"):
        owner.FinalGenerationPublicationCommand(
            lambda: session
        ).cleanup_retired_generations(limit=1)
    assert session.deleted == [retired]
    assert session.commits == 0
    assert session.rollbacks == 1


def test_processing_owned_retention_fences_vector_and_generation_cleanup() -> None:
    source = inspect.getsource(owner._FinalGenerationPublicationSql)
    assert source.count("AtlasProcessingGenerationRetentionEntryRow") >= 3
    assert "active processing-owned retention claim" in source

    generation = SimpleNamespace(status="retired")
    session = _SequencedSession(scalars=(generation, "request-pin"))
    with pytest.raises(
        owner.DocumentProcessingCurrentnessConflict,
        match="active processing-owned retention claim",
    ):
        owner.FinalGenerationPublicationCommand(
            lambda: session
        ).delete_retired_vector_points({"index-retired": ["point-1"]})
    assert session.rollbacks == 1
    assert session.commits == 0


def test_renew_batch_claim_audit_failure_rolls_back_cas(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    job = replace(
        _job_record(status="running", fence=2),
        lease_expires_at=now,
    )
    claim = owner.ProcessingBatchClaimRecord(
        batch_id="job-1:page:1",
        job_id=job.job_id,
        attempt=job.attempt,
        claim_token="claim-1",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        lease_expires_at=now.replace(year=now.year + 1),
        created_at=now,
        updated_at=now,
    )
    session = _SequencedSession(
        scalars=(
            SimpleNamespace(**asdict(job)),
            SimpleNamespace(**asdict(claim)),
        )
    )
    published = []

    def publish(_session, transition, **_kwargs):
        published.append(transition)

    def fail_audit(_self, _event):
        raise RuntimeError("audit append failed")

    monkeypatch.setattr(owner, "_publish_batch_lease_cas", publish)
    monkeypatch.setattr(owner.AuditEventWriter, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit append failed"):
        owner.BatchCheckpointCommand(lambda: session).renew_batch_claim(
            job_id=job.job_id,
            batch_id=claim.batch_id,
            attempt=job.attempt,
            claim_fence=job.fence,
            claim_token=claim.claim_token,
        )
    assert len(published) == 1
    assert session.commits == 0
    assert session.rollbacks == 1


def _retry_predecessor(*, outbox_id: str, status: str = "dispatching"):
    now = datetime.now(timezone.utc)
    record = owner.TaskOutboxRecord(
        outbox_id=outbox_id,
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={
            "job_id": "job-1",
            "batch_id": "job-1:page:1",
            "attempt": 1,
            "schema_version": 1,
        },
        payload_schema_version=1,
        celery_task_id=f"task-{outbox_id}",
        status=status,
        claim_owner=("dispatcher-1" if status == "dispatching" else None),
        claim_expires_at=(now if status == "dispatching" else None),
        attempts=(1 if status == "dispatching" else 0),
        available_at=now,
        last_error_code=None,
        created_at=now,
        dispatched_at=None,
    )
    return SimpleNamespace(**asdict(record))


def test_schedule_retry_creates_successor_without_mutating_predecessor(monkeypatch) -> None:
    job = _job_record(status="running")
    predecessor = _retry_predecessor(outbox_id="outbox-predecessor")
    predecessor_snapshot = dict(vars(predecessor))
    session = _SequencedSession(
        scalars=(SimpleNamespace(**asdict(job)), _document_parent(job.job_id)),
        collections=((), (predecessor,)),
    )
    captured = {}

    def capture(_self, _session, **values):
        captured.update(values)

    monkeypatch.setattr(owner._JobTransitionSql, "_publish_job_state_graph", capture)
    owner.JobTransitionCommand(lambda: session).schedule_retry(
        job.job_id,
        expected_attempt=1,
        task_name="atlas.processing.process_batch",
        queue_name="atlas.processing",
        payload={"job_id": job.job_id, "batch_id": "job-1:page:1"},
        code="dependency_unavailable",
        detail="temporary",
        delay_seconds=0,
    )
    (successor,) = captured["outbox"]
    assert successor.allowed_dispatching_predecessor_id == predecessor.outbox_id
    assert successor.record.outbox_id != predecessor.outbox_id
    assert vars(predecessor) == predecessor_snapshot
    assert captured["desired_job"].status == "retry_wait"
    assert captured["coordination_identity_keys"] == (
        owner._outbox_work_identity_owner_key(
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload=predecessor.payload,
        ),
    )


def test_schedule_retry_rejects_ambiguous_pending_successors(monkeypatch) -> None:
    job = _job_record(status="running")
    pending = (
        _retry_predecessor(outbox_id="outbox-pending-1", status="pending"),
        _retry_predecessor(outbox_id="outbox-pending-2", status="pending"),
    )
    session = _SequencedSession(
        scalars=(SimpleNamespace(**asdict(job)),),
        collections=(pending,),
    )
    monkeypatch.setattr(
        owner._JobTransitionSql,
        "_publish_job_state_graph",
        lambda *_args, **_kwargs: pytest.fail("ambiguous retry was published"),
    )
    with pytest.raises(owner.DocumentProcessingCurrentnessConflict, match="ambiguous"):
        owner.JobTransitionCommand(lambda: session).schedule_retry(
            job.job_id,
            expected_attempt=1,
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload={"job_id": job.job_id, "batch_id": "job-1:page:1"},
            code="dependency_unavailable",
            detail="temporary",
        )
    assert session.commits == 0
    assert session.rollbacks == 1


def test_stage_reindex_rejects_stale_active_source_before_mutation() -> None:
    job = replace(
        _job_record(status="running"),
        job_kind="reindex",
        processing_generation=None,
        index_generation_id="index-building",
    )
    document = _document_parent(job.job_id)
    document.active_index_generation_id = "index-other-active"
    document.active_processing_generation = 1
    generation = SimpleNamespace(
        index_generation_id="index-building",
        supersedes_index_generation_id="index-source",
        source_processing_generation=1,
    )
    session = _SequencedSession(
        scalars=(SimpleNamespace(**asdict(job)),),
        gets=(document, generation),
    )
    with pytest.raises(ValueError, match="reindex_source_generation_changed"):
        owner.BatchCheckpointCommand(lambda: session).stage_reindex_batch(
            job.job_id,
            f"{job.job_id}:reindex:0",
            expected_attempt=job.attempt,
        )
    assert session.commits == 0
    assert session.rollbacks == 1


def test_checkpoint_audit_failure_rolls_back_without_releasing_claim(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    job = replace(_job_record(status="running", fence=2), progress_total=1)
    claim = owner.ProcessingBatchClaimRecord(
        batch_id=f"{job.job_id}:page:1",
        job_id=job.job_id,
        attempt=job.attempt,
        claim_token="claim-1",
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        lease_expires_at=now.replace(year=now.year + 1),
        created_at=now,
        updated_at=now,
    )
    generation = _generation(status="building", expected_page_count=1)
    index = owner.IndexGenerationProjection(
        index_generation_id=job.index_generation_id,
        document_id=job.document_id,
        document_version_id=job.document_version_id,
        source_processing_generation=1,
        embedding_profile_id="profile-1",
        embedding_profile={},
        qdrant_collection="atlas_evidence_v1",
        status="building",
        expected_point_count=None,
        actual_point_count=0,
        expected_fts_count=None,
        actual_fts_count=0,
        manifest_digest=None,
        supersedes_index_generation_id=None,
        created_at=now,
        published_at=None,
    )
    session = _SequencedSession(
        scalars=(
            SimpleNamespace(**asdict(job)),
            None,
            _document_parent(job.job_id),
            SimpleNamespace(**asdict(claim)),
            generation,
            SimpleNamespace(**asdict(index)),
        )
    )

    def audit_failure(*_args, **_kwargs):
        raise RuntimeError("audit append failed")

    monkeypatch.setattr(owner, "_apply_sealed_family_mutation", audit_failure)
    with pytest.raises(RuntimeError, match="audit append failed"):
        owner.BatchCheckpointCommand(lambda: session).commit_checkpoint(
            job_id=job.job_id,
            attempt=job.attempt,
            claim_fence=job.fence,
            claim_token=claim.claim_token,
            batch_id=claim.batch_id,
            unit_start=1,
            unit_end=1,
            input_fingerprint="a" * 64,
            output_digest="b" * 64,
            evidence_rows=[],
            chunk_rows=[],
        )
    assert session.deleted == []
    assert session.commits == 0
    assert session.rollbacks == 1


def test_reconcile_audit_failure_rolls_back_job_publication(monkeypatch) -> None:
    expired = datetime(2000, 1, 1, tzinfo=timezone.utc)
    job = replace(
        _job_record(status="running", fence=2),
        lease_owner="worker-1",
        lease_expires_at=expired,
    )
    job_row = SimpleNamespace(**asdict(job))
    session = _SequencedSession(
        collections=((job.job_id,), (job_row,)),
        executes=((),),
    )
    published = []

    def publish(_session, transition, **_kwargs):
        published.append(transition)

    def fail_audit(_self, _event):
        raise RuntimeError("audit append failed")

    monkeypatch.setattr(owner, "acquire_owner_locks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(owner, "_publish_job_lease_reconciliation_cas", publish)
    monkeypatch.setattr(owner.AuditEventWriter, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit append failed"):
        owner.OutboxDeliveryCommand(lambda: session).reconcile_expired_claims(limit=1)
    assert len(published) == 1
    assert published[0].record.status == "retry_wait"
    assert session.commits == 0
    assert session.rollbacks == 1


def _publish_snapshot_through_command(monkeypatch, snapshot, *, replay=False):
    captured = {}

    def publish(_self, _session, _job_id, *, expected_attempt, verified_manifest_digest=None):
        points = owner._validate_generation_publication_snapshot(
            snapshot,
            expected_attempt=expected_attempt,
            require_recorded_manifest=replay,
        )
        captured["points"] = points
        if not replay:
            captured["change_set"] = owner._generation_publication_change_set(
                snapshot,
                manifest_digest="m" * 64,
                finalize=True,
                now=datetime.now(timezone.utc),
                artifact_inventory=owner.CurrentArtifactLockInventory((), ()),
            )
        return True

    monkeypatch.setattr(
        owner._FinalGenerationPublicationSql,
        "_publish_final_generation",
        publish,
    )
    command = owner.FinalGenerationPublicationCommand(lambda: None)
    assert command.publish_job(
        snapshot.job.job_id,
        expected_attempt=snapshot.job.attempt,
        verified_manifest_digest=("m" * 64 if replay else None),
    )
    return captured


def test_final_publish_preserves_warnings_and_completes_restore(monkeypatch) -> None:
    snapshot = _publication_snapshot(
        lifecycle_status="restoring",
        warning_codes=["preview_degraded"],
    )
    captured = _publish_snapshot_through_command(monkeypatch, snapshot)
    desired = captured["change_set"].document
    assert desired.intake_status == "ready_with_warnings"
    assert desired.warning_codes == ["preview_degraded"]
    assert desired.lifecycle_status == "active"
    assert desired.resource_lifecycle_epoch == snapshot.document.resource_lifecycle_epoch + 1


def test_final_publish_accepts_current_reindex_source(monkeypatch) -> None:
    snapshot = _publication_snapshot(
        job_kind="reindex",
        active_processing=1,
    )
    captured = _publish_snapshot_through_command(monkeypatch, snapshot)
    assert len(captured["points"]) == 1


def test_final_publish_rejects_stale_reindex_source(monkeypatch) -> None:
    snapshot = _publication_snapshot(
        job_kind="reindex",
        active_processing=2,
    )
    with pytest.raises(ValueError, match="publication_source_generation_changed"):
        _publish_snapshot_through_command(monkeypatch, snapshot)


def test_final_publish_rejects_stale_active_index_pointer(monkeypatch) -> None:
    snapshot = _publication_snapshot(active_index="index-foreign-active")
    with pytest.raises(ValueError, match="publication_source_generation_changed"):
        _publish_snapshot_through_command(monkeypatch, snapshot)


def test_final_publish_exact_manifest_and_pointer_replay(monkeypatch) -> None:
    snapshot = _publication_snapshot(
        job_status="succeeded",
        active_index="index-new",
        active_processing=1,
    )
    captured = _publish_snapshot_through_command(
        monkeypatch,
        snapshot,
        replay=True,
    )
    assert len(captured["points"]) == 1


@pytest.mark.parametrize("page_revision_id", (None, "revision-foreign"))
def test_final_publish_rejects_missing_or_foreign_page_revision(
    monkeypatch,
    page_revision_id,
) -> None:
    current_revision_id = "revision-current"
    base = _publication_snapshot()
    snapshot = replace(
        base,
        job=replace(
            base.job,
            processing_revision_id=current_revision_id,
        ),
        index=replace(
            base.index,
            processing_revision_id=current_revision_id,
        ),
        evidence_revision_ids=(current_revision_id,),
        page_revision_ids=(page_revision_id,),
        chunk_revision_ids=(current_revision_id,),
    )
    with pytest.raises(
        owner.DocumentProcessingCurrentnessConflict,
        match="publication owner graph is no longer current",
    ):
        _publish_snapshot_through_command(monkeypatch, snapshot)


@pytest.mark.parametrize(
    "family",
    ("generation", "index", "evidence", "page", "chunk", "vector"),
)
def test_final_publish_rejects_foreign_lineage_across_generation_graph(
    monkeypatch,
    family,
) -> None:
    snapshot = _publication_snapshot()
    if family == "generation":
        snapshot = replace(
            snapshot,
            generation=replace(snapshot.generation, document_id="foreign-document"),
        )
    elif family == "index":
        snapshot = replace(
            snapshot,
            index=replace(snapshot.index, document_version_id="foreign-version"),
        )
    elif family == "evidence":
        snapshot = replace(
            snapshot,
            evidence=(replace(snapshot.evidence[0], document_id="foreign-document"),),
        )
    elif family == "page":
        page = owner.EvidencePageArtifact(
            artifact_id="page-1",
            tenant_id="atlas-production",
            document_version_id="foreign-version",
            source_page_index=0,
            source_page_label="1",
            artifact_kind="pdf_single_page",
            artifact_digest="a" * 64,
            content_length=1,
            storage_artifact_id="artifact-page-1",
            source_crop_box=[0.0, 0.0, 1.0, 1.0],
            source_rotation=0,
            geometry_transform_version="v1",
            renderer_version="v1",
            created_at=datetime.now(timezone.utc).isoformat(),
            processing_generation=1,
        )
        snapshot = replace(snapshot, pages=(page,))
    elif family == "chunk":
        snapshot = replace(
            snapshot,
            chunks=(replace(snapshot.chunks[0], index_generation_id="foreign-index"),),
        )
    else:
        snapshot = replace(
            snapshot,
            vectors=(replace(snapshot.vectors[0], chunk_id="foreign-chunk"),),
        )
    with pytest.raises(owner.DocumentProcessingCurrentnessConflict):
        _publish_snapshot_through_command(monkeypatch, snapshot)


@pytest.mark.parametrize("eligible", (True, False))
def test_cleanup_staging_selects_only_retired_non_active_generations(eligible) -> None:
    now = datetime.now(timezone.utc)
    checkpoint = owner.async_rows.AtlasProcessingCheckpointRow(
        **asdict(
            owner.ProcessingCheckpointRecord(
                job_id="job-1",
                unit_kind="page",
                unit_start=1,
                unit_end=1,
                batch_id="job-1:page:1",
                claim_token="claim-1",
                fence=1,
                input_fingerprint="a" * 64,
                output_digest="b" * 64,
                evidence_count=1,
                chunk_count=1,
                preview_count=0,
                committed_at=now,
            )
        )
    )

    class CleanupSession(_SequencedSession):
        def __init__(self):
            super().__init__()
            self.statement = None

        def scalars(self, statement):
            self.statement = statement
            return _Rows((checkpoint,) if eligible else ())

    session = CleanupSession()
    owner.BatchCheckpointCommand(lambda: session).cleanup_staging(limit=1)
    sql = str(session.statement)
    assert "atlas_index_generations.status" in sql
    assert "IS DISTINCT FROM" in sql
    if eligible:
        assert session.deleted == [checkpoint]
        assert session.commits == 1
    else:
        assert session.deleted == []
        assert session.commits == 0


@pytest.mark.parametrize("mismatch", (False, True))
def test_enqueue_index_batch_exact_replay_and_mismatch(monkeypatch, mismatch) -> None:
    now = datetime.now(timezone.utc)
    job = replace(_job_record(status="running"), stage="indexing")
    checkpoint_record = owner.ProcessingCheckpointRecord(
        job_id=job.job_id,
        unit_kind="page",
        unit_start=1,
        unit_end=1,
        batch_id=f"{job.job_id}:page:1",
        claim_token="claim-1",
        fence=job.fence,
        input_fingerprint="a" * 64,
        output_digest="b" * 64,
        evidence_count=1,
        chunk_count=1,
        preview_count=0,
        committed_at=now,
    )
    session = _SequencedSession(
        scalars=(
            SimpleNamespace(**asdict(job)),
            SimpleNamespace(**asdict(checkpoint_record)),
            _document_parent(job.job_id),
            SimpleNamespace(**asdict(job)),
        )
    )

    def apply(_session, change_set, **_kwargs):
        if mismatch:
            raise owner.DocumentProcessingCurrentnessConflict("outbox replay mismatch")
        outbox_id = change_set.outbox[0].record.outbox_id
        return frozenset({owner._replay_key("outbox", outbox_id)})

    monkeypatch.setattr(owner, "_apply_sealed_family_mutation", apply)
    monkeypatch.setattr(
        owner,
        "_acquire_document_processing_mutation",
        lambda *_args, **_kwargs: object(),
    )
    command = owner.BatchCheckpointCommand(lambda: session)
    if mismatch:
        with pytest.raises(owner.DocumentProcessingCurrentnessConflict, match="mismatch"):
            command.enqueue_index_batch(
                job.job_id,
                checkpoint_record.batch_id,
                expected_attempt=job.attempt,
            )
        assert session.rollbacks == 1
    else:
        assert command.enqueue_index_batch(
            job.job_id,
            checkpoint_record.batch_id,
            expected_attempt=job.attempt,
        )
        assert session.commits == 0


def test_mark_batch_indexed_rejects_foreign_vector_mapping() -> None:
    job = replace(_job_record(status="running"), stage="indexing")
    generation = SimpleNamespace(
        index_generation_id=job.index_generation_id,
        status="building",
        actual_point_count=0,
        actual_fts_count=1,
    )
    chunk = SimpleNamespace(chunk_id="chunk-1")
    session = _SequencedSession(
        scalars=(SimpleNamespace(**asdict(job)), generation),
        collections=((chunk,),),
    )
    with pytest.raises(ValueError, match="foreign batch owner"):
        owner.BatchCheckpointCommand(lambda: session).mark_batch_indexed(
            job_id=job.job_id,
            batch_id=f"{job.job_id}:page:1",
            mappings=[
                {
                    "index_generation_id": "foreign-index",
                    "point_id": "point-1",
                    "chunk_id": chunk.chunk_id,
                    "payload_digest": "a" * 64,
                    "vector_digest": "b" * 64,
                }
            ],
            expected_attempt=job.attempt,
        )
    assert session.commits == 0
    assert session.rollbacks == 1
