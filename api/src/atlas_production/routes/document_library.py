from __future__ import annotations

from dataclasses import replace
import json
from uuid import uuid4

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from atlas_production.async_runtime.public import best_effort_dispatch
from atlas_production.infrastructure.postgres_document_intake_adapter import (
    DocumentLifecycleRequestInput,
    DocumentLibraryItemProjection,
)
from atlas_production.infrastructure.postgres_document_upload import (
    DocumentUploadAccessDenied,
    DocumentUploadReplayConflict,
    DocumentUploadUnauthenticated,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    DocumentLifecycleDenied,
    DocumentLifecycleProcessingAcceptance,
    DocumentProcessingCurrentnessConflict,
)
from atlas_production.modules.artifact_storage.public import ArtifactStorageError
from atlas_production.modules.audit.public import AuditEventList, audit_event_status
from atlas_production.modules.document_intake.public import (
    DocumentFormatError,
    DocumentLibraryListResult,
    DocumentLibraryMutationResult,
    DocumentLibrarySummary,
    DocumentLibraryUpdateRequest,
    DocumentRecord,
    DocumentTagRef,
    DocumentTagSummary,
    inspect_document_upload,
    source_allows_original_download,
    upload_request_fingerprint,
    uploaded_chunks,
)
from atlas_production.shared.http import admin_rejected, error
from atlas_production.shared.public import AdminActionResult, AuditEventRecord, utc_now_iso
from atlas_production.shared.http import session_token
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _audit(
    event_type: str,
    actor_id: str,
    document: DocumentRecord,
    message_code: str,
    metadata: dict[str, object],
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"audit-{uuid4().hex}",
        event_type=event_type,
        actor_id=actor_id,
        target_ref=f"document:{document.document_id}",
        project_id=document.scope_id if document.scope_type == "project" else None,
        message_code=message_code,
        metadata={"document_id": document.document_id, **metadata},
        created_at=utc_now_iso(),
        scope_type=document.scope_type,
        scope_id=document.scope_id,
        document_id=document.document_id,
    )


def _summary(
    item: DocumentLibraryItemProjection,
    document: DocumentRecord | None = None,
) -> DocumentLibrarySummary:
    current = document or item.document
    labels = {
        (scope_type, scope_id): label
        for scope_type, scope_id, label in item.scope_labels
    }
    download_available = item.download_available
    if document is not None and document.allow_member_download != item.document.allow_member_download:
        download_available = bool(
            item.can_administer
            and item.original_artifact_available
            and document.lifecycle_status == "active"
            and source_allows_original_download(
                document.content_type,
                source_download_restricted=document.source_download_restricted,
            )
        )
    assert current.scope_type in {"team", "project"} and current.scope_id
    return DocumentLibrarySummary(
        document_id=current.document_id,
        title=current.title,
        description=current.description,
        intake_status=current.intake_status,
        document_format=current.document_format,
        profile_id=current.processing_profile_id,
        profile_revision=current.processing_profile_revision,
        current_stage=current.current_stage,
        warning_codes=current.warning_codes,
        failure_code=current.failure_code,
        job_id=current.processing_job_id,
        lifecycle_status=current.lifecycle_status,
        uploader_actor_id=current.uploader_actor_id,
        scope_type=current.scope_type,
        scope_id=current.scope_id,
        direct_tags=[
            DocumentTagSummary(
                tag_type=tag.tag_type,
                tag_id=tag.tag_id,
                label=labels.get((tag.tag_type, tag.tag_id), tag.tag_id),
            )
            for tag in item.tags
        ],
        allow_member_download=current.allow_member_download,
        download_available=download_available,
        source_filename=current.source_filename,
        source_byte_size=current.source_byte_size,
        content_type=current.content_type,
        raw_sha256=current.raw_sha256,
        uploaded_at=current.uploaded_at,
        disabled_at=current.disabled_at,
        restored_at=current.restored_at,
        evidence_count=item.ready_evidence_count,
    )


def _projection(request: Request, *, document_id: str | None = None, events: bool = False):
    actor = current_user(request)
    if actor is None:
        raise PermissionError("document library request is unauthenticated")
    return api_composition(request).document_intake.document_library_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
        document_id=document_id,
        include_events=events,
    )


def _item(request: Request, document_id: str, *, events: bool = False):
    projection = _projection(request, document_id=document_id, events=events)
    return projection, next(
        (item for item in projection.items if item.document.document_id == document_id),
        None,
    )


