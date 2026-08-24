from __future__ import annotations

import inspect
import json
import sys
from types import SimpleNamespace

from atlas_production.async_runtime import celery_app, tasks, workflows
from atlas_production.infrastructure import portainer_smb_init
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.artifact_storage import offline_target
from atlas_production.modules.artifact_storage.records import (
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)
from atlas_production.worker_composition import (
    BeatWorkerComposition,
    DispatchWorkerComposition,
    ProcessingWorkerComposition,
    build_worker_composition,
)


def test_worker_runtime_configures_typed_postgres_ports_without_api_app(
    monkeypatch,
) -> None:
    runtime = PostgresRuntime(engine=SimpleNamespace(), session_factory=lambda: None)  # type: ignore[arg-type]
    bootstrap_calls: list[bool] = []
    monkeypatch.setattr(
        PostgresRuntime,
        "bootstrap_schema",
        lambda _runtime: bootstrap_calls.append(True),
    )
    index_builds: list[bool] = []
    monkeypatch.setattr(
        workflows,
        "VectorIndex",
        lambda: index_builds.append(True) or SimpleNamespace(),
    )

    assert workflows.configure_postgres_worker_runtime(runtime) is runtime
    dispatch = build_worker_composition("dispatch")
    processing = build_worker_composition("processing")

    assert isinstance(dispatch, DispatchWorkerComposition)
    assert isinstance(processing, ProcessingWorkerComposition)
    assert bootstrap_calls == [True]
    assert index_builds == []
    source = inspect.getsource(workflows)
    assert "create_app" not in source
    assert "worker_" + "store" not in source
    assert "default_" + "store" not in source


def test_tasks_resolve_their_role_composition_before_using_ports() -> None:
    source = inspect.getsource(tasks)
    for role in ("dispatch", "processing", "indexing", "maintenance"):
        assert f'_composition("{role}")' in source
    assert "AsyncJob" + "Repository" not in source
    assert "BoundedArtifactWriter" not in source


def test_beat_composition_requires_no_postgres_configuration() -> None:
    assert isinstance(build_worker_composition("beat"), BeatWorkerComposition)
    celery_app._build_beat_composition()


class _OperatorComposition:
    def configure_offline_target(self, **kwargs):
        assert kwargs["target_id"] == "target-1"
        return {
            "operation_id": "operation-1",
            "committed_blob_count": 2,
            "total_bytes": 42,
            "blob_set_digest": "a" * 64,
            "storage_epoch": 3,
            "verification_mode": "full_hash",
            "evidence_claim": "TARGET_COPY_CHECKSUM_VERIFIED",
        }

    def configure_portainer_target(self, **kwargs):
        assert kwargs["generation"] == 7
        assert kwargs["switch_mode"] == "operator_accepted_unverified"
        return {
            "generation": 7,
            "verification_mode": "operator_accepted_unverified",
            "evidence_claim": "OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
            "committed_blob_count": 2,
            "storage_epoch": 4,
        }


def test_offline_operator_preserves_json_contract_without_store(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        offline_target,
        "build_artifact_storage_composition",
        lambda: _OperatorComposition(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "offline-target",
            "--target-id", "target-1",
            "--target-revision", "1",
            "--label", "Target",
            "--operator-id", "operator-1",
            "--change-id", "change-1",
        ],
    )

    assert offline_target.main() == 0
    assert json.loads(capsys.readouterr().out)["operation_id"] == "operation-1"
    source = inspect.getsource(offline_target)
    assert "default_" + "store" not in source
    assert "build_artifact_storage_service" not in source


def test_portainer_operator_preserves_environment_and_json_contract(
    monkeypatch,
    capsys,
) -> None:
    runtime = SimpleNamespace(session_factory=object())
    monkeypatch.setattr(
        portainer_smb_init.PostgresRuntime,
        "from_environment",
        lambda: runtime,
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "build_artifact_storage_composition",
        lambda selected: _OperatorComposition(),
    )
    monkeypatch.setattr(
        portainer_smb_init,
        "SeedProcessingRegistryDefaultsCommand",
        lambda session_factory: SimpleNamespace(
            execute=lambda: SimpleNamespace(
                created=False,
                plugin_count=12,
                processing_profile_count=9,
            )
        ),
    )
    environment = {
        "ATLAS_SMB_GENERATION": "7",
        "ATLAS_ARTIFACT_SWITCH_MODE": "operator_accepted_unverified",
        "ATLAS_ARTIFACT_SWITCH_ACK": UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
        "ATLAS_SMB_HOST": "fileserver",
        "ATLAS_SMB_SHARE": "atlas",
        "ATLAS_SMB_SUBDIR": "artifacts",
        "ATLAS_SMB_USERNAME": "atlas",
    }

    assert portainer_smb_init.main(environment) == 0
    expected_status = {
        "committed_blob_count": 2,
        "evidence_claim": "OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
        "generation": 7,
        "processing_defaults_seeded": False,
        "processing_plugin_count": 12,
        "processing_profile_count": 9,
        "status": "succeeded",
        "storage_epoch": 4,
        "verification_mode": "operator_accepted_unverified",
    }
    assert json.loads(capsys.readouterr().out) == expected_status
    source = inspect.getsource(portainer_smb_init)
    assert "default_" + "store" not in source
    assert "build_artifact_storage_service" not in source
