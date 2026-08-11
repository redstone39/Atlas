from __future__ import annotations

from dataclasses import asdict, fields, replace
import inspect
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_model_routing_adapter import (
    PostgresModelRoutingAdapter,
)
from atlas_production.modules.processing_pipeline.public import (
    ProcessingExecutionSnapshot,
)
from atlas_production.infrastructure.postgres_owner.model_routing import (
    ConnectionDisablePrecondition,
    DefaultRouteIntent,
    DefaultRouteConnectionPrecondition,
    FinalizeDefaultRouteCommand,
    FinalizeDefaultRouteInput,
    FinalizeInvocationLifecycleCommand,
    FinalizeInvocationLifecycleInput,
    FinalizeProviderConfigurationCommand,
    FinalizeProviderConfigurationInput,
    ModelInvocationWriter,
    ModelInvocationWrite,
    ModelRouteWrite,
    ModelRoutingCurrentnessConflict,
    ProviderConnectionIntent,
    ProviderAttemptSnapshot,
    ProviderConnectionWrite,
)
from atlas_production.infrastructure.postgres_owner.ops import PostgresOpsReadinessRepository
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.postgres_owner.processing_registry import (
    FinalizePluginLifecycleCommand,
    FinalizePluginLifecycleInput,
    FinalizeProcessingProfileCommand,
    PluginVersionWrite,
    PluginDisablePrecondition,
    PluginActivationDependency,
    ProfileActivationPrecondition,
    ProcessingProfileRevisionWrite,
    ProcessingRegistryCurrentnessConflict,
    PluginPackageIntent,
    FinalizePluginPackageInput,
    FinalizeProcessingProfileInput,
    FinalizeProcessingRunCommand,
    FinalizeProcessingRunInput,
    ProcessingRunWrite,
    ProcessingRegistryReadModel,
    SourceRegionWrite,
)
from atlas_production.infrastructure.postgres_processing_adapter import (
    PostgresProcessingAdapter,
)
from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.modules.model_routing.ports import ModelRoutingRepository
from atlas_production.modules.model_routing.service import ModelRoutingService
from atlas_production.modules.processing_pipeline.records import (
    EvidenceRecord,
    PluginVersionRef,
    PluginVersionRecord,
    ProcessingIdempotencyRecord,
    ProcessingRun,
    ProcessingProfileRevision,
    SourceRegion,
)
from atlas_production.shared.public import AuditEventRecord
from atlas_production.providers import ProviderError
from atlas_production.worker_composition import (
    BeatWorkerComposition,
    DispatchWorkerComposition,
    IndexingWorkerComposition,
    MaintenanceWorkerComposition,
    ProcessingWorkerComposition,
    WorkerPortFactories,
    build_worker_composition,
    configure_worker_port_factories,
)
from tests import model_route_runtime_policy


class Rows:
    def __init__(self, values=()): self.values = list(values)
    def all(self): return list(self.values)
    def __iter__(self): return iter(self.values)


class Result:
    def __init__(self, values=()): self.values = values; self.rowcount = 1
    def scalars(self): return Rows(self.values)
    def all(self): return list(self.values)


class Session:
    def __init__(self, *, scalars=(), rows=(), fail_audit=False):
        self.scalar_values = list(scalars)
        self.row_values = list(rows)
        self.active = False
        self.rollbacks = 0
        self.commits = 0
        self.expire_calls = 0
        self.executed = []
        self.fail_audit = fail_audit
        self.merged = []
        self.added = []
    def __enter__(self): self.active = True; return self
    def __exit__(self, *_args): self.active = False
    def scalar(self, statement):
        self.executed.append(statement)
        return self.scalar_values.pop(0) if self.scalar_values else None
    def scalars(self, statement):
        self.executed.append(statement)
        return Rows(self.row_values.pop(0) if self.row_values else ())
    def execute(self, statement, parameters=None):
        self.executed.append((statement, parameters))
        return Result(self.row_values.pop(0) if self.row_values else ())
    def add(self, row):
        if self.fail_audit and isinstance(row, AtlasAuditEventRow):
            raise RuntimeError("audit unavailable")
        self.added.append(row)
    def merge(self, row): self.merged.append(row); return row
    def flush(self): pass
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def expire_all(self): self.expire_calls += 1


class SessionFactory:
    def __init__(self, *sessions): self.sessions = list(sessions)
    def __call__(self): return self.sessions.pop(0)


def _empty_processing_state() -> SimpleNamespace:
    return SimpleNamespace(
        plugin_packages={}, plugin_versions={}, runtime_profiles={},
        processing_profiles={}, processing_profile_revisions={}, processing_runs={},
        parser_adapter_invocations={}, source_regions={}, extraction_candidates={},
        candidate_groups={}, promotion_decisions={}, kpel_normalization_handoffs={},
        processing_routing_decisions={}, evidence_build_traces={},
        processing_idempotency={}, audit_events=[],
    )


def test_postgres_processing_adapter_exposes_the_live_route_facade() -> None:
    expected = {
        "upload_package", "list_plugins", "get_plugin", "mutate_plugin",
        "create_profile", "list_profiles", "create_revision", "activate_revision",
        "list_runs", "get_run", "retry_run", "run_ingestion",
    }
    assert expected <= {
        name for name, member in inspect.getmembers(
            PostgresProcessingAdapter, predicate=inspect.isfunction
        ) if not name.startswith("_")
    }


