from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.identity_access.public import (
    DirectoryConnectionCreateRequest,
    DirectoryConnectionListResult,
    DirectoryConnectionStatus,
    DirectoryConnectionTestResult,
    DirectoryConnectionUpdateRequest,
    DirectoryIdentityService,
    DirectoryProfileSummary,
    DirectoryUserImportRequest,
    DirectoryUserImportResult,
    DirectoryUserSearchRequest,
    DirectoryUserSearchResult,
    IdentityAccessError,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request) -> DirectoryIdentityService:
    return api_composition(request).directory_identity


def _error(exc: IdentityAccessError) -> JSONResponse:
    return error(
        exc.error_code,
        exc.message_code,
        exc.status_code,
        None,
    )


@router.get(
    "/api/v1/admin/directory-connections",
    response_model=DirectoryConnectionListResult,
)
def list_directory_connections(
    request: Request,
) -> DirectoryConnectionListResult | JSONResponse:
    try:
        return _service(request).list_connections(current_user(request))
    except IdentityAccessError as exc:
        return _error(exc)


@router.post(
    "/api/v1/admin/directory-connections",
    response_model=DirectoryConnectionStatus,
    status_code=201,
)
def create_directory_connection(
    payload: DirectoryConnectionCreateRequest,
    request: Request,
) -> DirectoryConnectionStatus | JSONResponse:
    try:
        return _service(request).create_connection(current_user(request), payload)
    except IdentityAccessError as exc:
        return _error(exc)


@router.patch(
    "/api/v1/admin/directory-connections/{connection_id}",
    response_model=DirectoryConnectionStatus,
)
def update_directory_connection(
    connection_id: str,
    payload: DirectoryConnectionUpdateRequest,
    request: Request,
) -> DirectoryConnectionStatus | JSONResponse:
    try:
        return _service(request).update_connection(
            current_user(request), connection_id, payload
        )
    except IdentityAccessError as exc:
        return _error(exc)


@router.post(
    "/api/v1/admin/directory-connections/{connection_id}/test",
    response_model=DirectoryConnectionTestResult,
)
def test_directory_connection(
    connection_id: str,
    request: Request,
) -> DirectoryConnectionTestResult | JSONResponse:
    try:
        return _service(request).test_connection(current_user(request), connection_id)
    except IdentityAccessError as exc:
        return _error(exc)


@router.post(
    "/api/v1/admin/directory-connections/{connection_id}/users/search",
    response_model=DirectoryUserSearchResult,
)
def search_directory_users(
    connection_id: str,
    payload: DirectoryUserSearchRequest,
    request: Request,
) -> DirectoryUserSearchResult | JSONResponse:
    try:
        return _service(request).search_users(
            current_user(request), connection_id, payload
        )
    except IdentityAccessError as exc:
        return _error(exc)


@router.post(
    "/api/v1/admin/directory-connections/{connection_id}/users/import",
    response_model=DirectoryUserImportResult,
)
def import_directory_users(
    connection_id: str,
    payload: DirectoryUserImportRequest,
    request: Request,
) -> DirectoryUserImportResult | JSONResponse:
    try:
        return _service(request).import_users(
            current_user(request), connection_id, payload
        )
    except IdentityAccessError as exc:
        return _error(exc)


@router.post(
    "/api/v1/admin/users/{actor_id}/directory-profile/refresh",
    response_model=DirectoryProfileSummary,
)
def refresh_directory_profile(
    actor_id: str,
    request: Request,
) -> DirectoryProfileSummary | JSONResponse:
    try:
        return _service(request).refresh_profile(current_user(request), actor_id)
    except IdentityAccessError as exc:
        return _error(exc)
