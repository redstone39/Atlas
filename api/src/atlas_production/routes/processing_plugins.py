from __future__ import annotations

from fastapi import APIRouter, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from atlas_production.modules.processing_pipeline.public import (
    IdempotentRequest, PluginMutationRequest, ProfileActivateRequest,
    ProfileCreateRequest, ProfileRevisionCreateRequest,
    ProcessingRegistryError,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request): return api_composition(request).processing_registry
def _failure(exc: ProcessingRegistryError): return error(exc.error_code, exc.message_code, exc.status_code)
def _key(body_key: str | None, header_key: str | None) -> str:
    if header_key is not None and body_key is not None and header_key != body_key:
        raise ProcessingRegistryError("idempotency_key_mismatch", 'request.idempotency_header_and_body_must_match', 422)
    key = header_key or body_key
    if not key:
        raise ProcessingRegistryError("idempotency_key_required", 'request.idempotency_key_is_required_for_mutations', 422)
    return key
def _expected(body_value: int | None, if_match: str | None) -> int | None:
    if if_match is None: return body_value
    try: header_value = int(if_match.strip('"'))
    except ValueError as exc: raise ProcessingRegistryError("invalid_if_match", 'request.if_match_must_contain_a_numeric_revision', 422) from exc
    if body_value is not None and body_value != header_value: raise ProcessingRegistryError("revision_mismatch", 'request.if_match_and_expected_revision_must_match', 422)
    return header_value
def _outcome(result):
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/api/v1/admin/processing-plugins/packages")
async def upload_package(request: Request, package: UploadFile = File(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try: return _outcome(_service(request).upload_package(current_user(request), package.filename or "", await package.read(), _key(None, idempotency_key)))
    except ProcessingRegistryError as exc: return _failure(exc)


@router.get("/api/v1/admin/processing-plugins")
def list_plugins(request: Request):
    try: return {"items": _service(request).list_plugins(current_user(request))}
    except ProcessingRegistryError as exc: return _failure(exc)


@router.get("/api/v1/admin/processing-plugins/{plugin_id}/versions/{version}")
def show_plugin(plugin_id: str, version: str, request: Request):
    try: return _service(request).get_plugin(current_user(request), plugin_id, version)
    except ProcessingRegistryError as exc: return _failure(exc)


def _plugin_mutation(plugin_id, version, operation, payload, request, idempotency_header, if_match):
    try: return _outcome(_service(request).mutate_plugin(current_user(request), plugin_id, version, operation, _key(payload.idempotency_key, idempotency_header), _expected(payload.expected_revision, if_match)))
    except ProcessingRegistryError as exc: return _failure(exc)


@router.post("/api/v1/admin/processing-plugins/{plugin_id}/versions/{version}/validate")
def validate_plugin(plugin_id: str, version: str, payload: PluginMutationRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")): return _plugin_mutation(plugin_id, version, "validate", payload, request, idempotency_key, if_match)
@router.post("/api/v1/admin/processing-plugins/{plugin_id}/versions/{version}/canary")
def canary_plugin(plugin_id: str, version: str, payload: PluginMutationRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")): return _plugin_mutation(plugin_id, version, "canary", payload, request, idempotency_key, if_match)
@router.post("/api/v1/admin/processing-plugins/{plugin_id}/versions/{version}/disable")
def disable_plugin(plugin_id: str, version: str, payload: PluginMutationRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")): return _plugin_mutation(plugin_id, version, "disable", payload, request, idempotency_key, if_match)


@router.get("/api/v1/admin/processing-profiles")
def list_profiles(request: Request):
    try: return {"items": _service(request).list_profiles(current_user(request))}
    except ProcessingRegistryError as exc: return _failure(exc)
@router.post("/api/v1/admin/processing-profiles")
def create_profile(payload: ProfileCreateRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try: payload.idempotency_key = _key(payload.idempotency_key, idempotency_key); return _outcome(_service(request).create_profile(current_user(request), payload))
    except ProcessingRegistryError as exc: return _failure(exc)
@router.post("/api/v1/admin/processing-profiles/{profile_id}/revisions")
def create_profile_revision(profile_id: str, payload: ProfileRevisionCreateRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        payload.idempotency_key = _key(payload.idempotency_key, idempotency_key)
        expected = _expected(None, if_match)
        if expected is None:
            raise ProcessingRegistryError("revision_required", 'processing.if_match_is_required_when_creating_a_profile_revision', 422)
        return _outcome(_service(request).create_revision(current_user(request), profile_id, payload, expected))
    except ProcessingRegistryError as exc: return _failure(exc)
@router.post("/api/v1/admin/processing-profiles/{profile_id}/revisions/{revision}/activate")
def activate_profile_revision(profile_id: str, revision: int, payload: ProfileActivateRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        payload.idempotency_key = _key(payload.idempotency_key, idempotency_key)
        payload.expected_revision = _expected(payload.expected_revision, if_match)
        if payload.expected_revision is None:
            raise ProcessingRegistryError("revision_required", 'request.if_match_or_expected_revision_is_required', 422)
        return _outcome(_service(request).activate_revision(current_user(request), profile_id, revision, payload))
    except ProcessingRegistryError as exc: return _failure(exc)


@router.get("/api/v1/admin/processing-runs")
def list_runs(request: Request):
    try: return {"items": _service(request).list_runs(current_user(request))}
    except ProcessingRegistryError as exc: return _failure(exc)
@router.get("/api/v1/admin/processing-runs/{run_id}")
def show_run(run_id: str, request: Request):
    try: return _service(request).get_run(current_user(request), run_id)
    except ProcessingRegistryError as exc: return _failure(exc)
@router.post("/api/v1/admin/processing-runs/{run_id}/retry")
def retry_run(run_id: str, payload: IdempotentRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try: payload.idempotency_key = _key(payload.idempotency_key, idempotency_key); return _outcome(_service(request).retry_run(current_user(request), run_id, payload))
    except ProcessingRegistryError as exc: return _failure(exc)
