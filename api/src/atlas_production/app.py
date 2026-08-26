from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .infrastructure.composition import ApiComposition, build_api_composition
from .infrastructure.mcp_research import AtlasMcpTransport, McpExactPathMiddleware
from .modules.artifact_storage.public import ArtifactStorageError
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
    knowledge_library,
    internal_notes_collaboration,
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
from .shared.correlation import server_correlation_context
from .shared.http import error, safe_validation_errors


logger = logging.getLogger(__name__)


def create_app(composition: ApiComposition | None = None) -> FastAPI:
    selected = composition or build_api_composition()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as stack:
            def register_shutdown(owner, method_name: str) -> None:
                callback = getattr(owner, method_name, None)
                if callable(callback):
                    stack.callback(callback)

            register_shutdown(selected.turn_lease_failure_sweeper, "stop")
            register_shutdown(selected.turn_resource_release_reconciler, "stop")
            register_shutdown(selected.turn_experience_reconciler, "stop")
            register_shutdown(selected.learner_reconciler, "stop")
            register_shutdown(selected.conversation_review_reconciler, "stop")
            register_shutdown(selected.turn_execution_carrier, "shutdown")
            register_shutdown(selected.skill_candidate_pipeline_reconciler, "stop")
            mcp_transport = getattr(selected, "mcp_transport", None)
            if isinstance(mcp_transport, AtlasMcpTransport):
                await stack.enter_async_context(
                    mcp_transport.server.session_manager.run()
                )
            yield

    app = FastAPI(title="Atlas Production API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def server_request_context(request: Request, call_next):
        with server_correlation_context() as correlation_id:
            request.state.correlation_id = correlation_id
            try:
                response = await call_next(request)
            except Exception as exc:
                frames = tuple(
                    f"{frame.name}:{frame.lineno}"
                    for frame in traceback.extract_tb(exc.__traceback__)[-8:]
                )
                logger.error(
                    "unhandled request error correlation_id=%s exception_type=%s frames=%s",
                    correlation_id,
                    type(exc).__name__,
                    frames,
                )
                response = error("internal_error", "common.rejected", 500)
            response.headers["X-Atlas-Correlation-ID"] = correlation_id
            return response

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": safe_validation_errors(exc.errors())},
        )

    @app.exception_handler(ArtifactStorageError)
    async def safe_artifact_storage_error(_request, exc: ArtifactStorageError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message_code": exc.message_code,
                "message_params": exc.message_params,
            },
        )

    app.state.api_composition = selected
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5174", "http://localhost:5174",
            "http://127.0.0.1:5184", "http://localhost:5184",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
    mcp_transport = getattr(selected, "mcp_transport", None)
    if isinstance(mcp_transport, AtlasMcpTransport):
        app.add_middleware(McpExactPathMiddleware)
        app.mount("/mcp", mcp_transport.asgi_app)
    return app


__all__ = ["create_app"]
