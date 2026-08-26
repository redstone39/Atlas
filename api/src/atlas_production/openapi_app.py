from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routes import (
    agent_access,
    agent_research_audit,
    answer_behavior,
    auth,
    conversations,
    conversation_learning,
    document_library,
    directory_admin,
    invitations,
    internal_notes_collaboration,
    knowledge_library,
    model_routes,
    ops,
    notes,
    processing_jobs,
    processing_plugins,
    prompt_skills,
    projects,
    rbac_admin,
    workspace,
)


def create_openapi_app() -> FastAPI:
    """Build the Production schema surface without constructing runtime services."""

    app = FastAPI(title="Atlas Production API", version="0.1.0")

    @app.middleware("http")
    async def reject_schema_only_requests(
        _request: Request,
        _call_next,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "OpenAPI schema-only app cannot serve requests"},
        )

    for router in (
        ops.router,
        auth.router,
        invitations.router,
        agent_access.router,
        agent_research_audit.router,
        answer_behavior.router,
        conversation_learning.router,
        rbac_admin.router,
        directory_admin.router,
        processing_plugins.router,
        prompt_skills.router,
        processing_jobs.router,
        projects.router,
        notes.router,
        internal_notes_collaboration.router,
        document_library.router,
        knowledge_library.router,
        model_routes.router,
        conversations.router,
        workspace.router,
    ):
        app.include_router(router)

    return app


__all__ = ["create_openapi_app"]
