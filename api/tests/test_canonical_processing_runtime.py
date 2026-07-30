from __future__ import annotations

from copy import deepcopy
import inspect
from types import SimpleNamespace

from atlas_production.infrastructure.postgres_owner.document_processing import (
    ProcessingExecutionSnapshot,
    _JobTransitionSql,
    _apply_sealed_family_mutation,
    _publish_canonical_revision,
    _terminalize_canonical_revision,
    _validate_generation_publication_snapshot,
    canonical_processing_spec_from_snapshot,
    processing_fingerprint_from_snapshot,
)
from atlas_production.infrastructure.postgres_document_intake_adapter import (
    PostgresDocumentIntakeAdapter,
)
from atlas_production.infrastructure.postgres_document_upload import (
    NewDocumentUploadCommand,
)
from atlas_production.modules.document_intake.public import (
    DocumentLibraryMutationResult,
)
from atlas_production.routes.document_library import upload_document_library_file


def _snapshot() -> ProcessingExecutionSnapshot:
    profile = {
        "profile_id": "default-pdf",
        "revision": 1,
        "status": "active",
        "accepted_media_types": ["application/pdf"],
        "base_parser_plugin_ref": {
            "plugin_id": "atlas-pypdf",
            "plugin_version": "1.0.0",
            "package_digest": "platform-builtin:atlas-pypdf:1.0.0",
            "runtime_profile": "atlas-python-v1",
        },
        "mandatory_processor_plugin_refs": [],
        "eligible_processor_plugin_refs": [],
        "plugin_priority": [],
        "planner_enabled": False,
        "planner_model_route_id": None,
        "channel_registry_version": "kpel-registry-v0.1",
        "trait_registry_version": "kpel-registry-v0.1",
        "max_regions_per_plan": 100,
        "max_modules_per_region": 4,
        "max_total_plugin_invocations": 500,
        "planner_failure_behavior": "mandatory_only",
        "created_by": "actor-a",
        "created_at": "2026-07-23T00:00:00Z",
    }
    version = {
        "plugin_id": "atlas-pypdf",
        "plugin_version": "1.0.0",
        "package_digest": "platform-builtin:atlas-pypdf:1.0.0",
        "runtime_profile": "atlas-python-v1",
        "plugin_kind": "base_parser",
        "status": "verified",
        "descriptor": {
            "entrypoint": "atlas_plugin_runner.builtin_plugins:PypdfPlugin",
            "output_contract_version": "eir-draft-v1",
        },
        "created_at": "2026-07-23T00:00:00Z",
    }
    runtime = {
        "runtime_profile_id": "atlas-python-v1",
        "available_packages": {"pypdf": "6.0.0"},
        "created_at": "2026-07-23T00:00:00Z",
    }
    return ProcessingExecutionSnapshot(
        profile_id="default-pdf",
        profile_revision=1,
        profile_snapshot=profile,
        plugin_versions=(version,),
        plugin_packages=(),
        runtime_profiles=(runtime,),
        acceptance_request_digest="a" * 64,
    )


def test_processing_fingerprint_excludes_request_and_document_metadata() -> None:
    first = _snapshot()
    second = ProcessingExecutionSnapshot(
        profile_id=first.profile_id,
        profile_revision=first.profile_revision,
        profile_snapshot={
            **deepcopy(first.profile_snapshot),
            "created_by": "actor-b",
            "created_at": "2099-01-01T00:00:00Z",
            "display_metadata": {"label": "renamed"},
        },
        plugin_versions=first.plugin_versions,
        plugin_packages=first.plugin_packages,
        runtime_profiles=first.runtime_profiles,
        acceptance_request_digest="b" * 64,
    )

    assert processing_fingerprint_from_snapshot(first) == (
        processing_fingerprint_from_snapshot(second)
    )


