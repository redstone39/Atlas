from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from litellm import exceptions as litellm_exceptions

from atlas_production.modules.model_routing.provider_contracts import (
    ProviderAuthenticationError,
    ProviderCompleted,
    ProviderConfigurationError,
    ProviderConversationRequest,
    ProviderFunctionTool,
    ProviderIncomplete,
    ProviderOutputDecodeError,
    ProviderOutputSchemaError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRefused,
    ProviderRequestRejectedError,
    ProviderSystemMessage,
    ProviderTimeoutError,
    ProviderToolCall,
    ProviderTransportError,
    ProviderUserMessage,
)
from atlas_production.modules.model_routing.records import (
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ProviderConnectionRecord,
)
from atlas_production.providers import (
    LiteLLMProvider,
    ProviderError,
    build_native_json_schema,
)
from tests import model_route_runtime_policy


SCHEMA = build_native_json_schema(
    "answer_v1",
    {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
)
TOOL = ProviderFunctionTool(
    name="lookup",
    description="Look up one value.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    strict=True,
)


@dataclass
class ModelResponse:
    payload: Any

    def model_dump(self):
        return deepcopy(self.payload)


def _connection(provider_type: str) -> ProviderConnectionRecord:
    endpoint = {
        "openai_compatible": "https://provider.example/custom/v1",
        "azure_openai": "https://example.openai.azure.com",
        "anthropic": "https://api.anthropic.com",
    }[provider_type]
    return ProviderConnectionRecord(
        connection_id=f"connection-{provider_type}",
        display_name=provider_type,
        provider_type=provider_type,
        endpoint_url=endpoint,
        api_version="2024-10-21" if provider_type == "azure_openai" else None,
        status="verified",
        enabled=True,
    )


def _route(provider_type: str) -> ModelRouteRecord:
    return ModelRouteRecord(
        route_id=f"route-{provider_type}",
        display_name=provider_type,
        provider_type=provider_type,
        model_name="upstream-model",
        connection_id=f"connection-{provider_type}",
        runtime_policy=ModelRouteRuntimePolicy(
            **model_route_runtime_policy(provider_invocation_timeout_seconds=47),
            revision=3,
        ),
        status="test_passed",
        enabled=True,
    )


def _request(*, tools: bool = False, timeout_seconds: float | None = None):
    return ProviderConversationRequest(
        messages=[
            ProviderSystemMessage(content="Return structured output."),
            ProviderUserMessage(content="Answer."),
        ],
        tools=[TOOL] if tools else [],
        tool_choice="auto" if tools else "none",
        parallel_tool_calls=False,
        max_output_tokens=321,
        timeout_seconds=timeout_seconds,
    )


def _completed_payload(**overrides):
    payload = {
        "id": "request-1",
        "model": "resolved-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"answer":"ok"}',
                    "refusal": None,
                    "tool_calls": None,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("provider_type", "expected_model", "token_key"),
    [
        ("openai_compatible", "openai/upstream-model", "max_completion_tokens"),
        ("azure_openai", "azure/upstream-model", "max_completion_tokens"),
        ("anthropic", "anthropic/upstream-model", "max_tokens"),
    ],
)
def test_three_profiles_send_exact_per_call_kwargs(
    monkeypatch,
    provider_type: str,
    expected_model: str,
    token_key: str,
) -> None:
    calls: list[dict[str, Any]] = []
    completion = lambda **kwargs: calls.append(kwargs) or ModelResponse(  # noqa: E731
        _completed_payload()
    )
    monkeypatch.setattr(
        "atlas_production.providers.litellm.supports_response_schema",
        lambda **kwargs: kwargs
        == {"model": "anthropic/upstream-model", "custom_llm_provider": "anthropic"},
    )
    for name in ("OPENAI_API_KEY", "AZURE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, f"ambient-{name}")
    prior_litellm_key = getattr(__import__("litellm"), "api_key", None)
    provider = LiteLLMProvider(_connection(provider_type), "stored-secret", completion)

    outcome = provider.complete(
        route=_route(provider_type),
        request=_request(timeout_seconds=12.5),
        response_schema=SCHEMA,
    )

    assert isinstance(outcome, ProviderCompleted)
    assert len(calls) == 1
    expected = {
        "model": expected_model,
        "messages": [
            {"role": "system", "content": "Return structured output."},
            {"role": "user", "content": "Answer."},
        ],
        "tools": [],
        "tool_choice": "none",
        "parallel_tool_calls": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "answer_v1",
                "strict": True,
                "schema": SCHEMA.schema,
            },
        },
        "temperature": 0,
        "timeout": 12.5,
        "num_retries": 0,
        "stream": False,
        "api_key": "stored-secret",
        "api_base": _connection(provider_type).endpoint_url,
        token_key: 321,
    }
    if provider_type == "azure_openai":
        expected["api_version"] = "2024-10-21"
    assert calls[0] == expected
    assert getattr(__import__("litellm"), "api_key", None) == prior_litellm_key
    assert all("ambient-" not in repr(value) for value in calls[0].values())


