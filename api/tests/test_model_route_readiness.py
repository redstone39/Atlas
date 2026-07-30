from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from atlas_production.infrastructure.postgres_model_routing_adapter import (
    PostgresModelRoutingAdapter,
)
from atlas_production.modules.model_routing.provider_contracts import (
    ProviderAssistantMessage,
    ProviderCompleted,
    ProviderImageContentPart,
    ProviderInvocationError,
    ProviderTextContentPart,
)
from atlas_production.modules.model_routing.records import (
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ProviderConnectionRecord,
)
from atlas_production.providers import ProviderError, ROUTE_READINESS_SCHEMA
from tests import model_route_runtime_policy


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch):
    monkeypatch.setattr(
        "atlas_production.modules.model_routing.records.tiktoken.get_encoding",
        lambda _name: object(),
    )


def _connection() -> ProviderConnectionRecord:
    return ProviderConnectionRecord(
        connection_id="connection-1",
        display_name="Azure",
        provider_type="azure_openai",
        endpoint_url="https://provider.invalid",
        status="verified",
        enabled=True,
        revision=1,
    )


def _route(*, supports_vision: bool) -> ModelRouteRecord:
    return ModelRouteRecord(
        route_id="route-1",
        display_name="Model",
        provider_type="azure_openai",
        connection_id="connection-1",
        model_name="model-1",
        supports_vision=supports_vision,
        runtime_policy=ModelRouteRuntimePolicy(
            **model_route_runtime_policy(), revision=1
        ),
        readiness_schema_name=ROUTE_READINESS_SCHEMA.name,
        readiness_schema_digest=ROUTE_READINESS_SCHEMA.digest,
        revision=1,
    )


def _completed(status: str) -> ProviderCompleted:
    return ProviderCompleted(
        provider_request_id="request-1",
        model_ref="model-1",
        finish_reason="stop",
        usage={"input_tokens": 1, "output_tokens": 1},
        output={"status": status},
        assistant_message=ProviderAssistantMessage(content='{"status":"ready"}'),
    )


def test_vision_route_readiness_requires_text_and_local_image_strict_schema_probes() -> None:
    calls: list[dict[str, object]] = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return _completed("ready")

    adapter = PostgresModelRoutingAdapter(lambda: None, lambda *_args: Provider())

    result = adapter.validate_route(_connection(), "secret", _route(supports_vision=True))

    assert result.output == {"status": "ready"}
    assert len(calls) == 2
    assert all(call["response_schema"] == ROUTE_READINESS_SCHEMA for call in calls)
    assert isinstance(calls[0]["request"].messages[-1].content, str)  # type: ignore[union-attr]
    visual_content = calls[1]["request"].messages[-1].content  # type: ignore[union-attr]
    assert isinstance(visual_content, tuple)
    assert isinstance(visual_content[0], ProviderTextContentPart)
    assert isinstance(visual_content[1], ProviderImageContentPart)
    assert visual_content[1].content.startswith(b"\x89PNG\r\n\x1a\n")
    assert visual_content[1].digest == hashlib.sha256(
        visual_content[1].content
    ).hexdigest()
    assert (visual_content[1].width, visual_content[1].height) == (8, 8)
    with Image.open(BytesIO(visual_content[1].content)) as image:
        assert image.mode == "RGB"
        assert image.size == (8, 8)


def test_text_only_route_readiness_does_not_send_an_image_probe() -> None:
    calls: list[dict[str, object]] = []
    provider = SimpleNamespace(
        complete=lambda **kwargs: calls.append(kwargs)
        or _completed("ready")
    )
    adapter = PostgresModelRoutingAdapter(lambda: None, lambda *_args: provider)

    adapter.validate_route(_connection(), "secret", _route(supports_vision=False))

    assert len(calls) == 1


def test_vision_route_readiness_fails_when_image_probe_is_not_schema_valid() -> None:
    outcomes = [
        _completed("ready"),
        _completed("not-ready"),
    ]
    provider = SimpleNamespace(complete=lambda **_kwargs: outcomes.pop(0))
    adapter = PostgresModelRoutingAdapter(lambda: None, lambda *_args: provider)

    with pytest.raises(ProviderError) as exc_info:
        adapter.validate_route(_connection(), "secret", _route(supports_vision=True))

    assert exc_info.value.code == "provider_response_invalid"
    assert outcomes == []


def test_vision_route_readiness_maps_image_provider_failure_to_safe_error() -> None:
    calls = 0

    def complete(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProviderInvocationError(safe_code="vision_probe_failed")
        return _completed("ready")

    adapter = PostgresModelRoutingAdapter(
        lambda: None, lambda *_args: SimpleNamespace(complete=complete)
    )

    with pytest.raises(ProviderError) as exc_info:
        adapter.validate_route(_connection(), "secret", _route(supports_vision=True))

    assert exc_info.value.code == "provider_connection_validation_failed"
    assert calls == 2
