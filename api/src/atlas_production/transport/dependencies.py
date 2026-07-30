from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.shared.http import (
    error,
    session_token,
)


def api_composition(request: Request) -> ApiComposition:
    composition = request.app.state.api_composition
    if not isinstance(composition, ApiComposition):
        raise RuntimeError("API composition is not configured")
    return composition


def current_user(request: Request) -> UserRecord | None:
    principal = api_composition(request).current_principal
    return principal.current_user(session_token(request))


def require_admin(request: Request) -> JSONResponse | None:
    actor = current_user(request)
    if not actor:
        return error("unauthenticated", 'auth.please_sign_in_before_using_admin_tools', 401)
    if actor.system_role != "admin":
        return error("access_denied", 'permission.admin_permission_is_required', 403)
    return None
