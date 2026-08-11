from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from atlas_production.infrastructure.postgres_artifact_journeys import (
    ProtectedOriginalUnauthenticated,
    ProtectedOriginalUnavailable,
)
from atlas_production.infrastructure.postgres_owner.artifact import (
    ArtifactCommandConflict,
    ArtifactProtectedOpenDenied,
    ArtifactProtectedOpenUnauthenticated,
)
from atlas_production.modules.document_intake.public import (
    DocumentRecord,
    DocumentTagRecord,
    KnowledgeDocumentListResult,
    KnowledgeDocumentSummary,
    KnowledgeScopeSummary,
)
from atlas_production.shared.http import error, session_token
from atlas_production.transport.dependencies import api_composition, current_user
from ..rbac import effective_document_scope


router = APIRouter()


def _has_active_knowledge_generation(document: DocumentRecord) -> bool:
    return (
        document.lifecycle_status == "active"
        and document.active_processing_generation > 0
        and bool(document.active_index_generation_id)
    )


@router.get("/api/v1/library/documents", response_model=KnowledgeDocumentListResult)
def list_knowledge_documents(request: Request) -> KnowledgeDocumentListResult | JSONResponse:
    actor = current_user(request)
    if not actor:
        return error(
            "unauthenticated",
            "auth.please_sign_in_before_opening_the_knowledge_library",
            401,
        )
    intake = api_composition(request).document_intake
    projection = intake.document_library_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=session_token(request) or "",
    )
    authorized_scope = effective_document_scope(
        projection.authorization_state,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="workspace_query",
    )
    documents: list[KnowledgeDocumentSummary] = []
    for item in sorted(
        projection.items,
        key=lambda current: (
            current.document.title.casefold(),
            current.document.document_id,
        ),
    ):
        document = item.document
        if not _has_active_knowledge_generation(document):
            continue
        authorized_tags = [
            tag
            for tag in item.tags
            if (tag.tag_type, tag.tag_id) in authorized_scope
        ]
        if authorized_tags:
            documents.append(
                _knowledge_document_summary(item, document, authorized_tags)
            )
    return KnowledgeDocumentListResult(documents=documents)


def _knowledge_document_summary(
    item,
    document: DocumentRecord,
    authorized_tags: list[DocumentTagRecord],
) -> KnowledgeDocumentSummary:
    labels = {
        (scope_type, scope_id): label
        for scope_type, scope_id, label in item.scope_labels
    }
    return KnowledgeDocumentSummary(
        document_id=document.document_id,
        title=document.title,
        description=document.description,
        document_format=document.document_format,
        authorized_scopes=[
            KnowledgeScopeSummary(
                scope_type=tag.tag_type,
                scope_id=tag.tag_id,
                scope_label=labels.get((tag.tag_type, tag.tag_id), tag.tag_id),
            )
            for tag in authorized_tags
        ],
        source_filename=document.source_filename,
        source_byte_size=document.source_byte_size,
        uploaded_at=document.uploaded_at,
        download_available=item.download_available,
    )


def _document_content(document_id: str, request: Request, *, head: bool = False):
    composition = api_composition(request)
    method = "HEAD" if head else "GET"
    try:
        journey = composition.protected_originals.build(
            document_id=document_id,
            presented_browser_session_token=session_token(request) or "",
            method=method,
            if_match=request.headers.get("if-match"),
            if_none_match=request.headers.get("if-none-match"),
            if_range=request.headers.get("if-range"),
            range_header=request.headers.get("range"),
        )
        result = composition.artifact_storage.open_original(
            journey.request,
            method=journey.method,
            filename=journey.filename,
            if_match=journey.if_match,
            if_none_match=journey.if_none_match,
            if_range=journey.if_range,
            range_header=journey.range_header,
        )
    except (ProtectedOriginalUnauthenticated, ArtifactProtectedOpenUnauthenticated):
        return error(
            "unauthenticated",
            "document.please_sign_in_before_reading_original_content",
            401,
        )
    except ArtifactProtectedOpenDenied:
        return error("access_denied", "document.was_not_found", 403)
    except ProtectedOriginalUnavailable as exc:
        return error(
            "not_found",
            "document.was_not_found",
            404,
            audit_event_ref=exc.audit_event_ref,
        )
    except (ArtifactCommandConflict, ValueError):
        return error("not_found", "document.was_not_found", 404)

    if head or result.status_code in {304, 412, 416}:
        return Response(status_code=result.status_code, headers=result.headers)
    return StreamingResponse(
        result.body,
        status_code=result.status_code,
        media_type=result.headers.get("Content-Type"),
        headers=result.headers,
    )


@router.get("/api/v1/library/documents/{document_id}/content", response_model=None)
def get_document_content(document_id: str, request: Request):
    return _document_content(document_id, request)


@router.head("/api/v1/library/documents/{document_id}/content", response_model=None)
def head_document_content(document_id: str, request: Request):
    return _document_content(document_id, request, head=True)
