from __future__ import annotations

from typing import Protocol

from atlas_production.modules.prompt_skills.public import PromptSkillCategory, PromptSkillError

from .public import (
    ApproveSkillCandidateV1,
    SkillCandidateError,
    SkillCandidateStoreError,
    RejectSkillCandidateV1,
    SkillCandidateDetailV1,
    SkillCandidateListV1,
    SkillCandidateMutationOutcomeV1,
)


class SkillCandidateStore(Protocol):
    def list_candidate_summaries(
        self, category: PromptSkillCategory | None = None
    ) -> SkillCandidateListV1: ...

    def read_candidate(self, candidate_ref: str) -> SkillCandidateDetailV1 | None: ...

    def approve_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: ApproveSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1: ...

    def reject_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: RejectSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1: ...


def _translate(exc: SkillCandidateStoreError) -> SkillCandidateError:
    code = str(exc)
    if code == "skill_candidate_not_found":
        return SkillCandidateError(code, "prompt_skills.candidate_was_not_found", 404)
    if code in {
        "skill_candidate_precondition_failed",
        "skill_candidate_idempotency_conflict",
    }:
        return SkillCandidateError(
            code, "prompt_skills.candidate_precondition_changed", 412
        )
    return SkillCandidateError(
        "skill_candidate_unavailable",
        "prompt_skills.candidate_is_unavailable",
        503,
    )


class SkillCandidateAdminService:
    def __init__(self, owner: SkillCandidateStore) -> None:
        self._owner = owner

    def list_candidates(
        self, actor_id: str, category: PromptSkillCategory | None = None
    ) -> SkillCandidateListV1:
        candidates = self._owner.list_candidate_summaries(category)
        return SkillCandidateListV1(
            items=[item for item in candidates.items if item.status == "draft"]
        )

    def get_candidate(
        self, actor_id: str, candidate_ref: str
    ) -> SkillCandidateDetailV1:
        candidate = self._owner.read_candidate(candidate_ref)
        if candidate is None:
            raise SkillCandidateError(
                "skill_candidate_not_found",
                "prompt_skills.candidate_was_not_found",
                404,
            )
        return candidate

    def approve_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: ApproveSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1:
        try:
            return self._owner.approve_candidate(actor_id, candidate_ref, command)
        except SkillCandidateStoreError as exc:
            raise _translate(exc) from exc
        except PromptSkillError as exc:
            raise SkillCandidateError(
                exc.error_code, exc.message_code, exc.status_code
            ) from exc

    def reject_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: RejectSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1:
        try:
            return self._owner.reject_candidate(actor_id, candidate_ref, command)
        except SkillCandidateStoreError as exc:
            raise _translate(exc) from exc


__all__ = [
    "SkillCandidateAdminService",
    "SkillCandidateError",
    "SkillCandidateStore",
]
