from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import math
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import litellm
from litellm import exceptions as litellm_exceptions

from atlas_production.modules.model_routing.records import (
    ModelRouteRecord,
    ProviderConnectionRecord,
)
from atlas_production.modules.model_routing.provider_contracts import (
    ProviderAssistantMessage,
    ProviderAssistantToolCallMessage,
    ProviderAuthenticationError,
    ProviderCompleted,
    ProviderConfigurationError,
    ProviderConversationOutcome,
    ProviderConversationRequest,
    ProviderFunctionCall,
    ProviderIncomplete,
    ProviderInvocationError,
    ProviderOutputDecodeError,
    ProviderOutputSchemaError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRefused,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderToolCall,
    ProviderTransportError,
    validate_json_schema_value,
)
from atlas_production.shared.user_messages import MessageParams, validate_message_reference


class ProviderError(RuntimeError):
    def __init__(self, code: str, message_code: str, message_params: MessageParams | None = None) -> None:
        validated_params = validate_message_reference(message_code, message_params or {})
        super().__init__(message_code)
        self.code = code
        self.message_code = message_code
        self.message_params = validated_params


@dataclass(frozen=True, init=False)
class NativeJsonSchema:
    name: str
    strict: Literal[True]
    digest: str
    _canonical_schema: str = field(repr=False)

    def __init__(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        strict: Literal[True],
        digest: str,
    ) -> None:
        if not isinstance(name, str) or not name.strip() or strict is not True:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        try:
            canonical = json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            ) from exc
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != expected:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        property_count = [0]
        normalized = _normalize_schema_node(
            schema,
            property_count=property_count,
        )
        if normalized != schema or normalized.get("type") != "object" or "anyOf" in normalized:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        _validate_normalized_schema(normalized)
        object.__setattr__(self, "name", name.strip())
        object.__setattr__(self, "strict", True)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "_canonical_schema", canonical)

    @property
    def schema(self) -> dict[str, Any]:
        # Return a fresh value so callers cannot mutate the schema that was
        # validated and bound to this contract's digest.
        value = json.loads(self._canonical_schema)
        assert isinstance(value, dict)
        return value


_REMOVED_SCHEMA_KEYWORDS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)
_ALLOWED_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
        "$defs",
        "$ref",
        "const",
    }
)


def build_native_json_schema(name: str, application_schema: dict[str, Any]) -> NativeJsonSchema:
    """Build the provider-owned strict subset from an application JSON Schema."""

    if not isinstance(name, str) or not name.strip() or not isinstance(application_schema, dict):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    property_count = [0]
    normalized = _normalize_schema_node(application_schema, property_count=property_count)
    if normalized.get("type") != "object" or "anyOf" in normalized:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    _validate_normalized_schema(normalized)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return NativeJsonSchema(
        name=name.strip(),
        schema=normalized,
        strict=True,
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )




