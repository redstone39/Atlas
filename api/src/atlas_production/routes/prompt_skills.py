from __future__ import annotations
from typing import Annotated

from fastapi import APIRouter, File, Header, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from atlas_production.modules.prompt_skills.public import (
    MAX_PROMPT_SKILL_SOURCE_BYTES,
    PromptSkillAdmin,
    PromptSkillCategory,
    PromptSkillError,
    PromptSkillLifecycleRequest,
    PromptSkillListV1,
    PromptSkillMutationOutcomeV1,
    PromptSkillRevisionV1,
)
from atlas_production.modules.skill_designer.public import (
    ApproveSkillCandidateV1,
    SkillCandidateError,
    RejectSkillCandidateV1,
    SkillCandidateAdmin,
    SkillCandidateDetailV1,
    SkillCandidateListV1,
    SkillCandidateMutationOutcomeV1,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


def _service(request: Request) -> PromptSkillAdmin:
    return api_composition(request).prompt_skills



def _candidate_service(request: Request) -> SkillCandidateAdmin:
    service = api_composition(request).skill_candidates
    if service is None:
        raise SkillCandidateError(
            "skill_candidate_unavailable",
            "prompt_skills.candidate_is_unavailable",
            503,
        )
    return service

def _failure(exc: PromptSkillError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


def _candidate_failure(exc: SkillCandidateError) -> JSONResponse:
    return error(exc.error_code, exc.message_code, exc.status_code)


def _required_key(header_value: str | None) -> str:
    if header_value is None or not header_value or len(header_value) > 200:
        raise PromptSkillError(
            "invalid_prompt_skill_request",
            "prompt_skills.idempotency_key_is_required",
            422,
        )
    return header_value


def _required_if_match(header_value: str | None) -> int:
    if header_value is None:
        raise PromptSkillError(
            "invalid_prompt_skill_request",
            "prompt_skills.if_match_is_required",
            422,
        )
    try:
        value = int(header_value)
    except ValueError as exc:
        raise PromptSkillError(
            "invalid_prompt_skill_request",
            "prompt_skills.if_match_must_be_numeric",
            422,
        ) from exc
    if value < 0:
        raise PromptSkillError(
            "invalid_prompt_skill_request",
            "prompt_skills.if_match_must_be_numeric",
            422,
        )
    return value


def _lifecycle_request(
    body: PromptSkillLifecycleRequest,
    *,
    idempotency_header: str | None,
    if_match: str | None,
) -> PromptSkillLifecycleRequest:
    key = _required_key(idempotency_header)
    expected = _required_if_match(if_match)
    if key != body.idempotency_key or expected != body.expected_control_revision:
        raise PromptSkillError(
            "invalid_prompt_skill_request",
            "prompt_skills.headers_and_body_must_match",
            422,
        )
    return body


def _require_admin_before_upload(request: Request):
    actor = current_user(request)
    if actor is None or not actor.active or actor.system_role != "admin":
        raise PromptSkillError(
            "access_denied",
            "permission.admin_permission_is_required",
            403,
        )
    return actor


async def _bounded_skill_file(file: UploadFile) -> bytes:
    content = await file.read(MAX_PROMPT_SKILL_SOURCE_BYTES + 1)
    if len(content) > MAX_PROMPT_SKILL_SOURCE_BYTES:
        raise PromptSkillError(
            "invalid_prompt_skill",
            "prompt_skills.skill_file_size_is_invalid",
            422,
        )
    return content


@router.get(
    "/api/v1/admin/prompt-skills",
    response_model=PromptSkillListV1,
)
def list_prompt_skills(
    request: Request,
    category: PromptSkillCategory = Query(default="planner"),
):
    try:
        return _service(request).list_skills(current_user(request), category)
    except PromptSkillError as exc:
        return _failure(exc)


@router.get(
    "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}",
    response_model=PromptSkillRevisionV1,
)
def get_prompt_skill_revision(
    category: PromptSkillCategory,
    name: str,
    revision: int,
    request: Request,
):
    try:
        return _service(request).get_revision(
            current_user(request), category, name, revision
        )
    except PromptSkillError as exc:
        return _failure(exc)


@router.post(
    "/api/v1/admin/prompt-skills/{category}/{name}/revisions",
    response_model=PromptSkillMutationOutcomeV1,
    status_code=201,
)
async def upload_prompt_skill_revision(
    category: PromptSkillCategory,
    name: str,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    try:
        actor = _require_admin_before_upload(request)
        expected_head_revision = _required_if_match(if_match)
        required_idempotency_key = _required_key(idempotency_key)
        content = await _bounded_skill_file(file)
        outcome = _service(request).upload(
            actor,
            category=category,
            path_name=name,
            filename=file.filename or "",
            content=content,
            expected_head_revision=expected_head_revision,
            idempotency_key=required_idempotency_key,
        )
        return JSONResponse(
            status_code=201,
            content=outcome.model_dump(mode="json"),
        )
    except PromptSkillError as exc:
        return _failure(exc)


def _mutate(
    *,
    enable: bool,
    category: PromptSkillCategory,
    name: str,
    revision: int,
    payload: PromptSkillLifecycleRequest,
    request: Request,
    idempotency_key: str | None,
    if_match: str | None,
):
    try:
        body = _lifecycle_request(
            payload,
            idempotency_header=idempotency_key,
            if_match=if_match,
        )
        method = _service(request).enable if enable else _service(request).disable
        return method(
            current_user(request),
            category=category,
            name=name,
            revision=revision,
            request=body,
        )
    except PromptSkillError as exc:
        return _failure(exc)


@router.post(
    "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}/enable",
    response_model=PromptSkillMutationOutcomeV1,
)
def enable_prompt_skill_revision(
    category: PromptSkillCategory,
    name: str,
    revision: int,
    payload: PromptSkillLifecycleRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    return _mutate(
        enable=True,
        category=category,
        name=name,
        revision=revision,
        payload=payload,
        request=request,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post(
    "/api/v1/admin/prompt-skills/{category}/{name}/revisions/{revision}/disable",
    response_model=PromptSkillMutationOutcomeV1,
)
def disable_prompt_skill_revision(
    category: PromptSkillCategory,
    name: str,
    revision: int,
    payload: PromptSkillLifecycleRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    return _mutate(
        enable=False,
        category=category,
        name=name,
        revision=revision,
        payload=payload,
        request=request,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


def _candidate_command(
    body: ApproveSkillCandidateV1 | RejectSkillCandidateV1,
    *,
    idempotency_header: str | None,
    if_match: str | None,
) -> ApproveSkillCandidateV1 | RejectSkillCandidateV1:
    key = _required_key(idempotency_header)
    expected = _required_if_match(if_match)
    if key != body.idempotency_key or expected != body.expected_draft_revision:
        raise SkillCandidateError(
            "invalid_skill_candidate_request",
            "prompt_skills.candidate_headers_and_body_must_match",
            422,
        )
    return body


@router.get(
    "/api/v1/admin/prompt-skill-candidates",
    response_model=SkillCandidateListV1,
)
def list_prompt_skill_candidates(
    request: Request,
    category: PromptSkillCategory | None = Query(default=None),
):
    try:
        actor = _require_admin_before_upload(request)
        return _candidate_service(request).list_candidates(actor.actor_id, category)
    except PromptSkillError as exc:
        return _failure(exc)
    except SkillCandidateError as exc:
        return _candidate_failure(exc)


@router.get(
    "/api/v1/admin/prompt-skill-candidates/{candidate_ref}",
    response_model=SkillCandidateDetailV1,
)
def get_prompt_skill_candidate(
    candidate_ref: Annotated[str, Path(min_length=1, max_length=300)],
    request: Request,
):
    try:
        actor = _require_admin_before_upload(request)
        return _candidate_service(request).get_candidate(
            actor.actor_id, candidate_ref
        )
    except PromptSkillError as exc:
        return _failure(exc)
    except SkillCandidateError as exc:
        return _candidate_failure(exc)


def _mutate_candidate(
    *,
    approve: bool,
    candidate_ref: str,
    payload: ApproveSkillCandidateV1 | RejectSkillCandidateV1,
    request: Request,
    idempotency_key: str | None,
    if_match: str | None,
):
    try:
        actor = _require_admin_before_upload(request)
        body = _candidate_command(
            payload,
            idempotency_header=idempotency_key,
            if_match=if_match,
        )
        service = _candidate_service(request)
        if approve:
            assert isinstance(body, ApproveSkillCandidateV1)
            return service.approve_candidate(actor.actor_id, candidate_ref, body)
        assert isinstance(body, RejectSkillCandidateV1)
        return service.reject_candidate(actor.actor_id, candidate_ref, body)
    except PromptSkillError as exc:
        return _failure(exc)
    except SkillCandidateError as exc:
        return _candidate_failure(exc)


@router.post(
    "/api/v1/admin/prompt-skill-candidates/{candidate_ref}/approve",
    response_model=SkillCandidateMutationOutcomeV1,
)
def approve_prompt_skill_candidate(
    candidate_ref: Annotated[str, Path(min_length=1, max_length=300)],
    payload: ApproveSkillCandidateV1,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    return _mutate_candidate(
        approve=True,
        candidate_ref=candidate_ref,
        payload=payload,
        request=request,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post(
    "/api/v1/admin/prompt-skill-candidates/{candidate_ref}/reject",
    response_model=SkillCandidateMutationOutcomeV1,
)
def reject_prompt_skill_candidate(
    candidate_ref: Annotated[str, Path(min_length=1, max_length=300)],
    payload: RejectSkillCandidateV1,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    return _mutate_candidate(
        approve=False,
        candidate_ref=candidate_ref,
        payload=payload,
        request=request,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


__all__ = ["router"]
