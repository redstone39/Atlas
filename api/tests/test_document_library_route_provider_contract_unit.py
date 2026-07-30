from __future__ import annotations

from pathlib import Path

from atlas_production.routes.document_library import router


ROUTE_FILE = (
    Path(__file__).parents[1]
    / "src/atlas_production/routes/document_library.py"
)


def test_document_library_route_uses_only_named_typed_providers() -> None:
    source = ROUTE_FILE.read_text()

    assert len(router.routes) == 7
    for required in (
        ".document_library_projection(",
        ".requested_scope_projection(",
        ".journey_facade(",
        ".document_uploads.upload(",
        ".document_restore_proofs.verify(",
        ".capture_processing_execution(",
        ".patch_document(",
        ".disable_document(",
        ".begin_restore(",
        ".finish_restore(",
        ".refresh_or_reindex(",
    ):
        assert required in source

    for forbidden in (
        "get_" + "store",
        "bounded_document_uploads",
        "append_document_audit",
        "update_document(",
        "register_document(",
        "put_document(",
        "effective_document_scope",
        "direct_team_role",
        "resolve_access",
        ".transaction()",
        ".create_processing_job(",
        "artifact_storage.write_artifact",
        "verify_document_restore_set",
    ):
        assert forbidden not in source


def test_restore_orders_durable_begin_proof_and_atomic_finish() -> None:
    source = ROUTE_FILE.read_text()

    begin = source.index(".begin_restore(")
    proof = source.index(".document_restore_proofs.verify(")
    finish = source.index(".finish_restore(")
    assert begin < proof < finish
    assert '"document_restore_failed"' in source
    assert '"document.restore_verification_failed"' in source
    assert "audit_event_ref=failure.event_id" in source
