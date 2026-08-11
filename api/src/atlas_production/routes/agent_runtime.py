from fastapi import APIRouter, Request

from atlas_production.modules.agent_runtime.public import (
    AgentQueryRequest,
)
from atlas_production.shared.public import ErrorResponse
from atlas_production.transport.dependencies import (
    api_composition,
)
from atlas_production.shared.http import (
    agent_bearer_token,
    error,
)

router = APIRouter()

@router.post(
    "/api/v1/agent/queries",
    status_code=501,
    response_model=ErrorResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
def agent_query(payload: AgentQueryRequest, request: Request):
    outcome = api_composition(request).agent_runtime.query(
        payload=payload,
        raw_token=agent_bearer_token(request),
    )
    return error(
        outcome.error_code,
        outcome.message_code,
        outcome.status_code,
        audit_event_ref=outcome.audit_event_ref,
    )
