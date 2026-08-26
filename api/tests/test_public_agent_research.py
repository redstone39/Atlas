from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.mcp_config import McpTransportConfig
from atlas_production.infrastructure.mcp_research import build_mcp_transport
from atlas_production.modules.agent_runtime.public import (
    AcceptedResearchSnapshotV1,
    AcceptedScopeSnapshotV1,
    AgentResearchAuditError,
    AgentResearchAuditService,
    AgentResearchRecordV1,
    AgentResearchReplayConflict,
    AgentResearchService,
    AgentResearchScopeRefV1,
    AllAuthorizedResearchScopeV1,
    ResearchPacketV1,
    SelectedResearchScopeV1,
    StartAgentResearchV1,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.turn_runtime.public import TerminalOutcomeV1
from atlas_production.openapi_app import create_openapi_app

from tests.public_synthetic_data import (
    PUBLIC_DIGEST_A,
    PUBLIC_DIGEST_B,
    PUBLIC_NOW,
    PUBLIC_RESEARCH_EXECUTION_ID,
    PUBLIC_RESEARCH_ID,
    PUBLIC_RESEARCH_PACKET_REF,
    PUBLIC_RESEARCH_QUESTION,
    synthetic_research_packet_payload,
)


def _snapshot() -> AcceptedResearchSnapshotV1:
    return AcceptedResearchSnapshotV1(
        scope=AcceptedScopeSnapshotV1(
            scope_ref="public-synthetic-scope-ref-1",
            scope_digest=PUBLIC_DIGEST_A,
            project_ids=["public-synthetic-project-1"],
            requested_refs=[
                AgentResearchScopeRefV1(
                    kind="project", id="public-synthetic-project-1"
                )
            ],
        ),
        grant_ref="public-synthetic-grant-ref-1",
        grant_digest=PUBLIC_DIGEST_A,
        catalog_ref="public-synthetic-catalog-ref-1",
        catalog_digest=PUBLIC_DIGEST_B,
        policy_ref="public-synthetic-policy-ref-1",
        policy_digest=PUBLIC_DIGEST_A,
        budget_ref="public-synthetic-budget-ref-1",
        budget_digest=PUBLIC_DIGEST_B,
    )


def _payload(*, question: str = PUBLIC_RESEARCH_QUESTION) -> StartAgentResearchV1:
    return StartAgentResearchV1(
        question=question,
        idempotency_key="public-synthetic-idempotency-1",
        scope=SelectedResearchScopeV1(
            refs=[
                AgentResearchScopeRefV1(
                    kind="project", id="public-synthetic-project-1"
                )
            ]
        ),
    )


def _accepted_record(payload: StartAgentResearchV1) -> AgentResearchRecordV1:
    return AgentResearchRecordV1(
        research_id=PUBLIC_RESEARCH_ID,
        execution_id=PUBLIC_RESEARCH_EXECUTION_ID,
        actor_id="public-synthetic-agent-1",
        idempotency_key=payload.idempotency_key,
        request_digest=payload.canonical_payload_digest(),
        question_ref="public-synthetic-question-ref-1",
        question=payload.question,
        output_mode=payload.output_mode,
        snapshot=_snapshot(),
        status="accepted",
        packet=None,
        packet_ref=None,
        packet_digest=None,
        accepted_at=PUBLIC_NOW,
        completed_at=None,
    )


@dataclass
class _ReplayAuthority:
    actor_id: str | None = "public-synthetic-agent-1"

    def identify_replay_actor(self, *, raw_token: str | None) -> str | None:
        assert raw_token == "public-synthetic-token"
        return self.actor_id

    def accept_research(self, **_values):
        raise AssertionError("exact replay must not reauthorize or allocate")


@dataclass
class _ReplayStore:
    record: AgentResearchRecordV1

    def find_replay(self, *, actor_id: str, idempotency_key: str):
        assert actor_id == self.record.actor_id
        assert idempotency_key == self.record.idempotency_key
        return self.record


@dataclass(frozen=True)
class _AuditEvent:
    event_id: str = "public-synthetic-audit-1"


class _AuditWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def append_read_audit(self, event_type: str, **values):
        self.calls.append((event_type, values))
        return _AuditEvent()


def test_strict_scope_packet_and_optional_answer_contracts() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        SelectedResearchScopeV1(
            refs=[
                AgentResearchScopeRefV1(kind="project", id="public-synthetic-project-1"),
                AgentResearchScopeRefV1(kind="project", id="public-synthetic-project-1"),
            ]
        )
    with pytest.raises(ValidationError, match="non-whitespace"):
        StartAgentResearchV1(
            question="  ",
            idempotency_key="public-synthetic-idempotency-1",
            scope=AllAuthorizedResearchScopeV1(),
        )

    packet = ResearchPacketV1.materialize(**synthetic_research_packet_payload())
    assert packet.packet_digest == ResearchPacketV1.digest_payload(
        packet.model_dump(mode="json")
    )
    with pytest.raises(ValidationError, match="outside the packet"):
        ResearchPacketV1.materialize(
            **{
                **synthetic_research_packet_payload(),
                "findings": [
                    {
                        "finding_id": "public-synthetic-finding-1",
                        "text": "Public synthetic unsupported finding.",
                        "evidence_ids": ["public-synthetic-unknown-evidence"],
                        "evidence_assessment": "aligned",
                    }
                ],
            }
        )


def test_exact_replay_returns_original_record_and_mismatch_conflicts() -> None:
    payload = _payload()
    record = _accepted_record(payload)
    audit = _AuditWriter()
    service = AgentResearchService(
        authority=_ReplayAuthority(), store=_ReplayStore(record), audit_writer=audit
    )

    outcome = service.start(payload=payload, raw_token="public-synthetic-token")

    assert outcome.status == "replayed"
    assert outcome.record is record
    assert audit.calls[0][0] == "agent_research_replayed"
    with pytest.raises(AgentResearchReplayConflict, match="payload conflicts"):
        service.start(
            payload=_payload(question="A different public synthetic question."),
            raw_token="public-synthetic-token",
        )


def test_terminal_result_kind_keeps_conversation_and_research_shapes_separate() -> None:
    common = {
        "execution_id": PUBLIC_RESEARCH_EXECUTION_ID,
        "scan_sequence": 1,
        "outcome": "completed",
        "terminal_commit_intent_ref": "public-synthetic-terminal-intent-1",
        "evidence_pack_ref": "public-synthetic-evidence-pack-1",
        "audit_draft_ref": "public-synthetic-audit-draft-1",
        "committed_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
    }
    conversation = TerminalOutcomeV1(
        **common,
        governed_answer_draft_ref="public-synthetic-answer-1",
        citation_binding_draft_ref="public-synthetic-citations-1",
    )
    research = TerminalOutcomeV1(
        **common,
        result_kind="agent_research",
        research_packet_ref=PUBLIC_RESEARCH_PACKET_REF,
        research_packet_digest=PUBLIC_DIGEST_A,
    )

    assert conversation.result_kind == "conversation_answer"
    assert research.result_kind == "agent_research"
    with pytest.raises(ValidationError, match="research outcome requires"):
        TerminalOutcomeV1(**common, result_kind="agent_research")


@pytest.mark.parametrize(
    ("value", "expected_url", "expected_host"),
    [
        ("https://atlas.example", "https://atlas.example/mcp", "atlas.example"),
        ("https://atlas.example/mcp", "https://atlas.example/mcp", "atlas.example"),
    ],
)
def test_mcp_public_url_normalizes_exact_transport_contract(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected_url: str,
    expected_host: str,
) -> None:
    monkeypatch.setenv("ATLAS_MCP_PUBLIC_URL", value)
    config = McpTransportConfig.from_environment()
    assert config.public_url == expected_url
    assert config.transport_security.allowed_hosts == [expected_host]


def test_mcp_public_url_defaults_localhost_and_rejects_unsafe_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATLAS_MCP_PUBLIC_URL", raising=False)
    local = McpTransportConfig.from_environment()
    assert local.mode == "localhost_only"
    assert "localhost:*" in local.transport_security.allowed_hosts

    for invalid in (
        "ftp://atlas.example/mcp",
        "https://user:password@atlas.example/mcp",
        "https://atlas.example/other",
        "https://atlas.example/mcp?token=public-synthetic-token",
    ):
        monkeypatch.setenv("ATLAS_MCP_PUBLIC_URL", invalid)
        with pytest.raises(
            RuntimeError,
            match=r"ATLAS_MCP_PUBLIC_URL must be an http\(s\) origin or exact /mcp URL",
        ):
            McpTransportConfig.from_environment()


class _McpAccess:
    def transport_actor(self, raw_token: str) -> str | None:
        if raw_token == "public-synthetic-token":
            return "public-synthetic-agent-1"
        return None


class _UnusedMcpApplication:
    pass


def _sse_result(response) -> dict[str, object]:
    data_line = next(
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    return json.loads(data_line)


def test_exact_mcp_path_requires_bearer_and_lists_exactly_four_tools() -> None:
    access = _McpAccess()
    transport = build_mcp_transport(
        application=_UnusedMcpApplication(),
        access=access,
        audit_writer=_AuditWriter(),
        config=McpTransportConfig.from_environment(),
    )
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values["mcp_transport"] = transport
    app = create_app(ApiComposition(**values))
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "public-synthetic-client", "version": "1.0"},
        },
    }
    base_headers = {"accept": "application/json, text/event-stream", "host": "localhost"}

    with TestClient(app) as client:
        denied = client.post("/mcp", json=initialize, headers=base_headers)
        assert denied.status_code == 401
        assert denied.json()["error_code"] == "invalid_agent_token"

        initialized = client.post(
            "/mcp",
            json=initialize,
            headers={
                **base_headers,
                "authorization": "Bearer public-synthetic-token",
            },
        )
        assert initialized.status_code == 200
        assert _sse_result(initialized)["result"]["protocolVersion"] == "2025-06-18"
        session_id = initialized.headers["mcp-session-id"]
        tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={
                **base_headers,
                "authorization": "Bearer public-synthetic-token",
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2025-06-18",
            },
        )
        assert tools.status_code == 200
        names = {item["name"] for item in _sse_result(tools)["result"]["tools"]}
        assert names == {
            "atlas.list_knowledge_scopes",
            "atlas.research",
            "atlas.get_research",
            "atlas.read_evidence",
        }


