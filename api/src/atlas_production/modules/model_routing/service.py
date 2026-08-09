from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json

from pydantic import BaseModel, SecretStr

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.providers import (
    NativeJsonSchema,
    ProviderError,
    ROUTE_READINESS_SCHEMA,
    normalize_provider_connection,
)
from .provider_contracts import ProviderConversationOutcome, ProviderConversationRequest
from atlas_production.shared.public import utc_now_iso
from .api_models import (
    ModelRouteCreateRequest,
    ModelRouteDefaultRequest,
    ModelRouteListResult,
    ModelRouteStatus,
    ModelRouteTestRequest,
    ModelRouteUpdateRequest,
    ProviderConnectionCreateRequest,
    ProviderConnectionListResult,
    ProviderConnectionStatus,
    ProviderConnectionTestRequest,
    ProviderConnectionTestResult,
    ProviderConnectionUpdateRequest,
    ProviderModelDiscoveryResult,
)
from .contracts import (
    ModelInvocationHandle,
    ModelRouteAuditCommand,
    ModelRouteOutcome,
    ModelRoutingError,
)
from .ports import ModelRoutingRepository, ProviderAttemptSession
from .records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
)


def _validated_duration(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value < 0:
        raise ValueError("invocation duration must be a non-negative integer")
    return value


class ModelRoutingService:
    def __init__(self, repository: ModelRoutingRepository) -> None:
        self.repository = repository

    @staticmethod
    def _lock_ids(connection_ids: list[str], idempotency_key: str) -> list[str]:
        return sorted({*connection_ids, f"idempotency:{idempotency_key}"})

    def _replayed(
        self,
        *,
        idempotency_key: str,
        operation: str,
        target_ref: str,
        response_model: type[BaseModel],
        payload: BaseModel,
    ) -> tuple[BaseModel, int] | None:
        request_fingerprint = self._request_fingerprint(payload)
        replay = self.repository.get_replay(
            idempotency_key,
            operation,
            target_ref,
            request_fingerprint,
        )
        if replay is None:
            return None
        if replay.response_model != response_model.__name__:
            raise ModelRoutingError(
                "idempotency_replay_invalid",
                'model.stored_model_routing_replay_is_invalid',
                503,
            )
        try:
            result = response_model.model_validate(replay.response_payload)
        except Exception as exc:
            raise ModelRoutingError(
                "idempotency_replay_invalid",
                'model.stored_model_routing_replay_is_invalid',
                503,
            ) from exc
        return result, replay.status_code

    def _commit_with_replay(
        self,
        *,
        idempotency_key: str,
        operation: str,
        target_ref: str,
        status_code: int,
        result_builder: Callable[[str], BaseModel],
        connections: list[ProviderConnectionRecord],
        secrets: list,
        routes: list[ModelRouteRecord],
        audits: list[ModelRouteAuditCommand],
        payload: BaseModel,
    ) -> BaseModel:
        result_holder: dict[str, BaseModel] = {}
        request_fingerprint = self._request_fingerprint(payload)

        def replay_factory(events) -> ModelRoutingReplayRecord:
            if not events:
                raise RuntimeError("idempotent mutation requires an audit event")
            result = result_builder(events[0].event_id)
            result_holder["result"] = result
            return ModelRoutingReplayRecord(
                idempotency_key=idempotency_key,
                operation=operation,
                target_ref=target_ref,
                request_fingerprint=request_fingerprint,
                response_model=type(result).__name__,
                response_payload=result.model_dump(mode="json"),
                status_code=status_code,
                created_at=events[0].created_at,
            )

        self.repository.commit_configuration(
            connections=connections,
            secrets=secrets,
            routes=routes,
            audits=audits,
            replay_factory=replay_factory,
        )
        return result_holder["result"]

    def _request_fingerprint(self, payload: BaseModel) -> str:
        def reveal(value):
            if isinstance(value, SecretStr):
                return value.get_secret_value()
            if isinstance(value, dict):
                return {key: reveal(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [reveal(item) for item in value]
            return value

        canonical = json.dumps(
            reveal(payload.model_dump(mode="python", exclude={"idempotency_key"})),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.repository.fingerprint_request(canonical)

    def list_connections(
        self,
        actor: UserRecord | None,
    ) -> ProviderConnectionListResult:
        self._require_admin(actor)
        with self.repository.mutation_scope([]):
            return ProviderConnectionListResult(
                connections=[
                    self._connection_status(
                        item,
                        self._connection_message(item),
                        "audit-provider-connection-listed",
                    )
                    for item in sorted(
                        self.repository.list_connections(),
                        key=lambda connection: connection.display_name.casefold(),
                    )
                ]
            )

    def create_connection(
        self,
        actor: UserRecord | None,
        payload: ProviderConnectionCreateRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        connection_id = payload.connection_id.strip()
        api_key = payload.api_key.get_secret_value()
        if not connection_id or not payload.display_name.strip() or not api_key:
            self._invalid("provider.connection_fields_are_invalid")
        try:
            endpoint, api_version = normalize_provider_connection(
                payload.provider_type,
                payload.endpoint_url,
                payload.api_version,
            )
        except ProviderError as exc:
            self._provider_error(exc)
        operation = "provider_connection_create"
        target_ref = f"provider-connection:{connection_id}"
        with self.repository.mutation_scope(
            self._lock_ids([connection_id], payload.idempotency_key)
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ProviderConnectionStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            if self.repository.get_connection(connection_id):
                raise ModelRoutingError(
                    "provider_connection_exists",
                    'provider.connection_already_exists',
                    409,
                )
            now = utc_now_iso()
            connection = ProviderConnectionRecord(
                connection_id=connection_id,
                display_name=payload.display_name.strip(),
                provider_type=payload.provider_type,
                endpoint_url=endpoint,
                api_version=api_version,
                status="configured",
                enabled=False,
                created_at=now,
                updated_at=now,
                last_rotated_at=now,
            )
            secret = self.repository.encrypt_secret(
                connection_id=connection_id,
                provider_type=connection.provider_type,
                version=1,
                plaintext=api_key,
            )
            discovery_available = True
            try:
                self.repository.discover_models(connection, api_key)
            except ProviderError:
                discovery_available = False
            if discovery_available:
                connection.status = "verified"
                connection.enabled = True
                connection.last_verified_at = now
                message = 'provider.connection_is_verified'
            else:
                message = 'provider.connection_is_configured_discovery_is_unavailable'
            audit = ModelRouteAuditCommand(
                event_type="provider_connection_created",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code=message,
                metadata={
                    "connection_id": connection_id,
                    "provider_type": connection.provider_type,
                    "discovery_status": (
                        "available" if discovery_available else "unavailable"
                    ),
                    "api_version": connection.api_version,
                    "request_id": payload.idempotency_key,
                },
            )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=201,
                result_builder=lambda audit_ref: self._connection_status(
                    connection,
                    message,
                    audit_ref,
                    credential_configured=True,
                    linked_model_count=0,
                ),
                connections=[connection],
                secrets=[secret],
                routes=[],
                audits=[audit],
                payload=payload,
            )
            return ModelRouteOutcome(result, 201)

    def update_connection(
        self,
        actor: UserRecord | None,
        connection_id: str,
        payload: ProviderConnectionUpdateRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        operation = "provider_connection_update"
        target_ref = f"provider-connection:{connection_id}"
        with self.repository.mutation_scope(
            self._lock_ids([connection_id], payload.idempotency_key)
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ProviderConnectionStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            current = self._connection(connection_id)
            self._check_revision(current.revision, payload.expected_revision)
            linked = self.repository.linked_routes(connection_id)
            if payload.enabled is False and any(route.is_default for route in linked):
                self._invalid(
                    "provider.choose_another_default_before_disabling_connection"
                )
            candidate = deepcopy(current)
            if payload.display_name is not None:
                if not payload.display_name.strip():
                    self._invalid("provider.connection_display_name_is_invalid")
                candidate.display_name = payload.display_name.strip()
            connection_fields_supplied = (
                payload.endpoint_url is not None
                or "api_version" in payload.model_fields_set
            )
            if "api_version" in payload.model_fields_set:
                candidate.api_version = payload.api_version
            if connection_fields_supplied:
                try:
                    candidate.endpoint_url, candidate.api_version = (
                        normalize_provider_connection(
                            candidate.provider_type,
                            (
                                payload.endpoint_url
                                if payload.endpoint_url is not None
                                else candidate.endpoint_url
                            ),
                            candidate.api_version,
                        )
                    )
                except ProviderError as exc:
                    self._provider_error(exc)
            supplied_key = (
                payload.api_key.get_secret_value() if payload.api_key is not None else ""
            )
            key_changed = bool(supplied_key)
            enabled_changed = payload.enabled is not None and payload.enabled != current.enabled
            secret = self.repository.get_secret(connection_id)
            validation_needed = connection_fields_supplied or key_changed or (
                enabled_changed and payload.enabled is True
            )
            if validation_needed and not supplied_key and secret is None:
                raise ModelRoutingError(
                    "provider_credential_unavailable",
                    'provider.credential_is_unavailable',
                    503,
                )
            api_key = supplied_key
            if validation_needed and not api_key and secret is not None:
                api_key = self.repository.decrypt_secret(current, secret)
            updated_routes: list[ModelRouteRecord] = []
            now = utc_now_iso()
            manual_profile_without_enabled_route = (
                candidate.provider_type in {"azure_openai", "anthropic"}
                and not any(route.enabled for route in linked)
            )
            if manual_profile_without_enabled_route:
                candidate.status = "configured"
                candidate.enabled = False
            elif validation_needed:
                try:
                    self._validate_connection(candidate, api_key, linked)
                except ProviderError:
                    self._record_rejection(
                        actor,
                        connection_id,
                        payload.idempotency_key,
                        "provider.connection_validation_failed",
                    )
                    raise ModelRoutingError(
                        "provider_connection_validation_failed",
                        'provider.connection_validation_failed',
                        422,
                    )
                candidate.status = "verified"
                candidate.enabled = True
                candidate.last_verified_at = now
                for route in linked:
                    if route.enabled:
                        self._mark_route_ready(route)
                        route.last_tested_at = now
                        route.revision += 1
                        updated_routes.append(route)
            if manual_profile_without_enabled_route:
                candidate.enabled = False
                candidate.status = "configured"
            elif payload.enabled is False:
                candidate.enabled = False
                candidate.status = "disabled"
            elif payload.enabled is True and not validation_needed:
                candidate.enabled = True
            changed = candidate != current or key_changed
            if not changed:
                audit = ModelRouteAuditCommand(
                    event_type="provider_connection_unchanged",
                    actor_id=actor.actor_id,
                    target_ref=target_ref,
                    message_code='provider.connection_is_unchanged',
                    metadata={
                        "connection_id": connection_id,
                        "request_id": payload.idempotency_key,
                        "revision": current.revision,
                    },
                )
                result = self._commit_with_replay(
                    idempotency_key=payload.idempotency_key,
                    operation=operation,
                    target_ref=target_ref,
                    status_code=200,
                    result_builder=lambda audit_ref: self._connection_status(
                        current,
                        'provider.connection_is_unchanged',
                        audit_ref,
                        credential_configured=secret is not None,
                        linked_model_count=len(linked),
                    ),
                    connections=[],
                    secrets=[],
                    routes=[],
                    audits=[audit],
                    payload=payload,
                )
                return ModelRouteOutcome(result, 200)
            candidate.revision += 1
            candidate.updated_at = now
            secrets = []
            if key_changed:
                rotated = self.repository.encrypt_secret(
                    connection_id=connection_id,
                    provider_type=candidate.provider_type,
                    version=1 if secret is None else secret.version + 1,
                    plaintext=supplied_key,
                )
                candidate.last_rotated_at = now
                secrets.append(rotated)
            audit = ModelRouteAuditCommand(
                event_type="provider_connection_updated",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code='provider.connection_is_updated',
                metadata={
                    "connection_id": connection_id,
                    "request_id": payload.idempotency_key,
                    "revision": candidate.revision,
                    "api_version": candidate.api_version,
                },
            )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=200,
                result_builder=lambda audit_ref: self._connection_status(
                    candidate,
                    'provider.connection_is_updated',
                    audit_ref,
                    credential_configured=secret is not None or bool(secrets),
                    linked_model_count=len(linked),
                ),
                connections=[candidate],
                secrets=secrets,
                routes=updated_routes,
                audits=[audit],
                payload=payload,
            )
            return ModelRouteOutcome(result, 200)

    def test_connection(
        self,
        actor: UserRecord | None,
        connection_id: str,
        payload: ProviderConnectionTestRequest,
    ) -> ProviderConnectionTestResult:
        actor = self._require_admin(actor)
        operation = "provider_connection_test"
        target_ref = f"provider-connection:{connection_id}"
        with self.repository.mutation_scope(
            self._lock_ids([connection_id], payload.idempotency_key)
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ProviderConnectionTestResult,
                payload=payload,
            )
            if replayed is not None:
                return replayed[0]
            connection = self._connection(connection_id)
            self._check_revision(connection.revision, payload.expected_revision)
            secret = self._secret(connection)
            api_key = self.repository.decrypt_secret(connection, secret)
            linked = self.repository.linked_routes(connection_id)
            tested_route_ids = [route.route_id for route in linked if route.enabled]
            now = utc_now_iso()
            try:
                self._validate_connection(connection, api_key, linked)
            except ProviderError:
                audit = ModelRouteAuditCommand(
                    event_type="provider_connection_validation_rejected",
                    actor_id=actor.actor_id,
                    target_ref=target_ref,
                    message_code='provider.connection_test_failed',
                    metadata={
                        "connection_id": connection_id,
                        "request_id": payload.idempotency_key,
                    },
                )
                response_connection = deepcopy(connection)
                response_connection.status = "verification_failed"
                failed_routes = []
                for route in linked:
                    if route.enabled:
                        route.status = "test_failed"
                        route.readiness_schema_name = None
                        route.readiness_schema_digest = None
                        route.last_tested_at = now
                        route.revision += 1
                        failed_routes.append(route)
                result = self._commit_with_replay(
                    idempotency_key=payload.idempotency_key,
                    operation=operation,
                    target_ref=target_ref,
                    status_code=200,
                    result_builder=lambda audit_ref: ProviderConnectionTestResult(
                        connection=self._connection_status(
                            response_connection,
                            'provider.connection_test_failed',
                            audit_ref,
                            credential_configured=True,
                            linked_model_count=len(linked),
                        ),
                        validation_status="failed",
                        tested_route_ids=tested_route_ids,
                        message_code='provider.connection_test_failed',
                        audit_event_ref=audit_ref,
                    ),
                    connections=[],
                    secrets=[],
                    routes=failed_routes,
                    audits=[audit],
                    payload=payload,
                )
                return result
            connection.status = "verified"
            connection.enabled = True
            connection.revision += 1
            connection.last_verified_at = now
            connection.updated_at = now
            routes = []
            for route in linked:
                if route.enabled:
                    self._mark_route_ready(route)
                    route.last_tested_at = now
                    route.revision += 1
                    routes.append(route)
            audit = ModelRouteAuditCommand(
                event_type="provider_connection_test_passed",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code='provider.connection_test_passed',
                metadata={
                    "connection_id": connection_id,
                    "tested_route_ids": tested_route_ids,
                    "request_id": payload.idempotency_key,
                },
            )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=200,
                result_builder=lambda audit_ref: ProviderConnectionTestResult(
                    connection=self._connection_status(
                        connection,
                        'provider.connection_test_passed',
                        audit_ref,
                        credential_configured=True,
                        linked_model_count=len(linked),
                    ),
                    validation_status="passed",
                    tested_route_ids=tested_route_ids,
                    message_code='provider.connection_test_passed',
                    audit_event_ref=audit_ref,
                ),
                connections=[connection],
                secrets=[],
                routes=routes,
                audits=[audit],
                payload=payload,
            )
            return result

    def discover_models(
        self,
        actor: UserRecord | None,
        connection_id: str,
    ) -> ProviderModelDiscoveryResult:
        self._require_admin(actor)
        with self.repository.mutation_scope([connection_id]):
            connection = self._connection(connection_id)
            secret = self._secret(connection)
            api_key = self.repository.decrypt_secret(connection, secret)
            try:
                models = self.repository.discover_models(connection, api_key)
            except ProviderError:
                return ProviderModelDiscoveryResult(
                    connection_id=connection_id,
                    discovery_status="unavailable",
                    models=[],
                    message_code='model.provider_model_discovery_is_unavailable_enter_a_model_manually',
                )
            return ProviderModelDiscoveryResult(
                connection_id=connection_id,
                discovery_status="available",
                models=models,
                message_code='model.provider_models_are_available',
            )

    def list_routes(self, actor: UserRecord | None) -> ModelRouteListResult:
        self._require_admin(actor)
        with self.repository.mutation_scope([]):
            default = self.repository.default_route()
            return ModelRouteListResult(
                routes=[
                    self._route_status(
                        route,
                    "model.route_is_available",
                        "audit-model-route-listed",
                    )
                    for route in sorted(
                        self.repository.list_routes(),
                        key=lambda item: item.display_name.casefold(),
                    )
                ],
                default_route_id=default.route_id if default else None,
            )

    def configure(
        self,
        actor: UserRecord | None,
        payload: ModelRouteCreateRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        operation = "model_route_create"
        target_ref = f"model-route:{payload.route_id}"
        with self.repository.mutation_scope(
            self._lock_ids([payload.connection_id], payload.idempotency_key)
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ModelRouteStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            if self.repository.get_route(payload.route_id):
                raise ModelRoutingError(
                    "model_route_exists",
                    'model.route_already_exists',
                    409,
                )
            connection = self._connection(payload.connection_id)
            route = ModelRouteRecord(
                route_id=payload.route_id,
                display_name=payload.display_name,
                provider_type=connection.provider_type,
                model_name=payload.model_name,
                connection_id=connection.connection_id,
                runtime_policy=ModelRouteRuntimePolicy(
                    **asdict(payload.runtime_policy),
                    revision=1,
                ),
                supports_vision=payload.supports_vision,
                enabled=payload.enabled,
                status="disabled" if not payload.enabled else "configured",
            )
            connections: list[ProviderConnectionRecord] = []
            if route.enabled:
                secret = self._secret(connection)
                api_key = self.repository.decrypt_secret(connection, secret)
                try:
                    self.repository.validate_route(connection, api_key, route)
                except ProviderError:
                    raise ModelRoutingError(
                        "provider_connection_validation_failed",
                        'provider.connection_validation_failed',
                        422,
                    )
                now = utc_now_iso()
                self._mark_route_ready(route)
                route.last_tested_at = now
                if connection.status != "verified" or not connection.enabled:
                    connection.status = "verified"
                    connection.enabled = True
                    connection.revision += 1
                    connection.last_verified_at = now
                    connection.updated_at = now
                    connections.append(connection)
            audit = ModelRouteAuditCommand(
                event_type="model_route_created",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code='model.route_is_configured',
                metadata={
                    "route_id": route.route_id,
                    "connection_id": route.connection_id,
                    "supports_vision": route.supports_vision,
                    "request_id": payload.idempotency_key,
                    **self._runtime_policy_audit_metadata(route),
                },
            )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=201,
                result_builder=lambda audit_ref: self._route_status(
                    route,
                    'model.route_is_configured',
                    audit_ref,
                ),
                connections=connections,
                secrets=[],
                routes=[route],
                audits=[audit],
                payload=payload,
            )
            return ModelRouteOutcome(result, 201)

    def update_route(
        self,
        actor: UserRecord | None,
        route_id: str,
        payload: ModelRouteUpdateRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        operation = "model_route_update"
        target_ref = f"model-route:{route_id}"
        initial = self.repository.get_route(route_id)
        if not initial:
            self._missing("Model route was not found.")
        target_connection_id = payload.connection_id or initial.connection_id
        with self.repository.mutation_scope(
            self._lock_ids(
                sorted({initial.connection_id, target_connection_id}),
                payload.idempotency_key,
            )
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ModelRouteStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            current = self._route(route_id)
            self._check_revision(current.revision, payload.expected_revision)
            if payload.enabled is False and current.is_default:
                self._invalid(
                    "model.choose_another_default_before_disabling_route"
                )
            candidate = deepcopy(current)
            if payload.display_name is not None:
                candidate.display_name = payload.display_name
            if payload.model_name is not None:
                candidate.model_name = payload.model_name
            if payload.connection_id is not None:
                candidate.connection_id = payload.connection_id
            if payload.enabled is not None:
                candidate.enabled = payload.enabled
            if payload.supports_vision is not None:
                candidate.supports_vision = payload.supports_vision
            candidate.runtime_policy = ModelRouteRuntimePolicy(
                **asdict(payload.runtime_policy),
                revision=current.runtime_policy.revision + 1,
            )
            connection = self._connection(candidate.connection_id)
            candidate.provider_type = connection.provider_type
            validation_needed = candidate.enabled and any(
                value is not None
                for value in (payload.model_name, payload.connection_id, payload.enabled)
            )
            connections: list[ProviderConnectionRecord] = []
            if validation_needed:
                secret = self._secret(connection)
                api_key = self.repository.decrypt_secret(connection, secret)
                try:
                    self.repository.validate_route(connection, api_key, candidate)
                except ProviderError:
                    raise ModelRoutingError(
                        "provider_connection_validation_failed",
                        'provider.connection_validation_failed',
                        422,
                    )
                now = utc_now_iso()
                self._mark_route_ready(candidate)
                candidate.last_tested_at = now
                if connection.status != "verified" or not connection.enabled:
                    connection.status = "verified"
                    connection.enabled = True
                    connection.revision += 1
                    connection.last_verified_at = now
                    connection.updated_at = now
                    connections.append(connection)
            elif payload.enabled is False:
                candidate.status = "disabled"
            if candidate == current:
                audit = ModelRouteAuditCommand(
                    event_type="model_route_unchanged",
                    actor_id=actor.actor_id,
                    target_ref=target_ref,
                    message_code='model.route_is_unchanged',
                    metadata={
                        "route_id": route_id,
                        "request_id": payload.idempotency_key,
                        "revision": current.revision,
                    },
                )
                result = self._commit_with_replay(
                    idempotency_key=payload.idempotency_key,
                    operation=operation,
                    target_ref=target_ref,
                    status_code=200,
                    result_builder=lambda audit_ref: self._route_status(
                        current,
                        'model.route_is_unchanged',
                        audit_ref,
                    ),
                    connections=[],
                    secrets=[],
                    routes=[],
                    audits=[audit],
                    payload=payload,
                )
                return ModelRouteOutcome(result, 200)
            candidate.revision += 1
            audit = ModelRouteAuditCommand(
                event_type="model_route_updated",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code='model.route_is_updated',
                metadata={
                    "route_id": route_id,
                    "source_connection_id": current.connection_id,
                    "target_connection_id": candidate.connection_id,
                    "supports_vision": candidate.supports_vision,
                    "request_id": payload.idempotency_key,
                    **self._runtime_policy_audit_metadata(candidate),
                },
            )
            audits = [audit]
            if candidate.connection_id != current.connection_id:
                audits.extend(
                    [
                        ModelRouteAuditCommand(
                            event_type="model_route_connection_detached",
                            actor_id=actor.actor_id,
                            target_ref=f"provider-connection:{current.connection_id}",
                            message_code='model.a_model_route_moved_away_from_this_provider_connection',
                            metadata={
                                "route_id": route_id,
                                "source_connection_id": current.connection_id,
                                "target_connection_id": candidate.connection_id,
                                "request_id": payload.idempotency_key,
                            },
                        ),
                        ModelRouteAuditCommand(
                            event_type="model_route_connection_attached",
                            actor_id=actor.actor_id,
                            target_ref=f"provider-connection:{candidate.connection_id}",
                            message_code='model.a_model_route_moved_to_this_provider_connection',
                            metadata={
                                "route_id": route_id,
                                "source_connection_id": current.connection_id,
                                "target_connection_id": candidate.connection_id,
                                "request_id": payload.idempotency_key,
                            },
                        ),
                    ]
                )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=200,
                result_builder=lambda audit_ref: self._route_status(
                    candidate,
                    'model.route_is_updated',
                    audit_ref,
                ),
                connections=connections,
                secrets=[],
                routes=[candidate],
                audits=audits,
                payload=payload,
            )
            return ModelRouteOutcome(result, 200)

    def set_default(
        self,
        actor: UserRecord | None,
        route_id: str,
        payload: ModelRouteDefaultRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        operation = "model_route_default"
        target_ref = f"model-route:{route_id}"
        with self.repository.default_route_scope(payload.idempotency_key, route_id):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ModelRouteStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            route = self._route(route_id)
            self._check_revision(route.revision, payload.expected_revision)
            connection = self._connection(route.connection_id)
            secret = self._secret(connection)
            if (
                not route.enabled
                or route.status != "test_passed"
                or not self._has_current_readiness_proof(route)
                or not connection.enabled
                or connection.status != "verified"
            ):
                self._invalid("model.test_this_route_before_making_it_default")
            self.repository.decrypt_secret(connection, secret)
            previous = self.repository.default_route()
            audit = ModelRouteAuditCommand(
                event_type="model_route_default_set",
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code='model.default_model_route_is_updated',
                metadata={
                    "previous_default_route_id": previous.route_id if previous else None,
                    "new_default_route_id": route.route_id,
                    "request_id": payload.idempotency_key,
                    "route_status": route.status,
                },
            )
            selected = route
            selected.is_default = True
            selected.revision += 1
            routes = [selected]
            if previous is not None and previous.route_id != selected.route_id:
                previous.is_default = False
                previous.revision += 1
                routes.insert(0, previous)
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=200,
                result_builder=lambda audit_ref: self._route_status(
                    selected,
                    'model.default_model_route_is_updated',
                    audit_ref,
                ),
                connections=[],
                secrets=[],
                routes=routes,
                audits=[audit],
                payload=payload,
            )
            return ModelRouteOutcome(
                result,
                200,
            )

    def test_route(
        self,
        actor: UserRecord | None,
        route_id: str,
        payload: ModelRouteTestRequest,
    ) -> ModelRouteOutcome:
        actor = self._require_admin(actor)
        operation = "model_route_test"
        target_ref = f"model-route:{route_id}"
        initial = self._route(route_id)
        with self.repository.mutation_scope(
            self._lock_ids([initial.connection_id], payload.idempotency_key)
        ):
            replayed = self._replayed(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                response_model=ModelRouteStatus,
                payload=payload,
            )
            if replayed is not None:
                return ModelRouteOutcome(replayed[0], replayed[1])
            route = self._route(route_id)
            self._check_revision(route.revision, payload.expected_revision)
            connection = self._connection(route.connection_id)
            secret = self._secret(connection)
            api_key = self.repository.decrypt_secret(connection, secret)
            now = utc_now_iso()
            try:
                self.repository.validate_route(connection, api_key, route)
                self._mark_route_ready(route)
                message_code = 'model.provider_model_route_passed_the_controlled_test'
                event_type = "model_route_test_passed"
            except ProviderError:
                route.status = "test_failed"
                route.readiness_schema_name = None
                route.readiness_schema_digest = None
                message_code = 'model.provider_model_route_test_failed'
                event_type = "model_route_test_failed"
            route.last_tested_at = now
            route.revision += 1
            routes = [route]
            audit = ModelRouteAuditCommand(
                event_type=event_type,
                actor_id=actor.actor_id,
                target_ref=target_ref,
                message_code=message_code,
                metadata={
                    "route_id": route_id,
                    "request_id": payload.idempotency_key,
                    "status": route.status,
                },
            )
            result = self._commit_with_replay(
                idempotency_key=payload.idempotency_key,
                operation=operation,
                target_ref=target_ref,
                status_code=200,
                result_builder=lambda audit_ref: self._route_status(
                    route,
                    message_code,
                    audit_ref,
                ),
                connections=[],
                secrets=[],
                routes=routes,
                audits=[audit],
                payload=payload,
            )
            return ModelRouteOutcome(result, 200)

    def open_tested_attempt(
        self,
        route_id: str | None = None,
    ) -> ProviderAttemptSession:
        return self.repository.open_tested_attempt(route_id)

    def tested_route(self) -> ModelRouteRecord | None:
        return self.repository.tested_route()

    def tested_vision_route(self, route_id: str) -> ModelRouteRecord | None:
        route = self.repository.get_route(route_id)
        if (
            route is None
            or not route.enabled
            or route.status != "test_passed"
            or not route.supports_vision
            or not self._has_current_readiness_proof(route)
            or not self._runtime_eligible(route)
        ):
            return None
        return route

    def visual_invocation(self, execution_key: str) -> ModelInvocationRecord | None:
        return self.repository.invocation_for_execution_key(execution_key)

    def open_attempt(self, route: ModelRouteRecord) -> ProviderAttemptSession:
        return self.repository.open_attempt(route)

    def invoke(
        self,
        session: ProviderAttemptSession,
        request: ProviderConversationRequest,
        response_schema: NativeJsonSchema,
    ) -> ProviderConversationOutcome:
        return self.repository.invoke(session, request, response_schema)

    def prepare_invocation(
        self,
        route: ModelRouteRecord,
        response_schema: NativeJsonSchema,
        *,
        invocation_purpose: str = "conversation",
        subject_kind: str = "conversation",
        subject_ref: str | None = None,
        request_artifact_ref: str | None = None,
        execution_key: str | None = None,
        prompt_digest: str | None = None,
        input_digest: str | None = None,
        input_content_type: str | None = None,
        input_width: int | None = None,
        input_height: int | None = None,
        attempt_ordinal: int | None = None,
        repair_origin_error_codes: list[str] | tuple[str, ...] = (),
    ) -> ModelInvocationHandle:
        invocation_id = self.repository.next_invocation_id()
        created_at = utc_now_iso()
        handle = ModelInvocationHandle(
            invocation_id=invocation_id,
            route_id=route.route_id,
            provider_type=route.provider_type,
            model_name=route.model_name,
            prompt_snapshot_ref=request_artifact_ref or f"prompt-{invocation_id}",
            response_schema_name=response_schema.name,
            response_schema_digest=response_schema.digest,
            route_revision=route.revision,
            runtime_policy_schema_version=route.runtime_policy.schema_version,
            runtime_policy_revision=route.runtime_policy.revision,
            runtime_policy_snapshot=asdict(route.runtime_policy),
            created_at=created_at,
            invocation_purpose=invocation_purpose,
            subject_kind=subject_kind,
            subject_ref=subject_ref,
            request_artifact_ref=request_artifact_ref,
            execution_key=execution_key,
            prompt_digest=prompt_digest,
            input_digest=input_digest,
            input_content_type=input_content_type,
            input_width=input_width,
            input_height=input_height,
            attempt_ordinal=attempt_ordinal,
            repair_origin_error_codes=tuple(sorted(set(repair_origin_error_codes))),
        )
        self.repository.put_invocation(
            self._invocation_from_handle(handle, status="planned")
        )
        return handle

    def record_invocation_started(
        self,
        handle: ModelInvocationHandle,
    ) -> ModelInvocationRecord:
        current = self._required_invocation(handle)
        if current.status != "planned":
            raise RuntimeError("model_invocation_transition_conflict")
        invocation = replace(current, status="started", started_at=utc_now_iso())
        self.repository.put_invocation(invocation)
        return invocation

    def record_invocation_success(
        self,
        handle: ModelInvocationHandle,
        token_usage: dict[str, int],
        *,
        response_artifact_ref: str | None = None,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord:
        current = self._required_invocation(handle)
        if current.status != "started":
            raise RuntimeError("model_invocation_transition_conflict")
        invocation = replace(
            current,
            status="completed",
            completed_at=utc_now_iso(),
            duration_ms=_validated_duration(duration_ms),
            token_usage=dict(token_usage),
            response_artifact_ref=response_artifact_ref,
        )
        self.repository.put_invocation(invocation)
        return invocation

    def record_invocation_failure(
        self,
        handle: ModelInvocationHandle,
        error_code: str,
        *,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord:
        current = self._required_invocation(handle)
        if current.status not in {"planned", "started"}:
            raise RuntimeError("model_invocation_transition_conflict")
        invocation = replace(
            current,
            status="failed",
            completed_at=utc_now_iso(),
            duration_ms=(
                _validated_duration(duration_ms)
                if current.status == "started"
                else None
            ),
            error_code=error_code,
        )
        self.repository.put_invocation(invocation)
        return invocation

    def _required_invocation(
        self, handle: ModelInvocationHandle
    ) -> ModelInvocationRecord:
        invocation = self.repository.get_invocation(handle.invocation_id)
        if invocation is None:
            raise RuntimeError("model_invocation_missing")
        return invocation

    @staticmethod
    def _invocation_from_handle(
        handle: ModelInvocationHandle, *, status: str
    ) -> ModelInvocationRecord:
        return ModelInvocationRecord(
            invocation_id=handle.invocation_id,
            route_id=handle.route_id,
            provider_type=handle.provider_type,
            model_name=handle.model_name,
            status=status,
            created_at=handle.created_at,
            prompt_snapshot_ref=handle.prompt_snapshot_ref,
            response_schema_name=handle.response_schema_name,
            response_schema_digest=handle.response_schema_digest,
            route_revision=handle.route_revision,
            runtime_policy_schema_version=handle.runtime_policy_schema_version,
            runtime_policy_revision=handle.runtime_policy_revision,
            runtime_policy_snapshot=handle.runtime_policy_snapshot,
            invocation_purpose=handle.invocation_purpose,
            subject_kind=handle.subject_kind,
            subject_ref=handle.subject_ref,
            request_artifact_ref=handle.request_artifact_ref,
            execution_key=handle.execution_key,
            prompt_digest=handle.prompt_digest,
            input_digest=handle.input_digest,
            input_content_type=handle.input_content_type,
            input_width=handle.input_width,
            input_height=handle.input_height,
            attempt_ordinal=handle.attempt_ordinal,
            repair_origin_error_codes=list(handle.repair_origin_error_codes),
        )

    def _validate_connection(
        self,
        connection: ProviderConnectionRecord,
        api_key: str,
        routes: list[ModelRouteRecord],
    ) -> None:
        enabled_routes = sorted(
            [route for route in routes if route.enabled], key=lambda route: route.route_id
        )
        if not enabled_routes:
            self.repository.discover_models(connection, api_key)
            return
        for route in enabled_routes:
            self.repository.validate_route(connection, api_key, route)

    def _record_rejection(
        self,
        actor: UserRecord,
        connection_id: str,
        request_id: str,
        message: str,
    ):
        audit = ModelRouteAuditCommand(
            event_type="provider_connection_validation_rejected",
            actor_id=actor.actor_id,
            target_ref=f"provider-connection:{connection_id}",
            message_code=message,
            metadata={"connection_id": connection_id, "request_id": request_id},
        )
        return self.repository.commit_configuration(
            connections=[], secrets=[], routes=[], audits=[audit]
        )[0]

    def _runtime_eligible(self, route: ModelRouteRecord) -> bool:
        connection = self.repository.get_connection(route.connection_id)
        secret = self.repository.get_secret(route.connection_id)
        if (
            connection is None
            or secret is None
            or not connection.enabled
            or connection.status != "verified"
            or connection.provider_type != route.provider_type
        ):
            return False
        try:
            self.repository.decrypt_secret(connection, secret)
        except ModelRoutingError:
            return False
        return True

    @staticmethod
    def _mark_route_ready(route: ModelRouteRecord) -> None:
        route.status = "test_passed"
        route.readiness_schema_name = ROUTE_READINESS_SCHEMA.name
        route.readiness_schema_digest = ROUTE_READINESS_SCHEMA.digest

    @staticmethod
    def _has_current_readiness_proof(route: ModelRouteRecord) -> bool:
        return (
            isinstance(route.runtime_policy, ModelRouteRuntimePolicy)
            and route.readiness_schema_name == ROUTE_READINESS_SCHEMA.name
            and route.readiness_schema_digest == ROUTE_READINESS_SCHEMA.digest
        )

    def _require_admin(self, actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise ModelRoutingError(
                "unauthenticated",
                'auth.please_sign_in_before_using_admin_tools',
                401,
            )
        if not self.repository.is_system_admin(actor):
            raise ModelRoutingError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            )
        return actor

    def _connection(self, connection_id: str) -> ProviderConnectionRecord:
        connection = self.repository.get_connection(connection_id)
        if not connection:
            raise ModelRoutingError(
                "provider_connection_not_found",
                'provider.connection_was_not_found',
                404,
            )
        return connection

    def _route(self, route_id: str) -> ModelRouteRecord:
        route = self.repository.get_route(route_id)
        if not route:
            self._missing("Model route was not found.")
        return route

    def _secret(self, connection: ProviderConnectionRecord):
        secret = self.repository.get_secret(connection.connection_id)
        if not secret:
            raise ModelRoutingError(
                "provider_credential_unavailable",
                'provider.credential_is_unavailable',
                503,
            )
        return secret

    @staticmethod
    def _check_revision(current: int, expected: int) -> None:
        if current != expected:
            raise ModelRoutingError(
                "configuration_revision_conflict",
                'provider.configuration_changed_refresh_and_try_again',
                409,
            )

    @staticmethod
    def _invalid(message: str) -> None:
        raise ModelRoutingError("invalid_request", message, 422)

    @staticmethod
    def _missing(message: str) -> None:
        raise ModelRoutingError("model_route_not_found", message, 404)

    @staticmethod
    def _provider_error(exc: ProviderError) -> None:
        raise ModelRoutingError(exc.code, exc.message_code, 422) from exc

    def _connection_status(
        self,
        connection: ProviderConnectionRecord,
        message_code: str,
        audit_event_ref: str,
        *,
        credential_configured: bool | None = None,
        linked_model_count: int | None = None,
    ) -> ProviderConnectionStatus:
        return ProviderConnectionStatus(
            connection_id=connection.connection_id,
            display_name=connection.display_name,
            provider_type=connection.provider_type,
            endpoint_url=connection.endpoint_url,
            api_version=connection.api_version,
            credential_configured=(
                self.repository.get_secret(connection.connection_id) is not None
                if credential_configured is None
                else credential_configured
            ),
            status=connection.status,
            enabled=connection.enabled,
            linked_model_count=(
                len(self.repository.linked_routes(connection.connection_id))
                if linked_model_count is None
                else linked_model_count
            ),
            revision=connection.revision,
            last_verified_at=connection.last_verified_at,
            last_rotated_at=connection.last_rotated_at,
            message_code=message_code,
            audit_event_ref=audit_event_ref,
        )

    @staticmethod
    def _connection_message(connection: ProviderConnectionRecord) -> str:
        return {
            "credential_required": "provider.api_key_is_required",
            "configured": "provider.connection_is_configured",
            "verified": 'provider.connection_is_verified',
            "verification_failed": "provider.connection_verification_failed",
            "disabled": "provider.connection_is_disabled",
        }[connection.status]

    @staticmethod
    def _route_status(
        route: ModelRouteRecord,
        message_code: str,
        audit_event_ref: str,
    ) -> ModelRouteStatus:
        return ModelRouteStatus(
            route_id=route.route_id,
            display_name=route.display_name,
            provider_type=route.provider_type,
            model_name=route.model_name,
            connection_id=route.connection_id,
            status=route.status,
            message_code=message_code,
            enabled=route.enabled,
            supports_vision=route.supports_vision,
            revision=route.revision,
            runtime_policy=route.runtime_policy,
            audit_event_ref=audit_event_ref,
            is_default=route.is_default,
        )

    @staticmethod
    def _runtime_policy_audit_metadata(route: ModelRouteRecord) -> dict[str, object]:
        payload = asdict(route.runtime_policy)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "runtime_policy": payload,
            "runtime_policy_revision": route.runtime_policy.revision,
            "runtime_policy_digest": digest,
        }
