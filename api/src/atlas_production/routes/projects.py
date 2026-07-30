from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.shared.public import (
    AdminActionResult,
)
from atlas_production.modules.project_governance.public import (
    ProjectAccessGrant,
    ProjectAccessGrantCreateRequest,
    ProjectAccessGrantListResult,
    ProjectAccessGrantUpdateRequest,
    ProjectAdminListResult,
    ProjectCreateRequest,
    ProjectMemberCandidatesResult,
    ProjectUpdateRequest,
)
from ..modules.project_governance.public import (
    ProjectAccessGrantOutcome,
    ProjectActionOutcome,
    ProjectGovernanceError,
    ProjectGovernanceService,
)
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
)
from atlas_production.shared.http import (
    error,
)

router = APIRouter()


def _project_service(request: Request) -> ProjectGovernanceService:
    return api_composition(request).project_governance


def _project_error(
    exc: ProjectGovernanceError,
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


def _action_response(
    outcome: ProjectActionOutcome,
) -> AdminActionResult | JSONResponse:
    if outcome.success_status_code == 200:
        return outcome.result
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


def _access_grant_response(
    outcome: ProjectAccessGrantOutcome,
) -> ProjectAccessGrant | JSONResponse:
    if outcome.success_status_code == 200:
        return outcome.result
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


@router.get("/api/v1/admin/projects", response_model=ProjectAdminListResult)
def list_projects(request: Request) -> ProjectAdminListResult | JSONResponse:
    try:
        return _project_service(request).list_projects(current_user(request))
    except ProjectGovernanceError as exc:
        return _project_error(exc)


@router.get(
    "/api/v1/admin/projects/{project_id}/members",
    response_model=ProjectAccessGrantListResult,
)
def list_project_access_grants(
    project_id: str,
    request: Request,
) -> ProjectAccessGrantListResult | JSONResponse:
    try:
        return _project_service(request).list_access_grants(
            current_user(request),
            project_id,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc)


@router.get(
    "/api/v1/admin/projects/{project_id}/member-candidates",
    response_model=ProjectMemberCandidatesResult,
)
def list_project_member_candidates(
    project_id: str,
    request: Request,
) -> ProjectMemberCandidatesResult | JSONResponse:
    try:
        return _project_service(request).list_member_candidates(
            current_user(request),
            project_id,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc)


@router.post(
    "/api/v1/admin/projects/{project_id}/members",
    response_model=ProjectAccessGrant,
)
def create_project_access_grant(
    project_id: str,
    payload: ProjectAccessGrantCreateRequest,
    request: Request,
) -> ProjectAccessGrant | JSONResponse:
    try:
        outcome = _project_service(request).create_access_grant(
            current_user(request),
            project_id,
            payload,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc, payload.idempotency_key)
    return _access_grant_response(outcome)


@router.patch(
    "/api/v1/admin/projects/{project_id}/members/{grant_id}",
    response_model=ProjectAccessGrant,
)
def update_project_access_grant(
    project_id: str,
    grant_id: str,
    payload: ProjectAccessGrantUpdateRequest,
    request: Request,
) -> ProjectAccessGrant | JSONResponse:
    try:
        outcome = _project_service(request).update_access_grant(
            current_user(request),
            project_id,
            grant_id,
            payload,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc, payload.idempotency_key)
    return _access_grant_response(outcome)


@router.delete(
    "/api/v1/admin/projects/{project_id}/members/{grant_id}",
    response_model=ProjectAccessGrant,
)
def revoke_project_access_grant(
    project_id: str,
    grant_id: str,
    request: Request,
) -> ProjectAccessGrant | JSONResponse:
    try:
        outcome = _project_service(request).revoke_access_grant(
            current_user(request),
            project_id,
            grant_id,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc, f"revoke-{grant_id}")
    return _access_grant_response(outcome)


@router.post("/api/v1/admin/projects")
def create_project(
    payload: ProjectCreateRequest,
    request: Request,
):
    try:
        outcome = _project_service(request).create_project(
            current_user(request),
            payload,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc, payload.idempotency_key)
    return _action_response(outcome)


@router.patch("/api/v1/admin/projects/{project_id}", response_model=AdminActionResult)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        outcome = _project_service(request).update_project(
            current_user(request),
            project_id,
            payload,
        )
    except ProjectGovernanceError as exc:
        return _project_error(exc, payload.idempotency_key)
    return _action_response(outcome)
