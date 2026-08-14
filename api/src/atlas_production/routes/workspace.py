"""Non-conversation Workspace support routes.

Conversation execution and citations use their strict owner contracts. This
module retains only the current-authorized Project and Team scope projection.
"""

from fastapi import APIRouter, Request

from atlas_production.modules.document_intake.public import (
    DocumentTagSummary,
    WorkspaceTagScopeResult,
)
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


@router.get("/api/v1/workspace/tag-scope", response_model=WorkspaceTagScopeResult)
def workspace_tag_scope(request: Request):
    actor = current_user(request)
    if not actor:
        return error("unauthenticated", "auth.please_sign_in_before_asking_a_question", 401)
    scope_labels = (
        api_composition(request)
        .workspace_scope_authority.effective_document_scope_labels(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="workspace_query",
        )
    )
    return WorkspaceTagScopeResult(
        tags=[
            DocumentTagSummary(tag_type=tag_type, tag_id=tag_id, label=label)
            for tag_type, tag_id, label in scope_labels
        ]
    )