def test_route_timeout_is_used_when_request_has_no_override() -> None:
    calls = []
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **kwargs: calls.append(kwargs) or ModelResponse(_completed_payload()),
    )

    provider.complete(
        route=_route("openai_compatible"),
        request=_request(),
        response_schema=SCHEMA,
    )

    assert calls[0]["timeout"] == 47.0


def test_completed_usage_and_request_metadata_are_normalized() -> None:
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(_completed_payload()),
    )

    outcome = provider.complete(
        route=_route("openai_compatible"),
        request=_request(),
        response_schema=SCHEMA,
    )

    assert outcome == ProviderCompleted(
        provider_request_id="request-1",
        model_ref="resolved-model",
        finish_reason="stop",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
            "cached_input_tokens": 4,
        },
        output={"answer": "ok"},
        assistant_message=outcome.assistant_message,
    )
    assert outcome.assistant_message.content == '{"answer":"ok"}'


def test_anthropic_usage_projects_cache_read_and_drops_unknown_token_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "atlas_production.providers.litellm.supports_response_schema",
        lambda **_kwargs: True,
    )
    payload = _completed_payload()
    payload["usage"] = {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
        "cache_read_input_tokens": 4,
        "cache_creation_input_tokens": 2,
        "unexpected_tokens": 99,
    }
    provider = LiteLLMProvider(
        _connection("anthropic"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(payload),
    )

    outcome = provider.complete(
        route=_route("anthropic"),
        request=_request(),
        response_schema=SCHEMA,
    )

    assert outcome.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
        "cached_input_tokens": 4,
    }


def test_single_function_tool_call_is_locally_validated() -> None:
    payload = _completed_payload()
    payload["choices"][0] = {
        "finish_reason": "tool_calls",
        "message": {
            "content": None,
            "refusal": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"query":"atlas"}',
                    },
                }
            ],
        },
    }
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(payload),
    )

    outcome = provider.complete(
        route=_route("openai_compatible"),
        request=_request(tools=True),
        response_schema=SCHEMA,
    )

    assert isinstance(outcome, ProviderToolCall)
    assert outcome.call.name == "lookup"
    assert outcome.call.arguments == {"query": "atlas"}


def test_refusal_is_normalized_without_raw_reason() -> None:
    payload = _completed_payload()
    payload["choices"][0] = {
        "finish_reason": "stop",
        "message": {
            "content": None,
            "refusal": "raw provider policy explanation",
            "tool_calls": None,
        },
    }
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(payload),
    )

    outcome = provider.complete(
        route=_route("openai_compatible"),
        request=_request(),
        response_schema=SCHEMA,
    )

    assert isinstance(outcome, ProviderRefused)
    assert outcome.reason_code == "provider_refusal"
    assert "raw provider" not in repr(outcome)


@pytest.mark.parametrize(
    ("finish_reason", "expected_reason"),
    [("length", "max_output_tokens"), ("content_filter", "content_filter")],
)
def test_incomplete_terminal_reasons_are_normalized(
    finish_reason: str,
    expected_reason: str,
) -> None:
    payload = _completed_payload()
    payload["choices"][0] = {
        "finish_reason": finish_reason,
        "message": {"content": None, "refusal": None, "tool_calls": None},
    }
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(payload),
    )

    outcome = provider.complete(
        route=_route("openai_compatible"),
        request=_request(),
        response_schema=SCHEMA,
    )

    assert isinstance(outcome, ProviderIncomplete)
    assert outcome.reason == expected_reason


