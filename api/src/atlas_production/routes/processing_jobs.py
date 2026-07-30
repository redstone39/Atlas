from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.async_runtime.public import best_effort_dispatch
from atlas_production.infrastructure.postgres_owner.document_processing import (
    ProcessingControlDenied,
)
from atlas_production.rbac import (
    direct_team_role,
    effective_document_scope,
    is_system_admin,
    resolve_access,
    team_role_covers,
)
from atlas_production.shared.http import error, session_token
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
    require_admin,
)


router = APIRouter()
logger = logging.getLogger(__name__)
PUBLIC_PROCESSING_STATUSES = frozenset({
    "queued", "processing", "waiting_retry", "publishing", "ready",
    "ready_with_warnings", "failed", "cancelled",
})


def _public_status(job, document) -> str:
    if job.status == "queued": return "queued"
    if job.status == "retry_wait": return "waiting_retry"
    if job.status == "running" and job.stage == "publishing": return "publishing"
    if job.status == "running": return "processing"
    if job.status == "succeeded":
        if document.processing_job_id == job.job_id and document.warning_codes:
            return "ready_with_warnings"
        return "ready"
    if job.status == "cancelled": return "cancelled"
    return "failed"


def _elapsed(job) -> int:
    end = job.updated_at if job.status in {"succeeded", "failed", "cancelled"} else datetime.now(timezone.utc)
    return max(0, int((end - job.attempt_started_at).total_seconds()))


def _can_control(state, actor, document) -> bool:
    if document.uploader_actor_id == actor.actor_id: return True
    if is_system_admin(state, actor.actor_type, actor.actor_id): return True
    if document.scope_type == "team":
        return team_role_covers(
            direct_team_role(state, actor.actor_type, actor.actor_id, document.scope_id or ""),
            "admin",
        )
    if document.scope_type == "project" and document.scope_id:
        return resolve_access(
            state,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            project_id=document.scope_id,
            action="permission_manage",
            persist=False,
        ).allowed
    return False


def _serialize(job, document, *, profile_pin, can_control: bool) -> dict:
    status = _public_status(job, document)
    is_current = document.processing_job_id == job.job_id
    payload = {
        "document_id": job.document_id,
        "document_format": document.document_format,
        "profile_id": profile_pin.profile_id if profile_pin else None,
        "profile_revision": profile_pin.profile_revision if profile_pin else None,
        "current_stage": job.stage,
        "warning_codes": list(document.warning_codes) if is_current else [],
        "failure_code": job.failure_code if status == "failed" else None,
        "job_id": job.job_id,
        "status": status,
        "status_url": f"/api/v1/processing/jobs/{job.job_id}",
        "retry_available": is_current and can_control and status in {"failed", "cancelled"},
        "cancel_available": is_current and can_control and status in {"queued", "processing", "waiting_retry", "publishing"},
        "review_available": True,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_unit": job.progress_unit,
        "elapsed_seconds": _elapsed(job),
        "attempt_started_at": job.attempt_started_at.isoformat(),
        "is_current": is_current,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
    assert payload["status"] in PUBLIC_PROCESSING_STATUSES
    return payload


def _projection(request: Request, job_id: str):
    actor = current_user(request)
    if actor is None: return actor, None
    item = api_composition(request).document_processing.get_document_job_request_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
        job_id=job_id,
    )
    return actor, item


@router.get("/api/v1/processing/jobs/{job_id}")
def get_processing_job(job_id: str, request: Request):
    actor, item = _projection(request, job_id)
    if actor is None:
        return error("unauthenticated", "processing.please_sign_in_before_opening_processing_status", 401)
    if item is None:
        return error("not_found", "processing.job_was_not_found", 404)
    allowed = effective_document_scope(
        item.authorization_state,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="read_derived",
    )
    if not any(ref in allowed for ref in item.tag_refs):
        return error("not_found", "processing.job_was_not_found", 404)
    return _serialize(
        item.job, item.document, profile_pin=item.profile_pin,
        can_control=_can_control(item.authorization_state, actor, item.document),
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
        return error("unauthenticated", "processing.please_sign_in_before_opening_processing_status", 401)
    projections = api_composition(request).document_processing.list_document_job_request_projections(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
        document_id=document_id,
    )
    jobs = []
    for item in projections:
        allowed = effective_document_scope(
            item.authorization_state,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="read_derived",
        )
        if not any(ref in allowed for ref in item.tag_refs): continue
        projected = _serialize(
            item.job, item.document, profile_pin=item.profile_pin,
            can_control=_can_control(item.authorization_state, actor, item.document),
        )
        if status is not None and projected["status"] != status: continue
        if profile_id is not None and projected["profile_id"] != profile_id: continue
        if profile_revision is not None and projected["profile_revision"] != profile_revision: continue
        jobs.append(projected)
    return {"jobs": jobs}


def _control_job(job_id: str, request: Request, *, retry: bool):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "processing.please_sign_in_before_opening_processing_status", 401)
    command = api_composition(request).document_processing
    try:
        result = (
            command.retry_processing_job_request if retry
            else command.stop_processing_job_request
        )(
            job_id=job_id,
            presented_browser_session_token=session_token(request) or "",
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
        )
    except ProcessingControlDenied as exc:
        return error(
            "access_denied",
            "processing.only_the_uploader_or_scope_admin_can_control_this_job",
            403,
            audit_event_ref=exc.audit_event.event_id,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "processing_job_not_found":
            return error("not_found", "processing.job_was_not_found", 404)
        message = (
            "processing.only_a_failed_or_stopped_job_can_start_a_new_attempt"
            if retry else "processing.only_an_active_processing_job_can_be_stopped"
        )
        return error(code, message, 409)
    except Exception:
        logger.exception(
            "processing job control failed",
            extra={"job_id": job_id, "control": "retry" if retry else "stop"},
        )
        raise
    item = api_composition(request).document_processing.get_document_job_request_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
        job_id=job_id,
    )
    if item is None:
        return error("not_found", "processing.job_was_not_found", 404)
    if retry: best_effort_dispatch()
    return JSONResponse(
        status_code=202 if retry else 200,
        content={
            **_serialize(
                result.job, item.document, profile_pin=item.profile_pin,
                can_control=True,
            ),
            "audit_event_ref": result.audit_event.event_id,
        },
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
    if (denied := require_admin(request)) is not None: return denied
    actor = current_user(request)
    assert actor is not None
    composition = api_composition(request)
    projection = composition.document_intake.document_library_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
        document_id=document_id,
    )
    if len(projection.items) != 1 or projection.items[0].document.lifecycle_status != "active":
        return error("not_found", "document.was_not_found", 404)
    document = projection.items[0].document
    if document.active_processing_generation <= 0:
        return error("document_not_published", "document.has_no_published_processing_generation", 409)
    job = composition.document_processing.create_processing_job(
        document_id=document_id,
        document_version_id=composition.document_intake.processing_document_version_id(document),
        job_kind="reindex",
        idempotency_scope="document_reindex",
        idempotency_key=idempotency_key or f"reindex-{document_id}-{uuid4().hex}",
        created_by=actor.actor_id,
    )
    best_effort_dispatch()
    return JSONResponse(
        status_code=202,
        content=_serialize(job, document, profile_pin=None, can_control=True),
    )
