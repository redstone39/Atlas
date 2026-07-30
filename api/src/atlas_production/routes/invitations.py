from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.shared.public import (
    AdminActionResult,
)
from atlas_production.modules.identity_access.public import (
    InviteAcceptRequest,
    InviteAcceptResult,
    UserInviteCreateRequest,
    UserInviteCreateResult,
    UserInviteListResult,
    UserInviteRevokeRequest,
)
from ..modules.identity_access.public import IdentityAccessError, IdentityAccessService
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
)
from atlas_production.shared.http import (
    admin_rejected,
    error,
)

router = APIRouter()


def identity_service(request: Request) -> IdentityAccessService:
    return api_composition(request).identity_access


def identity_error(
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


@router.post("/api/v1/admin/user-invites", response_model=UserInviteCreateResult)
def create_user_invite(payload: UserInviteCreateRequest, request: Request):
    try:
        result = identity_service(request).create_invite(current_user(request), payload)
    except IdentityAccessError as exc:
        return identity_error(exc, payload.idempotency_key)
    return JSONResponse(status_code=201, content=result.model_dump())


@router.get("/api/v1/admin/user-invites", response_model=UserInviteListResult)
def list_user_invites(request: Request):
    try:
        return identity_service(request).list_invites(current_user(request))
    except IdentityAccessError as exc:
        return identity_error(exc)


@router.post(
    "/api/v1/admin/user-invites/{invite_id}/revoke",
    response_model=AdminActionResult,
)
def revoke_user_invite(
    invite_id: str,
    payload: UserInviteRevokeRequest,
    request: Request,
):
    try:
        return identity_service(request).revoke_invite(
            current_user(request),
            invite_id,
            payload,
        )
    except IdentityAccessError as exc:
        return identity_error(exc, payload.idempotency_key)


@router.post("/api/v1/auth/invitations/accept", response_model=InviteAcceptResult)
def accept_user_invite(payload: InviteAcceptRequest, request: Request):
    try:
        return identity_service(request).accept_invite(payload)
    except IdentityAccessError as exc:
        return identity_error(exc, payload.idempotency_key)
