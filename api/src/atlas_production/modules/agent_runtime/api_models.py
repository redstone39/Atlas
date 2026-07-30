from typing import Literal

from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    project_id: str
    query_text: str
    purpose: str
    conversation_id: str | None = None
    evidence_budget: int | None = Field(default=None, ge=1, le=20)
    idempotency_key: str | None = None