def test_processing_route_provider_has_no_detached_aggregate_or_generic_uow() -> None:
    source = inspect.getsource(
        __import__(
            "atlas_production.infrastructure.postgres_processing_adapter",
            fromlist=["PostgresProcessingAdapter"],
        )
    )
    for forbidden in (
        "_DetachedProcessingState", "ContextVar", "read_state",
        "persist_operation", "package_upload_operation", "profile_operation",
        "run_operation", "_active_state", "_baseline",
    ):
        assert forbidden not in source


def test_profile_create_dispatches_one_typed_finalize_input(monkeypatch) -> None:
    adapter = PostgresProcessingAdapter(lambda: None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_processing_adapter.BeginProcessingProfileIntentCommand.execute",
        lambda *_args: SimpleNamespace(replay=None, profile=None, revisions=()),
    )
    finalized: list[FinalizeProcessingProfileInput] = []
    monkeypatch.setattr(FinalizeProcessingProfileCommand, "execute", lambda _self, request: finalized.append(request))
    actor = SimpleNamespace(actor_id="admin-1", system_role="admin")
    request = SimpleNamespace(
        idempotency_key="profile-key", profile_id="profile-1", display_name="Profile",
        model_dump=lambda: {"profile_id": "profile-1", "display_name": "Profile", "idempotency_key": "profile-key"},
    )

    result, status = adapter.create_profile(actor, request)

    assert status == 201
    assert result["profile_id"] == "profile-1"
    assert len(finalized) == 1
    assert finalized[0].profiles[0].profile_id == "profile-1"
    assert finalized[0].idempotency_record.idempotency_key == "profile-key"


def test_target_mutators_do_not_call_unrelated_bounded_list_reads() -> None:
    for method_name in (
        "upload_package", "mutate_plugin", "create_profile", "create_revision",
        "activate_revision", "run_ingestion", "retry_run",
    ):
        source = inspect.getsource(getattr(PostgresProcessingAdapter, method_name))
        for forbidden in (
            "list_packages", "list_plugin_versions", "list_runtime_profiles",
            "list_processing_profiles", "list_profile_revisions", "list_runs",
        ):
            assert forbidden not in source


def test_model_provider_discovery_runs_after_intent_session_closed(monkeypatch) -> None:
    state = SimpleNamespace(sql_active=False, provider_calls=0)

    def detached_execute(_self, _ids, _key: str) -> ProviderConnectionIntent:
        state.sql_active = True
        try:
            return ProviderConnectionIntent(None, (), (), ())
        finally:
            state.sql_active = False

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_model_routing_adapter.BeginProviderConnectionIntentCommand.execute",
        detached_execute,
    )

    class Provider:
        def discover_models(self):
            assert not state.sql_active
            state.provider_calls += 1
            return ["model-1"]

    connection = ProviderConnectionRecord(
        connection_id="connection-1",
        display_name="Provider",
        provider_type="openai_compatible",
        endpoint_url="https://provider.invalid/v1",
        status="configured",
        enabled=True,
        revision=1,
        created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:00:00+00:00",
    )
    adapter = PostgresModelRoutingAdapter(
        lambda: (_ for _ in ()).throw(AssertionError("unexpected SQL")),
        lambda *_args: Provider(),
    )
    with adapter.operation_intent([connection.connection_id]):
        assert adapter.discover_models(connection, "secret") == ["model-1"]
    assert state.provider_calls == 1


def test_connection_disable_adapter_attaches_default_link_precondition(
    monkeypatch,
) -> None:
    prior, disabled, replay = _provider_disable_records()
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_model_routing_adapter.BeginProviderConnectionIntentCommand.execute",
        lambda *_args: ProviderConnectionIntent(None, (prior,), (), ()),
    )
    finalized: list[FinalizeProviderConfigurationInput] = []
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_model_routing_adapter.FinalizeProviderConfigurationCommand.execute",
        lambda _self, request: finalized.append(request),
    )
    adapter = PostgresModelRoutingAdapter(
        lambda: None, lambda *_args: None
    )  # type: ignore[arg-type]
    with adapter.operation_intent([prior.connection_id]):
        adapter.commit_configuration(
            connections=[disabled], secrets=[], routes=[], audits=[],
            replay_factory=lambda _events: replay,
        )
    assert finalized[0].connection_disable_preconditions == (
        ConnectionDisablePrecondition("connection-B"),
    )


