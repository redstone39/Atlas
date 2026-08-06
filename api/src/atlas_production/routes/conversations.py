import json
from collections.abc import Iterable
from threading import Event
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from atlas_production.modules.turn_runtime.public import ExecutionState, RuntimeEventV1
from atlas_production.modules.citation_preview.public import (
    ProtectedCitationEvidenceV1,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.workspace_turn.public import (
    WorkspaceConversationArchiveV1,
    WorkspaceConversationCreateV1,
    WorkspaceConversationDetailV1,
    WorkspaceConversationListV1,
    WorkspaceExecutionStatusV1,
    WorkspaceTurnApplication,
    WorkspaceTurnCreateV1,
    WorkspaceTurnError,
    WorkspaceTurnRetryV1,
    TurnAcceptedV1,
)
from atlas_production.modules.conversation.public import ConversationArchiveResultV1
from atlas_production.modules.conversation_audit.public import (
    AdminConversationListResult,
    RuntimeTraceDetail,
)
from ..modules.conversation_audit.public import (
    ConversationAuditError,
    ConversationAuditService,
)
from atlas_production.transport.dependencies import (
    api_composition,
    current_user,
)
from atlas_production.shared.http import (
    error,
)

router = APIRouter()


class RuntimeEventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


def _workspace_turn_application(request: Request) -> WorkspaceTurnApplication:
    return api_composition(request).workspace_turn


def _conversation_audit_service(request: Request) -> ConversationAuditService:
    return api_composition(request).conversation_audit


def _workspace_turn_error(exc: WorkspaceTurnError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


def _conversation_audit_error(exc: ConversationAuditError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


def _accepted_page_media_types(accept: str | None) -> frozenset[str]:
    accepted: set[str] = set()
    for item in (accept or "").split(","):
        media_type, *parameters = (
            part.strip().casefold() for part in item.split(";")
        )
        if media_type not in {"application/pdf", "image/png"}:
            continue
        quality = 1.0
        try:
            quality = next(
                (
                    float(parameter.removeprefix("q=").strip())
                    for parameter in parameters
                    if parameter.startswith("q=")
                ),
                1.0,
            )
        except ValueError:
            quality = 0.0
        if quality <= 0:
            continue
        accepted.add(media_type)
    return frozenset(accepted)


@router.post("/api/v1/workspace/conversations", response_model=WorkspaceConversationDetailV1)
def create_workspace_conversation(payload: WorkspaceConversationCreateV1, request: Request):
    try:
        return _workspace_turn_application(request).create_conversation(current_user(request), payload)
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.get("/api/v1/workspace/conversations", response_model=WorkspaceConversationListV1)
def list_workspace_conversations(request: Request):
    try:
        return _workspace_turn_application(request).list_conversations(current_user(request))
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.post(
    "/api/v1/workspace/conversations/{conversation_id}/archive",
    response_model=ConversationArchiveResultV1,
)
def archive_workspace_conversation(
    conversation_id: str,
    payload: WorkspaceConversationArchiveV1,
    request: Request,
):
    try:
        return _workspace_turn_application(request).archive_conversation(
            current_user(request), conversation_id, payload
        )
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.get("/api/v1/workspace/conversations/{conversation_id}", response_model=WorkspaceConversationDetailV1)
def get_workspace_conversation(conversation_id: str, request: Request):
    try:
        return _workspace_turn_application(request).get_conversation(current_user(request), conversation_id)
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.get(
    "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/citations/{citation_ref}",
    response_model=ProtectedCitationEvidenceV1,
)
def read_workspace_citation(
    conversation_id: str,
    turn_id: str,
    citation_ref: str,
    request: Request,
):
    try:
        return _workspace_turn_application(request).read_citation(
            current_user(request), conversation_id, turn_id, citation_ref
        )
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.get(
    "/api/v1/workspace/conversations/{conversation_id}/turns/{turn_id}/declared-evidence/{protected_open_ref}",
    response_model=ProtectedDeclaredEvidenceV1,
    responses={
        200: {
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"}
                },
                "image/png": {
                    "schema": {"type": "string", "format": "binary"}
                },
            }
        }
    },
)
def read_workspace_declared_evidence(
    conversation_id: str,
    turn_id: str,
    protected_open_ref: str,
    request: Request,
    accept: Annotated[str | None, Header()] = None,
):
    try:
        result = _workspace_turn_application(request).read_declared_evidence(
            current_user(request),
            conversation_id,
            turn_id,
            protected_open_ref,
            accepted_page_media_types=_accepted_page_media_types(accept),
        )
        if isinstance(result, ProtectedDeclaredEvidencePageV1):
            return Response(
                content=result.content,
                media_type=result.media_type,
                headers={"Cache-Control": "private, no-store"},
            )
        return result
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.post(
    "/api/v1/workspace/conversations/{conversation_id}/turns",
    status_code=202,
    response_model=TurnAcceptedV1,
)
def create_workspace_turn(
    conversation_id: str,
    payload: WorkspaceTurnCreateV1,
    request: Request,
):
    try:
        result = _workspace_turn_application(request).accept_turn(
            current_user(request), conversation_id, payload
        )
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)
    return JSONResponse(status_code=202, content=result.model_dump(mode="json"))


