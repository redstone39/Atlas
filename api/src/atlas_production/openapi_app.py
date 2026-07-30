from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routes import (
    agent_access,
    agent_runtime,
    answer_behavior,
    auth,
    conversations,
    document_library,
    invitations,
    knowledge_library,
    model_routes,
    ops,
    processing_jobs,
    processing_plugins,
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
        answer_behavior.router,
        rbac_admin.router,
        processing_plugins.router,
        processing_jobs.router,
        projects.router,
        document_library.router,
        knowledge_library.router,
        model_routes.router,
        conversations.router,
        workspace.router,
        agent_runtime.router,
    ):
        app.include_router(router)

    return app


__all__ = ["create_openapi_app"]