def test_processing_fingerprint_changes_for_material_plugin_contract() -> None:
    first = _snapshot()
    changed_version = deepcopy(first.plugin_versions[0])
    changed_version["descriptor"]["output_contract_version"] = "eir-v2"
    second = ProcessingExecutionSnapshot(
        profile_id=first.profile_id,
        profile_revision=first.profile_revision,
        profile_snapshot=deepcopy(first.profile_snapshot),
        plugin_versions=(changed_version,),
        plugin_packages=first.plugin_packages,
        runtime_profiles=first.runtime_profiles,
        acceptance_request_digest=first.acceptance_request_digest,
    )

    assert processing_fingerprint_from_snapshot(first) != (
        processing_fingerprint_from_snapshot(second)
    )


def test_processing_fingerprint_changes_for_material_runtime_packages() -> None:
    first = _snapshot()
    changed_runtime = deepcopy(first.runtime_profiles[0])
    changed_runtime["available_packages"]["pypdf"] = "7.0.0"
    second = ProcessingExecutionSnapshot(
        profile_id=first.profile_id,
        profile_revision=first.profile_revision,
        profile_snapshot=deepcopy(first.profile_snapshot),
        plugin_versions=first.plugin_versions,
        plugin_packages=first.plugin_packages,
        runtime_profiles=(changed_runtime,),
        acceptance_request_digest=first.acceptance_request_digest,
    )

    assert processing_fingerprint_from_snapshot(first) != (
        processing_fingerprint_from_snapshot(second)
    )


def test_processing_spec_carries_explicit_material_runtime_contracts() -> None:
    spec = canonical_processing_spec_from_snapshot(_snapshot())

    assert set(spec) == {
        "schema_version",
        "parser",
        "ocr",
        "renderer",
        "normalization",
        "chunking",
        "embedding",
        "indexing",
    }
    assert spec["embedding"]["revision"]
    assert spec["renderer"]["pdf_page_raster"]
    assert spec["normalization"]["contract"]
    assert spec["chunking"]["contract"]
    assert spec["indexing"]["collection"] == "atlas_evidence_v1"


def test_current_hit_upload_contract_preserves_optional_job_fields_as_null() -> None:
    payload = DocumentLibraryMutationResult(
        request_id="upload-1",
        status="accepted",
        target_ref="document:document-1",
        message_code="document.upload_is_accepted_for_asynchronous_processing",
        audit_event_ref="audit-1",
        artifact_id="artifact-1",
        job_id=None,
        status_url=None,
    ).model_dump()

    assert payload["job_id"] is None
    assert payload["status_url"] is None


def test_upload_dispatches_only_when_a_shared_job_is_returned() -> None:
    source = inspect.getsource(upload_document_library_file)

    guard = source.index("if result.publication.job is not None:")
    dispatch = source.index("best_effort_dispatch()", guard)
    job_id = source.index("job_id = (", dispatch)
    assert guard < dispatch < job_id
    assert ").model_dump()" in source


def test_shared_job_status_projects_identity_bound_documents_but_not_control() -> None:
    list_source = inspect.getsource(_JobTransitionSql.list_job_projection_batch)
    get_source = inspect.getsource(
        _JobTransitionSql.get_document_job_request_projection
    )

    assert "processing_identity_id" in list_source
    assert "effective_document_scope" in get_source
    assert 'action="read_derived"' in get_source
    assert "_authorize_document_control" not in get_source


def test_shared_job_list_authorizes_expanded_bindings_before_limit() -> None:
    batch_source = inspect.getsource(_JobTransitionSql.list_job_projection_batch)
    list_source = inspect.getsource(
        _JobTransitionSql.list_document_job_request_projections
    )

    assert "discovered_identity_ids" in batch_source
    assert "bound_document.processing_identity_id" in batch_source
    assert "limit=None" in list_source
    assert list_source.index("effective_document_scope") < list_source.index(
        "len(allowed_projections) >= limit"
    )


