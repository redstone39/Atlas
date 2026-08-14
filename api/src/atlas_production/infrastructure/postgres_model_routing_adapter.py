from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import struct
from uuid import uuid4
import zlib
from typing import Literal

from atlas_production.infrastructure.provider_key_cipher import (
    AesGcmCredentialCipher,
    CredentialCryptoError,
    model_routing_request_fingerprint,
)
from atlas_production.infrastructure.postgres_owner.identity import IdentityRepository
from atlas_production.infrastructure.postgres_owner.model_routing import (
    BeginDefaultRouteIntentCommand,
    BeginProviderConnectionIntentCommand,
    ConnectionDisablePrecondition,
    DefaultRouteConnectionPrecondition,
    FinalizeDefaultRouteCommand,
    FinalizeDefaultRouteInput,
    FinalizeInvocationLifecycleCommand,
    FinalizeInvocationLifecycleInput,
    FinalizeProviderConfigurationCommand,
    FinalizeProviderConfigurationInput,
    FinalizeRouteConfigurationCommand,
    FinalizeRouteConfigurationInput,
    ModelInvocationWrite,
    ModelRoutingCurrentnessConflict,
    ModelRouteWrite,
    DefaultRouteIntent,
    ProviderConnectionIntent,
    ModelRoutingReadModel,
    ProviderConnectionSecretWrite,
    ProviderConnectionWrite,
    SessionFactory,
)
from atlas_production.modules.audit.public import safe_audit_metadata
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.model_routing.contracts import (
    ModelRouteAuditCommand,
    ModelRoutingError,
)
from atlas_production.modules.model_routing.ports import ProviderAttemptSession
from atlas_production.modules.model_routing.provider_contracts import (
    ProviderCompleted,
    ProviderConversationOutcome,
    ProviderConversationRequest,
    ProviderImageContentPart,
    ProviderInvocationError,
    ProviderSystemMessage,
    ProviderTextContentPart,
    ProviderUserMessage,
)
from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.providers import (
    NativeJsonSchema,
    ProviderError,
    ROUTE_READINESS_SCHEMA,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


ProviderAdapterFactory = Callable[[ProviderConnectionRecord, str], object]


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    content = chunk_type + payload
    return (
        struct.pack(">I", len(payload))
        + content
        + struct.pack(">I", zlib.crc32(content))
    )


def _local_vision_readiness_png() -> bytes:
    """Generate a small, deterministic, non-secret RGB canary without file I/O."""
    width = height = 8
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            scanlines.extend((255, 96, 32) if (x + y) % 2 == 0 else (24, 64, 192))
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(
                b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
            ),
            _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


@dataclass(frozen=True, slots=True)
class PostgresModelRoutingAdapter:
    """Detached-intent orchestration adapter for the current model service port."""

    session_factory: SessionFactory
    provider_adapter_factory: ProviderAdapterFactory
    _intent: ContextVar[ProviderConnectionIntent | DefaultRouteIntent | None] = field(init=False, repr=False)
    _default_connection: ContextVar[ProviderConnectionRecord | None] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_intent",
            ContextVar[ProviderConnectionIntent | DefaultRouteIntent | None](
                f"atlas_model_routing_intent_{id(self)}",
                default=None,
            ),
        )
        object.__setattr__(
            self,
            "_default_connection",
            ContextVar[ProviderConnectionRecord | None](
                f"atlas_model_default_connection_{id(self)}", default=None
            ),
        )

    @property
    def _reader(self) -> ModelRoutingReadModel:
        return ModelRoutingReadModel(self.session_factory)

    @contextmanager
    def operation_intent(
        self,
        connection_ids: list[str],
        *,
        idempotency_key: str = "",
    ) -> Iterator[None]:
        # execute() fully detaches rows before yielding; external work cannot
        # accidentally inherit a SQL Session from this orchestration boundary.
        embedded_keys = tuple(
            value.removeprefix("idempotency:")
            for value in connection_ids
            if value.startswith("idempotency:")
        )
        scoped_connections = tuple(
            value for value in connection_ids if not value.startswith("idempotency:")
        )
        replay_key = idempotency_key or (embedded_keys[0] if embedded_keys else "")
        intent = BeginProviderConnectionIntentCommand(self.session_factory).execute(
            scoped_connections, replay_key
        )
        token = self._intent.set(intent)
        try:
            yield
        finally:
            self._intent.reset(token)

    def mutation_scope(
        self, connection_ids: list[str]
    ) -> AbstractContextManager[None]:
        """Current service port; implementation yields only detached preimages."""
        return self.operation_intent(connection_ids)

    @contextmanager
    def default_route_scope(
        self,
        idempotency_key: str,
        route_id: str,
        purpose: Literal["text", "vision"],
    ) -> Iterator[None]:
        intent = BeginDefaultRouteIntentCommand(self.session_factory).execute(
            idempotency_key, route_id, purpose
        )
        selected_connection = (
            self._reader.get_connection(intent.selected.connection_id)
            if intent.selected is not None
            else None
        )
        token = self._intent.set(intent)
        connection_token = self._default_connection.set(selected_connection)
        try:
            yield
        finally:
            self._default_connection.reset(connection_token)
            self._intent.reset(token)

    def _active_intent(self) -> ProviderConnectionIntent | DefaultRouteIntent:
        intent = self._intent.get()
        if intent is None:
            raise RuntimeError("model-routing mutation requires a detached intent")
        return intent

    def get_replay(
        self,
        idempotency_key: str,
        operation: str,
        target_ref: str,
        request_fingerprint: str,
    ) -> ModelRoutingReplayRecord | None:
        replay = self._reader.get_replay(idempotency_key)
        if replay is not None and (
            replay.operation != operation
            or replay.target_ref != target_ref
            or replay.request_fingerprint != request_fingerprint
        ):
            raise ModelRoutingError(
                "idempotency_key_conflict",
                "model.idempotency_key_was_already_used_for_another_model_routing_operation",
                409,
            )
        return replay

    def fingerprint_request(self, canonical_payload: bytes) -> str:
        try:
            return model_routing_request_fingerprint(canonical_payload)
        except CredentialCryptoError as exc:
            raise ModelRoutingError(exc.code, exc.message_code, 503) from exc

    def get_connection(self, connection_id: str) -> ProviderConnectionRecord | None:
        return self._reader.get_connection(connection_id)

    def list_connections(self) -> list[ProviderConnectionRecord]:
        return self._reader.list_connections(limit=500)

    def get_secret(self, connection_id: str) -> ProviderConnectionSecretRecord | None:
        return self._reader.get_secret(connection_id)

    def get_route(self, route_id: str) -> ModelRouteRecord | None:
        intent = self._intent.get()
        if isinstance(intent, DefaultRouteIntent) and intent.selected is not None:
            if intent.selected.route_id == route_id:
                return deepcopy(intent.selected)
        return self._reader.get_route(route_id)

    def list_routes(self) -> list[ModelRouteRecord]:
        return self._reader.list_routes(limit=500)

    def linked_routes(self, connection_id: str) -> list[ModelRouteRecord]:
        return self._reader.linked_routes(connection_id, limit=500)

    def default_route(
        self, purpose: Literal["text", "vision"]
    ) -> ModelRouteRecord | None:
        intent = self._intent.get()
        if isinstance(intent, DefaultRouteIntent) and intent.purpose == purpose:
            return deepcopy(intent.current_default)
        return self._reader.default_route(purpose)

    def tested_route(self) -> ModelRouteRecord | None:
        return self._reader.tested_route()
    def tested_vision_default_route(self) -> ModelRouteRecord | None:
        return self._reader.tested_vision_default_route()

    def is_system_admin(self, actor: UserRecord) -> bool:
        current = IdentityRepository(self.session_factory).get_user(actor.actor_id)
        return bool(
            current
            and current.active
            and current.actor_type == actor.actor_type
            and current.system_role == "admin"
        )

    @staticmethod
    def _cipher() -> AesGcmCredentialCipher:
        try:
            return AesGcmCredentialCipher.from_environment()
        except CredentialCryptoError as exc:
            raise ModelRoutingError(exc.code, exc.message_code, 503) from exc

    def encrypt_secret(self, *, connection_id: str, provider_type: str, version: int, plaintext: str) -> ProviderConnectionSecretRecord:
        try:
            return self._cipher().encrypt(connection_id=connection_id, provider_type=provider_type, secret_version=version, plaintext=plaintext)
        except CredentialCryptoError as exc:
            raise ModelRoutingError(exc.code, exc.message_code, 503) from exc

    def decrypt_secret(self, connection: ProviderConnectionRecord, secret: ProviderConnectionSecretRecord) -> str:
        try:
            return self._cipher().decrypt(secret, connection_id=connection.connection_id, provider_type=connection.provider_type)
        except CredentialCryptoError as exc:
            raise ModelRoutingError(exc.code, exc.message_code, 503) from exc

    def _provider(self, connection: ProviderConnectionRecord, api_key: str):
        return self.provider_adapter_factory(deepcopy(connection), api_key)

    def discover_models(self, connection: ProviderConnectionRecord, api_key: str) -> list[str]:
        discover = getattr(self._provider(connection, api_key), "discover_models", None)
        if discover is None:
            raise ProviderError("provider_discovery_unavailable", "model.provider_model_discovery_is_unavailable")
        try:
            return list(discover())
        except ProviderInvocationError:
            raise ProviderError("provider_discovery_unavailable", "model.provider_model_discovery_is_unavailable") from None

    def validate_route(
        self,
        connection: ProviderConnectionRecord,
        api_key: str,
        route: ModelRouteRecord,
    ) -> ProviderCompleted:
        provider = self._provider(connection, api_key)
        requests = [
            ProviderConversationRequest(
                messages=[
                    ProviderSystemMessage(
                        content="Return the route readiness result."
                    ),
                    ProviderUserMessage(content="Verify this model route."),
                ],
                tools=[],
                tool_choice="none",
                parallel_tool_calls=False,
                max_output_tokens=route.runtime_policy.max_output_tokens_per_invocation,
            )
        ]
        if route.supports_vision:
            canary = _local_vision_readiness_png()
            requests.append(
                ProviderConversationRequest(
                    messages=[
                        ProviderSystemMessage(
                            content="Return the route readiness result."
                        ),
                        ProviderUserMessage(
                            content=(
                                ProviderTextContentPart(
                                    text=(
                                        "Verify this vision route using the supplied "
                                        "local canary image."
                                    )
                                ),
                                ProviderImageContentPart(
                                    content=canary,
                                    digest=hashlib.sha256(canary).hexdigest(),
                                    width=8,
                                    height=8,
                                ),
                            )
                        ),
                    ],
                    tools=[],
                    tool_choice="none",
                    parallel_tool_calls=False,
                    max_output_tokens=route.runtime_policy.max_output_tokens_per_invocation,
                )
            )
        try:
            answers = [
                provider.complete(
                    route=deepcopy(route),
                    request=request,
                    response_schema=ROUTE_READINESS_SCHEMA,
                )
                for request in requests
            ]
        except ProviderInvocationError:
            raise ProviderError(
                "provider_connection_validation_failed",
                "provider.connection_validation_failed",
            ) from None
        if any(
            not isinstance(answer, ProviderCompleted)
            or answer.output != {"status": "ready"}
            for answer in answers
        ):
            raise ProviderError(
                "provider_response_invalid", "provider.response_was_invalid"
            )
        return answers[-1]

    @staticmethod
    def _events(commands: list[ModelRouteAuditCommand]) -> list[AuditEventRecord]:
        return [AuditEventRecord(event_id=f"audit-{uuid4().hex}", event_type=command.event_type, actor_id=command.actor_id, target_ref=command.target_ref, project_id=None, message_code=command.message_code, metadata=safe_audit_metadata(command.metadata), created_at=utc_now_iso()) for command in commands]

    def commit_configuration(self, *, connections: list[ProviderConnectionRecord], secrets: list[ProviderConnectionSecretRecord], routes: list[ModelRouteRecord], audits: list[ModelRouteAuditCommand], replay_factory: Callable[[list[AuditEventRecord]], ModelRoutingReplayRecord] | None = None) -> list[AuditEventRecord]:
        intent = self._active_intent()
        if isinstance(intent, DefaultRouteIntent):
            if connections or secrets or replay_factory is None:
                raise RuntimeError("default-route intent accepts only route writes and replay")
            current_routes = {
                item.route_id: item
                for item in (intent.selected, intent.current_default)
                if item is not None
            }
            unexpected_route_ids = {
                item.route_id for item in routes
            } - current_routes.keys()
            if unexpected_route_ids:
                raise ModelRoutingCurrentnessConflict(
                    "default route identity changed after intent"
                )
            expected_default_field = f"is_{intent.purpose}_default"
            expected_other_default_field = (
                "is_vision_default"
                if intent.purpose == "text"
                else "is_text_default"
            )
            route_writes = tuple(
                ModelRouteWrite(
                    item,
                    current_routes[item.route_id].revision,
                    preserve_revision=True,
                    expected_default=getattr(
                        current_routes[item.route_id],
                        expected_default_field,
                    ),
                    expected_other_default=getattr(
                        current_routes[item.route_id],
                        expected_other_default_field,
                    ),
                )
                for item in routes
            )
            events = self._events(audits)
            replay = replay_factory(events)
            selected_connection = self._default_connection.get()
            if selected_connection is None:
                raise ModelRoutingCurrentnessConflict(
                    "default route Provider connection preimage is unavailable"
                )
            connection_precondition = DefaultRouteConnectionPrecondition(
                selected_connection.connection_id,
                selected_connection.revision,
                selected_connection.enabled,
                selected_connection.status,
            )
            try:
                FinalizeDefaultRouteCommand(self.session_factory).execute(
                    FinalizeDefaultRouteInput(
                        route_writes,
                        tuple(events),
                        replay,
                        connection_precondition,
                        intent.purpose,
                    )
                )
            except ModelRoutingCurrentnessConflict as exc:
                raise ModelRoutingError(
                    "configuration_revision_conflict",
                    "provider.configuration_changed_refresh_and_try_again",
                    409,
                ) from exc
            return events
        current_connections = {item.connection_id: item for item in intent.connections}
        current_secrets = {item.connection_id: item for item in intent.secrets}
        current_routes = {item.route_id: item for item in intent.routes}
        connection_writes = tuple(ProviderConnectionWrite(item, current_connections[item.connection_id].revision if item.connection_id in current_connections else None) for item in connections if current_connections.get(item.connection_id) != item)
        disable_preconditions = tuple(
            ConnectionDisablePrecondition(item.connection_id)
            for item in connections
            if (
                (current := current_connections.get(item.connection_id)) is not None
                and current.enabled
                and not item.enabled
            )
        )
        secret_writes = tuple(ProviderConnectionSecretWrite(item, current_secrets[item.connection_id].version if item.connection_id in current_secrets else None) for item in secrets if current_secrets.get(item.connection_id) != item)
        route_writes = tuple(ModelRouteWrite(item, current_routes[item.route_id].revision if item.route_id in current_routes else None) for item in routes if current_routes.get(item.route_id) != item)
        events = self._events(audits)
        replay = replay_factory(events) if replay_factory is not None else None
        if replay is not None and (connection_writes or secret_writes):
            FinalizeProviderConfigurationCommand(self.session_factory).execute(
                FinalizeProviderConfigurationInput(
                    connection_writes, secret_writes, route_writes, replay,
                    tuple(events), disable_preconditions,
                )
            )
        elif replay is not None:
            FinalizeRouteConfigurationCommand(self.session_factory).execute(FinalizeRouteConfigurationInput(route_writes, replay, tuple(events)))
        else:
            FinalizeDefaultRouteCommand(self.session_factory).execute(
                FinalizeDefaultRouteInput(route_writes, tuple(events))
            )
        return events



    def open_attempt(self, route: ModelRouteRecord) -> ProviderAttemptSession:
        snapshot = self._reader.provider_attempt_snapshot(route.route_id)
        if (
            snapshot is None
            or snapshot.route != route
            or snapshot.route.revision != route.revision
            or snapshot.route.runtime_policy.revision
            != route.runtime_policy.revision
        ):
            raise ProviderError("model_route_revision_conflict", "model.route_changed_before_the_attempt_started")
        api_key = self.decrypt_secret(snapshot.connection, snapshot.secret)
        return ProviderAttemptSession(
            route=deepcopy(snapshot.route),
            provider=self._provider(snapshot.connection, api_key),
        )

    def open_tested_attempt(
        self,
        route_id: str | None = None,
    ) -> ProviderAttemptSession:
        """Accept one attempt from one joined, detached configuration read."""

        snapshot = self._reader.provider_attempt_snapshot(route_id)
        if snapshot is None:
            raise ProviderError(
                "model_route_unavailable",
                "model.route_is_unavailable",
            )
        api_key = self.decrypt_secret(snapshot.connection, snapshot.secret)
        return ProviderAttemptSession(
            route=deepcopy(snapshot.route),
            provider=self._provider(snapshot.connection, api_key),
        )

    def invoke(self, session: ProviderAttemptSession, request: ProviderConversationRequest, response_schema: NativeJsonSchema) -> ProviderConversationOutcome:
        return session.provider.complete(route=session.route, request=deepcopy(request), response_schema=response_schema)

    @staticmethod
    def next_invocation_id() -> str:
        return f"mi-{uuid4().hex}"

    def put_invocation(self, invocation: ModelInvocationRecord) -> None:
        current = self._reader.get_invocation(invocation.invocation_id)
        audit = AuditEventRecord(
            event_id=f"audit-{uuid4().hex}",
            event_type=f"model_invocation.{invocation.status}",
            actor_id="atlas-model-runtime",
            target_ref=f"model-invocation:{invocation.invocation_id}",
            project_id=None,
            message_code="model.provider_model_route_passed_the_controlled_test",
            metadata={
                "invocation_id": invocation.invocation_id,
                "route_id": invocation.route_id,
                "status": invocation.status,
            },
            created_at=utc_now_iso(),
        )
        FinalizeInvocationLifecycleCommand(self.session_factory).execute(
            FinalizeInvocationLifecycleInput(
                ModelInvocationWrite(invocation, current),
                (audit,),
            )
        )

    def get_invocation(self, invocation_id: str) -> ModelInvocationRecord | None:
        return self._reader.get_invocation(invocation_id)

    def invocation_for_execution_key(self, execution_key: str) -> ModelInvocationRecord | None:
        return self._reader.invocation_for_execution_key(execution_key)


__all__ = ["PostgresModelRoutingAdapter"]
