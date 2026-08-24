from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.audit.public import AuditEventList
from atlas_production.modules.document_intake.public import (
    DocumentLibraryListResult,
    DocumentLibraryMutationResult,
    DocumentLibraryOutcomeV1,
    DocumentLibraryUploadCommand,
    DocumentLibraryUpdateRequest,
)
from atlas_production.shared.http import admin_rejected, error, session_token
from atlas_production.shared.public import AdminActionResult
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()

def _form_text(form, field: str) -> str | None:
    value = form.get(field)
    return None if value is None else str(value)




def _response(outcome: DocumentLibraryOutcomeV1):
    if outcome.failure is not None:
        failure = outcome.failure
        if failure.kind == "admin_rejected":
            assert failure.request_id is not None and failure.audit_event_ref is not None
            return admin_rejected(
                failure.request_id,
                failure.message_code,
                failure.audit_event_ref,
                failure.status_code,
            )
        assert failure.error_code is not None
        return error(
            failure.error_code,
            failure.message_code,
            failure.status_code,
            audit_event_ref=failure.audit_event_ref,
        )
    assert outcome.value is not None
    if outcome.status_code != 200:
        content = (
            outcome.value.model_dump()
            if hasattr(outcome.value, "model_dump")
            else outcome.value
        )
        return JSONResponse(status_code=outcome.status_code, content=content)
    return outcome.value


@router.get("/api/v1/admin/document-library", response_model=DocumentLibraryListResult)
def list_document_library(
    request: Request,
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_using_document_library",
            401,
        )
    return _response(
        api_composition(request).document_library.list(
            actor=actor,
            session_token=session_token(request) or "",
            scope_type=scope_type,
            scope_id=scope_id,
        )
    )


@router.post(
    "/api/v1/admin/document-library",
    response_model=DocumentLibraryMutationResult,
)
async def upload_document_library_file(request: Request):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_uploading_a_document",
            401,
        )
    try:
        form = await request.form()
    except Exception:
        command = None
    else:
        if "document_id" in form:
            return error(
                "validation_error",
                "document.upload_was_not_valid",
                422,
            )
        uploaded = form.get("file")
        command = DocumentLibraryUploadCommand(
            idempotency_key=_form_text(form, "idempotency_key"),
            scope_type=_form_text(form, "scope_type"),
            scope_id=_form_text(form, "scope_id"),
            tag_refs=_form_text(form, "tag_refs"),
            allow_member_download=_form_text(form, "allow_member_download"),
            description=_form_text(form, "description"),
            filename=getattr(uploaded, "filename", None),
            content_type=getattr(uploaded, "content_type", None),
            file=(
                getattr(uploaded, "file", None)
                if uploaded is not None and hasattr(uploaded, "read")
                else None
            ),
        )
    return _response(
        api_composition(request).document_library.upload(
            actor=actor,
            session_token=session_token(request) or "",
            command=command,
        )
    )


@router.patch(
    "/api/v1/admin/document-library/{document_id}",
    response_model=DocumentLibraryMutationResult,
)
def update_document_library(
    document_id: str,
    payload: DocumentLibraryUpdateRequest,
    request: Request,
):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_updating_a_document",
            401,
        )
    return _response(
        api_composition(request).document_library.update(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
            payload=payload,
        )
    )


@router.post(
    "/api/v1/admin/document-library/{document_id}/refresh-searchable-content",
    response_model=None,
)
def refresh_searchable_content(
    document_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "auth.please_sign_in_before_updating_searchable_content",
            401,
        )
    return _response(
        api_composition(request).document_library.refresh(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/api/v1/admin/document-library/{document_id}/disable",
    response_model=AdminActionResult,
)
def disable_document(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_changing_document_status",
            401,
        )
    return _response(
        api_composition(request).document_library.disable(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
        )
    )


@router.post(
    "/api/v1/admin/document-library/{document_id}/restore",
    response_model=AdminActionResult,
)
def restore_document(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_changing_document_status",
            401,
        )
    return _response(
        api_composition(request).document_library.restore(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
        )
    )


@router.get(
    "/api/v1/admin/document-library/{document_id}/events",
    response_model=AuditEventList,
)
def document_events(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "document.please_sign_in_before_opening_document_records",
            401,
        )
    return _response(
        api_composition(request).document_library.events(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
        )
    )
