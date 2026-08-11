from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.shared.public import (
    AdminActionResult,
)
from atlas_production.modules.identity_access.public import (
    AgentTokenIssueRequest,
    AgentTokenIssueResult,
    AgentUserCreateRequest,
    AgentUserCreateResult,
    AgentUserListResult,
    AgentUserUpdateRequest,
)
from atlas_production.modules.audit.public import (
    AdminAuditEventReadService,
    AuditEventList,
    AuditEventReadError,
)
from ..modules.identity_access.public import (
    AgentAccessError,
    AgentAccessService,
    AgentActionOutcome,
    AgentCreateOutcome,
    AgentTokenOutcome,
)
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
)
from atlas_production.shared.http import (
    error,
)

router = APIRouter()


def _agent_service(request: Request) -> AgentAccessService:
    return api_composition(request).agent_access

def _admin_audit_event_service(request: Request) -> AdminAuditEventReadService:
    return api_composition(request).admin_audit_events


def _agent_error(
    exc: AgentAccessError,
    fallback_request_id: str | None = None,
) -> JSONResponse:
    if exc.error_code == "admin_action_rejected":
        request_id = exc.request_id or fallback_request_id
        assert request_id is not None
        assert exc.audit_event_ref is not None
        return JSONResponse(
            status_code=exc.status_code,
            content=AdminActionResult(
                request_id=request_id,
                status="rejected",
                target_ref=exc.target_ref,
                message_code=exc.message_code,
                audit_event_ref=exc.audit_event_ref,
            ).model_dump(),
        )
    return error(
        exc.error_code,
        exc.message_code,
        exc.status_code,
        exc.audit_event_ref,
    )


def _action_response(outcome: AgentActionOutcome) -> AdminActionResult | JSONResponse:
    if outcome.success_status_code == 200:
        return outcome.result
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


def _create_response(outcome: AgentCreateOutcome) -> JSONResponse:
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


def _token_response(outcome: AgentTokenOutcome) -> JSONResponse:
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


@router.post("/api/v1/admin/agent-users", response_model=AgentUserCreateResult)
def create_agent_user(payload: AgentUserCreateRequest, request: Request):
    try:
        outcome = _agent_service(request).create_agent(current_user(request), payload)
    except AgentAccessError as exc:
        return _agent_error(exc, payload.idempotency_key)
    return _create_response(outcome)


@router.get("/api/v1/admin/agent-users", response_model=AgentUserListResult)
def list_agent_users(request: Request) -> AgentUserListResult | JSONResponse:
    try:
        return _agent_service(request).list_agents(current_user(request))
    except AgentAccessError as exc:
        return _agent_error(exc)


@router.patch("/api/v1/admin/agent-users/{actor_id}", response_model=AdminActionResult)
def update_agent_user(
    actor_id: str,
    payload: AgentUserUpdateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        outcome = _agent_service(request).update_agent(
            current_user(request),
            actor_id,
            payload,
        )
    except AgentAccessError as exc:
        return _agent_error(exc, payload.idempotency_key)
    return _action_response(outcome)


@router.post(
    "/api/v1/admin/agent-users/{actor_id}/tokens",
    response_model=AgentTokenIssueResult,
)
def issue_agent_token(
    actor_id: str,
    payload: AgentTokenIssueRequest,
    request: Request,
):
    try:
        outcome = _agent_service(request).issue_token(
            current_user(request),
            actor_id,
            payload,
        )
    except AgentAccessError as exc:
        return _agent_error(exc, payload.idempotency_key)
    return _token_response(outcome)


@router.delete("/api/v1/admin/agent-tokens/{token_id}", response_model=AdminActionResult)
def revoke_agent_token(token_id: str, request: Request) -> AdminActionResult | JSONResponse:
    try:
        outcome = _agent_service(request).revoke_token(current_user(request), token_id)
    except AgentAccessError as exc:
        return _agent_error(exc, f"revoke-{token_id}")
    return _action_response(outcome)


@router.get("/api/v1/admin/audit/events", response_model=AuditEventList)
def list_audit_events(request: Request) -> AuditEventList | JSONResponse:
    try:
        return _admin_audit_event_service(request).list_admin(
            current_user(request),
            limit=50,
        )
    except AuditEventReadError as exc:
        return error(
            exc.error_code,
            exc.message_code,
            exc.status_code,
            message_params=exc.message_params,
        )
