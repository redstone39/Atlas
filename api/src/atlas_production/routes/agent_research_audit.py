from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.agent_runtime.public import (
    AgentResearchAuditDetailV1,
    AgentResearchAuditError,
    AgentResearchAuditListV1,
    AgentResearchAuditService,
    AgentResearchEvidenceContentV1,
    AgentResearchRuntimeDetailV1,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request) -> AgentResearchAuditService:
    return api_composition(request).agent_research_audit


def _error(exc: AgentResearchAuditError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


@router.get(
    "/api/v1/admin/audit/agent-research",
    response_model=AgentResearchAuditListV1,
)
def list_agent_research_audit(
    request: Request,
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
) -> AgentResearchAuditListV1 | JSONResponse:
    try:
        return _service(request).list_admin(
            current_user(request),
            cursor=cursor,
            limit=limit,
        )
    except AgentResearchAuditError as exc:
        return _error(exc)


@router.get(
    "/api/v1/admin/audit/agent-research/{research_id}",
    response_model=AgentResearchAuditDetailV1,
)
def get_agent_research_audit(
    research_id: str,
    request: Request,
) -> AgentResearchAuditDetailV1 | JSONResponse:
    try:
        return _service(request).get_admin(current_user(request), research_id)
    except AgentResearchAuditError as exc:
        return _error(exc)


@router.get(
    "/api/v1/admin/audit/agent-research/{research_id}/runtime",
    response_model=AgentResearchRuntimeDetailV1,
)
def get_agent_research_runtime(
    research_id: str,
    request: Request,
) -> AgentResearchRuntimeDetailV1 | JSONResponse:
    try:
        return _service(request).get_runtime(current_user(request), research_id)
    except AgentResearchAuditError as exc:
        return _error(exc)


@router.get(
    "/api/v1/admin/audit/agent-research/{research_id}/evidence/{evidence_id}",
    response_model=AgentResearchEvidenceContentV1,
)
def read_agent_research_evidence(
    research_id: str,
    evidence_id: str,
    request: Request,
    representation: Literal["text", "visual", "native"] = Query(),
) -> AgentResearchEvidenceContentV1 | JSONResponse:
    try:
        return _service(request).read_evidence(
            current_user(request),
            research_id,
            evidence_id,
            representation,
        )
    except AgentResearchAuditError as exc:
        return _error(exc)
