from fastapi import APIRouter, Request, Response

from atlas_production.modules.identity_access.public import (
    FirstAdminClaimRequest,
    FirstAdminStatus,
    LoginRequest,
    SessionState,
)
from ..modules.identity_access.public import IdentityAccessError, IdentityAccessService
from atlas_production.shared.http import (
    error,
    session_token,
)
from atlas_production.transport.dependencies import api_composition

router = APIRouter()


def identity_service(request: Request) -> IdentityAccessService:
    return api_composition(request).identity_access
def _write_session_cookie(response: Response, raw_session_token: str) -> None:
    response.set_cookie(
        "atlas_session",
        raw_session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


@router.get("/api/v1/auth/first-admin", response_model=FirstAdminStatus)
def get_first_admin_status(request: Request) -> FirstAdminStatus:
    return identity_service(request).first_admin_status()


@router.post(
    "/api/v1/auth/first-admin",
    response_model=SessionState,
    status_code=201,
)
def claim_first_admin(
    payload: FirstAdminClaimRequest,
    request: Request,
    response: Response,
):
    try:
        outcome = identity_service(request).claim_first_admin(payload)
    except IdentityAccessError as exc:
        return error(
            exc.error_code,
            exc.message_code,
            exc.status_code,
            exc.audit_event_ref,
        )
    _write_session_cookie(response, outcome.raw_session_token)
    return outcome.session




@router.get("/api/v1/auth/session", response_model=SessionState)
def get_session(request: Request) -> SessionState:
    return identity_service(request).session_for_token(session_token(request))


@router.post("/api/v1/auth/sessions", response_model=SessionState)
def login(payload: LoginRequest, request: Request, response: Response):
    try:
        outcome = identity_service(request).login(payload)
    except IdentityAccessError as exc:
        return error(
            exc.error_code,
            exc.message_code,
            exc.status_code,
            exc.audit_event_ref,
        )
    _write_session_cookie(response, outcome.raw_session_token)
    return outcome.session


@router.delete("/api/v1/auth/session", status_code=204)
def logout(request: Request) -> Response:
    identity_service(request).logout(session_token(request))
    logout_response = Response(status_code=204)
    logout_response.delete_cookie("atlas_session", path="/")
    return logout_response
