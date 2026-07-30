"""Non-conversation Workspace support routes.

Conversation execution and citations use their strict owner contracts.  This
module retains only the document-library tag projection used by management UI.
"""

from fastapi import APIRouter, Request

from atlas_production.modules.document_intake.public import (
    DocumentTagSummary,
    WorkspaceTagScopeResult,
)
from atlas_production.rbac import effective_document_scope
from atlas_production.shared.http import error
from atlas_production.transport.dependencies import api_composition, current_user


router = APIRouter()


@router.get("/api/v1/workspace/tag-scope", response_model=WorkspaceTagScopeResult)
def workspace_tag_scope(request: Request):
    actor = current_user(request)
    if not actor:
        return error("unauthenticated", "auth.please_sign_in_before_asking_a_question", 401)
    projection = api_composition(request).document_intake.document_library_projection(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        presented_browser_session_token=request.cookies.get("atlas_session", ""),
    )
    scope = effective_document_scope(
        projection.authorization_state,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="workspace_query",
    )
    tags: list[DocumentTagSummary] = []
    for tag_type, tag_id in sorted(scope):
        label = tag_id
        for item in projection.items:
            labels = {
                (kind, scope_id): current_label
                for kind, scope_id, current_label in item.scope_labels
            }
            label = labels.get((tag_type, tag_id), label)
        tags.append(DocumentTagSummary(tag_type=tag_type, tag_id=tag_id, label=label))
    return WorkspaceTagScopeResult(tags=tags)
