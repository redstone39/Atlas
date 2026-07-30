from __future__ import annotations

from contextlib import AbstractContextManager
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...providers import NativeJsonSchema
from .provider_contracts import ProviderCompleted, ProviderConversationOutcome, ProviderConversationRequest
from atlas_production.shared.public import AuditEventRecord
from .records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.modules.identity_access.records import UserRecord
from .contracts import ModelInvocationHandle, ModelRouteAuditCommand


@dataclass(frozen=True)
class ProviderAttemptSession:
    route: ModelRouteRecord
    provider: Any = field(repr=False)


class ModelRoutingRuntime(Protocol):
    def open_tested_attempt(
        self,
        route_id: str | None = None,
    ) -> ProviderAttemptSession: ...

    def tested_route(self) -> ModelRouteRecord | None: ...

    def tested_vision_route(self, route_id: str) -> ModelRouteRecord | None: ...

    def visual_invocation(self, execution_key: str) -> ModelInvocationRecord | None: ...

    def open_attempt(self, route: ModelRouteRecord) -> ProviderAttemptSession: ...

    def invoke(
        self,
        session: ProviderAttemptSession,
        request: ProviderConversationRequest,
        response_schema: NativeJsonSchema,
    ) -> ProviderConversationOutcome: ...

    def prepare_invocation(
        self,
        route: ModelRouteRecord,
        response_schema: NativeJsonSchema,
        **kwargs: Any,
    ) -> ModelInvocationHandle: ...

    def record_invocation_success(
        self,
        handle: ModelInvocationHandle,
        token_usage: dict[str, int],
        *,
        response_artifact_ref: str | None = None,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord: ...

    def record_invocation_started(
        self,
        handle: ModelInvocationHandle,
    ) -> ModelInvocationRecord: ...

    def record_invocation_failure(
        self,
        handle: ModelInvocationHandle,
        error_code: str,
        *,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord: ...


class ModelRoutingRepository(Protocol):
    def open_tested_attempt(
        self,
        route_id: str | None = None,
    ) -> ProviderAttemptSession: ...

    def mutation_scope(self, connection_ids: list[str]) -> AbstractContextManager[None]: ...

    def default_route_scope(
        self, idempotency_key: str, route_id: str
    ) -> AbstractContextManager[None]: ...

    def get_replay(
        self,
        idempotency_key: str,
        operation: str,
        target_ref: str,
        request_fingerprint: str,
    ) -> ModelRoutingReplayRecord | None: ...

    def fingerprint_request(self, canonical_payload: bytes) -> str: ...

    def get_connection(self, connection_id: str) -> ProviderConnectionRecord | None: ...

    def list_connections(self) -> list[ProviderConnectionRecord]: ...

    def get_secret(self, connection_id: str) -> ProviderConnectionSecretRecord | None: ...

    def get_route(self, route_id: str) -> ModelRouteRecord | None: ...

    def list_routes(self) -> list[ModelRouteRecord]: ...

    def linked_routes(self, connection_id: str) -> list[ModelRouteRecord]: ...

    def default_route(self) -> ModelRouteRecord | None: ...

    def tested_route(self) -> ModelRouteRecord | None: ...

    def is_system_admin(self, actor: UserRecord) -> bool: ...

    def encrypt_secret(
        self,
        *,
        connection_id: str,
        provider_type: str,
        version: int,
        plaintext: str,
    ) -> ProviderConnectionSecretRecord: ...

    def decrypt_secret(
        self,
        connection: ProviderConnectionRecord,
        secret: ProviderConnectionSecretRecord,
    ) -> str: ...

    def discover_models(
        self,
        connection: ProviderConnectionRecord,
        api_key: str,
    ) -> list[str]: ...

    def validate_route(
        self,
        connection: ProviderConnectionRecord,
        api_key: str,
        route: ModelRouteRecord,
    ) -> ProviderCompleted: ...

    def commit_configuration(
        self,
        *,
        connections: list[ProviderConnectionRecord],
        secrets: list[ProviderConnectionSecretRecord],
        routes: list[ModelRouteRecord],
        audits: list[ModelRouteAuditCommand],
        replay_factory: Callable[
            [list[AuditEventRecord]], ModelRoutingReplayRecord
        ] | None = None,
    ) -> list[AuditEventRecord]: ...

    def mark_default(
        self,
        route: ModelRouteRecord,
        audit: ModelRouteAuditCommand,
    ) -> tuple[ModelRouteRecord, AuditEventRecord]: ...

    def open_attempt(self, route: ModelRouteRecord) -> ProviderAttemptSession: ...

    def invoke(
        self,
        session: ProviderAttemptSession,
        request: ProviderConversationRequest,
        response_schema: NativeJsonSchema,
    ) -> ProviderConversationOutcome: ...

    def next_invocation_id(self) -> str: ...

    def put_invocation(self, invocation: ModelInvocationRecord) -> None: ...

    def get_invocation(self, invocation_id: str) -> ModelInvocationRecord | None: ...

    def invocation_for_execution_key(self, execution_key: str) -> ModelInvocationRecord | None: ...
