"""Resolve one execution-fixed Answer behavior snapshot for model input."""

from __future__ import annotations

from atlas_production.modules.turn_execution.public import (
    AnswerBehaviorInputV1,
    AnswerBehaviorOwner,
)
from atlas_production.modules.turn_runtime.public import ExecutionSnapshotV1


def project_answer_behavior(
    owner: AnswerBehaviorOwner,
    snapshot: ExecutionSnapshotV1,
) -> AnswerBehaviorInputV1:
    revision = owner.read_exact(
        revision=snapshot.applied_guidance_revision,
        guidance_digest=snapshot.applied_guidance_digest,
    )
    return AnswerBehaviorInputV1(
        response_language=snapshot.response_language,
        applied_guidance_revision=revision.revision,
        applied_guidance_digest=revision.guidance_digest,
        custom_guidance=revision.custom_guidance,
    )


__all__ = ["project_answer_behavior"]
