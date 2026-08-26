from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_agent_adapter import (
    _attach_research_packet_in_session,
    _lock_research_packet_in_session,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
)
from atlas_production.modules.agent_runtime.public import (
    AgentResearchRecordV1,
    ResearchPacketV1,
)
from atlas_production.modules.turn_runtime.public import (
    CommitTerminalV1,
    ExecutionSnapshotV1,
)


SessionFactory = Callable[[], Session]


class ResearchTerminalPublicationConflict(RuntimeError):
    """The two existing research success representations are inconsistent."""


@dataclass(frozen=True, slots=True)
class ResearchTerminalPublicationV1:
    research: AgentResearchRecordV1
    execution: ExecutionSnapshotV1


@dataclass(frozen=True, slots=True)
class PostgresResearchTerminalPublisher:
    """Atomically publish the existing Agent and Turn research terminal pair."""

    session_factory: SessionFactory
    runtime: PostgresTurnRuntimeOwner

    def publish(
        self,
        *,
        command: CommitTerminalV1,
        packet_ref: str,
        packet: ResearchPacketV1,
    ) -> ResearchTerminalPublicationV1:
        if packet.execution_id != command.execution_id:
            raise ResearchTerminalPublicationConflict(
                "research packet and terminal command execution differ"
            )
        with self.session_factory() as session, session.begin():
            turn_state = self.runtime._lock_research_terminal_in_session(
                session,
                command,
                research_id=packet.research_id,
            )
            research = _lock_research_packet_in_session(
                session,
                research_id=packet.research_id,
                execution_id=packet.execution_id,
                packet_ref=packet_ref,
                packet=packet,
            )
            if turn_state == "completed":
                if research.status != "completed":
                    raise ResearchTerminalPublicationConflict(
                        "Turn terminal is completed without its Agent packet"
                    )
                execution = self.runtime._replay_research_terminal_in_session(
                    session,
                    command,
                    packet_ref=packet_ref,
                    packet_digest=packet.packet_digest,
                )
                return ResearchTerminalPublicationV1(
                    research=research,
                    execution=execution,
                )
            if research.status != "accepted":
                raise ResearchTerminalPublicationConflict(
                    "Agent packet is completed without its Turn terminal"
                )
            research = _attach_research_packet_in_session(
                session,
                research_id=packet.research_id,
                execution_id=packet.execution_id,
                packet_ref=packet_ref,
                packet=packet,
            )
            execution = self.runtime._commit_research_terminal_in_session(
                session,
                command,
                packet_ref=packet_ref,
                packet_digest=packet.packet_digest,
            )
            return ResearchTerminalPublicationV1(
                research=research,
                execution=execution,
            )
