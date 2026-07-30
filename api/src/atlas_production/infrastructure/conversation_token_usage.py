"""Read-only observed Provider usage for lock-free conversation admission."""

from __future__ import annotations

from sqlalchemy import select

from atlas_production.infrastructure.persistence.model_routing import (
    AtlasModelInvocationRow,
)
from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnExecutionRow,
)


def _usage_value(usage: dict, *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


class PostgresConversationTokenUsageReader:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def observed_tokens(self, conversation_id: str) -> int:
        if not conversation_id:
            raise ValueError("conversation_id must be non-empty")
        with self._session_factory() as session:
            usages = session.scalars(
                select(AtlasModelInvocationRow.token_usage)
                .join(
                    AtlasTurnExecutionRow,
                    AtlasTurnExecutionRow.execution_id
                    == AtlasModelInvocationRow.subject_ref,
                )
                .where(
                    AtlasTurnExecutionRow.conversation_id == conversation_id,
                    AtlasModelInvocationRow.status == "completed",
                )
            ).all()
        return sum(
            _usage_value(usage, "input_tokens", "prompt_tokens")
            + _usage_value(usage, "output_tokens", "completion_tokens")
            for usage in usages
        )


__all__ = ["PostgresConversationTokenUsageReader"]
