from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.processing_pipeline.public import ProcessingJobsOutcomeV1
from atlas_production.shared.http import error, session_token
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _response(outcome: ProcessingJobsOutcomeV1):
    if outcome.failure is not None:
        failure = outcome.failure
        return error(
            failure.error_code,
            failure.message_code,
            failure.status_code,
            audit_event_ref=failure.audit_event_ref,
        )
    assert outcome.value is not None
    if outcome.status_code != 200:
        return JSONResponse(status_code=outcome.status_code, content=outcome.value)
    return outcome.value


@router.get("/api/v1/processing/jobs/{job_id}")
def get_processing_job(job_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "processing.please_sign_in_before_opening_processing_status",
            401,
        )
    return _response(
        api_composition(request).processing_jobs.get(
            actor=actor,
            session_token=session_token(request) or "",
            job_id=job_id,
        )
    )


@router.get("/api/v1/processing/jobs")
def list_processing_jobs(
    request: Request,
    document_id: str | None = Query(default=None),
    profile_id: str | None = Query(default=None),
    profile_revision: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None),
):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "processing.please_sign_in_before_opening_processing_status",
            401,
        )
    return _response(
        api_composition(request).processing_jobs.list(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
            profile_id=profile_id,
            profile_revision=profile_revision,
            status=status,
        )
    )


def _control_job(job_id: str, request: Request, *, retry: bool):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "processing.please_sign_in_before_opening_processing_status",
            401,
        )
    return _response(
        api_composition(request).processing_jobs.control(
            actor=actor,
            session_token=session_token(request) or "",
            job_id=job_id,
            retry=retry,
        )
    )


@router.post("/api/v1/processing/jobs/{job_id}/cancel")
def cancel_processing_job(job_id: str, request: Request):
    return _control_job(job_id, request, retry=False)


@router.post("/api/v1/processing/jobs/{job_id}/retry")
def retry_processing_job(job_id: str, request: Request):
    return _control_job(job_id, request, retry=True)


@router.post("/api/v1/admin/document-library/{document_id}/reindex")
def reindex_document(
    document_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    actor = current_user(request)
    if actor is None:
        return error(
            "unauthenticated",
            "auth.please_sign_in_before_using_admin_tools",
            401,
        )
    return _response(
        api_composition(request).processing_jobs.reindex(
            actor=actor,
            session_token=session_token(request) or "",
            document_id=document_id,
            idempotency_key=idempotency_key,
        )
    )