def _normalize_schema_node(
    value: Any,
    *,
    property_count: list[int],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    unknown = set(value) - _ALLOWED_SCHEMA_KEYWORDS - _REMOVED_SCHEMA_KEYWORDS
    if unknown or ("const" in value and "enum" in value):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in _REMOVED_SCHEMA_KEYWORDS:
            continue
        if key == "const":
            normalized["enum"] = [item]
        elif key == "properties":
            if not isinstance(item, dict):
                raise ProviderError(
                    "native_schema_contract_invalid",
                    'common.native_json_schema_contract_is_invalid',
                )
            property_count[0] += len(item)
            if property_count[0] > 100:
                raise ProviderError(
                    "native_schema_contract_invalid",
                    'common.native_json_schema_contract_is_invalid',
                )
            normalized[key] = {
                property_name: _normalize_schema_node(
                    property_schema,
                    property_count=property_count,
                )
                for property_name, property_schema in item.items()
            }
        elif key == "items":
            normalized[key] = _normalize_schema_node(
                item,
                property_count=property_count,
            )
        elif key == "anyOf":
            if not isinstance(item, list) or not item:
                raise ProviderError(
                    "native_schema_contract_invalid",
                    'common.native_json_schema_contract_is_invalid',
                )
            normalized[key] = [
                _normalize_schema_node(
                    branch,
                    property_count=property_count,
                )
                for branch in item
            ]
        elif key == "$defs":
            if not isinstance(item, dict):
                raise ProviderError(
                    "native_schema_contract_invalid",
                    'common.native_json_schema_contract_is_invalid',
                )
            normalized[key] = {
                definition_name: _normalize_schema_node(
                    definition,
                    property_count=property_count,
                )
                for definition_name, definition in item.items()
            }
        else:
            normalized[key] = item

    schema_type = normalized.get("type")
    if schema_type is not None and (
        not isinstance(schema_type, str)
        or schema_type not in {
            "string",
            "number",
            "boolean",
            "integer",
            "object",
            "array",
            "null",
        }
    ):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    enum = normalized.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if "$ref" in normalized and set(normalized) != {"$ref"}:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if "anyOf" in normalized and set(normalized) != {"anyOf"}:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if schema_type == "object":
        properties = normalized.get("properties")
        required = normalized.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        if (
            any(not isinstance(item, str) for item in required)
            or any(not isinstance(item, str) for item in properties)
            or set(required) != set(properties)
            or len(required) != len(properties)
        ):
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        if value.get("additionalProperties", False) is not False:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        normalized["additionalProperties"] = False
    elif any(
        key in normalized
        for key in ("properties", "required", "additionalProperties")
    ):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if schema_type == "array" and "items" not in normalized:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if schema_type != "array" and "items" in normalized:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    if schema_type is None and not ({"$ref", "anyOf"} & set(normalized)):
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    return normalized


def _validate_normalized_schema(normalized: dict[str, Any]) -> None:
    definitions = normalized.get("$defs", {})
    _validate_local_refs(normalized, definitions)
    _validate_object_depth(normalized, definitions, object_depth=0, ref_stack=())
    for name, definition in definitions.items():
        _validate_object_depth(
            definition,
            definitions,
            object_depth=0,
            ref_stack=(name,),
        )


def _validate_local_refs(node: dict[str, Any], definitions: dict[str, Any]) -> None:
    ref = node.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        if ref.removeprefix("#/$defs/") not in definitions:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
    for key in ("properties", "$defs"):
        for child in node.get(key, {}).values():
            _validate_local_refs(child, definitions)
    if "items" in node:
        _validate_local_refs(node["items"], definitions)
    for child in node.get("anyOf", []):
        _validate_local_refs(child, definitions)


def _validate_object_depth(
    node: dict[str, Any],
    definitions: dict[str, Any],
    *,
    object_depth: int,
    ref_stack: tuple[str, ...],
) -> None:
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.removeprefix("#/$defs/")
        if name in ref_stack:
            raise ProviderError(
                "native_schema_contract_invalid",
                'common.native_json_schema_contract_is_invalid',
            )
        _validate_object_depth(
            definitions[name],
            definitions,
            object_depth=object_depth,
            ref_stack=(*ref_stack, name),
        )
        return
    next_depth = object_depth + (1 if node.get("type") == "object" else 0)
    if next_depth > 5:
        raise ProviderError(
            "native_schema_contract_invalid",
            'common.native_json_schema_contract_is_invalid',
        )
    for child in node.get("properties", {}).values():
        _validate_object_depth(
            child,
            definitions,
            object_depth=next_depth,
            ref_stack=ref_stack,
        )
    if "items" in node:
        _validate_object_depth(
            node["items"],
            definitions,
            object_depth=next_depth,
            ref_stack=ref_stack,
        )
    for child in node.get("anyOf", []):
        _validate_object_depth(
            child,
            definitions,
            object_depth=next_depth,
            ref_stack=ref_stack,
        )


ROUTE_READINESS_SCHEMA = build_native_json_schema(
    "atlas_route_readiness_v1",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready"]},
        },
        "required": ["status"],
        "additionalProperties": False,
    },
)


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_provider_endpoint(provider_type: str, endpoint_url: str) -> str:
    try:
        parsed = urlsplit(endpoint_url.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError(
            "provider_endpoint_invalid",
            'provider.endpoint_is_invalid',
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and not _is_loopback(parsed.hostname))
    ):
        raise ProviderError(
            "provider_endpoint_invalid",
            'provider.endpoint_is_invalid',
        )
    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/")
    if provider_type == "openai_compatible":
        normalized_path = path
        for operation_suffix in ("/chat/completions", "/models"):
            if normalized_path.endswith(operation_suffix):
                normalized_path = normalized_path[: -len(operation_suffix)].rstrip("/")
                break
    elif provider_type == "azure_openai":
        if path:
            raise ProviderError(
                "provider_endpoint_invalid",
                'provider.endpoint_is_invalid',
            )
        normalized_path = ""
    elif provider_type == "anthropic":
        if (
            parsed.scheme != "https"
            or parsed.hostname.lower() != "api.anthropic.com"
            or port is not None
            or path
        ):
            raise ProviderError(
                "provider_endpoint_invalid",
                'provider.endpoint_is_invalid',
            )
        normalized_path = ""
    else:
        raise ProviderError(
            "provider_type_unsupported",
            'provider.type_is_not_supported',
        )
    return urlunsplit((parsed.scheme, netloc, normalized_path, "", "")).rstrip("/")


