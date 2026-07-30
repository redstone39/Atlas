from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
from typing import Any, ClassVar, Literal, TypeAlias


JsonObject: TypeAlias = dict[str, Any]


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


@dataclass(frozen=True)
class ProviderSystemMessage:
    content: str
    role: Literal["system"] = field(default="system", init=False)

    def __post_init__(self) -> None:
        _required_text(self.content, "content")


@dataclass(frozen=True)
class ProviderTextContentPart:
    text: str
    type: Literal["text"] = field(default="text", init=False)

    def __post_init__(self) -> None:
        _required_text(self.text, "text")


@dataclass(frozen=True)
class ProviderImageContentPart:
    content: bytes = field(repr=False)
    content_type: Literal["image/png"] = "image/png"
    digest: str = ""
    width: int = 0
    height: int = 0
    type: Literal["image"] = field(default="image", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("image content is required")
        expected = hashlib.sha256(self.content).hexdigest()
        if self.digest != expected:
            raise ValueError("image digest does not match content")
        if self.content_type != "image/png":
            raise ValueError("only normalized PNG image content is supported")
        if (
            isinstance(self.width, bool)
            or isinstance(self.height, bool)
            or not isinstance(self.width, int)
            or not isinstance(self.height, int)
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError("image dimensions must be positive integers")


ProviderUserContentPart: TypeAlias = ProviderTextContentPart | ProviderImageContentPart


@dataclass(frozen=True)
class ProviderUserMessage:
    content: str | tuple[ProviderUserContentPart, ...]
    role: Literal["user"] = field(default="user", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.content, str):
            _required_text(self.content, "content")
            return
        if (
            not isinstance(self.content, tuple)
            or not self.content
            or any(
                not isinstance(part, (ProviderTextContentPart, ProviderImageContentPart))
                for part in self.content
            )
            or sum(isinstance(part, ProviderImageContentPart) for part in self.content) != 1
            or not any(isinstance(part, ProviderTextContentPart) for part in self.content)
        ):
            raise ValueError("multimodal user content requires text and exactly one image")


@dataclass(frozen=True)
class ProviderAssistantMessage:
    content: str
    role: Literal["assistant"] = field(default="assistant", init=False)

    def __post_init__(self) -> None:
        _required_text(self.content, "content")


@dataclass(frozen=True)
class ProviderFunctionCall:
    call_id: str
    name: str
    arguments: JsonObject
    arguments_json: str

    def __post_init__(self) -> None:
        _required_text(self.call_id, "call_id")
        _required_text(self.name, "name")
        if not isinstance(self.arguments, dict):
            raise ValueError("arguments must be an object")
        _required_text(self.arguments_json, "arguments_json")


@dataclass(frozen=True)
class ProviderAssistantToolCallMessage:
    tool_calls: list[ProviderFunctionCall]
    role: Literal["assistant"] = field(default="assistant", init=False)
    content: None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if len(self.tool_calls) != 1 or not isinstance(self.tool_calls[0], ProviderFunctionCall):
            raise ValueError("assistant tool-call message requires exactly one call")


@dataclass(frozen=True)
class ProviderToolResultMessage:
    tool_call_id: str
    content: str
    role: Literal["tool"] = field(default="tool", init=False)

    def __post_init__(self) -> None:
        _required_text(self.tool_call_id, "tool_call_id")
        _required_text(self.content, "content")


ProviderMessage: TypeAlias = (
    ProviderSystemMessage
    | ProviderUserMessage
    | ProviderAssistantMessage
    | ProviderAssistantToolCallMessage
    | ProviderToolResultMessage
)


@dataclass(frozen=True)
class ProviderFunctionTool:
    name: str
    description: str
    parameters: JsonObject
    strict: Literal[True]

    def __post_init__(self) -> None:
        _required_text(self.name, "name")
        _required_text(self.description, "description")
        if self.strict is not True or not isinstance(self.parameters, dict):
            raise ValueError("provider function tool must be strict")
        _validate_json_schema_value({}, self.parameters, self.parameters, validate_value=False)


@dataclass(frozen=True)
class ProviderConversationRequest:
    messages: list[ProviderMessage]
    tools: list[ProviderFunctionTool]
    tool_choice: Literal["auto", "none"]
    parallel_tool_calls: Literal[False]
    max_output_tokens: int
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.messages, list) or any(
            not isinstance(
                message,
                (
                    ProviderSystemMessage,
                    ProviderUserMessage,
                    ProviderAssistantMessage,
                    ProviderAssistantToolCallMessage,
                    ProviderToolResultMessage,
                ),
            )
            for message in self.messages
        ):
            raise ValueError("messages must use provider message types")
        if not isinstance(self.tools, list) or any(
            not isinstance(tool, ProviderFunctionTool) for tool in self.tools
        ):
            raise ValueError("tools must use provider function types")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("tool names must be unique")
        if self.tool_choice not in {"auto", "none"}:
            raise ValueError("tool_choice is invalid")
        if self.tool_choice == "auto" and not self.tools:
            raise ValueError("auto tool choice requires tools")
        if self.tool_choice == "none" and self.tools:
            raise ValueError("none tool choice forbids tools")
        if self.parallel_tool_calls is not False:
            raise ValueError("parallel tool calls are forbidden")
        if (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        pending_call_id: str | None = None
        seen_call_ids: set[str] = set()
        for message in self.messages:
            if isinstance(message, ProviderAssistantToolCallMessage):
                if pending_call_id is not None:
                    raise ValueError("tool call is missing its matching result")
                call_id = message.tool_calls[0].call_id
                if call_id in seen_call_ids:
                    raise ValueError("tool call ids must be unique")
                seen_call_ids.add(call_id)
                pending_call_id = call_id
            elif isinstance(message, ProviderToolResultMessage):
                if pending_call_id is None:
                    raise ValueError("tool result is missing its matching call")
                if message.tool_call_id != pending_call_id:
                    raise ValueError("tool result does not match the preceding call")
                pending_call_id = None
            elif pending_call_id is not None:
                raise ValueError("tool call and result must be an atomic adjacent pair")
        if pending_call_id is not None:
            raise ValueError("tool call is missing its matching result")

    def to_payload(self) -> JsonObject:
        return {
            "messages": [provider_message_payload(message) for message in self.messages],
            "tools": [provider_tool_payload(tool) for tool in self.tools],
            "tool_choice": self.tool_choice,
            "parallel_tool_calls": False,
            "max_completion_tokens": self.max_output_tokens,
        }


ProviderUsage: TypeAlias = dict[str, int]


@dataclass(frozen=True)
class ProviderCompleted:
    provider_request_id: str | None
    model_ref: str
    finish_reason: str | None
    usage: ProviderUsage
    output: JsonObject
    assistant_message: ProviderAssistantMessage
    kind: Literal["completed"] = field(default="completed", init=False)


@dataclass(frozen=True)
class ProviderToolCall:
    provider_request_id: str | None
    model_ref: str
    finish_reason: str | None
    usage: ProviderUsage
    call: ProviderFunctionCall
    assistant_message: ProviderAssistantToolCallMessage
    kind: Literal["tool_call"] = field(default="tool_call", init=False)

    def __post_init__(self) -> None:
        if self.assistant_message.tool_calls != [self.call]:
            raise ValueError("tool outcome and assistant tool message must match")


@dataclass(frozen=True)
class ProviderRefused:
    provider_request_id: str | None
    model_ref: str
    finish_reason: str | None
    usage: ProviderUsage
    reason_code: str
    message_code: str | None
    kind: Literal["refused"] = field(default="refused", init=False)


ProviderIncompleteReason: TypeAlias = Literal[
    "max_output_tokens", "content_filter", "provider_stop", "unknown"
]


@dataclass(frozen=True)
class ProviderIncomplete:
    provider_request_id: str | None
    model_ref: str
    finish_reason: str | None
    usage: ProviderUsage
    reason: ProviderIncompleteReason
    kind: Literal["incomplete"] = field(default="incomplete", init=False)


ProviderConversationOutcome: TypeAlias = (
    ProviderCompleted | ProviderToolCall | ProviderRefused | ProviderIncomplete
)


class ProviderInvocationError(RuntimeError):
    kind: ClassVar[str] = "invocation"

    def __init__(
        self,
        *,
        safe_code: str,
        cause: BaseException | None = None,
        provider_request_id: str | None = None,
        provider_status: int | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        self.safe_code = _required_text(safe_code, "safe_code")
        self.provider_request_id = provider_request_id
        self.provider_status = provider_status
        self.retry_after_ms = retry_after_ms
        cause_type = type(cause).__name__ if cause is not None else "none"
        self.cause_digest = hashlib.sha256(
            f"{self.kind}:{self.safe_code}:{cause_type}".encode("utf-8")
        ).hexdigest()
        super().__init__(self.safe_code)


class ProviderConfigurationError(ProviderInvocationError):
    kind = "configuration"


class ProviderAuthenticationError(ProviderInvocationError):
    kind = "authentication"


class ProviderRateLimitError(ProviderInvocationError):
    kind = "rate_limit"


class ProviderTimeoutError(ProviderInvocationError):
    kind = "timeout"


class ProviderTransportError(ProviderInvocationError):
    kind = "transport"


class ProviderRequestRejectedError(ProviderInvocationError):
    kind = "request_rejected"


class ProviderProtocolError(ProviderInvocationError):
    kind = "protocol"


class ProviderOutputDecodeError(ProviderInvocationError):
    kind = "output_decode"


class ProviderOutputSchemaError(ProviderInvocationError):
    kind = "output_schema"


ProviderInvocationErrorUnion: TypeAlias = (
    ProviderConfigurationError
    | ProviderAuthenticationError
    | ProviderRateLimitError
    | ProviderTimeoutError
    | ProviderTransportError
    | ProviderRequestRejectedError
    | ProviderProtocolError
    | ProviderOutputDecodeError
    | ProviderOutputSchemaError
)


def provider_message_payload(message: ProviderMessage) -> JsonObject:
    if isinstance(message, ProviderAssistantToolCallMessage):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [provider_function_call_payload(message.tool_calls[0])],
        }
    if isinstance(message, ProviderToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if isinstance(message, ProviderUserMessage) and isinstance(message.content, tuple):
        content: list[JsonObject] = []
        for part in message.content:
            if isinstance(part, ProviderTextContentPart):
                content.append({"type": "text", "text": part.text})
            else:
                encoded = base64.b64encode(part.content).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part.content_type};base64,{encoded}",
                    },
                })
        return {"role": "user", "content": content}
    return {"role": message.role, "content": message.content}


