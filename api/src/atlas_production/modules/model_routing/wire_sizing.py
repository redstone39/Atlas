from __future__ import annotations

from dataclasses import dataclass
import json
import math

import tiktoken

from atlas_production.providers import NativeJsonSchema

from .provider_contracts import (
    ProviderConversationRequest,
    ProviderImageContentPart,
    ProviderProtocolError,
    ProviderUserMessage,
)
from .records import ModelRouteRuntimePolicy


_IMAGE_BASE_TOKENS = 85
_IMAGE_TILE_TOKENS = 170
_IMAGE_TILE_SIZE = 512


@dataclass(frozen=True, slots=True)
class ProviderWireEstimate:
    input_tokens: int
    response_reserve_tokens: int
    tool_reserve_tokens: int

    @property
    def context_reserve_tokens(self) -> int:
        return self.input_tokens + self.response_reserve_tokens

    @property
    def projected_tokens(self) -> int:
        return self.context_reserve_tokens + self.tool_reserve_tokens


def _image_input_risk_tokens(image: ProviderImageContentPart) -> int:
    """Estimate high-detail vision usage without tokenizing base64 transport bytes.

    Provider image inputs are billed/model-counted as image tokens, not as the
    textual tokenization of their base64 wire representation. Counting the data
    URL as text can overstate a single normalized page by hundreds of thousands
    of tokens and incorrectly fail a valid turn before the provider sees it.

    We intentionally tile the original dimensions without provider downscaling,
    making this a conservative risk estimate rather than usage reconciliation.
    """

    horizontal_tiles = math.ceil(image.width / _IMAGE_TILE_SIZE)
    vertical_tiles = math.ceil(image.height / _IMAGE_TILE_SIZE)
    return _IMAGE_BASE_TOKENS + (
        _IMAGE_TILE_TOKENS * horizontal_tiles * vertical_tiles
    )


def _request_payload_for_sizing(
    request: ProviderConversationRequest,
) -> tuple[dict[str, object], int]:
    payload = request.to_payload()
    messages = payload["messages"]
    image_tokens = 0
    for message, message_payload in zip(
        request.messages, messages, strict=True
    ):
        if isinstance(message, ProviderUserMessage) and isinstance(
            message.content, tuple
        ):
            content_payload = message_payload["content"]
            for part, part_payload in zip(
                message.content, content_payload, strict=True
            ):
                if not isinstance(part, ProviderImageContentPart):
                    continue
                image_tokens += _image_input_risk_tokens(part)
                part_payload["image_url"]["url"] = (
                    f"data:{part.content_type};base64,<omitted:{part.digest}>"
                )
    return payload, image_tokens


def estimate_provider_wire(
    *,
    policy: ModelRouteRuntimePolicy,
    request: ProviderConversationRequest,
    response_schema: NativeJsonSchema,
    tool_reserve_tokens: int = 0,
) -> ProviderWireEstimate:
    if tool_reserve_tokens < 0:
        raise ValueError("tool_reserve_tokens must be nonnegative")
    tokenizer = tiktoken.get_encoding(policy.tokenizer_profile)
    request_payload, image_tokens = _request_payload_for_sizing(request)
    wire = json.dumps(
        {
            "request": request_payload,
            "response_format": {
                "name": response_schema.name,
                "strict": response_schema.strict,
                "schema": response_schema.schema,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return ProviderWireEstimate(
        input_tokens=len(tokenizer.encode(wire)) + image_tokens,
        response_reserve_tokens=request.max_output_tokens,
        tool_reserve_tokens=tool_reserve_tokens,
    )


def require_provider_wire_within_limits(
    *,
    policy: ModelRouteRuntimePolicy,
    request: ProviderConversationRequest,
    response_schema: NativeJsonSchema,
    tool_reserve_tokens: int = 0,
) -> ProviderWireEstimate:
    estimate = estimate_provider_wire(
        policy=policy,
        request=request,
        response_schema=response_schema,
        tool_reserve_tokens=tool_reserve_tokens,
    )
    if request.max_output_tokens > policy.max_output_tokens_per_invocation:
        raise ProviderProtocolError(safe_code="context_limit_exceeded")
    if (
        estimate.input_tokens > policy.max_input_tokens_per_invocation
        or estimate.context_reserve_tokens > policy.context_window_tokens
        or estimate.projected_tokens > policy.context_window_tokens
    ):
        raise ProviderProtocolError(safe_code="context_limit_exceeded")
    return estimate


__all__ = [
    "ProviderWireEstimate",
    "estimate_provider_wire",
    "require_provider_wire_within_limits",
]
