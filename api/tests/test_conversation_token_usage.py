from __future__ import annotations

from atlas_production.infrastructure.conversation_token_usage import (
    PostgresConversationTokenUsageReader,
    _usage_value,
)


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Session:
    def __init__(self, values):
        self.values = values
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalars(self, statement):
        self.statement = statement
        return _ScalarResult(self.values)


def test_usage_value_prefers_normalized_provider_names_without_double_counting() -> None:
    usage = {
        "input_tokens": 11,
        "prompt_tokens": 99,
        "output_tokens": 7,
        "completion_tokens": 88,
    }

    assert _usage_value(usage, "input_tokens", "prompt_tokens") == 11
    assert _usage_value(usage, "output_tokens", "completion_tokens") == 7
    assert _usage_value({"input_tokens": True}, "input_tokens") == 0


def test_conversation_usage_sums_only_observed_normalized_usage() -> None:
    session = _Session(
        [
            {"input_tokens": 10, "output_tokens": 3},
            {"prompt_tokens": 5, "completion_tokens": 2},
            {},
        ]
    )
    reader = PostgresConversationTokenUsageReader(lambda: session)

    assert reader.observed_tokens("conversation-1") == 20
    assert session.statement is not None
