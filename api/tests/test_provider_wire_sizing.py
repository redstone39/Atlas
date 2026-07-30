from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from atlas_production.modules.model_routing.public import (
    ProviderConversationRequest,
    ProviderImageContentPart,
    ProviderProtocolError,
    ProviderSystemMessage,
    ProviderTextContentPart,
    ProviderUserMessage,
    estimate_provider_wire,
    require_provider_wire_within_limits,
)
from atlas_production.providers import build_native_json_schema


def test_tool_reserve_that_overfills_context_is_rejected() -> None:
    policy = SimpleNamespace(
        tokenizer_profile="cl100k_base",
        max_output_tokens_per_invocation=100,
        max_input_tokens_per_invocation=800,
        context_window_tokens=1000,
    )
    request = ProviderConversationRequest(
        messages=[
            ProviderSystemMessage(
                content="system " + ("bounded provider request " * 80)
            )
        ],
        tools=[],
        tool_choice="none",
        parallel_tool_calls=False,
        max_output_tokens=100,
    )
    schema = build_native_json_schema(
        "wire_sizing_test",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    estimate = estimate_provider_wire(
        policy=policy,
        request=request,
        response_schema=schema,
        tool_reserve_tokens=700,
    )
    assert estimate.input_tokens <= policy.max_input_tokens_per_invocation
    assert estimate.context_reserve_tokens <= policy.context_window_tokens
    assert estimate.projected_tokens > policy.context_window_tokens

    with pytest.raises(ProviderProtocolError) as error:
        require_provider_wire_within_limits(
            policy=policy,
            request=request,
            response_schema=schema,
            tool_reserve_tokens=700,
        )

    assert error.value.safe_code == "context_limit_exceeded"


def test_visual_wire_sizes_image_tokens_instead_of_base64_text() -> None:
    policy = SimpleNamespace(
        tokenizer_profile="cl100k_base",
        max_output_tokens_per_invocation=2000,
        max_input_tokens_per_invocation=12000,
        context_window_tokens=16000,
    )
    content = b"normalized-page-png" * 100_000
    image = ProviderImageContentPart(
        content=content,
        digest=hashlib.sha256(content).hexdigest(),
        width=1190,
        height=1684,
    )
    request = ProviderConversationRequest(
        messages=[
            ProviderUserMessage(
                content=(
                    ProviderTextContentPart(text="Inspect this page."),
                    image,
                )
            )
        ],
        tools=[],
        tool_choice="none",
        parallel_tool_calls=False,
        max_output_tokens=2000,
    )
    schema = build_native_json_schema(
        "visual_wire_sizing_test",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    estimate = require_provider_wire_within_limits(
        policy=policy,
        request=request,
        response_schema=schema,
    )

    # Original dimensions conservatively occupy 3 x 4 high-detail tiles:
    # 85 base + 170 * 12 = 2,125 image tokens, plus bounded text/schema wire.
    assert 2125 < estimate.input_tokens < 3000


def test_larger_visual_dimensions_increase_risk_estimate() -> None:
    policy = SimpleNamespace(
        tokenizer_profile="cl100k_base",
        max_output_tokens_per_invocation=2000,
        max_input_tokens_per_invocation=20000,
        context_window_tokens=24000,
    )
    content = b"normalized-page-png"
    digest = hashlib.sha256(content).hexdigest()
    schema = build_native_json_schema(
        "visual_dimension_sizing_test",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    def estimate(width: int, height: int) -> int:
        request = ProviderConversationRequest(
            messages=[
                ProviderUserMessage(
                    content=(
                        ProviderTextContentPart(text="Inspect this page."),
                        ProviderImageContentPart(
                            content=content,
                            digest=digest,
                            width=width,
                            height=height,
                        ),
                    )
                )
            ],
            tools=[],
            tool_choice="none",
            parallel_tool_calls=False,
            max_output_tokens=2000,
        )
        return estimate_provider_wire(
            policy=policy,
            request=request,
            response_schema=schema,
        ).input_tokens

    assert estimate(1024, 1024) < estimate(1190, 1684)