@pytest.mark.parametrize(
    ("mutate", "error_type"),
    [
        (lambda payload: payload.update(choices=[]), ProviderProtocolError),
        (
            lambda payload: payload["choices"].append(deepcopy(payload["choices"][0])),
            ProviderProtocolError,
        ),
        (
            lambda payload: payload["choices"][0]["message"].update(
                content="not-json"
            ),
            ProviderOutputDecodeError,
        ),
        (
            lambda payload: payload["choices"][0]["message"].update(
                content='{"wrong":"value"}'
            ),
            ProviderOutputSchemaError,
        ),
        (
            lambda payload: payload.update(
                usage={"prompt_tokens": -1, "total_tokens": 0}
            ),
            ProviderProtocolError,
        ),
        (
            lambda payload: payload.update(
                usage={"prompt_tokens": True, "total_tokens": 0}
            ),
            ProviderProtocolError,
        ),
        (
            lambda payload: payload["choices"][0]["message"].update(
                tool_calls=[{}, {}], content=None
            ),
            ProviderProtocolError,
        ),
        (
            lambda payload: payload["choices"][0]["message"].update(
                refusal="refused"
            ),
            ProviderProtocolError,
        ),
    ],
)
def test_malformed_response_envelopes_fail_closed(mutate, error_type) -> None:
    payload = _completed_payload()
    mutate(payload)
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: ModelResponse(payload),
    )

    with pytest.raises(error_type):
        provider.complete(
            route=_route("openai_compatible"),
            request=_request(),
            response_schema=SCHEMA,
        )


def _http_response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"x-request-id": "request-safe"},
        request=httpx.Request("POST", "https://provider.example"),
    )


def _litellm_errors():
    raw = "raw-body stored-secret header-secret"
    common = {"message": raw, "model": "model", "llm_provider": "provider"}
    return [
        (
            litellm_exceptions.AuthenticationError(
                **common, response=_http_response(401)
            ),
            ProviderAuthenticationError,
            "provider_authentication_error",
        ),
        (
            litellm_exceptions.PermissionDeniedError(
                **common, response=_http_response(403)
            ),
            ProviderAuthenticationError,
            "provider_authentication_error",
        ),
        (
            litellm_exceptions.RateLimitError(
                **common, response=_http_response(429)
            ),
            ProviderRateLimitError,
            "provider_rate_limit",
        ),
        (
            litellm_exceptions.Timeout(
                message=raw, model="model", llm_provider="provider"
            ),
            ProviderTimeoutError,
            "provider_timeout",
        ),
        (
            litellm_exceptions.BadRequestError(**common, response=_http_response(400)),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.UnprocessableEntityError(
                **common, response=_http_response(422)
            ),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.NotFoundError(**common, response=_http_response(404)),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.ContentPolicyViolationError(
                **common, response=_http_response(400)
            ),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.ContextWindowExceededError(
                **common, response=_http_response(400)
            ),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.RejectedRequestError(
                message=raw,
                model="model",
                llm_provider="provider",
                request_data={},
            ),
            ProviderRequestRejectedError,
            "provider_request_rejected",
        ),
        (
            litellm_exceptions.APIConnectionError(
                message=raw, llm_provider="provider", model="model"
            ),
            ProviderTransportError,
            "provider_transport_error",
        ),
        (
            litellm_exceptions.ServiceUnavailableError(
                **common, response=_http_response(503)
            ),
            ProviderTransportError,
            "provider_transport_error",
        ),
        (
            litellm_exceptions.BadGatewayError(
                **common, response=_http_response(502)
            ),
            ProviderTransportError,
            "provider_transport_error",
        ),
        (
            litellm_exceptions.InternalServerError(
                **common, response=_http_response(500)
            ),
            ProviderTransportError,
            "provider_transport_error",
        ),
        (
            litellm_exceptions.APIError(
                status_code=500,
                message=raw,
                llm_provider="provider",
                model="model",
            ),
            ProviderTransportError,
            "provider_transport_error",
        ),
    ]


