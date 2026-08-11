from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AgentQueryAuthorizationStatus = Literal[
    "invalid_token",
    "invalid_agent",
    "revoked",
    "denied",
    "allowed",
]


@dataclass(frozen=True, slots=True)
class AgentQueryAuthorizationV1:
    status: AgentQueryAuthorizationStatus
    actor_id: str | None = None
    token_id: str | None = None
    token_fingerprint: str | None = None
    access_decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentQueryOutcomeV1:
    error_code: str
    message_code: str
    status_code: int
    audit_event_ref: str