def normalize_provider_connection(
    provider_type: str,
    endpoint_url: str,
    api_version: str | None,
) -> tuple[str, str | None]:
    endpoint = normalize_provider_endpoint(provider_type, endpoint_url)
    normalized_version = api_version.strip() if api_version is not None else None
    if provider_type == "azure_openai":
        if not normalized_version:
            raise ProviderError(
                "provider_connection_fields_invalid",
                'provider.connection_fields_are_invalid',
            )
    elif normalized_version:
        raise ProviderError(
            "provider_connection_fields_invalid",
            'provider.connection_fields_are_invalid',
        )
    else:
        normalized_version = None
    return endpoint, normalized_version


def _safe_litellm_error_metadata(
    exc: BaseException,
) -> tuple[str | None, int | None, int | None]:
    request_id = getattr(exc, "request_id", None)
    if not isinstance(request_id, str) or not request_id.strip():
        request_id = None
    else:
        request_id = request_id.strip()
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        status = None
    retry_after: Any = getattr(exc, "retry_after", None)
    if retry_after is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            retry_after = headers.get("retry-after")
    if isinstance(retry_after, str):
        try:
            retry_after = float(retry_after)
        except ValueError:
            retry_after = None
    retry_after_ms: int | None = None
    if (
        isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and math.isfinite(retry_after)
        and retry_after >= 0
    ):
        retry_after_ms = int(retry_after * 1_000)
    return request_id, status, retry_after_ms


_MAPPED_LITELLM_EXCEPTIONS = (
    litellm_exceptions.AuthenticationError,
    litellm_exceptions.PermissionDeniedError,
    litellm_exceptions.RateLimitError,
    litellm_exceptions.Timeout,
    litellm_exceptions.BadRequestError,
    litellm_exceptions.UnprocessableEntityError,
    litellm_exceptions.NotFoundError,
    litellm_exceptions.ContentPolicyViolationError,
    litellm_exceptions.ContextWindowExceededError,
    litellm_exceptions.RejectedRequestError,
    litellm_exceptions.APIConnectionError,
    litellm_exceptions.ServiceUnavailableError,
    litellm_exceptions.BadGatewayError,
    litellm_exceptions.InternalServerError,
    litellm_exceptions.APIError,
)