@pytest.mark.parametrize(("source_error", "error_type", "safe_code"), _litellm_errors())
def test_litellm_errors_are_mapped_once_without_raw_leakage(
    source_error: Exception,
    error_type,
    safe_code: str,
) -> None:
    calls = 0

    def completion(**_kwargs):
        nonlocal calls
        calls += 1
        raise source_error

    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        completion,
    )

    with pytest.raises(error_type) as error:
        provider.complete(
            route=_route("openai_compatible"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert calls == 1
    assert error.value.safe_code == safe_code
    assert "raw-body" not in repr(error.value)
    assert "stored-secret" not in repr(error.value)
    assert "header-secret" not in repr(error.value)


def test_rate_limit_retry_after_header_is_preserved() -> None:
    response = httpx.Response(
        429,
        headers={"x-request-id": "request-safe", "retry-after": "2"},
        request=httpx.Request("POST", "https://provider.example"),
    )
    source_error = litellm_exceptions.RateLimitError(
        message="rate limited",
        model="model",
        llm_provider="provider",
        response=response,
    )
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: (_ for _ in ()).throw(source_error),
    )

    with pytest.raises(ProviderRateLimitError) as error:
        provider.complete(
            route=_route("openai_compatible"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert error.value.provider_request_id == "request-safe"
    assert error.value.provider_status == 429
    assert error.value.retry_after_ms == 2_000


def test_programming_errors_propagate_unchanged() -> None:
    source_error = RuntimeError("programming error")
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: (_ for _ in ()).throw(source_error),
    )

    with pytest.raises(RuntimeError) as error:
        provider.complete(
            route=_route("openai_compatible"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert error.value is source_error


def test_schema_support_programming_errors_propagate_unchanged(monkeypatch) -> None:
    source_error = RuntimeError("schema support programming error")
    monkeypatch.setattr(
        "atlas_production.providers.litellm.supports_response_schema",
        lambda **_kwargs: (_ for _ in ()).throw(source_error),
    )
    provider = LiteLLMProvider(
        _connection("anthropic"),
        "stored-secret",
        lambda **_kwargs: None,
    )

    with pytest.raises(RuntimeError) as error:
        provider.complete(
            route=_route("anthropic"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert error.value is source_error


def test_response_model_programming_errors_propagate_unchanged() -> None:
    source_error = RuntimeError("response programming error")

    class Response:
        def model_dump(self):
            raise source_error

    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: Response(),
    )

    with pytest.raises(RuntimeError) as error:
        provider.complete(
            route=_route("openai_compatible"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert error.value is source_error


def test_anthropic_schema_support_fails_before_completion(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "atlas_production.providers.litellm.supports_response_schema",
        lambda **_kwargs: False,
    )
    provider = LiteLLMProvider(
        _connection("anthropic"),
        "stored-secret",
        lambda **kwargs: calls.append(kwargs),
    )

    with pytest.raises(ProviderConfigurationError) as error:
        provider.complete(
            route=_route("anthropic"),
            request=_request(),
            response_schema=SCHEMA,
        )

    assert error.value.safe_code == "provider_schema_unsupported"
    assert calls == []


def test_discovery_is_openai_compatible_only_and_honors_process_environment(
    monkeypatch,
) -> None:
    opened = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "z-model"}, {"id": "A-model"}]}

    class Client:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, headers):
            assert url == "https://provider.example/custom/v1/models"
            assert headers == {"Authorization": "Bearer stored-secret"}
            return Response()

    monkeypatch.setattr("atlas_production.providers.httpx.Client", Client)
    provider = LiteLLMProvider(
        _connection("openai_compatible"),
        "stored-secret",
        lambda **_kwargs: pytest.fail("completion must not be called"),
    )

    assert provider.discover_models() == ["A-model", "z-model"]
    assert opened == [{}]
    for provider_type in ("azure_openai", "anthropic"):
        with pytest.raises(ProviderError) as error:
            LiteLLMProvider(
                _connection(provider_type),
                "stored-secret",
            ).discover_models()
        assert error.value.code == "provider_discovery_unavailable"