def test_upload_replay_covers_joined_build_current_and_terminal_outcomes() -> None:
    source = inspect.getsource(NewDocumentUploadCommand.canonical_result)

    assert "valid_joined_build" in source
    assert "valid_current_hit" in source
    assert "valid_terminal_hit" in source
    assert "processing_fingerprint_from_snapshot" in source
    assert "job=shared_job" in source
    assert "job=None" in source


def test_checkpoint_outputs_and_publication_are_revision_sealed() -> None:
    mutation_source = inspect.getsource(_apply_sealed_family_mutation)
    publication_source = inspect.getsource(
        _validate_generation_publication_snapshot
    )

    assert mutation_source.count(
        '["processing_revision_id"] = processing_revision_id'
    ) == 1
    assert "evidence_row.processing_revision_id = processing_revision_id" in (
        mutation_source
    )
    assert "page_row.processing_revision_id = processing_revision_id" in (
        mutation_source
    )
    for field in (
        "evidence_revision_ids",
        "page_revision_ids",
        "chunk_revision_ids",
    ):
        assert field in publication_source


def test_document_library_processing_status_is_identity_backed_at_read_time() -> None:
    source = inspect.getsource(
        PostgresDocumentIntakeAdapter.document_library_projection
    )

    assert "current_processing_revisions" in source
    assert "latest_terminal_revisions" in source
    assert "active_processing_jobs" in source
    assert source.index("if active_job is not None:") < source.index(
        'current_revision.state == "ready"'
    )
    assert "project_processing_presentation(document)" in source


class _ScalarSession:
    def __init__(self, *values):
        self.values = list(values)
        self.flush_count = 0

    def scalar(self, _statement):
        return self.values.pop(0)

    def flush(self):
        self.flush_count += 1


def test_successful_publication_sets_ready_manifest_then_current_pointer() -> None:
    revision = SimpleNamespace(
        processing_revision_id="revision-2",
        processing_identity_id="identity-1",
        state="building",
        manifest_digest=None,
        page_artifact_count=None,
        evidence_count=None,
        chunk_count=None,
        index_point_count=None,
        finalized_at=None,
    )
    identity = SimpleNamespace(current_revision_id="revision-1")
    session = _ScalarSession(revision, identity)
    snapshot = SimpleNamespace(
        job=SimpleNamespace(
            processing_identity_id="identity-1",
            processing_revision_id="revision-2",
        ),
        index=SimpleNamespace(
            processing_revision_id="revision-2",
            actual_point_count=5,
        ),
        generation=SimpleNamespace(actual_chunk_count=4),
        pages=(object(), object()),
        evidence=(object(), object(), object()),
    )

    _publish_canonical_revision(
        session,
        snapshot,
        manifest_digest="f" * 64,
    )

    assert revision.state == "ready"
    assert revision.manifest_digest == "f" * 64
    assert (
        revision.page_artifact_count,
        revision.evidence_count,
        revision.chunk_count,
        revision.index_point_count,
    ) == (2, 3, 4, 5)
    assert session.flush_count == 1
    assert identity.current_revision_id == "revision-2"


def test_failed_or_cancelled_revision_never_moves_prior_current() -> None:
    for state in ("failed", "cancelled"):
        revision = SimpleNamespace(state="building", finalized_at=None)
        identity = SimpleNamespace(current_revision_id="revision-1")
        _terminalize_canonical_revision(
            _ScalarSession(revision),
            processing_revision_id="revision-2",
            state=state,
        )
        assert revision.state == state
        assert identity.current_revision_id == "revision-1"


def test_canonical_retry_creates_successor_without_reactivating_terminal_job() -> None:
    source = inspect.getsource(_JobTransitionSql.retry_terminal_job)
    canonical = source[
        source.index("canonical retry snapshot is missing") :
        source.index("_validate_progress_total_bound")
    ]

    assert ").create_processing_job(" in canonical
    assert 'job_kind="reprocess"' in canonical
    assert "successor" in canonical
    assert "desired = replace(" not in canonical