def _facade(request: Request):
    return api_composition(request).document_intake.journey_facade()


def _lifecycle_input(
    request: Request,
    *,
    expected: DocumentRecord,
    document: DocumentRecord,
    item: DocumentLibraryItemProjection,
    success: AuditEventRecord,
    denial: AuditEventRecord,
    processing: DocumentLifecycleProcessingAcceptance | None = None,
    restore_verification=None,
) -> DocumentLifecycleRequestInput:
    actor = current_user(request)
    assert actor is not None
    return DocumentLifecycleRequestInput(
        presented_browser_session_token=session_token(request) or "",
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        expected_document=expected,
        document=document,
        tags=item.tags,
        audit_events=(success,),
        denial_audit_event=denial,
        processing_acceptance=processing,
        restore_verification=restore_verification,
    )


def _denied(exc: DocumentLifecycleDenied, message_code: str) -> JSONResponse:
    return error(
        "access_denied",
        message_code,
        403,
        audit_event_ref=exc.audit_event.event_id,
    )


def _normalize_description(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


def _title(filename: str | None) -> str | None:
    if not filename:
        return None
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        return None
    stem = name.rsplit(".", 1)[0].strip() if "." in name else name
    return " ".join((stem or name).split()) or None


def _parse_tags(value, primary: DocumentTagRef) -> list[DocumentTagRef] | None:
    if value is None:
        return [primary]
    try:
        raw = json.loads(str(value))
        if not isinstance(raw, list):
            return None
        refs = [DocumentTagRef.model_validate(item) for item in raw]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    deduped: list[DocumentTagRef] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.tag_type, ref.tag_id)
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    if (primary.tag_type, primary.tag_id) not in seen:
        return None
    return deduped


def _scope_error(scope_type: str, exists: bool, active: bool) -> JSONResponse | None:
    if exists and active:
        return None
    return admin_rejected(
        "document-library-scope",
        "team.was_not_found" if scope_type == "team" else "project.was_not_found",
        "audit-document-library-rejected",
        404,
    )