def _mapped_litellm_error(exc: BaseException) -> ProviderInvocationError:
    request_id, status, retry_after_ms = _safe_litellm_error_metadata(exc)
    if isinstance(
        exc,
        (
            litellm_exceptions.AuthenticationError,
            litellm_exceptions.PermissionDeniedError,
        ),
    ):
        error_type = ProviderAuthenticationError
        safe_code = "provider_authentication_error"
    elif isinstance(exc, litellm_exceptions.RateLimitError):
        error_type = ProviderRateLimitError
        safe_code = "provider_rate_limit"
    elif isinstance(exc, litellm_exceptions.Timeout):
        error_type = ProviderTimeoutError
        safe_code = "provider_timeout"
    elif isinstance(
        exc,
        (
            litellm_exceptions.BadRequestError,
            litellm_exceptions.UnprocessableEntityError,
            litellm_exceptions.NotFoundError,
            litellm_exceptions.ContentPolicyViolationError,
            litellm_exceptions.ContextWindowExceededError,
            litellm_exceptions.RejectedRequestError,
        ),
    ):
        error_type = ProviderRequestRejectedError
        safe_code = "provider_request_rejected"
    else:
        error_type = ProviderTransportError
        safe_code = "provider_transport_error"
    return error_type(
        safe_code=safe_code,
        cause=exc,
        provider_request_id=request_id,
        provider_status=status,
        retry_after_ms=retry_after_ms,
    )


