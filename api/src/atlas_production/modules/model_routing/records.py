from dataclasses import dataclass, field
from typing import Any, Literal

import tiktoken


@dataclass
class ProviderConnectionRecord:
    connection_id: str
    display_name: str
    provider_type: Literal["openai_compatible", "azure_openai", "anthropic"]
    endpoint_url: str
    api_version: str | None = None
    auth_method: str = "api_key"
    status: str = "credential_required"
    enabled: bool = False
    revision: int = 1
    last_verified_at: str | None = None
    last_rotated_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ProviderConnectionSecretRecord:
    connection_id: str
    ciphertext: str
    nonce: str
    key_id: str
    version: int
    storage_backend: str = "encrypted_database"
    algorithm: str = "AES-256-GCM"
    updated_at: str = ""


@dataclass(frozen=True)
class ModelRouteRuntimePolicyInput:
    schema_version: Literal["model-route-runtime-policy-v8"]
    tokenizer_profile: str
    max_tool_executions: int
    max_provider_invocations: int
    max_reasoning_revision_cycles: int
    max_catalog_pages: int
    max_search_rounds: int
    max_model_visible_items_per_turn: int
    max_retrieval_repairs: int
    max_schema_retries_per_turn: int
    max_selected_anchor_pages_per_round: int
    provider_invocation_timeout_seconds: int
    tool_execution_timeout_seconds: int
    turn_timeout_seconds: int
    context_window_tokens: int
    max_input_tokens_per_invocation: int
    max_output_tokens_per_invocation: int
    max_tool_result_tokens_per_execution: int
    max_total_tokens_per_conversation: int

    def __post_init__(self) -> None:
        if self.schema_version != "model-route-runtime-policy-v8":
            raise ValueError("invalid runtime policy schema")
        if not self.tokenizer_profile.strip():
            raise ValueError("tokenizer profile is required")
        try:
            tiktoken.get_encoding(self.tokenizer_profile)
        except Exception as exc:
            raise ValueError("tokenizer profile is not supported") from exc
        numeric_values = (
            self.max_tool_executions,
            self.max_provider_invocations,
            self.max_catalog_pages,
            self.max_search_rounds,
            self.max_model_visible_items_per_turn,
            self.max_retrieval_repairs,
            self.max_schema_retries_per_turn,
            self.max_selected_anchor_pages_per_round,
            self.provider_invocation_timeout_seconds,
            self.tool_execution_timeout_seconds,
            self.turn_timeout_seconds,
            self.context_window_tokens,
            self.max_input_tokens_per_invocation,
            self.max_output_tokens_per_invocation,
            self.max_tool_result_tokens_per_execution,
            self.max_total_tokens_per_conversation,
        )
        if any(isinstance(value, bool) or value <= 0 for value in numeric_values):
            raise ValueError("runtime policy numeric values must be positive")
        if (
            isinstance(self.max_reasoning_revision_cycles, bool)
            or self.max_reasoning_revision_cycles < 0
            or self.max_reasoning_revision_cycles > 3
        ):
            raise ValueError("reasoning revision cycle limit must be between zero and three")
        required_provider_invocations = (
            self.max_tool_executions
            + 6 * self.max_reasoning_revision_cycles
            + 9
        )
        if self.max_provider_invocations < required_provider_invocations:
            raise ValueError(
                "provider invocation limit must cover tools, selectors, planning, evaluation, revisions, and terminal actions"
            )
        if self.max_retrieval_repairs > 3:
            raise ValueError("retrieval repair limit cannot exceed three")
        if self.max_schema_retries_per_turn > 3:
            raise ValueError("schema retry limit cannot exceed three")
        if self.max_selected_anchor_pages_per_round > 20:
            raise ValueError("selected anchor page limit cannot exceed twenty")
        if (
            self.max_input_tokens_per_invocation
            + self.max_output_tokens_per_invocation
            > self.context_window_tokens
        ):
            raise ValueError("input and output limits exceed context window")
        if self.max_tool_result_tokens_per_execution > self.max_input_tokens_per_invocation:
            raise ValueError("tool result limit exceeds input limit")
        if (
            self.max_total_tokens_per_conversation
            < self.max_input_tokens_per_invocation
            + self.max_output_tokens_per_invocation
        ):
            raise ValueError("conversation token limit is too small")
        if self.turn_timeout_seconds < self.provider_invocation_timeout_seconds:
            raise ValueError("turn timeout is shorter than provider timeout")
        if self.turn_timeout_seconds < self.tool_execution_timeout_seconds:
            raise ValueError("turn timeout is shorter than tool timeout")


@dataclass(frozen=True)
class ModelRouteRuntimePolicy(ModelRouteRuntimePolicyInput):
    revision: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.revision, bool) or self.revision <= 0:
            raise ValueError("runtime policy revision must be positive")


@dataclass
class ModelRouteRecord:
    route_id: str
    display_name: str
    provider_type: str
    model_name: str
    connection_id: str
    runtime_policy: ModelRouteRuntimePolicy
    supports_vision: bool = False
    status: str = "configured"
    enabled: bool = True
    revision: int = 1
    last_tested_at: str | None = None
    is_text_default: bool = False
    is_vision_default: bool = False
    readiness_schema_name: str | None = None
    readiness_schema_digest: str | None = None


@dataclass
class ModelRoutingReplayRecord:
    idempotency_key: str
    operation: str
    target_ref: str
    request_fingerprint: str
    response_model: str
    response_payload: dict[str, Any]
    status_code: int
    created_at: str


@dataclass
class ModelInvocationRecord:
    invocation_id: str
    route_id: str
    provider_type: str
    model_name: str
    status: str
    created_at: str
    prompt_snapshot_ref: str
    response_schema_name: str
    response_schema_digest: str
    token_usage: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    route_revision: int | None = None
    runtime_policy_schema_version: str | None = None
    runtime_policy_revision: int | None = None
    runtime_policy_snapshot: dict[str, Any] = field(default_factory=dict)
    invocation_purpose: str = "conversation"
    subject_kind: str = "conversation"
    subject_ref: str | None = None
    request_artifact_ref: str | None = None
    response_artifact_ref: str | None = None
    execution_key: str | None = None
    prompt_digest: str | None = None
    input_digest: str | None = None
    input_content_type: str | None = None
    input_width: int | None = None
    input_height: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    attempt_ordinal: int | None = None
    repair_origin_error_codes: list[str] = field(default_factory=list)
