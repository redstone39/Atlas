from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from atlas_production.modules.notes.public import *
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user

router=APIRouter()
class ScopeList(BaseModel): model_config=ConfigDict(frozen=True); items:tuple[NoteScopeRefV1,...]
class NoteList(BaseModel): model_config=ConfigDict(frozen=True); items:tuple[NoteSummaryV1,...]
class CategoryList(BaseModel): model_config=ConfigDict(frozen=True); items:tuple[NoteCategoryV1,...]
class RevisionList(BaseModel): model_config=ConfigDict(frozen=True); items:tuple[NoteRevisionHistoryV1,...]
class SavepointList(BaseModel): model_config=ConfigDict(frozen=True); items:tuple[NoteSavepointSummaryV1,...]
def _service(request: Request):
    return api_composition(request).notes


def _actor(request):
    user=current_user(request)
    if user is None: raise NotesError("unauthenticated","Authentication is required",401)
    return user.actor_id
def _failure(exc): return error(exc.code,"common.rejected",exc.status_code)
def _bind(command,header_key,if_match):
    if header_key and command.idempotency_key!=header_key: raise NotesError("idempotency_payload_conflict","Header/body idempotency differs",409)
    if if_match:
        try: expected=int(if_match.strip().removeprefix("W/").strip('"'))
        except ValueError: raise NotesError("stale_metadata_revision","Invalid If-Match",409)
        for field in ("expected_metadata_revision","expected_revision_head","expected_settings_revision"):
            if hasattr(command,field) and getattr(command,field)!=expected: raise NotesError("stale_metadata_revision","If-Match/body revision differs",409)
    return command

def _call(callback):
    try:return callback()
    except NotesError as exc:return _failure(exc)

@router.get("/api/v1/notes/scopes",response_model=ScopeList)
def scopes(request:Request):return _call(lambda:ScopeList(items=_service(request).list_scopes(_actor(request))))
@router.get("/api/v1/notes",response_model=NoteList)
def notes_list(request:Request,scope_type:ScopeType,scope_id:str,lifecycle_status:LifecycleStatus="active",category_id:str|None=None):return _call(lambda:NoteList(items=_service(request).list_notes(_actor(request),scope_type=scope_type,scope_id=scope_id,lifecycle_status=lifecycle_status,category_id=category_id)))
@router.post("/api/v1/notes",response_model=NoteDetailV1,status_code=201)
def notes_create(payload:NoteCreateRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):return _call(lambda:_service(request).create_note(_actor(request),_bind(payload,idempotency_key,None)))
@router.get("/api/v1/notes/{note_id}",response_model=NoteDetailV1)
def note_get(note_id:str,request:Request):return _call(lambda:_service(request).get_note(_actor(request),note_id))
@router.post("/api/v1/notes/{note_id}/attachments",response_model=NoteAttachmentV1,status_code=201)
async def attachment_upload(
    note_id:str,
    request:Request,
    file:Annotated[UploadFile,File()],
    expected_collaboration_epoch:Annotated[int,Form(ge=1)],
    idempotency_key:Annotated[str,Form(min_length=1,max_length=200)],
    header_idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=200)],
):
    try:
        if idempotency_key != header_idempotency_key:
            raise NotesError("idempotency_payload_conflict","Header/form idempotency differs",409)
        content=await file.read(MAX_NOTE_BINARY_BYTES+1)
        if not content or len(content)>MAX_NOTE_BINARY_BYTES:
            raise NotesError("payload_oversize","Attachment is empty or too large",413)
        return _service(request).upload_attachment(
            _actor(request),note_id,
            expected_collaboration_epoch=expected_collaboration_epoch,
            idempotency_key=idempotency_key,
            filename=file.filename,
            claimed_mime_type=file.content_type,
            content=content,
        )
    except NotesError as exc:
        return _failure(exc)
    finally:
        await file.close()
@router.get(
    "/api/v1/notes/{note_id}/attachments/{attachment_ref}/content",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/webp": {},
            }
        }
    },
)
def attachment_content(note_id:str,attachment_ref:str,request:Request):
    try:
        opened=_service(request).open_attachment(_actor(request),note_id,attachment_ref)
        return Response(content=opened.content,media_type=opened.mime_type,headers={
            "Cache-Control":"private, no-store",
            "X-Content-Type-Options":"nosniff",
        })
    except NotesError as exc:return _failure(exc)