def test_openapi_removes_legacy_query_and_exposes_only_admin_research_reads() -> None:
    paths = create_openapi_app().openapi()["paths"]
    assert "/api/v1/agent/queries" not in paths
    expected = {
        "/api/v1/admin/audit/agent-research",
        "/api/v1/admin/audit/agent-research/{research_id}",
        "/api/v1/admin/audit/agent-research/{research_id}/runtime",
        "/api/v1/admin/audit/agent-research/{research_id}/evidence/{evidence_id}",
    }
    assert {path for path in paths if path.startswith("/api/v1/admin/audit/agent-research")} == expected
    assert all(set(paths[path]) == {"get"} for path in expected)


def test_admin_research_audit_denies_before_prefetch() -> None:
    class _ForbiddenDependency:
        def __getattr__(self, name: str):
            raise AssertionError(f"non-admin request reached protected dependency: {name}")

    forbidden = _ForbiddenDependency()
    service = AgentResearchAuditService(
        researches=forbidden,
        audit_events=forbidden,
        audit_writer=forbidden,
        runtime=forbidden,
        evidence=forbidden,
    )
    non_admin = UserRecord(
        actor_id="public-synthetic-user-1",
        display_name="Public Synthetic User",
        email=None,
        system_role="user",
        password_digest=None,
    )

    with pytest.raises(AgentResearchAuditError) as denied:
        service.list_admin(non_admin, cursor=None, limit=50)
    assert (denied.value.error_code, denied.value.status_code) == ("access_denied", 403)