def provider_function_call_payload(call: ProviderFunctionCall) -> JsonObject:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments_json},
    }


def provider_tool_payload(tool: ProviderFunctionTool) -> JsonObject:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": True,
        },
    }


def validate_json_schema_value(value: Any, schema: JsonObject) -> None:
    _validate_json_schema_value(value, schema, schema, validate_value=True)


def _validate_json_schema_value(
    value: Any,
    schema: JsonObject,
    root: JsonObject,
    *,
    validate_value: bool,
) -> None:
    if not isinstance(schema, dict):
        raise ValueError("schema node is invalid")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise ValueError("schema ref is invalid")
        target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        if not isinstance(target, dict):
            raise ValueError("schema ref is unresolved")
        _validate_json_schema_value(value, target, root, validate_value=validate_value)
        return
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise ValueError("schema union is invalid")
        if validate_value:
            successes = 0
            for branch in branches:
                try:
                    _validate_json_schema_value(value, branch, root, validate_value=True)
                    successes += 1
                except ValueError:
                    pass
            if successes != 1:
                raise ValueError("value does not match exactly one schema branch")
        return
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValueError("schema type is invalid")
    if "enum" in schema and (
        not isinstance(schema["enum"], list)
        or not schema["enum"]
        or (validate_value and value not in schema["enum"])
    ):
        raise ValueError("enum is invalid")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or set(required) != set(properties)
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError("object schema is not strict")
        for child_schema in properties.values():
            _validate_json_schema_value(None, child_schema, root, validate_value=False)
        if validate_value:
            if not isinstance(value, dict) or set(value) != set(properties):
                raise ValueError("object value does not match schema")
            for key, child_schema in properties.items():
                _validate_json_schema_value(value[key], child_schema, root, validate_value=True)
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise ValueError("array schema is invalid")
        _validate_json_schema_value(None, items, root, validate_value=False)
        if validate_value:
            if not isinstance(value, list):
                raise ValueError("array value is invalid")
            for item in value:
                _validate_json_schema_value(item, items, root, validate_value=True)
    elif validate_value:
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(schema_type, False)
        if not valid:
            raise ValueError("value type does not match schema")
