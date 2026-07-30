from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atlas_production.modules.turn_execution.public import (
    AnswerBehaviorAdmin,
    AnswerBehaviorError,
    AnswerBehaviorStatus,
    AnswerBehaviorUpdateRequest,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request) -> AnswerBehaviorAdmin:
    return api_composition(request).answer_behavior


def _error(exc: AnswerBehaviorError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


@router.get(
    "/api/v1/admin/answer-behavior",
    response_model=AnswerBehaviorStatus,
)
def get_answer_behavior(request: Request):
    try:
        return _service(request).get(current_user(request))
    except AnswerBehaviorError as exc:
        return _error(exc)


@router.put(
    "/api/v1/admin/answer-behavior",
    response_model=AnswerBehaviorStatus,
)
def update_answer_behavior(
    payload: AnswerBehaviorUpdateRequest,
    request: Request,
):
    try:
        return _service(request).update(current_user(request), payload)
    except AnswerBehaviorError as exc:
        return _error(exc)


__all__ = ["router"]