def _normalized_usage(raw_usage: Any) -> dict[str, int]:
    if not isinstance(raw_usage, dict):
        raise TypeError("usage is invalid")
    allowed_keys = {
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
    }
    usage: dict[str, int] = {}
    for key in allowed_keys:
        if key not in raw_usage:
            continue
        value = raw_usage[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError("token usage is invalid")
        usage[key] = value
    anthropic_cached_tokens = raw_usage.get("cache_read_input_tokens")
    if anthropic_cached_tokens is not None:
        if (
            isinstance(anthropic_cached_tokens, bool)
            or not isinstance(anthropic_cached_tokens, int)
            or anthropic_cached_tokens < 0
        ):
            raise TypeError("cached token usage is invalid")
        existing = usage.get("cached_input_tokens")
        if existing is not None and existing != anthropic_cached_tokens:
            raise TypeError("cached token usage conflicts")
        usage["cached_input_tokens"] = anthropic_cached_tokens
    prompt_details = raw_usage.get("prompt_tokens_details")
    if prompt_details is not None:
        if not isinstance(prompt_details, dict):
            raise TypeError("prompt token details are invalid")
        cached_tokens = prompt_details.get("cached_tokens")
        if cached_tokens is not None:
            if (
                isinstance(cached_tokens, bool)
                or not isinstance(cached_tokens, int)
                or cached_tokens < 0
            ):
                raise TypeError("cached token usage is invalid")
            existing = usage.get("cached_input_tokens")
            if existing is not None and existing != cached_tokens:
                raise TypeError("cached token usage conflicts")
            usage["cached_input_tokens"] = cached_tokens
    return usage


def _normalize_litellm_response(
    data: Any,
    *,
    route: ModelRouteRecord,
    request: ProviderConversationRequest,
    response_schema: NativeJsonSchema,
) -> ProviderConversationOutcome:
    provider_request_id: str | None = None
    try:
        if not isinstance(data, dict):
            raise TypeError("response is not an object")
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise TypeError("exactly one choice is required")
        first = choices[0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise TypeError("message is missing")
        message = first["message"]
        finish_reason = first.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError("finish reason is invalid")
        provider_request_id = data.get("id")
        if provider_request_id is not None:
            if (
                not isinstance(provider_request_id, str)
                or not provider_request_id.strip()
            ):
                raise TypeError("provider request id is invalid")
            provider_request_id = provider_request_id.strip()
        model_ref = data.get("model", route.model_name)
        if not isinstance(model_ref, str) or not model_ref.strip():
            raise TypeError("model ref is invalid")
        model_ref = model_ref.strip()
        token_usage = _normalized_usage(data.get("usage", {}))
        refusal = message.get("refusal")
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        has_refusal = isinstance(refusal, str) and bool(refusal.strip())
        has_content = isinstance(content, str) and bool(content.strip())
        has_tool_calls = tool_calls is not None
        if refusal is not None and not has_refusal:
            raise TypeError("refusal is invalid")
        if sum((has_refusal, has_content, has_tool_calls)) > 1:
            raise TypeError("response contains mixed terminal states")
        if has_refusal:
            return ProviderRefused(
                provider_request_id=provider_request_id,
                model_ref=model_ref,
                finish_reason=finish_reason,
                usage=token_usage,
                reason_code="provider_refusal",
                message_code='provider.refused_the_request',
            )
        if has_tool_calls:
            if not isinstance(tool_calls, list) or len(tool_calls) != 1:
                raise TypeError("exactly one tool call is required")
            raw_call = tool_calls[0]
            if (
                not isinstance(raw_call, dict)
                or raw_call.get("type") != "function"
                or not isinstance(raw_call.get("function"), dict)
            ):
                raise TypeError("tool call is invalid")
            call_id = raw_call.get("id")
            function = raw_call["function"]
            name = function.get("name")
            arguments_json = function.get("arguments")
            if (
                not isinstance(call_id, str)
                or not call_id.strip()
                or not isinstance(name, str)
                or not name.strip()
                or not isinstance(arguments_json, str)
                or not arguments_json.strip()
            ):
                raise TypeError("tool call fields are invalid")
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError as exc:
                raise ProviderOutputDecodeError(
                    safe_code="provider_output_decode_error",
                    cause=exc,
                    provider_request_id=provider_request_id,
                ) from exc
            if not isinstance(arguments, dict):
                raise ProviderOutputSchemaError(
                    safe_code="provider_output_schema_error",
                    provider_request_id=provider_request_id,
                )
            tool = next((tool for tool in request.tools if tool.name == name), None)
            if tool is None:
                raise ProviderOutputSchemaError(
                    safe_code="provider_output_schema_error",
                    provider_request_id=provider_request_id,
                )
            try:
                validate_json_schema_value(arguments, tool.parameters)
            except ValueError as exc:
                raise ProviderOutputSchemaError(
                    safe_code="provider_output_schema_error",
                    cause=exc,
                    provider_request_id=provider_request_id,
                ) from exc
            call = ProviderFunctionCall(
                call_id=call_id.strip(),
                name=name.strip(),
                arguments=arguments,
                arguments_json=arguments_json,
            )
            return ProviderToolCall(
                provider_request_id=provider_request_id,
                model_ref=model_ref,
                finish_reason=finish_reason,
                usage=token_usage,
                call=call,
                assistant_message=ProviderAssistantToolCallMessage(tool_calls=[call]),
            )
        if finish_reason != "stop":
            reason = {
                "length": "max_output_tokens",
                "content_filter": "content_filter",
                "stop": "provider_stop",
            }.get(finish_reason, "unknown")
            return ProviderIncomplete(
                provider_request_id=provider_request_id,
                model_ref=model_ref,
                finish_reason=finish_reason,
                usage=token_usage,
                reason=reason,
            )
        if not has_content:
            raise ValueError("content is empty")
        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderOutputDecodeError(
                safe_code="provider_output_decode_error",
                cause=exc,
                provider_request_id=provider_request_id,
            ) from exc
        try:
            validate_json_schema_value(output, response_schema.schema)
        except ValueError as exc:
            raise ProviderOutputSchemaError(
                safe_code="provider_output_schema_error",
                cause=exc,
                provider_request_id=provider_request_id,
            ) from exc
        assert isinstance(output, dict)
        return ProviderCompleted(
            provider_request_id=provider_request_id,
            model_ref=model_ref,
            finish_reason=finish_reason,
            usage=token_usage,
            output=output,
            assistant_message=ProviderAssistantMessage(content=content),
        )
    except ProviderInvocationError:
        raise
    except json.JSONDecodeError as exc:
        raise ProviderOutputDecodeError(
            safe_code="provider_output_decode_error",
            cause=exc,
            provider_request_id=provider_request_id,
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderProtocolError(
            safe_code="provider_protocol_error",
            cause=exc,
            provider_request_id=provider_request_id,
        ) from exc


class LiteLLMProvider:
    def __init__(
        self,
        connection: ProviderConnectionRecord,
        api_key: str,
        completion: Callable[..., Any] = litellm.completion,
    ) -> None:
        endpoint, api_version = normalize_provider_connection(
            connection.provider_type,
            connection.endpoint_url,
            connection.api_version,
        )
        if not api_key:
            raise ProviderConfigurationError(
                safe_code="provider_credential_unavailable",
            )
        self.provider_type = connection.provider_type
        self.api_base = endpoint
        self.api_version = api_version
        self.api_key = api_key
        self.completion = completion

    def discover_models(self) -> list[str]:
        if self.provider_type != "openai_compatible":
            raise ProviderError(
                "provider_discovery_unavailable",
                'model.provider_model_discovery_is_unavailable',
            )
        request_url = f"{self.api_base}/models"
        try:
            with httpx.Client() as client:
                response = client.get(
                    request_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()
            raw_models = data["data"]
            if not isinstance(raw_models, list):
                raise TypeError("data is not a list")
            models = {
                item["id"].strip()
                for item in raw_models
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].strip()
            }
            if not models:
                raise ValueError("no usable models")
            return sorted(models, key=str.casefold)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderError(
                "provider_discovery_unavailable",
                'model.provider_model_discovery_is_unavailable',
            ) from None

    def complete(
        self,
        *,
        route: ModelRouteRecord,
        request: ProviderConversationRequest,
        response_schema: NativeJsonSchema,
    ) -> ProviderConversationOutcome:
        if not isinstance(response_schema, NativeJsonSchema) or not isinstance(
            request, ProviderConversationRequest
        ):
            raise ProviderProtocolError(safe_code="provider_protocol_error")
        model = {
            "openai_compatible": f"openai/{route.model_name}",
            "azure_openai": f"azure/{route.model_name}",
            "anthropic": f"anthropic/{route.model_name}",
        }.get(self.provider_type)
        if model is None:
            raise ProviderConfigurationError(
                safe_code="provider_type_unsupported",
            )
        if self.provider_type == "anthropic":
            schema_supported = litellm.supports_response_schema(
                model=model,
                custom_llm_provider="anthropic",
            )
            if schema_supported is not True:
                raise ProviderConfigurationError(
                    safe_code="provider_schema_unsupported",
                )
        payload = request.to_payload()
        route_timeout = request.timeout_seconds or getattr(
            getattr(route, "runtime_policy", None),
            "provider_invocation_timeout_seconds",
            30.0,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": payload["messages"],
            "tools": payload["tools"],
            "tool_choice": request.tool_choice,
            "parallel_tool_calls": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.name,
                    "strict": True,
                    "schema": response_schema.schema,
                },
            },
            "temperature": 0,
            "timeout": float(route_timeout),
            "num_retries": 0,
            "stream": False,
            "api_key": self.api_key,
            "api_base": self.api_base,
        }
        if self.provider_type == "anthropic":
            kwargs["max_tokens"] = request.max_output_tokens
        else:
            kwargs["max_completion_tokens"] = request.max_output_tokens
        if self.provider_type == "azure_openai":
            kwargs["api_version"] = self.api_version
        try:
            response = self.completion(**kwargs)
        except _MAPPED_LITELLM_EXCEPTIONS as exc:
            raise _mapped_litellm_error(exc) from exc
        try:
            data = response.model_dump()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProviderProtocolError(
                safe_code="provider_protocol_error",
                cause=exc,
            ) from exc
        return _normalize_litellm_response(
            data,
            route=route,
            request=request,
            response_schema=response_schema,
        )


def default_provider_adapter_factory(
    connection: ProviderConnectionRecord,
    api_key: str,
) -> LiteLLMProvider:
    return LiteLLMProvider(connection, api_key)