def test_postgres_model_adapter_matches_current_repository_port() -> None:
    methods = {
        name
        for name, value in ModelRoutingRepository.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert methods <= {
        name
        for name, value in PostgresModelRoutingAdapter.__dict__.items()
        if callable(value) and not name.startswith("_")
    }


def test_owner_modules_do_not_export_generic_repository_or_change_set() -> None:
    from atlas_production.infrastructure.postgres_owner import model_routing, processing_registry

    for module in (model_routing, processing_registry):
        assert not any(
            name.endswith("Repository") or name.endswith("ChangeSet")
            for name in module.__all__
        )
    source = inspect.getsource(PostgresModelRoutingAdapter.mutation_scope)
    assert "operation_intent" in source


def test_role_builder_constructs_only_role_dependencies() -> None:
    calls: list[str] = []

    def factory(name: str):
        def build():
            calls.append(name)
            return SimpleNamespace(name=name)
        return build

    configure_worker_port_factories(
        WorkerPortFactories(
            job=factory("job"),
            artifact=factory("artifact"),
            processing=factory("processing"),
            execution=factory("execution"),
            model_routing=factory("model_routing"),
            indexing=factory("indexing"),
        )
    )
    assert isinstance(build_worker_composition("dispatch"), DispatchWorkerComposition)
    assert calls == ["job"]
    calls.clear()
    assert isinstance(build_worker_composition("indexing"), IndexingWorkerComposition)
    assert calls == ["job", "indexing"]
    calls.clear()
    assert isinstance(build_worker_composition("maintenance"), MaintenanceWorkerComposition)
    assert calls == ["job", "artifact", "indexing"]
    calls.clear()
    assert isinstance(build_worker_composition("beat"), BeatWorkerComposition)
    assert calls == []
    calls.clear()
    assert isinstance(build_worker_composition("processing"), ProcessingWorkerComposition)
    assert calls == [
        "job",
        "artifact",
        "processing",
        "execution",
        "model_routing",
        "indexing",
    ]


def test_role_compositions_are_typed_and_immutable() -> None:
    assert [item.name for item in fields(DispatchWorkerComposition)] == ["job"]
    with pytest.raises(ValueError, match="unsupported worker role"):
        build_worker_composition("unknown")


def _invocation(status: str, **changes) -> ModelInvocationRecord:
    base = ModelInvocationRecord(
        invocation_id="invocation-1", route_id="route-1",
        provider_type="openai_compatible", model_name="model-1",
        status=status, created_at="2026-07-18T00:00:00+00:00",
        prompt_snapshot_ref="audit:prompt", response_schema_name="schema",
        response_schema_digest="a" * 64, route_revision=1,
        runtime_policy_schema_version="model-route-runtime-policy-v8",
        runtime_policy_revision=1,
        execution_key="execution-1", subject_kind="document",
        subject_ref="document-1",
    )
    return replace(base, **changes)


def _invocation_row(record: ModelInvocationRecord):
    return SimpleNamespace(**asdict(record))


def _invocation_audit() -> AuditEventRecord:
    return AuditEventRecord(
        event_id="audit-invocation", event_type="model_invocation.started",
        actor_id="atlas-model-runtime", target_ref="model-invocation:invocation-1",
        project_id=None, message_code="model.provider_model_route_passed_the_controlled_test",
        metadata={}, created_at="2026-07-18T00:01:00+00:00",
    )


def _model_route(route_id: str, *, revision: int, is_default: bool) -> ModelRouteRecord:
    return ModelRouteRecord(
        route_id=route_id, display_name=route_id,
        provider_type="openai_compatible", model_name=f"model-{route_id}",
        connection_id=f"connection-{route_id}",
        runtime_policy=ModelRouteRuntimePolicy(
            **model_route_runtime_policy(), revision=1
        ),
        status="test_passed", enabled=True, revision=revision,
        last_tested_at="2026-07-18T00:00:00+00:00", is_default=is_default,
    )


def _default_replay() -> ModelRoutingReplayRecord:
    route = _model_route("B", revision=8, is_default=True)
    return ModelRoutingReplayRecord(
        idempotency_key="default-key", operation="model_route_default",
        target_ref="model-route:B", request_fingerprint="f" * 64,
        response_model="ModelRouteStatus", response_payload={
            "message_code": "model.default_model_route_is_updated",
            "message_params": {}, "route_id": route.route_id,
            "display_name": route.display_name, "provider_type": route.provider_type,
            "model_name": route.model_name, "connection_id": route.connection_id,
            "status": route.status, "enabled": route.enabled,
            "supports_vision": route.supports_vision, "revision": route.revision,
            "runtime_policy": asdict(route.runtime_policy),
            "audit_event_ref": "audit-invocation", "is_default": True,
        },
        status_code=200, created_at="2026-07-18T00:00:00+00:00",
    )


def _provider_disable_records():
    prior = ProviderConnectionRecord(
        connection_id="connection-B", display_name="Provider B",
        provider_type="openai_compatible", endpoint_url="https://provider.invalid/v1",
        status="verified", enabled=True, revision=1,
        created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:00:00+00:00",
    )
    candidate = replace(
        prior, status="disabled", enabled=False, revision=2,
        updated_at="2026-07-18T00:01:00+00:00",
    )
    replay = ModelRoutingReplayRecord(
        idempotency_key="disable-connection-B",
        operation="provider_connection_update",
        target_ref="provider-connection:connection-B",
        request_fingerprint="e" * 64,
        response_model="ProviderConnectionStatus",
        response_payload={
            "message_code": "provider.connection_is_updated", "message_params": {},
            "connection_id": candidate.connection_id,
            "display_name": candidate.display_name,
            "provider_type": candidate.provider_type,
            "endpoint_url": candidate.endpoint_url,
            "credential_configured": True, "status": candidate.status,
            "enabled": candidate.enabled, "linked_model_count": 1,
            "revision": candidate.revision,
            "last_verified_at": candidate.last_verified_at,
            "last_rotated_at": candidate.last_rotated_at,
            "audit_event_ref": "audit-invocation",
        },
        status_code=200, created_at="2026-07-18T00:01:00+00:00",
    )
    return prior, candidate, replay


def _default_b_request() -> FinalizeDefaultRouteInput:
    prior_a = _model_route("A", revision=3, is_default=True)
    prior_b = _model_route("B", revision=7, is_default=False)
    return FinalizeDefaultRouteInput(
        routes=(
            ModelRouteWrite(replace(prior_a, revision=4, is_default=False), 3),
            ModelRouteWrite(replace(prior_b, revision=8, is_default=True), 7),
        ),
        audit_events=(_invocation_audit(),), replay=_default_replay(),
        connection_precondition=DefaultRouteConnectionPrecondition(
            "connection-B", 1, True, "verified"
        ),
    )


def test_default_route_a_to_b_uses_both_exact_preimages_and_rejects_stale() -> None:
    prior_a = _model_route("A", revision=3, is_default=True)
    prior_b = _model_route("B", revision=7, is_default=False)
    candidate_a = replace(prior_a, revision=4, is_default=False)
    candidate_b = replace(prior_b, revision=8, is_default=True)
    request = FinalizeDefaultRouteInput(
        routes=(ModelRouteWrite(candidate_a, 3), ModelRouteWrite(candidate_b, 7)),
        audit_events=(_invocation_audit(),), replay=_default_replay(),
    )
    success = Session(
        scalars=(
            SimpleNamespace(route_id="A", revision=3, is_default=True),
            SimpleNamespace(route_id="B", revision=7, is_default=False),
            SimpleNamespace(route_id="A", revision=3, is_default=True),
            None,
        )
    )
    FinalizeDefaultRouteCommand(SessionFactory(success)).execute(request)
    assert success.commits == 1
    assert {row.route_id for row in success.merged} == {"A", "B"}

    stale_selected = Session(
        scalars=(
            SimpleNamespace(route_id="A", revision=3, is_default=True),
            SimpleNamespace(route_id="B", revision=8, is_default=False),
        )
    )
    with pytest.raises(ModelRoutingCurrentnessConflict, match="route revision"):
        FinalizeDefaultRouteCommand(SessionFactory(stale_selected)).execute(request)
    assert stale_selected.rollbacks == 1

    stale_prior = Session(
        scalars=(SimpleNamespace(route_id="A", revision=4, is_default=True),)
    )
    with pytest.raises(ModelRoutingCurrentnessConflict, match="route revision"):
        FinalizeDefaultRouteCommand(SessionFactory(stale_prior)).execute(request)
    assert stale_prior.rollbacks == 1


def test_connection_disable_then_set_default_rejects_stale_connection() -> None:
    _prior, disabled, replay = _provider_disable_records()
    disable = FinalizeProviderConfigurationInput(
        connections=(ProviderConnectionWrite(disabled, 1),), secrets=(), routes=(),
        replay=replay, audit_events=(_invocation_audit(),),
        connection_disable_preconditions=(
            ConnectionDisablePrecondition("connection-B"),
        ),
    )
    disable_session = Session(
        scalars=(None, SimpleNamespace(revision=1), None)
    )
    FinalizeProviderConfigurationCommand(
        SessionFactory(disable_session)
    ).execute(disable)
    assert disable_session.commits == 1

    stale_default = Session(
        scalars=(
            SimpleNamespace(
                connection_id="connection-B", revision=2,
                enabled=False, status="disabled",
            ),
        )
    )
    with pytest.raises(
        ModelRoutingCurrentnessConflict, match="connection changed or is unavailable"
    ):
        FinalizeDefaultRouteCommand(SessionFactory(stale_default)).execute(
            _default_b_request()
        )
    assert stale_default.rollbacks == 1


def test_set_default_then_connection_disable_rejects_current_default_link() -> None:
    default_session = Session(
        scalars=(
            SimpleNamespace(
                connection_id="connection-B", revision=1,
                enabled=True, status="verified",
            ),
            SimpleNamespace(route_id="A", revision=3, is_default=True),
            SimpleNamespace(route_id="B", revision=7, is_default=False),
            SimpleNamespace(route_id="A", revision=3, is_default=True),
            None,
        )
    )
    FinalizeDefaultRouteCommand(SessionFactory(default_session)).execute(
        _default_b_request()
    )
    assert default_session.commits == 1

    _prior, disabled, replay = _provider_disable_records()
    stale_disable = Session(
        scalars=(SimpleNamespace(route_id="B", connection_id="connection-B"),)
    )
    with pytest.raises(
        ModelRoutingCurrentnessConflict, match="current default route"
    ):
        FinalizeProviderConfigurationCommand(
            SessionFactory(stale_disable)
        ).execute(
            FinalizeProviderConfigurationInput(
                connections=(ProviderConnectionWrite(disabled, 1),),
                secrets=(), routes=(), replay=replay,
                audit_events=(_invocation_audit(),),
                connection_disable_preconditions=(
                    ConnectionDisablePrecondition("connection-B"),
                ),
            )
        )
    assert stale_disable.rollbacks == 1


def test_default_route_scope_uses_named_selected_and_prior_intent(monkeypatch) -> None:
    selected = _model_route("B", revision=7, is_default=False)
    prior = _model_route("A", revision=3, is_default=True)
    concurrent_default = _model_route("C", revision=2, is_default=True)
    calls: list[tuple[str, str]] = []

    def detached(_self, key: str, route_id: str) -> DefaultRouteIntent:
        calls.append((key, route_id))
        return DefaultRouteIntent(None, selected, prior)

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_model_routing_adapter.BeginDefaultRouteIntentCommand.execute",
        detached,
    )
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.ModelRoutingReadModel.default_route",
        lambda _self: concurrent_default,
    )
    selected_connection, _disabled, _replay = _provider_disable_records()
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.ModelRoutingReadModel.get_connection",
        lambda _self, _connection_id: selected_connection,
    )
    adapter = PostgresModelRoutingAdapter(lambda: None, lambda *_args: None)  # type: ignore[arg-type]
    finalized: list[FinalizeDefaultRouteInput] = []
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_model_routing_adapter.FinalizeDefaultRouteCommand.execute",
        lambda _self, request: finalized.append(request),
    )
    with adapter.default_route_scope("default-key", "B"):
        intent = adapter._active_intent()
        assert isinstance(intent, DefaultRouteIntent)
        assert intent.selected is selected
        assert intent.current_default is prior
        assert adapter.default_route().route_id == "A"
        assert adapter.get_route("B").revision == 7
        with pytest.raises(
            ModelRoutingCurrentnessConflict, match="identity changed"
        ):
            adapter.commit_configuration(
                connections=[], secrets=[],
                routes=[
                    replace(concurrent_default, revision=3, is_default=False),
                    replace(selected, revision=8, is_default=True),
                ],
                audits=[], replay_factory=lambda _events: _default_replay(),
            )
        adapter.commit_configuration(
            connections=[], secrets=[],
            routes=[
                replace(prior, revision=4, is_default=False),
                replace(selected, revision=8, is_default=True),
            ],
            audits=[], replay_factory=lambda _events: _default_replay(),
        )
        assert finalized[0].connection_precondition == (
            DefaultRouteConnectionPrecondition(
                "connection-B", 1, True, "verified"
            )
        )
    assert calls == [("default-key", "B")]


