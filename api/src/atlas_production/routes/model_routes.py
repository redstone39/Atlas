from fastapi import APIRouter, Request
from typing import Literal
from fastapi.responses import JSONResponse

from atlas_production.modules.model_routing.public import (
    ModelRouteCreateRequest,
    ModelRouteDefaultRequest,
    ModelRouteListResult,
    ModelRouteStatus,
    ModelRouteTestRequest,
    ModelRouteUpdateRequest,
    ProviderConnectionCreateRequest,
    ProviderConnectionListResult,
    ProviderConnectionStatus,
    ProviderConnectionTestRequest,
    ProviderConnectionTestResult,
    ProviderConnectionUpdateRequest,
    ProviderModelDiscoveryResult,
    ModelRouteOutcome,
    ModelRoutingError,
    ModelRoutingService,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _model_routing_service(request: Request) -> ModelRoutingService:
    return api_composition(request).model_routing


def _model_routing_error(exc: ModelRoutingError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


def _model_route_response(outcome: ModelRouteOutcome):
    if outcome.success_status_code == 200:
        return outcome.result
    return JSONResponse(
        status_code=outcome.success_status_code,
        content=outcome.result.model_dump(),
    )


@router.get(
    "/api/v1/admin/config/provider-connections",
    response_model=ProviderConnectionListResult,
)
def list_provider_connections(request: Request):
    try:
        return _model_routing_service(request).list_connections(current_user(request))
    except ModelRoutingError as exc:
        return _model_routing_error(exc)


@router.post(
    "/api/v1/admin/config/provider-connections",
    response_model=ProviderConnectionStatus,
    status_code=201,
)
def create_provider_connection(
    payload: ProviderConnectionCreateRequest,
    request: Request,
):
    try:
        outcome = _model_routing_service(request).create_connection(
            current_user(request), payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)


@router.patch(
    "/api/v1/admin/config/provider-connections/{connection_id}",
    response_model=ProviderConnectionStatus,
)
def update_provider_connection(
    connection_id: str,
    payload: ProviderConnectionUpdateRequest,
    request: Request,
):
    try:
        outcome = _model_routing_service(request).update_connection(
            current_user(request), connection_id, payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)


@router.post(
    "/api/v1/admin/config/provider-connections/{connection_id}/test",
    response_model=ProviderConnectionTestResult,
)
def test_provider_connection(
    connection_id: str,
    payload: ProviderConnectionTestRequest,
    request: Request,
):
    try:
        return _model_routing_service(request).test_connection(
            current_user(request), connection_id, payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)


@router.get(
    "/api/v1/admin/config/provider-connections/{connection_id}/available-models",
    response_model=ProviderModelDiscoveryResult,
)
def discover_provider_models(connection_id: str, request: Request):
    try:
        return _model_routing_service(request).discover_models(
            current_user(request), connection_id
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)


@router.get("/api/v1/admin/config/model-routes", response_model=ModelRouteListResult)
def list_model_routes(request: Request):
    try:
        return _model_routing_service(request).list_routes(current_user(request))
    except ModelRoutingError as exc:
        return _model_routing_error(exc)


@router.post(
    "/api/v1/admin/config/model-routes",
    response_model=ModelRouteStatus,
    status_code=201,
)
def configure_model_route(payload: ModelRouteCreateRequest, request: Request):
    try:
        outcome = _model_routing_service(request).configure(
            current_user(request), payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)


@router.patch(
    "/api/v1/admin/config/model-routes/{route_id}",
    response_model=ModelRouteStatus,
)
def update_model_route(
    route_id: str,
    payload: ModelRouteUpdateRequest,
    request: Request,
):
    try:
        outcome = _model_routing_service(request).update_route(
            current_user(request), route_id, payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)


@router.post(
    "/api/v1/admin/config/model-routes/{route_id}/defaults/{purpose}",
    response_model=ModelRouteStatus,
)
def set_default_model_route(
    route_id: str,
    purpose: Literal["text", "vision"],
    payload: ModelRouteDefaultRequest,
    request: Request,
):
    try:
        outcome = _model_routing_service(request).set_default(
            current_user(request), route_id, purpose, payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)


@router.post(
    "/api/v1/admin/config/model-routes/{route_id}/test",
    response_model=ModelRouteStatus,
)
def test_model_route(
    route_id: str,
    payload: ModelRouteTestRequest,
    request: Request,
):
    try:
        outcome = _model_routing_service(request).test_route(
            current_user(request), route_id, payload
        )
    except ModelRoutingError as exc:
        return _model_routing_error(exc)
    return _model_route_response(outcome)