@router.get("/api/v1/workspace/turn-executions/{execution_id}", response_model=WorkspaceExecutionStatusV1)
def get_workspace_turn_execution(execution_id: str, request: Request):
    try:
        return _workspace_turn_application(request).execution_status(current_user(request), execution_id)
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)


@router.get(
    "/api/v1/workspace/turn-executions/{execution_id}/events",
    response_class=RuntimeEventStreamResponse,
    response_model=None,
    responses={
        200: {
            "description": "Durable RuntimeEventV1 frames until terminal; reconnect with Last-Event-ID.",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "description": "SSE frames with id, event, and JSON data matching RuntimeEventV1.",
                    }
                }
            },
        }
    },
)
def reconnect_workspace_turn(
    execution_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    application = _workspace_turn_application(request)
    actor = current_user(request)
    try:
        events = application.execution_events(
            actor,
            execution_id,
            after_event_id=last_event_id,
        )
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)
    return StreamingResponse(
        live_runtime_events(
            application,
            actor,
            execution_id,
            events,
            initial_cursor=last_event_id,
        ),
        media_type="text/event-stream",
    )


@router.post(
    "/api/v1/workspace/turns/{turn_id}/retry",
    status_code=202,
    response_model=TurnAcceptedV1,
)
def retry_workspace_turn(turn_id: str, payload: WorkspaceTurnRetryV1, request: Request):
    try:
        result = _workspace_turn_application(request).retry_turn(
            current_user(request), turn_id, payload
        )
    except WorkspaceTurnError as exc:
        return _workspace_turn_error(exc)
    return JSONResponse(status_code=202, content=result.model_dump(mode="json"))


def replay_runtime_events(events: list[RuntimeEventV1]) -> Iterable[str]:
    for event in events:
        yield sse(
            event.event_type,
            {
                **event.model_dump(mode="json"),
            },
            event_id=event.event_id,
        )


def live_runtime_events(
    application: WorkspaceTurnApplication,
    actor: object,
    execution_id: str,
    initial_events: list[RuntimeEventV1],
    *,
    initial_cursor: str | None = None,
    poll_interval_seconds: float = 0.1,
) -> Iterable[str]:
    """Read only newly durable events until terminal; never resumes execution."""

    events = initial_events
    cursor = initial_cursor
    while True:
        for event in events:
            cursor = event.event_id
            yield from replay_runtime_events([event])
            if event.state in {
                ExecutionState.TERMINAL_COMPLETED,
                ExecutionState.TERMINAL_FAILED,
            }:
                return
        status = application.execution_status(actor, execution_id)
        if status.state in {ExecutionState.TERMINAL_COMPLETED, ExecutionState.TERMINAL_FAILED}:
            # Terminal outcome and terminal event are committed atomically. A
            # final cursor drain closes the fetch/status race. Empty means the
            # supplied cursor was itself the already-delivered terminal event.
            events = application.execution_events(
                actor,
                execution_id,
                after_event_id=cursor,
            )
            if not events:
                return
            continue
        Event().wait(poll_interval_seconds)
        events = application.execution_events(
            actor,
            execution_id,
            after_event_id=cursor,
        )


def sse(event: str, data: dict, *, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def chunk_text(value: str, size: int = 80) -> Iterable[str]:
    for start in range(0, len(value), size):
        yield value[start : start + size]


@router.get(
    "/api/v1/admin/conversations",
    response_model=AdminConversationListResult,
)
def list_admin_conversations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
):
    try:
        return _conversation_audit_service(request).list_admin(
            current_user(request),
            limit=limit,
            cursor=cursor,
        )
    except ConversationAuditError as exc:
        return _conversation_audit_error(exc)

@router.get(
    "/api/v1/admin/conversations/{conversation_id}",
    response_model=WorkspaceConversationDetailV1,
)
def get_admin_conversation(conversation_id: str, request: Request):
    try:
        return _conversation_audit_service(request).get_admin(
            current_user(request),
            conversation_id,
        )
    except ConversationAuditError as exc:
        return _conversation_audit_error(exc)


@router.get(
    "/api/v1/admin/conversations/{conversation_id}/turns/{turn_id}/runtime",
    response_model=RuntimeTraceDetail,
)
def get_admin_turn_runtime(conversation_id: str, turn_id: str, request: Request):
    try:
        return _conversation_audit_service(request).get_runtime(
            current_user(request),
            conversation_id,
            turn_id,
        )
    except ConversationAuditError as exc:
        return _conversation_audit_error(exc)


@router.get(
    "/api/v1/admin/conversations/{conversation_id}/turns/{turn_id}/declared-evidence/{protected_open_ref}",
    response_model=ProtectedDeclaredEvidenceV1,
)
def read_admin_declared_evidence(
    conversation_id: str,
    turn_id: str,
    protected_open_ref: str,
    request: Request,
):
    try:
        return _conversation_audit_service(request).read_declared_evidence(
            current_user(request),
            conversation_id,
            turn_id,
            protected_open_ref,
        )
    except ConversationAuditError as exc:
        return _conversation_audit_error(exc)