def test_same_session_invocation_writer_validates_preimage_before_raw_write(
    monkeypatch,
) -> None:
    writes: list[ModelInvocationRecord] = []
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.model_routing.write_invocation_row",
        lambda _session, invocation: writes.append(invocation),
    )
    planned = _invocation("planned")
    started = replace(planned, status="started", started_at="2026-07-18T00:01:00+00:00")
    session = Session(scalars=(_invocation_row(planned), _invocation_row(planned), _invocation_row(planned)))
    writer = ModelInvocationWriter(session)  # type: ignore[arg-type]
    writer.write(started, locked_prior=planned)
    assert writes == [started]

    with pytest.raises(ModelRoutingCurrentnessConflict, match="lineage"):
        writer.write(replace(started, route_id="foreign"), locked_prior=planned)
    try:
        writer.write(replace(planned, status="completed"), locked_prior=planned)
    except ModelRoutingCurrentnessConflict:
        session.rollback()
    assert session.rollbacks == 1
    assert writes == [started]


@pytest.mark.parametrize("terminal_status", ("completed", "failed"))
def test_same_session_invocation_writer_preserves_terminal_checkpoint_create_and_replay(
    monkeypatch,
    terminal_status: str,
) -> None:
    writes: list[ModelInvocationRecord] = []
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.model_routing.write_invocation_row",
        lambda _session, invocation: writes.append(invocation),
    )
    planned = _invocation("planned")
    terminal = replace(
        planned,
        status=terminal_status,
        completed_at="2026-07-18T00:02:00+00:00",
        error_code="provider_failed" if terminal_status == "failed" else None,
    )
    session = Session(
        scalars=(
            None,
            _invocation_row(terminal),
            _invocation_row(planned),
        )
    )
    writer = ModelInvocationWriter(session)  # type: ignore[arg-type]

    writer.write(terminal)
    assert writes == [terminal]
    writer.write(terminal)
    assert writes == [terminal]
    with pytest.raises(ModelRoutingCurrentnessConflict, match="lineage"):
        writer.write(replace(terminal, route_id="foreign"))