@router.get("/api/v1/admin/document-library", response_model=DocumentLibraryListResult)
def list_document_library(
    request: Request,
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
) -> DocumentLibraryListResult | JSONResponse:
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_using_document_library", 401)
    if (scope_type is None) != (scope_id is None) or scope_type not in {None, "team", "project"}:
        return admin_rejected("document-library-scope", "project.choose_a_valid_team_or_project", "audit-document-library-rejected", 422)
    if scope_type is not None and scope_id is not None:
        scope = api_composition(request).document_intake.requested_scope_projection(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token(request) or "",
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if response := _scope_error(scope_type, scope.exists, scope.active):
            return response
    projection = _projection(request)
    return DocumentLibraryListResult(
        documents=[
            _summary(item)
            for item in sorted(
                projection.items,
                key=lambda current: (
                    current.document.title,
                    current.document.document_id,
                ),
            )
            if item.can_view
            and (
                scope_type is None
                or any(
                    tag.tag_type == scope_type and tag.tag_id == scope_id
                    for tag in item.tags
                )
            )
        ]
    )


@router.post("/api/v1/admin/document-library", response_model=DocumentLibraryMutationResult)
async def upload_document_library_file(request: Request):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_uploading_a_document", 401)
    try:
        form = await request.form()
    except Exception:
        return admin_rejected(f"upload-{uuid4().hex[:10]}", "document.upload_was_not_valid", "audit-document-library-upload-rejected", 422)
    key = str(form.get("idempotency_key") or "").strip() or f"upload-{uuid4().hex[:10]}"
    scope_type = str(form.get("scope_type") or "").strip()
    scope_id = str(form.get("scope_id") or "").strip()
    if scope_type not in {"team", "project"} or not scope_id:
        return admin_rejected(key, "project.choose_a_team_or_project_before_uploading", "audit-document-library-upload-rejected", 422)
    tags = _parse_tags(form.get("tag_refs"), DocumentTagRef(tag_type=scope_type, tag_id=scope_id))
    if tags is None:
        return admin_rejected(key, "document.tags_were_not_valid", "audit-document-library-upload-rejected", 422)
    for tag in tags:
        scope = api_composition(request).document_intake.requested_scope_projection(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token(request) or "",
            scope_type=tag.tag_type,
            scope_id=tag.tag_id,
            record_upload_denial=True,
        )
        if response := _scope_error(tag.tag_type, scope.exists, scope.active):
            return response
        if not scope.can_upload:
            return error(
                "access_denied",
                "document.upload_requires_uploader_or_admin_access_to_this_scope",
                403,
                audit_event_ref=(
                    scope.denial_audit_event.event_id
                    if scope.denial_audit_event is not None
                    else None
                ),
            )
    uploaded = form.get("file")
    if uploaded is None or not hasattr(uploaded, "read"):
        return admin_rejected(key, "document.file_upload_requires_a_file", "audit-document-library-upload-rejected", 422)
    filename = getattr(uploaded, "filename", None)
    title = _title(filename)
    if not title:
        return admin_rejected(key, "document.file_upload_requires_a_named_file", "audit-document-library-upload-rejected", 422)
    try:
        detected = inspect_document_upload(
            uploaded.file,
            filename=filename,
            client_mime=getattr(uploaded, "content_type", None),
        )
    except ArtifactStorageError as exc:
        return error(exc.error_code, exc.message_code, exc.status_code)
    except DocumentFormatError as exc:
        return error(exc.error_code, exc.message_code, 413 if exc.error_code == "artifact_too_large" else 422)
    document_id = str(form.get("document_id") or "").strip() or f"doc-{uuid4().hex[:12]}"
    _existing, item = _item(request, document_id)
    if item is not None:
        return admin_rejected(key, "document.identity_already_exists", "audit-document-library-upload-rejected", 409)
    allow_download = str(form.get("allow_member_download") or "").strip().lower() in {"1", "true", "yes", "on"}
    document = DocumentRecord(
        document_id=document_id,
        title=title,
        description=_normalize_description(str(form.get("description"))) if form.get("description") is not None else None,
        source_digest="sha256:pending",
        searchable_projection="",
        intake_status="queued",
        source_kind="file_upload",
        document_format=detected.document_format,
        content_type=detected.canonical_mime,
        source_filename=filename,
        uploader_actor_id=actor.actor_id,
        scope_type=scope_type,
        scope_id=scope_id,
        allow_member_download=allow_download,
        source_download_restricted=detected.source_download_restricted,
        uploaded_at=utc_now_iso(),
    )
    try:
        result = api_composition(request).document_uploads.upload(
            chunks=uploaded_chunks(uploaded.file),
            request_fingerprint=upload_request_fingerprint(
                {
                    "document_id": document_id,
                    "filename": filename,
                    "owner": [scope_type, scope_id],
                    "tags": [[tag.tag_type, tag.tag_id] for tag in tags],
                    "allow_member_download": allow_download,
                    "document_format": detected.document_format,
                },
                None,
            ),
            artifact_class="original_document",
            logical_identity=f"document:{document_id}:original_document:{key}",
            content_type=detected.canonical_mime,
            document=document,
            tag_refs=tags,
            authorization_bindings=tuple(
                (tag.tag_type, tag.tag_id)
                for tag in tags
                if (tag.tag_type, tag.tag_id) != (scope_type, scope_id)
            ),
            job_kind="ingest",
            idempotency_scope="document_library_upload",
            idempotency_key=key,
            created_by=actor.actor_id,
            audit_event_type="document_library_uploaded",
            audit_message_code="document.upload_is_accepted_for_asynchronous_processing",
            audit_metadata={
                "allow_member_download": allow_download,
                "document_format": detected.document_format,
                "canonical_mime": detected.canonical_mime,
            },
            presented_browser_session_token=session_token(request) or "",
            actor_type=actor.actor_type,
        )
    except DocumentUploadAccessDenied as exc:
        return error("access_denied", "document.upload_requires_uploader_or_admin_access_to_this_scope", 403, audit_event_ref=exc.audit_event.event_id)
    except DocumentUploadUnauthenticated:
        return error("unauthenticated", "document.please_sign_in_before_uploading_a_document", 401)
    except (DocumentUploadReplayConflict, ValueError):
        return error("artifact_upload_conflict", "document.the_upload_conflicts_with_an_existing_upload_identity", 409)
    except ArtifactStorageError as exc:
        return error(exc.error_code, exc.message_code, exc.status_code)
    if result.publication.job is not None:
        best_effort_dispatch()
    _projection_after, uploaded_item = _item(request, document_id)
    if uploaded_item is None:
        raise RuntimeError("accepted document upload is not projectable")
    job_id = (
        result.publication.job.job_id
        if result.publication.job is not None
        else None
    )
    return JSONResponse(
        status_code=202,
        content=DocumentLibraryMutationResult(
            request_id=key,
            status="accepted",
            target_ref=f"document:{document_id}",
            message_code="document.upload_is_accepted_for_asynchronous_processing",
            audit_event_ref=result.publication.audit.event_id,
            document=_summary(uploaded_item),
            artifact_id=result.artifact.artifact_id,
            job_id=job_id,
            status_url=(
                f"/api/v1/processing/jobs/{job_id}"
                if job_id is not None
                else None
            ),
        ).model_dump(),
    )


@router.patch("/api/v1/admin/document-library/{document_id}", response_model=DocumentLibraryMutationResult)
def update_document_library(document_id: str, payload: DocumentLibraryUpdateRequest, request: Request):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_updating_a_document", 401)
    _current, item = _item(request, document_id)
    if item is None:
        return admin_rejected(payload.idempotency_key, "document.was_not_found", "audit-document-library-update-rejected", 404)
    updated = replace(item.document)
    if payload.description is not None:
        updated.description = _normalize_description(payload.description)
    if payload.allow_member_download is not None:
        updated.allow_member_download = payload.allow_member_download
    success = _audit("document_library_updated", actor.actor_id, updated, "document.settings_are_updated", {"allow_member_download": updated.allow_member_download, "tag_refs": [{"tag_type": tag.tag_type, "tag_id": tag.tag_id} for tag in item.tags]})
    denial_code = "document.only_scope_admins_can_change_member_download" if payload.allow_member_download is not None else "document.update_requires_scope_admin_or_uploader_access"
    denial = _audit("document_update_denied", actor.actor_id, item.document, denial_code, {"reason": "missing_document_role"})
    try:
        _facade(request).patch_document(_lifecycle_input(request, expected=item.document, document=updated, item=item, success=success, denial=denial))
    except DocumentLifecycleDenied as exc:
        return _denied(exc, denial_code)
    except DocumentProcessingCurrentnessConflict:
        return admin_rejected(payload.idempotency_key, "document.was_not_found", "audit-document-library-update-rejected", 409)
    return DocumentLibraryMutationResult(request_id=payload.idempotency_key, status="applied", target_ref=f"document:{document_id}", message_code="document.settings_are_updated", audit_event_ref=success.event_id, document=_summary(item, updated))


def _processing_acceptance(request: Request, document: DocumentRecord, version_id: str, key: str, job_kind: str) -> DocumentLifecycleProcessingAcceptance:
    actor = current_user(request)
    assert actor is not None
    media_type = document.content_type or "application/octet-stream"
    snapshot = api_composition(request).document_processing.capture_processing_execution(
        media_type=media_type,
        document_id=document.document_id,
        document_version_id=version_id,
        job_kind=job_kind,
        created_by=actor.actor_id,
    )
    return DocumentLifecycleProcessingAcceptance(media_type, version_id, job_kind, "document_refresh_processing" if job_kind == "reprocess" else "document_restore_rebuild", key, actor.actor_id, snapshot)


@router.post("/api/v1/admin/document-library/{document_id}/refresh-searchable-content", response_model=None)
def refresh_searchable_content(document_id: str, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "auth.please_sign_in_before_updating_searchable_content", 401)
    _current, item = _item(request, document_id)
    if item is None:
        return admin_rejected(f"refresh-{document_id}", "document.was_not_found", "audit-document-refresh-rejected", 404)
    document = item.document
    if document.lifecycle_status == "disabled":
        return admin_rejected(f"refresh-{document_id}", "document.disabled_documents_cannot_update_searchable_content", "audit-document-refresh-rejected", 409)
    if not document.original_artifact_id:
        return admin_rejected(f"refresh-{document_id}", "artifact.source_document_is_unavailable_this_file_cannot_be_processed", "audit-document-refresh-rejected", 422)
    current_job = api_composition(request).document_processing.get_job(document.processing_job_id) if document.processing_job_id else None
    if current_job is not None and current_job.status in {"failed", "cancelled"}:
        return error("processing_job_requires_explicit_retry", "processing.use_the_job_retry_control_for_failed_or_stopped_work", 409)
    if current_job is not None and current_job.status in {"queued", "running", "retry_wait"}:
        job = current_job
        key = idempotency_key or f"resume-{job.job_id}"
        processing = None
    else:
        version_id = api_composition(request).document_intake.processing_document_version_id(document_id)
        if version_id is None:
            return admin_rejected(f"refresh-{document_id}", "artifact.source_document_is_unavailable_this_file_cannot_be_processed", "audit-document-refresh-rejected", 422)
        key = idempotency_key or f"refresh-{document_id}:{version_id}:{uuid4().hex}"
        processing = _processing_acceptance(request, document, version_id, key, "reprocess")
        job = None
    success = _audit("document_searchable_content_queued", actor.actor_id, document, "document.searchable_content_update_is_queued", {})
    denial = _audit("document_refresh_denied", actor.actor_id, document, "document.updating_searchable_content_requires_scope_admin_or_uploader_access", {"reason": "missing_document_role"})
    desired_document = replace(
        document,
        # A fresh generation must not inherit the prior generation's quality
        # warnings. A resume/retry keeps its existing state and checkpoints.
        warning_codes=[] if processing is not None else document.warning_codes,
    )
    try:
        accepted = _facade(request).refresh_or_reindex(_lifecycle_input(request, expected=document, document=desired_document, item=item, success=success, denial=denial, processing=processing)) if processing is not None else (_facade(request).patch_document(_lifecycle_input(request, expected=document, document=desired_document, item=item, success=success, denial=denial)) or job)
    except DocumentLifecycleDenied as exc:
        return _denied(exc, "document.updating_searchable_content_requires_scope_admin_or_uploader_access")
    assert accepted is not None
    best_effort_dispatch()
    return JSONResponse(status_code=202, content={"request_id": key, "status": "accepted", "job_id": accepted.job_id, "status_url": f"/api/v1/processing/jobs/{accepted.job_id}", "target_ref": f"document:{document_id}", "message_code": "document.searchable_content_update_is_queued", "audit_event_ref": success.event_id})


@router.post("/api/v1/admin/document-library/{document_id}/disable", response_model=AdminActionResult)
def disable_document(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_changing_document_status", 401)
    _current, item = _item(request, document_id)
    if item is None:
        return admin_rejected(f"lifecycle-{document_id}", "document.was_not_found", "audit-document-lifecycle-rejected", 404)
    if item.document.lifecycle_status == "disabled":
        return AdminActionResult(request_id=f"lifecycle-{document_id}", status="applied", target_ref=f"document:{document_id}", message_code="document.is_disabled", audit_event_ref="document-already-disabled")
    updated = replace(item.document, lifecycle_status="disabled", resource_lifecycle_epoch=item.document.resource_lifecycle_epoch + 1, disabled_at=utc_now_iso())
    success = _audit("document_disabled", actor.actor_id, updated, "document.is_disabled", {"lifecycle_status": "disabled", "resource_lifecycle_epoch": updated.resource_lifecycle_epoch, "active_processing_generation": updated.active_processing_generation})
    denial = _audit("document_lifecycle_denied", actor.actor_id, item.document, "document.changing_document_status_requires_scope_admin_access", {"reason": "missing_scope_admin"})
    try:
        _facade(request).disable_document(_lifecycle_input(request, expected=item.document, document=updated, item=item, success=success, denial=denial))
    except DocumentLifecycleDenied as exc:
        return _denied(exc, "document.changing_document_status_requires_scope_admin_access")
    return AdminActionResult(request_id=f"lifecycle-{document_id}", status="applied", target_ref=f"document:{document_id}", message_code="document.is_disabled", audit_event_ref=success.event_id)


@router.post("/api/v1/admin/document-library/{document_id}/restore", response_model=AdminActionResult)
def restore_document(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_changing_document_status", 401)
    _current, item = _item(request, document_id)
    if item is None:
        return admin_rejected(f"lifecycle-{document_id}", "document.was_not_found", "audit-document-lifecycle-rejected", 404)
    if item.document.lifecycle_status not in {"disabled", "restoring"}:
        return admin_rejected(f"lifecycle-{document_id}", "document.only_a_disabled_document_can_be_restored", "audit-document-lifecycle-rejected", 409)
    restoring = replace(item.document, lifecycle_status="restoring")
    denial = _audit("document_lifecycle_denied", actor.actor_id, item.document, "document.changing_document_status_requires_scope_admin_access", {"reason": "missing_scope_admin"})
    if item.document.lifecycle_status == "disabled":
        begin = _audit("document_restore_started", actor.actor_id, restoring, "document.settings_are_updated", {"resource_lifecycle_epoch": restoring.resource_lifecycle_epoch})
        try:
            _facade(request).begin_restore(_lifecycle_input(request, expected=item.document, document=restoring, item=item, success=begin, denial=denial))
        except DocumentLifecycleDenied as exc:
            return _denied(exc, "document.changing_document_status_requires_scope_admin_access")
    try:
        proof = api_composition(request).document_restore_proofs.verify(restoring)
    except ArtifactStorageError as exc:
        failure = _audit(
            "document_restore_failed",
            actor.actor_id,
            restoring,
            "document.restore_verification_failed",
            {"failure_code": exc.error_code},
        )
        try:
            _facade(request).begin_restore(
                _lifecycle_input(
                    request,
                    expected=restoring,
                    document=replace(restoring),
                    item=item,
                    success=failure,
                    denial=denial,
                )
            )
        except DocumentLifecycleDenied as denied:
            return _denied(
                denied,
                "document.changing_document_status_requires_scope_admin_access",
            )
        except DocumentProcessingCurrentnessConflict:
            return admin_rejected(
                f"lifecycle-{document_id}",
                "document.was_not_found",
                "audit-document-lifecycle-rejected",
                409,
            )
        return error(
            exc.error_code,
            exc.message_code,
            exc.status_code,
            audit_event_ref=failure.event_id,
        )
    except ValueError:
        return admin_rejected(
            f"lifecycle-{document_id}",
            "document.restore_verification_failed",
            "audit-document-lifecycle-rejected",
            409,
        )
    if proof.reusable_processing_generation:
        active = replace(restoring, lifecycle_status="active", resource_lifecycle_epoch=restoring.resource_lifecycle_epoch + 1, restored_at=utc_now_iso())
        success = _audit("document_restored", actor.actor_id, active, "document.is_restored", {"lifecycle_status": "active", "resource_lifecycle_epoch": active.resource_lifecycle_epoch, "active_processing_generation": active.active_processing_generation})
        _facade(request).finish_restore(_lifecycle_input(request, expected=restoring, document=active, item=item, success=success, denial=denial, restore_verification=proof))
        return AdminActionResult(request_id=f"lifecycle-{document_id}", status="applied", target_ref=f"document:{document_id}", message_code="document.is_restored", audit_event_ref=success.event_id)
    version_id = api_composition(request).document_intake.processing_document_version_id(document_id)
    if version_id is None:
        return admin_rejected(f"lifecycle-{document_id}", "artifact.source_document_is_unavailable_this_file_cannot_be_processed", "audit-document-lifecycle-rejected", 422)
    key = f"{document_id}:{restoring.resource_lifecycle_epoch}:{restoring.active_processing_generation + 1}"
    processing = _processing_acceptance(request, restoring, version_id, key, "reprocess")
    processing = replace(processing, idempotency_scope="document_restore_rebuild")
    rebuilding = replace(restoring, intake_status="processing")
    success = _audit("document_restore_rebuild_queued", actor.actor_id, rebuilding, "document.restore_is_queued_for_evidence_rebuild", {})
    job = _facade(request).finish_restore(_lifecycle_input(request, expected=restoring, document=rebuilding, item=item, success=success, denial=denial, processing=processing, restore_verification=proof))
    assert job is not None
    best_effort_dispatch()
    return JSONResponse(status_code=202, content={"request_id": job.job_id, "status": "accepted", "job_id": job.job_id, "status_url": f"/api/v1/processing/jobs/{job.job_id}", "target_ref": f"document:{document_id}", "message_code": "document.restore_is_queued_for_evidence_rebuild", "audit_event_ref": success.event_id})


@router.get("/api/v1/admin/document-library/{document_id}/events", response_model=AuditEventList)
def document_events(document_id: str, request: Request):
    actor = current_user(request)
    if actor is None:
        return error("unauthenticated", "document.please_sign_in_before_opening_document_records", 401)
    _current, item = _item(request, document_id, events=True)
    if item is None:
        return admin_rejected(f"events-{document_id}", "document.was_not_found", "audit-document-events-rejected", 404)
    if not item.can_view_logs:
        denial = _audit("document_events_denied", actor.actor_id, item.document, "document.records_require_scope_admin_or_uploader_access", {"reason": "missing_log_permission"})
        success = _audit("document_events_opened", actor.actor_id, item.document, "document.records_require_scope_admin_or_uploader_access", {})
        try:
            _facade(request).patch_document(_lifecycle_input(request, expected=item.document, document=replace(item.document), item=item, success=success, denial=denial))
        except DocumentLifecycleDenied as exc:
            return _denied(exc, "document.records_require_scope_admin_or_uploader_access")
    return AuditEventList(events=[audit_event_status(event) for event in item.events])
