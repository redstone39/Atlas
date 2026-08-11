from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.identity_access.public import (
    TeamCreateRequest,
    TeamListResult,
    TeamMemberCandidatesResult,
    TeamMemberListResult,
    TeamMembershipCreateRequest,
    TeamUpdateRequest,
    UserAdminListResult,
    UserAdminUpdateRequest,
)
from atlas_production.shared.public import (
    AdminActionResult,
)
from ..modules.identity_access.public import (
    IdentityAccessError,
    IdentityAccessService,
    TeamAccessError,
    TeamAccessService,
    TeamActionOutcome,
)
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
)
from atlas_production.shared.http import (
    admin_rejected,
    error,
)

router = APIRouter()


def _identity_service(request: Request) -> IdentityAccessService:
    return api_composition(request).identity_access


def _identity_error(
    exc: IdentityAccessError,
    request_id: str | None = None,
) -> JSONResponse:
    if exc.error_code == "admin_action_rejected":
        assert request_id is not None
        assert exc.audit_event_ref is not None
        return admin_rejected(
            request_id,
            exc.message_code,
            exc.audit_event_ref,
            exc.status_code,
        )
    return error(
        exc.error_code,
        exc.message_code,
        exc.status_code,
        exc.audit_event_ref,
    )


@router.get("/api/v1/admin/users", response_model=UserAdminListResult)
def list_users(
    request: Request,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    account_source: Literal["local", "directory"] | None = None,
    directory_connection_id: Annotated[
        str | None, Query(min_length=1, max_length=200)
    ] = None,
    active: bool | None = None,
    directory_profile_status: Literal[
        "current", "stale", "missing", "disabled"
    ]
    | None = None,
    directory_group: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    department: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    title: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    employee_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> UserAdminListResult | JSONResponse:
    try:
        return _identity_service(request).list_users(
            current_user(request),
            q=q,
            account_source=account_source,
            directory_connection_id=directory_connection_id,
            active=active,
            directory_profile_status=directory_profile_status,
            directory_group=directory_group,
            department=department,
            title=title,
            employee_id=employee_id,
        )
    except IdentityAccessError as exc:
        return _identity_error(exc)


@router.patch("/api/v1/admin/users/{actor_id}", response_model=AdminActionResult)
def update_user(
    actor_id: str,
    payload: UserAdminUpdateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        return _identity_service(request).update_user(
            current_user(request),
            actor_id,
            payload,
        )
    except IdentityAccessError as exc:
        return _identity_error(exc, payload.idempotency_key)


def _team_service(request: Request) -> TeamAccessService:
    return api_composition(request).team_access


def _team_error(
    exc: TeamAccessError,
    request_id: str | None = None,
) -> JSONResponse:
    if exc.error_code == "admin_action_rejected":
        assert request_id is not None
        assert exc.audit_event_ref is not None
        return admin_rejected(
            request_id,
            exc.message_code,
            exc.audit_event_ref,
            exc.status_code,
        )
    return error(
        exc.error_code,
        exc.message_code,
        exc.status_code,
        exc.audit_event_ref,
    )


def _team_outcome_response(outcome: TeamActionOutcome):
    if outcome.success_status_code == 200:
        return outcome.result
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


@router.get("/api/v1/admin/teams", response_model=TeamListResult)
def list_teams(request: Request) -> TeamListResult | JSONResponse:
    try:
        return _team_service(request).list_teams(current_user(request))
    except TeamAccessError as exc:
        return _team_error(exc)


@router.get("/api/v1/admin/teams/{team_id}/members", response_model=TeamMemberListResult)
def list_team_members(
    team_id: str,
    request: Request,
) -> TeamMemberListResult | JSONResponse:
    try:
        return _team_service(request).list_members(current_user(request), team_id)
    except TeamAccessError as exc:
        return _team_error(exc)


@router.get(
    "/api/v1/admin/teams/{team_id}/member-candidates",
    response_model=TeamMemberCandidatesResult,
)
def list_team_member_candidates(
    team_id: str,
    request: Request,
) -> TeamMemberCandidatesResult | JSONResponse:
    try:
        return _team_service(request).list_member_candidates(
            current_user(request),
            team_id,
        )
    except TeamAccessError as exc:
        return _team_error(exc)


@router.post("/api/v1/admin/teams", response_model=AdminActionResult)
def create_team(
    payload: TeamCreateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        outcome = _team_service(request).create_team(current_user(request), payload)
    except TeamAccessError as exc:
        return _team_error(exc, payload.idempotency_key)
    return _team_outcome_response(outcome)


@router.patch("/api/v1/admin/teams/{team_id}", response_model=AdminActionResult)
def update_team(
    team_id: str,
    payload: TeamUpdateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        outcome = _team_service(request).update_team(
            current_user(request),
            team_id,
            payload,
        )
    except TeamAccessError as exc:
        return _team_error(exc, payload.idempotency_key)
    return _team_outcome_response(outcome)


@router.post("/api/v1/admin/teams/{team_id}/members", response_model=AdminActionResult)
def add_team_member(
    team_id: str,
    payload: TeamMembershipCreateRequest,
    request: Request,
) -> AdminActionResult | JSONResponse:
    try:
        outcome = _team_service(request).add_member(
            current_user(request),
            team_id,
            payload,
        )
    except TeamAccessError as exc:
        return _team_error(exc, payload.idempotency_key)
    return _team_outcome_response(outcome)


@router.delete(
    "/api/v1/admin/teams/{team_id}/members/{membership_id}",
    response_model=AdminActionResult,
)
def remove_team_member(
    team_id: str,
    membership_id: str,
    request: Request,
) -> AdminActionResult | JSONResponse:
    request_id = f"remove-{membership_id}"
    try:
        outcome = _team_service(request).remove_member(
            current_user(request),
            team_id,
            membership_id,
        )
    except TeamAccessError as exc:
        return _team_error(exc, request_id)
    return _team_outcome_response(outcome)