def test_invocation_lifecycle_is_monotonic_lineage_safe_and_atomic() -> None:
    audit = _invocation_audit()
    planned = _invocation("planned")
    create = Session(scalars=(None,))
    FinalizeInvocationLifecycleCommand(SessionFactory(create)).execute(
        FinalizeInvocationLifecycleInput(ModelInvocationWrite(planned, None), (audit,))
    )
    assert create.commits == 1

    started = replace(planned, status="started", started_at="2026-07-18T00:01:00+00:00")
    transition = Session(scalars=(_invocation_row(planned),))
    FinalizeInvocationLifecycleCommand(SessionFactory(transition)).execute(
        FinalizeInvocationLifecycleInput(ModelInvocationWrite(started, planned), (audit,))
    )
    assert transition.commits == 1

    lineage = Session(scalars=(_invocation_row(planned),))
    with pytest.raises(ModelRoutingCurrentnessConflict, match="lineage"):
        FinalizeInvocationLifecycleCommand(SessionFactory(lineage)).execute(
            FinalizeInvocationLifecycleInput(
                ModelInvocationWrite(replace(started, route_id="route-foreign"), planned),
                (audit,),
            )
        )

    completed = replace(started, status="completed", completed_at="2026-07-18T00:02:00+00:00")
    terminal = Session(scalars=(_invocation_row(completed),))
    assert FinalizeInvocationLifecycleCommand(SessionFactory(terminal)).execute(
        FinalizeInvocationLifecycleInput(ModelInvocationWrite(completed, completed), (audit,))
    ) is True
    assert terminal.merged == []

    illegal = Session(scalars=(_invocation_row(completed),))
    with pytest.raises(ModelRoutingCurrentnessConflict, match="monotonic"):
        FinalizeInvocationLifecycleCommand(SessionFactory(illegal)).execute(
            FinalizeInvocationLifecycleInput(
                ModelInvocationWrite(replace(completed, status="failed"), completed),
                (audit,),
            )
        )

    audit_failure = Session(scalars=(_invocation_row(planned),), fail_audit=True)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        FinalizeInvocationLifecycleCommand(SessionFactory(audit_failure)).execute(
            FinalizeInvocationLifecycleInput(ModelInvocationWrite(started, planned), (audit,))
        )
    assert audit_failure.rollbacks == 1


