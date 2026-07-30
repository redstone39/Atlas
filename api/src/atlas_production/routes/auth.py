from fastapi import APIRouter, Request, Response

from atlas_production.modules.identity_access.public import (
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
    response.set_cookie(
        "atlas_session",
        outcome.raw_session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return outcome.session


@router.delete("/api/v1/auth/session", status_code=204)
def logout(request: Request) -> Response:
    identity_service(request).logout(session_token(request))
    logout_response = Response(status_code=204)
    logout_response.delete_cookie("atlas_session", path="/")
    return logout_response
