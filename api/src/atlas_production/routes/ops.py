from fastapi import APIRouter, Request

from atlas_production.modules.ops.public import (
    ReadinessState,
)
from atlas_production.transport.dependencies import api_composition

router = APIRouter()

@router.get("/api/v1/ops/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/api/v1/ops/readiness", response_model=ReadinessState)
def readiness(request: Request) -> ReadinessState:
    return api_composition(request).ops_readiness.readiness()
