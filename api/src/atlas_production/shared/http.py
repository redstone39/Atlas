from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .http_contracts import AdminActionResult, ErrorResponse
from .correlation import current_correlation_id
from .user_messages import MessageParams


SENSITIVE_VALIDATION_KEYS = (
    "api_key",
    "password",
    "secret",
    "token",
    "credential",
    "pem",
    "dn",
)


def safe_validation_errors(errors: list[dict]) -> list[dict]:
    def sanitize(value, key: str = ""):
        if any(marker in key.casefold() for marker in SENSITIVE_VALIDATION_KEYS):
            return "[redacted]"
        if isinstance(value, dict):
            return {
                item_key: sanitize(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [sanitize(item, key) for item in value]
        if isinstance(value, tuple):
            return [sanitize(item, key) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return type(value).__name__

    sanitized = []
    for error_item in errors:
        item = sanitize(error_item)
        if (
            error_item.get("type") == "value_error"
            and isinstance(error_item.get("input"), dict)
            and "input" in item
        ):
            item["input"] = "[redacted]"
        location = [str(part).casefold() for part in error_item.get("loc", [])]
        if any(
            marker in part
            for part in location
            for marker in SENSITIVE_VALIDATION_KEYS
        ) and "input" in item:
            item["input"] = "[redacted]"
        sanitized.append(item)
    return sanitized


def session_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
    return request.cookies.get("atlas_session")


def agent_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
    return None


def error(
    code: str,
    message_code: str,
    status_code: int,
    audit_event_ref: str | None = None,
    message_params: MessageParams | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code=code,
            message_code=message_code,
            message_params=message_params or {},
            correlation_id=current_correlation_id(),
            audit_event_ref=audit_event_ref,
        ).model_dump(),
    )


def admin_rejected(
    request_id: str,
    message_code: str,
    audit_event_ref: str,
    status_code: int,
    message_params: MessageParams | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=AdminActionResult(
            request_id=request_id,
            status="rejected",
            target_ref=None,
            message_code=message_code,
            message_params=message_params or {},
            audit_event_ref=audit_event_ref,
        ).model_dump(),
    )