@router.patch("/api/v1/notes/{note_id}",response_model=NoteDetailV1)
def note_update(note_id:str,payload:NoteMetadataUpdateRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).update_note(_actor(request),note_id,_bind(payload,idempotency_key,if_match)))
@router.post("/api/v1/notes/{note_id}/trash",response_model=NoteDetailV1)
def note_trash(note_id:str,payload:NoteTrashRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).trash_note(_actor(request),note_id,_bind(payload,idempotency_key,if_match)))
@router.post("/api/v1/notes/{note_id}/restore",response_model=NoteDetailV1)
def note_restore(note_id:str,payload:NoteRestoreRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).restore_note(_actor(request),note_id,_bind(payload,idempotency_key,if_match)))
@router.get("/api/v1/note-categories",response_model=CategoryList)
def categories(request:Request,scope_type:ScopeType,scope_id:str,lifecycle_status:LifecycleStatus="active"):return _call(lambda:CategoryList(items=_service(request).list_categories(_actor(request),scope_type=scope_type,scope_id=scope_id,lifecycle_status=lifecycle_status)))
@router.post("/api/v1/note-categories",response_model=NoteCategoryV1,status_code=201)
def category_create(payload:NoteCategoryCreateRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):return _call(lambda:_service(request).create_category(_actor(request),_bind(payload,idempotency_key,None)))
@router.patch("/api/v1/note-categories/{category_id}",response_model=NoteCategoryV1)
def category_update(category_id:str,payload:NoteCategoryUpdateRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).update_category(_actor(request),category_id,_bind(payload,idempotency_key,if_match)))
@router.post("/api/v1/note-categories/{category_id}/trash",response_model=NoteCategoryV1)
def category_trash(category_id:str,payload:NoteCategoryTrashRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).trash_category(_actor(request),category_id,_bind(payload,idempotency_key,if_match)))
@router.post("/api/v1/note-categories/{category_id}/restore",response_model=NoteCategoryV1)
def category_restore(category_id:str,payload:NoteCategoryRestoreRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).restore_category(_actor(request),category_id,_bind(payload,idempotency_key,if_match)))
@router.post("/api/v1/notes/{note_id}/collaboration-ticket",response_model=CollaborationTicketV1)
def ticket(note_id:str,request:Request):return _call(lambda:_service(request).collaboration_ticket(_actor(request),note_id))
@router.get("/api/v1/notes/{note_id}/revisions",response_model=RevisionList)
def revisions(note_id:str,request:Request,after_sequence:int|None=Query(None,ge=1),limit:int=Query(100,ge=1,le=500)):return _call(lambda:RevisionList(items=_service(request).list_revisions(_actor(request),note_id,after_sequence=after_sequence,limit=limit)))
@router.get("/api/v1/notes/{note_id}/savepoints",response_model=SavepointList)
def savepoints(note_id:str,request:Request):return _call(lambda:SavepointList(items=_service(request).list_savepoints(_actor(request),note_id)))
@router.get("/api/v1/notes/{note_id}/savepoints/{savepoint_id}",response_model=NoteSavepointPreviewV1)
def savepoint(note_id:str,savepoint_id:str,request:Request):return _call(lambda:_service(request).get_savepoint(_actor(request),note_id,savepoint_id))
@router.post("/api/v1/notes/{note_id}/savepoints/{savepoint_id}/restore-body",response_model=BodyRestoreResultV1)
def restore_body(note_id:str,savepoint_id:str,payload:NoteBodyRestoreRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):
    def call():
        if payload.savepoint_id!=savepoint_id:raise NotesError("note_not_found","Path/body savepoint differs",404)
        return _service(request).restore_body(_actor(request),note_id,_bind(payload,idempotency_key,if_match))
    return _call(call)
@router.get("/api/v1/admin/notes/settings",response_model=NotesSettingsV1)
def settings(request:Request):return _call(lambda:_service(request).get_settings(_actor(request)))
@router.patch("/api/v1/admin/notes/settings",response_model=NotesSettingsV1)
def settings_update(payload:NotesSettingsUpdateRequestV1,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),if_match:str|None=Header(None,alias="If-Match")):return _call(lambda:_service(request).update_settings(_actor(request),_bind(payload,idempotency_key,if_match)))

__all__=["router"]
