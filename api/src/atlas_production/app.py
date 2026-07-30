from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .infrastructure.composition import ApiComposition, build_api_composition
from .modules.artifact_storage.public import ArtifactStorageError
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
from .shared.correlation import server_correlation_context
from .shared.http import error, safe_validation_errors


logger = logging.getLogger(__name__)


def create_app(composition: ApiComposition | None = None) -> FastAPI:
    selected = composition or build_api_composition()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            try:
                shutdown = getattr(selected.turn_execution_carrier, "shutdown", None)
                if callable(shutdown):
                    shutdown()
            finally:
                stop = getattr(selected.turn_lease_failure_sweeper, "stop", None)
                if callable(stop):
                    stop()

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


__all__ = ["create_app"]
