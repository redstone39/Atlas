from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from atlas_production.modules.agent_runtime.public import (
    AgentQueryRequest,
)
from atlas_production.transport.dependencies import (
    api_composition,
)
from atlas_production.shared.http import (
    agent_bearer_token,
    error,
)

router = APIRouter()

@router.post("/api/v1/agent/queries")
def agent_query(payload: AgentQueryRequest, request: Request):
    composition = api_composition(request)
    raw_token = agent_bearer_token(request)
    authorization = composition.agent_query_authority.authorize(
        raw_token=raw_token,
        project_id=payload.project_id,
    )
    token = authorization.token
    agent = authorization.agent
    if authorization.status == "invalid_token":
        audit = composition.audit_writer.append_read_audit(
            "agent_query_denied",
            actor_id=None,
            target_ref=None,
            project_id=payload.project_id,
            message_code='agent.token_is_missing_or_invalid',
            metadata={"reason": "invalid_agent_token"},
        )
        return error(
            "invalid_agent_token",
            'agent.token_is_missing_or_invalid',
            401,
            audit_event_ref=audit.event_id,
        )
    if authorization.status == "invalid_agent":
        assert token is not None
        audit = composition.audit_writer.append_read_audit(
            "agent_query_denied",
            actor_id=token.actor_id,
            target_ref=f"agent:{token.actor_id}",
            project_id=payload.project_id,
            message_code='agent.user_is_inactive_or_missing',
            metadata={
                "reason": "invalid_agent_user",
                "token_fingerprint": token.token_fingerprint,
            },
        )
        return error(
            "invalid_agent_token",
            'agent.token_is_missing_or_invalid',
            401,
            audit_event_ref=audit.event_id,
        )
    if authorization.status == "revoked":
        assert token is not None and agent is not None
        audit = composition.audit_writer.append_read_audit(
            "agent_query_denied",
            actor_id=agent.actor_id,
            target_ref=f"agent-token:{token.token_id}",
            project_id=payload.project_id,
            message_code='agent.token_has_been_revoked',
            metadata={
                "reason": "agent_token_revoked",
                "token_fingerprint": token.token_fingerprint,
            },
        )
        return error(
            "agent_token_revoked",
            'agent.token_has_been_revoked',
            403,
            audit_event_ref=audit.event_id,
        )
    assert token is not None and agent is not None
    decision = authorization.decision
    assert decision is not None
    if authorization.status == "denied":
        audit = composition.audit_writer.append_read_audit(
            "agent_query_denied",
            actor_id=agent.actor_id,
            target_ref=f"agent:{agent.actor_id}",
            project_id=payload.project_id,
            message_code='agent.does_not_have_active_access_to_this_project',
            metadata={
                "reason": "agent_project_access_denied",
                "access_decision_id": decision.decision_id,
                "token_fingerprint": token.token_fingerprint,
            },
        )
        return error(
            "agent_project_access_denied",
            'agent.does_not_have_active_access_to_this_project',
            403,
            audit_event_ref=audit.event_id,
        )
    audit = composition.audit_writer.append_read_audit(
        "agent_query_deferred",
        actor_id=agent.actor_id,
        target_ref=f"agent:{agent.actor_id}",
        project_id=payload.project_id,
        message_code='agent.query_execution_is_deferred_until_runtime_is_available',
        metadata={
            "access_decision_id": decision.decision_id,
            "token_fingerprint": token.token_fingerprint,
        },
    )
    return error(
        "feature_deferred",
        'agent.query_execution_is_deferred_until_runtime_is_available',
        501,
        audit_event_ref=audit.event_id,
    )
