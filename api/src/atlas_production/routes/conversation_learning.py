from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.conversation_review.public import (
    ConversationLearningSettingsError,
    ConversationLearningSettingsService,
    ConversationLearningSettingsUpdateRequestV1,
    ConversationLearningSettingsV1,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request) -> ConversationLearningSettingsService:
    return api_composition(request).conversation_learning_settings


def _error(exc: ConversationLearningSettingsError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


@router.get(
    "/api/v1/admin/conversation-learning/settings",
    response_model=ConversationLearningSettingsV1,
)
def get_conversation_learning_settings(request: Request):
    try:
        return _service(request).get(current_user(request))
    except ConversationLearningSettingsError as exc:
        return _error(exc)


@router.patch(
    "/api/v1/admin/conversation-learning/settings",
    response_model=ConversationLearningSettingsV1,
)
def update_conversation_learning_settings(
    payload: ConversationLearningSettingsUpdateRequestV1,
    request: Request,
):
    try:
        return _service(request).update(current_user(request), payload)
    except ConversationLearningSettingsError as exc:
        return _error(exc)


__all__ = ["router"]
