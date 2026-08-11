from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition

from atlas_production.modules.agent_runtime.public import (
    AgentQueryAuthorizationV1,
    AgentQueryRequest,
    AgentRuntimeApplication,
)


@dataclass
class _Authority:
    authorization: AgentQueryAuthorizationV1

    def authorize(self, *, raw_token: str | None, project_id: str):
        assert raw_token == "raw-token"
        assert project_id == "project-1"
        return self.authorization


@dataclass(frozen=True)
class _AuditEvent:
    event_id: str = "audit-1"


@dataclass
class _AuditWriter:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def append_read_audit(self, event_type: str, **values):
        self.calls.append((event_type, values))
        return _AuditEvent()


def _http_client(application: AgentRuntimeApplication) -> TestClient:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(agent_runtime=application)
    return TestClient(create_app(ApiComposition(**values)))


@dataclass
class _TokenAuthority:
    authorization: AgentQueryAuthorizationV1
    tokens: list[str | None] = field(default_factory=list)

    def authorize(self, *, raw_token: str | None, project_id: str):
        self.tokens.append(raw_token)
        assert project_id == "project-1"
        return self.authorization




@pytest.mark.parametrize(
    ("authorization", "expected_outcome", "event_type", "expected_audit"),
    [
        (
            AgentQueryAuthorizationV1("invalid_token"),
            (
                "invalid_agent_token",
                "agent.token_is_missing_or_invalid",
                401,
                "audit-1",
            ),
            "agent_query_denied",
            {
                "actor_id": None,
                "target_ref": None,
                "project_id": "project-1",
                "message_code": "agent.token_is_missing_or_invalid",
                "metadata": {"reason": "invalid_agent_token"},
            },
        ),
        (
            AgentQueryAuthorizationV1(
                "invalid_agent",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
            ),
            (
                "invalid_agent_token",
                "agent.token_is_missing_or_invalid",
                401,
                "audit-1",
            ),
            "agent_query_denied",
            {
                "actor_id": "agent-1",
                "target_ref": "agent:agent-1",
                "project_id": "project-1",
                "message_code": "agent.user_is_inactive_or_missing",
                "metadata": {
                    "reason": "invalid_agent_user",
                    "token_fingerprint": "fingerprint-1",
                },
            },
        ),
        (
            AgentQueryAuthorizationV1(
                "revoked",
                actor_id="agent-1",
                token_id="token-1",
                token_fingerprint="fingerprint-1",
            ),
            (
                "agent_token_revoked",
                "agent.token_has_been_revoked",
                403,
                "audit-1",
            ),
            "agent_query_denied",
            {
                "actor_id": "agent-1",
                "target_ref": "agent-token:token-1",
                "project_id": "project-1",
                "message_code": "agent.token_has_been_revoked",
                "metadata": {
                    "reason": "agent_token_revoked",
                    "token_fingerprint": "fingerprint-1",
                },
            },
        ),
        (
            AgentQueryAuthorizationV1(
                "denied",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
                access_decision_id="decision-1",
            ),
            (
                "agent_project_access_denied",
                "agent.does_not_have_active_access_to_this_project",
                403,
                "audit-1",
            ),
            "agent_query_denied",
            {
                "actor_id": "agent-1",
                "target_ref": "agent:agent-1",
                "project_id": "project-1",
                "message_code": "agent.does_not_have_active_access_to_this_project",
                "metadata": {
                    "reason": "agent_project_access_denied",
                    "access_decision_id": "decision-1",
                    "token_fingerprint": "fingerprint-1",
                },
            },
        ),
        (
            AgentQueryAuthorizationV1(
                "allowed",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
                access_decision_id="decision-1",
            ),
            (
                "feature_deferred",
                "agent.query_execution_is_deferred_until_runtime_is_available",
                501,
                "audit-1",
            ),
            "agent_query_deferred",
            {
                "actor_id": "agent-1",
                "target_ref": "agent:agent-1",
                "project_id": "project-1",
                "message_code": (
                    "agent.query_execution_is_deferred_until_runtime_is_available"
                ),
                "metadata": {
                    "access_decision_id": "decision-1",
                    "token_fingerprint": "fingerprint-1",
                },
            },
        ),
    ],
)
def test_agent_query_preserves_fail_closed_outcomes_and_exact_audit(
    authorization: AgentQueryAuthorizationV1,
    expected_outcome: tuple[str, str, int, str],
    event_type: str,
    expected_audit: dict[str, object],
) -> None:
    audit = _AuditWriter()
    application = AgentRuntimeApplication(_Authority(authorization), audit)

    outcome = application.query(
        payload=AgentQueryRequest(
            project_id="project-1",
            query_text="question",
            purpose="research",
        ),
        raw_token="raw-token",
    )

    assert (
        outcome.error_code,
        outcome.message_code,
        outcome.status_code,
        outcome.audit_event_ref,
    ) == expected_outcome
    assert audit.calls == [(event_type, expected_audit)]



def test_agent_query_http_matrix_preserves_all_outcomes_and_bearer_only_input() -> None:
    cases = (
        (AgentQueryAuthorizationV1("invalid_token"), 401, "invalid_agent_token"),
        (
            AgentQueryAuthorizationV1(
                "invalid_agent",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
            ),
            401,
            "invalid_agent_token",
        ),
        (
            AgentQueryAuthorizationV1(
                "revoked",
                actor_id="agent-1",
                token_id="token-1",
                token_fingerprint="fingerprint-1",
            ),
            403,
            "agent_token_revoked",
        ),
        (
            AgentQueryAuthorizationV1(
                "denied",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
                access_decision_id="decision-1",
            ),
            403,
            "agent_project_access_denied",
        ),
        (
            AgentQueryAuthorizationV1(
                "allowed",
                actor_id="agent-1",
                token_fingerprint="fingerprint-1",
                access_decision_id="decision-1",
            ),
            501,
            "feature_deferred",
        ),
    )
    payload = {
        "project_id": "project-1",
        "query_text": "question",
        "purpose": "research",
    }

    for authorization, status_code, error_code in cases:
        authority = _TokenAuthority(authorization)
        audit = _AuditWriter()
        client = _http_client(AgentRuntimeApplication(authority, audit))
        response = client.post(
            "/api/v1/agent/queries",
            json=payload,
            headers={"Authorization": "Bearer raw-token"},
        )

        assert response.status_code == status_code
        assert response.json()["error_code"] == error_code
        assert response.json()["audit_event_ref"] == "audit-1"
        assert authority.tokens == ["raw-token"]
        assert len(audit.calls) == 1

    authority = _TokenAuthority(AgentQueryAuthorizationV1("invalid_token"))
    client = _http_client(AgentRuntimeApplication(authority, _AuditWriter()))
    basic = client.post(
        "/api/v1/agent/queries",
        json=payload,
        headers={"Authorization": "Basic raw-token"},
    )
    assert basic.status_code == 401
    assert authority.tokens == [None]