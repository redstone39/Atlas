from typing import Literal

from pydantic import BaseModel, Field


class ReadinessState(BaseModel):
    ready: bool
    health: Literal["ok", "degraded"]
    setup_blockers: list[str]
    evidence_ready_projects: list[str]
    message_code: str
    message_params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
