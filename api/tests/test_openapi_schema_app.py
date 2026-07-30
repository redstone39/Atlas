from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from fastapi.responses import Response

from atlas_production.openapi_app import create_openapi_app
from atlas_production.modules.citation_preview.public import (
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceV1,
)
from atlas_production.routes import conversations
from atlas_production.routes.conversations import _accepted_page_media_types


FIXTURE = Path(__file__).parent / "contracts" / "openapi-v1.json"
EXPECTED_FIXTURE_SHA256 = (
    "918b42fcb492f027a53477ba6a4ad6ea9e3976e6fd3e1156df4bef214a17f420"
)


def test_schema_only_app_matches_fixed_openapi_without_runtime_services() -> None:
    app = create_openapi_app()

    assert vars(app.state) == {"_state": {}}
    assert app.openapi() == json.loads(FIXTURE.read_text())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == EXPECTED_FIXTURE_SHA256


def test_openapi_exposes_only_strict_execution_conversation_surface() -> None:
    schema = json.loads(FIXTURE.read_text())
    paths = schema["paths"]

    assert "/api/v1/workspace/turn-executions/{execution_id}" in paths
    assert "/api/v1/workspace/turn-executions/{execution_id}/events" in paths
    assert (
        "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/citations/{citation_ref}"
        in paths
    )
    assert (
        "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/declared-evidence/{protected_open_ref}"
        in paths
    )
    assert (
        "/api/v1/admin/conversations/{conversation_id}/turns/{turn_id}/declared-evidence/{protected_open_ref}"
        in paths
    )
    assert "/api/v1/admin/conversations/{conversation_id}/turns/{turn_id}/runtime" in paths
    for removed_path in (
        "/api/v1/workspace/conversations/{conversation_id}/turns/stream",
        "/api/v1/workspace/turn-requests/{turn_request_id}/stream",
        "/api/v1/workspace/citations/{citation_id}/viewer-sessions",
        "/api/v1/workspace/citation-viewer-sessions/{viewer_session_id}",
    ):
        assert removed_path not in paths

    schemas = schema["components"]["schemas"]
    assert schemas["AdminConversationListResult"]["additionalProperties"] is False
    assert schemas["RuntimeTraceDetail"]["additionalProperties"] is False
    assert schemas["ProtectedDeclaredEvidenceV1"]["additionalProperties"] is False
    assert set(
        schemas["WorkspaceConversationSummaryV1"]["properties"]["last_turn_status"][
            "anyOf"
        ][0]["enum"]
    ) == {"processing", "completed", "failed_closed"}
    assert "CitationViewerManifest" not in schemas


def test_declared_evidence_contract_negotiates_only_explicit_page_media_types() -> None:
    schema = json.loads(FIXTURE.read_text())
    operation = schema["paths"][
        "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/declared-evidence/{protected_open_ref}"
    ]["get"]
    response_content = operation["responses"]["200"]["content"]

    assert set(response_content) == {
        "application/json",
        "application/pdf",
        "image/png",
    }
    assert any(
        parameter["in"] == "header" and parameter["name"] == "accept"
        for parameter in operation["parameters"]
    )
    assert _accepted_page_media_types(None) == frozenset()
    assert _accepted_page_media_types("*/*") == frozenset()
    assert _accepted_page_media_types("application/json") == frozenset()
    assert _accepted_page_media_types(
        "application/pdf, image/png, application/json;q=0.5"
    ) == frozenset({"application/pdf", "image/png"})
    assert _accepted_page_media_types(
        "application/pdf;q=0, image/png;q=0.000"
    ) == frozenset()


def test_workspace_declared_evidence_route_returns_binary_page_or_json_fallback(
    monkeypatch,
) -> None:
    page = ProtectedDeclaredEvidencePageV1(
        media_type="application/pdf",
        content=b"%PDF exact page",
    )
    excerpt = ProtectedDeclaredEvidenceV1(
        evidence_handle="kh_evidence_one",
        locator_label="Page 1",
        snippet="authorized",
        content="authorized excerpt",
        modality="text",
    )
    application = SimpleNamespace(
        read_declared_evidence=lambda *_args, **kwargs: (
            page if kwargs["accepted_page_media_types"] else excerpt
        )
    )
    monkeypatch.setattr(
        conversations,
        "_workspace_turn_application",
        lambda _request: application,
    )
    monkeypatch.setattr(conversations, "current_user", lambda _request: object())

    binary = conversations.read_workspace_declared_evidence(
        "conversation-1",
        "turn-1",
        "open-ref-1",
        object(),
        "application/pdf, application/json;q=0.5",
    )
    assert isinstance(binary, Response)
    assert binary.body == b"%PDF exact page"
    assert binary.media_type == "application/pdf"
    assert binary.headers["cache-control"] == "private, no-store"

    fallback = conversations.read_workspace_declared_evidence(
        "conversation-1",
        "turn-1",
        "open-ref-1",
        object(),
        "*/*",
    )
    assert fallback == excerpt


def test_schema_only_app_rejects_real_ops_routes_before_service_lookup() -> None:
    app = create_openapi_app()

    client = TestClient(app)
    for path in ("/api/v1/ops/health", "/api/v1/ops/readiness"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {
            "detail": "OpenAPI schema-only app cannot serve requests"
        }
    assert vars(app.state) == {"_state": {}}
