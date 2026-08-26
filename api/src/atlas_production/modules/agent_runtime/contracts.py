from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .api_models import AcceptedResearchSnapshotV1, ResearchPacketV1





AgentResearchAuthorizationStatus = Literal[
    "invalid_token",
    "invalid_agent",
    "revoked",
    "denied",
    "allowed",
]


@dataclass(frozen=True, slots=True)
class AgentResearchAuthorizationV1:
    status: AgentResearchAuthorizationStatus
    actor_id: str | None = None
    token_id: str | None = None
    token_fingerprint: str | None = None
    snapshot: AcceptedResearchSnapshotV1 | None = None

    def __post_init__(self) -> None:
        allowed = self.status == "allowed"
        if allowed != (self.actor_id is not None and self.snapshot is not None):
            raise ValueError("allowed research authorization requires actor and snapshot")
        if not allowed and self.snapshot is not None:
            raise ValueError("denied research authorization cannot expose a snapshot")


@dataclass(frozen=True, slots=True)
class AgentResearchAcceptanceV1:
    authorization: AgentResearchAuthorizationV1
    record: AgentResearchRecordV1 | None = None
    replayed: bool = False

    def __post_init__(self) -> None:
        allowed = self.authorization.status == "allowed"
        if allowed != (self.record is not None):
            raise ValueError("allowed research acceptance requires a durable record")
        if allowed:
            assert self.record is not None
            if (
                self.record.actor_id != self.authorization.actor_id
                or self.record.snapshot != self.authorization.snapshot
            ):
                raise ValueError(
                    "research acceptance record must match its authorization preimage"
                )
        if not allowed and self.replayed:
            raise ValueError("denied research acceptance cannot be replayed")


@dataclass(frozen=True, slots=True)
class CreateAcceptedAgentResearchV1:
    research_id: str
    execution_id: str
    actor_id: str
    idempotency_key: str
    request_digest: str
    question_ref: str
    question: str
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"]
    snapshot: AcceptedResearchSnapshotV1


@dataclass(frozen=True, slots=True)
class AgentResearchRecordV1:
    research_id: str
    execution_id: str
    actor_id: str
    idempotency_key: str
    request_digest: str
    question_ref: str
    question: str
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"]
    snapshot: AcceptedResearchSnapshotV1
    status: Literal["accepted", "completed"]
    packet: ResearchPacketV1 | None
    packet_ref: str | None
    packet_digest: str | None
    accepted_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        terminal = self.status == "completed"
        packet_fields = (self.packet, self.packet_ref, self.packet_digest, self.completed_at)
        if terminal != all(value is not None for value in packet_fields):
            raise ValueError("completed research requires one immutable terminal packet")
        if not terminal and any(value is not None for value in packet_fields):
            raise ValueError("accepted research cannot carry terminal packet fields")


@dataclass(frozen=True, slots=True)
class AgentResearchAuditSummaryV1:
    research_id: str
    execution_id: str
    actor_id: str
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"]
    status: Literal["accepted", "completed"]
    accepted_at: datetime
    completed_at: datetime | None



@dataclass(frozen=True, slots=True)
class StartAgentResearchOutcomeV1:
    status: Literal["accepted", "replayed", "denied"]
    error_code: str | None = None
    message_code: str | None = None
    audit_event_ref: str | None = None
    record: AgentResearchRecordV1 | None = None

    def __post_init__(self) -> None:
        if self.status in {"accepted", "replayed"}:
            if self.record is None or any(
                value is not None
                for value in (self.error_code, self.message_code)
            ):
                raise ValueError("accepted research outcome requires only a record")
        elif self.record is not None or self.error_code is None or self.message_code is None:
            raise ValueError("denied research outcome requires safe error codes only")


class AgentResearchError(RuntimeError):
    """Base class for typed Agent Runtime owner failures."""


class AgentResearchReplayConflict(AgentResearchError):
    pass


class AgentResearchTerminalConflict(AgentResearchError):
    pass