def test_processing_revision_and_audit_rollback_are_atomic() -> None:
    version = PluginVersionRecord(
        plugin_id="plugin-1", plugin_version="1.0.0",
        package_digest="sha256:package", runtime_profile="runtime-1",
        plugin_kind="base_parser", status="verified",
        trust_provenance="trusted_signature", revision=2,
        created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:01:00+00:00", descriptor={},
    )
    replay = ProcessingIdempotencyRecord(
        "key-1", "package.validate", "digest", {"status": "verified"}, 200,
        "2026-07-18T00:01:00+00:00",
    )
    audit = AuditEventRecord(
        event_id="audit-1", event_type="processing_plugin.validated",
        actor_id="admin-1", target_ref="processing-plugin:plugin-1:1.0.0",
        project_id=None, message_code="processing.plugin_mutation_is_recorded",
        metadata={}, created_at="2026-07-18T00:01:00+00:00",
    )
    changed = SimpleNamespace(payload={"revision": 3})
    stale_session = Session(scalars=(changed,))
    with pytest.raises(ProcessingRegistryCurrentnessConflict, match="revision"):
        FinalizePluginLifecycleCommand(SessionFactory(stale_session)).execute(
            FinalizePluginLifecycleInput(
                (PluginVersionWrite(version, 1),), replay, (audit,)
            )
        )
    assert stale_session.rollbacks == 1

    audit_session = Session(
        scalars=(SimpleNamespace(payload={"revision": 1}), None),
        fail_audit=True,
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        FinalizePluginLifecycleCommand(SessionFactory(audit_session)).execute(
            FinalizePluginLifecycleInput(
                (PluginVersionWrite(version, 1),), replay, (audit,)
            )
        )
    assert audit_session.rollbacks == 1
    assert audit_session.commits == 0


def test_plugin_disable_rechecks_profile_activation_predicate_under_lock() -> None:
    version = PluginVersionRecord(
        plugin_id="plugin-race", plugin_version="1.0.0",
        package_digest="sha256:race", runtime_profile="runtime-1",
        plugin_kind="region_processor", status="disabled",
        trust_provenance="trusted_signature", revision=2,
        created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:01:00+00:00",
        canary_passed_at="2026-07-18T00:00:30+00:00", descriptor={},
    )
    replay = ProcessingIdempotencyRecord(
        "disable-race", "package.disable", "digest", {"status": "disabled"},
        200, "2026-07-18T00:01:00+00:00",
    )
    active_profile_row = SimpleNamespace(payload={
        "profile_id": "profile-activated-concurrently", "revision": 1,
        "status": "active", "accepted_media_types": ["application/pdf"],
        "base_parser_plugin_ref": {
            "plugin_id": "base", "plugin_version": "1.0.0"
        },
        "eligible_processor_plugin_refs": [{
            "plugin_id": "plugin-race", "plugin_version": "1.0.0"
        }],
    })
    stale_disable = Session(
        scalars=(active_profile_row,), rows=((), (), ())
    )
    with pytest.raises(
        ProcessingRegistryCurrentnessConflict, match="became referenced"
    ):
        FinalizePluginLifecycleCommand(SessionFactory(stale_disable)).execute(
            FinalizePluginLifecycleInput(
                (PluginVersionWrite(version, 1),), replay, (_invocation_audit(),),
                PluginDisablePrecondition("plugin-race", "1.0.0"),
            )
        )
    assert stale_disable.rollbacks == 1
    assert stale_disable.commits == 0


def test_overlapping_profile_activations_recheck_mime_predicate_under_lock() -> None:
    parser = PluginVersionRef("base", "1.0.0", "sha256:base", "runtime-1")
    candidate = ProcessingProfileRevision(
        profile_id="profile-b", revision=1, status="active",
        accepted_media_types=("application/pdf",), base_parser_plugin_ref=parser,
        mandatory_processor_plugin_refs=(), eligible_processor_plugin_refs=(),
        plugin_priority=(), planner_enabled=False, planner_model_route_id=None,
        channel_registry_version="kpel-registry-v0.1",
        trait_registry_version="kpel-registry-v0.1", max_regions_per_plan=100,
        max_modules_per_region=4, max_total_plugin_invocations=500,
        planner_failure_behavior="mandatory_only", created_by="admin",
        created_at="2026-07-18T00:00:00+00:00",
        activated_at="2026-07-18T00:01:00+00:00",
    )
    conflicting_active = SimpleNamespace(payload={
        "profile_id": "profile-a", "revision": 1, "status": "active",
        "accepted_media_types": ["application/pdf"],
        "base_parser_plugin_ref": {
            "plugin_id": "base", "plugin_version": "1.0.0"
        },
        "eligible_processor_plugin_refs": [],
    })
    replay = ProcessingIdempotencyRecord(
        "activate-b", "profile.activate", "digest", {"status": "active"},
        200, "2026-07-18T00:01:00+00:00",
    )
    stale_activation = Session(
        scalars=(conflicting_active,), rows=((), (), ())
    )
    with pytest.raises(
        ProcessingRegistryCurrentnessConflict, match="MIME predicate"
    ):
        FinalizeProcessingProfileCommand(
            SessionFactory(stale_activation)
        ).execute(
            FinalizeProcessingProfileInput(
                (), (ProcessingProfileRevisionWrite(candidate, "draft"),),
                replay, (_invocation_audit(),),
                ProfileActivationPrecondition(
                    "profile-b", 1, ("application/pdf",), ()
                ),
            )
        )
    assert stale_activation.rollbacks == 1
    assert stale_activation.commits == 0


def test_profile_activation_rechecks_plugin_disable_preimage_under_lock() -> None:
    parser = PluginVersionRef(
        "plugin-race", "1.0.0", "sha256:race", "runtime-1"
    )
    candidate = ProcessingProfileRevision(
        profile_id="profile-race", revision=1, status="active",
        accepted_media_types=("application/pdf",), base_parser_plugin_ref=parser,
        mandatory_processor_plugin_refs=(), eligible_processor_plugin_refs=(),
        plugin_priority=(), planner_enabled=False, planner_model_route_id=None,
        channel_registry_version="kpel-registry-v0.1",
        trait_registry_version="kpel-registry-v0.1", max_regions_per_plan=100,
        max_modules_per_region=4, max_total_plugin_invocations=500,
        planner_failure_behavior="mandatory_only", created_by="admin",
        created_at="2026-07-18T00:00:00+00:00",
        activated_at="2026-07-18T00:01:00+00:00",
    )
    disabled_plugin_row = SimpleNamespace(payload={
        "revision": 2, "status": "disabled",
        "trust_provenance": "trusted_signature",
        "canary_passed_at": "2026-07-18T00:00:30+00:00",
    })
    replay = ProcessingIdempotencyRecord(
        "activate-race", "profile.activate", "digest", {"status": "active"},
        200, "2026-07-18T00:01:00+00:00",
    )
    stale_activation = Session(
        scalars=(None, disabled_plugin_row), rows=((), (), ())
    )
    with pytest.raises(
        ProcessingRegistryCurrentnessConflict, match="plugin dependency changed"
    ):
        FinalizeProcessingProfileCommand(
            SessionFactory(stale_activation)
        ).execute(
            FinalizeProcessingProfileInput(
                (), (ProcessingProfileRevisionWrite(candidate, "draft"),),
                replay, (_invocation_audit(),),
                ProfileActivationPrecondition(
                    "profile-race", 1, ("application/pdf",),
                    (
                        PluginActivationDependency(
                            "plugin-race", "1.0.0", 1, "verified",
                            "trusted_signature",
                            "2026-07-18T00:00:30+00:00",
                        ),
                    ),
                ),
            )
        )
    assert stale_activation.rollbacks == 1


def _run_graph_records():
    run = ProcessingRun(
        run_id="run-1", document_id="document-1",
        document_version_id="version-1", profile_id="profile-1",
        profile_revision=1, status="succeeded", attempt=1,
        created_by="admin", created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:01:00+00:00",
        policy_snapshot_payload={
            "policy_snapshot_schema": "processing-policy-snapshot-v1",
            "document": {"document_id": "document-1", "lifecycle_status": "active", "source_digest": "digest"},
            "document_version": {"document_version_id": "version-1", "status": "ready", "source_digest": "digest", "content_digest": "digest"},
            "tags": [], "project_policies": [], "project_acl": [],
        },
    )
    region = SourceRegion("region-1", "run-1", {"region_kind": "page"})
    replay = ProcessingIdempotencyRecord(
        "run-key", "run.execute", "digest", {"status": "succeeded"}, 200,
        "2026-07-18T00:01:00+00:00",
    )
    audit = AuditEventRecord(
        event_id="audit-run", event_type="processing_run.completed",
        actor_id="admin", target_ref="processing-run:run-1", project_id=None,
        message_code="processing.retry_is_completed", metadata={},
        created_at="2026-07-18T00:01:00+00:00",
    )
    return run, region, replay, audit


def test_processing_run_named_child_finalize_positive_and_foreign_guard() -> None:
    assert {item.name for item in fields(FinalizeProcessingRunInput)} >= {
        "parser_invocations", "source_regions", "extraction_candidates",
        "candidate_groups", "promotion_decisions", "kpel_handoffs",
        "routing_decisions", "evidence_traces",
    }
    run, region, replay, audit = _run_graph_records()
    session = Session(scalars=(None, None, None))
    FinalizeProcessingRunCommand(SessionFactory(session)).execute(
        FinalizeProcessingRunInput(
            runs=(ProcessingRunWrite(run),), idempotency_record=replay,
            audit_events=(audit,), source_regions=(SourceRegionWrite(region),),
        )
    )
    assert session.commits == 1
    assert len(session.merged) == 2

    foreign = SourceRegion("region-foreign", "run-foreign", {})
    rejected = Session(scalars=(None,))
    with pytest.raises(ProcessingRegistryCurrentnessConflict, match="foreign run"):
        FinalizeProcessingRunCommand(SessionFactory(rejected)).execute(
            FinalizeProcessingRunInput(
                runs=(ProcessingRunWrite(run),), idempotency_record=replay,
                audit_events=(audit,), source_regions=(SourceRegionWrite(foreign),),
            )
        )
    assert rejected.rollbacks == 1


def test_processing_run_child_stale_replay_and_audit_rollback() -> None:
    run, region, replay, audit = _run_graph_records()
    prior_run = SimpleNamespace(payload={"status": "running", "attempt": 1})
    expected_region = SourceRegion("region-1", "run-1", {"region_kind": "old"})
    current_region = SimpleNamespace(
        payload={"region_id": "region-1", "run_id": "run-1", "payload": {"region_kind": "new"}}
    )
    stale = Session(scalars=(prior_run, current_region))
    with pytest.raises(ProcessingRegistryCurrentnessConflict, match="preimage changed"):
        FinalizeProcessingRunCommand(SessionFactory(stale)).execute(
            FinalizeProcessingRunInput(
                runs=(ProcessingRunWrite(run, "running", 1),),
                idempotency_record=replay, audit_events=(audit,),
                source_regions=(SourceRegionWrite(region, expected_region),),
            )
        )

    duplicate = Session(scalars=(None, None, SimpleNamespace()))
    with pytest.raises(ProcessingRegistryCurrentnessConflict, match="idempotency"):
        FinalizeProcessingRunCommand(SessionFactory(duplicate)).execute(
            FinalizeProcessingRunInput(
                runs=(ProcessingRunWrite(run),), idempotency_record=replay,
                audit_events=(audit,), source_regions=(SourceRegionWrite(region),),
            )
        )

    audit_failure = Session(scalars=(None, None, None), fail_audit=True)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        FinalizeProcessingRunCommand(SessionFactory(audit_failure)).execute(
            FinalizeProcessingRunInput(
                runs=(ProcessingRunWrite(run),), idempotency_record=replay,
                audit_events=(audit,), source_regions=(SourceRegionWrite(region),),
            )
        )
    assert audit_failure.rollbacks == 1
    assert audit_failure.commits == 0


def test_ops_ready_projects_are_complete_and_probes_are_session_free() -> None:
    source = inspect.getsource(PostgresOpsReadinessRepository.evidence_ready_project_ids)
    assert ".limit(" not in source
    sql_session = Session(scalars=("project-1",))

    class Probe:
        def available(self):
            assert not sql_session.active
            return True

    owner = PostgresOpsReadinessRepository(
        SessionFactory(sql_session), Probe(), Probe()
    )
    assert owner.has_projects()
    assert owner.processing_runner_available()
    assert owner.credential_encryption_available()

    ready_session = Session(rows=(("project-1", "project-2", "project-3"),))
    ready_owner = PostgresOpsReadinessRepository(
        SessionFactory(ready_session), Probe(), Probe()
    )
    assert ready_owner.evidence_ready_project_ids() == [
        "project-1", "project-2", "project-3"
    ]
def test_model_attempt_uses_one_detached_joined_snapshot(monkeypatch) -> None:
    route = _model_route("A", revision=3, is_default=True)
    connection = ProviderConnectionRecord(
        connection_id=route.connection_id,
        display_name="Provider A",
        provider_type=route.provider_type,
        endpoint_url="https://provider.example/v1",
        status="verified",
        enabled=True,
        revision=7,
    )
    secret = ProviderConnectionSecretRecord(
        connection_id=route.connection_id,
        ciphertext="ciphertext-not-plaintext",
        nonce="nonce",
        key_id="key-1",
        version=4,
    )
    snapshot = ProviderAttemptSnapshot(route, connection, secret)
    snapshot_calls: list[str | None] = []
    current_snapshot: list[ProviderAttemptSnapshot | None] = [snapshot]
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.ModelRoutingReadModel.provider_attempt_snapshot",
        lambda _self, route_id=None: (
            snapshot_calls.append(route_id) or current_snapshot[0]
        ),
    )
    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.model_routing.ModelRoutingReadModel.tested_route",
        lambda _self: pytest.fail("tested route was read separately"),
    )
    built: list[tuple[ProviderConnectionRecord, str]] = []
    adapter = PostgresModelRoutingAdapter(
        lambda: pytest.fail("unexpected SQL Session"),
        lambda detached_connection, api_key: built.append(
            (detached_connection, api_key)
        ) or SimpleNamespace(complete=lambda **_kwargs: None),
    )
    monkeypatch.setattr(
        PostgresModelRoutingAdapter,
        "decrypt_secret",
        lambda *_args: "plain-secret",
    )

    routed_attempt = adapter.open_attempt(replace(route))
    service = ModelRoutingService(adapter)
    attempt = service.open_tested_attempt()
    route.model_name = "changed-after-acceptance"
    connection.endpoint_url = "https://changed.invalid/v1"
    secret.version = 5

    assert snapshot_calls == [route.route_id, None]
    assert routed_attempt.route.model_name == "model-A"
    assert attempt.route.model_name == "model-A"
    assert [
        (item.provider_type, item.endpoint_url, api_key)
        for item, api_key in built
    ] == [
        ("openai_compatible", "https://provider.example/v1", "plain-secret"),
        ("openai_compatible", "https://provider.example/v1", "plain-secret"),
    ]
    assert all(item is not connection for item, _api_key in built)
    assert "plain-secret" not in repr(attempt)
    assert "ciphertext-not-plaintext" not in repr(snapshot)

    current_snapshot[0] = None
    with pytest.raises(ProviderError) as unavailable:
        service.open_tested_attempt()
    assert unavailable.value.code == "model_route_unavailable"

    current_snapshot[0] = snapshot
    with pytest.raises(ProviderError) as conflict:
        adapter.open_attempt(replace(route, revision=route.revision + 1))
    assert conflict.value.code == "model_route_revision_conflict"
