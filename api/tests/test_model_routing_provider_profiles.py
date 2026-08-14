from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import hashlib

import pytest
from pydantic import ValidationError

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.model_routing.api_models import (
    ModelRouteDefaultRequest,
    ModelRouteTestRequest,
    ProviderConnectionCreateRequest,
    ProviderConnectionUpdateRequest,
)
from atlas_production.modules.model_routing.records import (
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.modules.model_routing.contracts import ModelRoutingError
from atlas_production.modules.model_routing.service import ModelRoutingService
from atlas_production.providers import (
    ProviderError,
    ROUTE_READINESS_SCHEMA,
    normalize_provider_connection,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso
from tests import model_route_runtime_policy


ADMIN = UserRecord("admin-1", "Admin", None, "admin", None)


class ConnectionRepository:
    def __init__(self, connection: ProviderConnectionRecord | None = None) -> None:
        self.connections = (
            {} if connection is None else {connection.connection_id: deepcopy(connection)}
        )
        self.secrets = (
            {}
            if connection is None
            else {
                connection.connection_id: ProviderConnectionSecretRecord(
                    connection_id=connection.connection_id,
                    ciphertext="ciphertext",
                    nonce="nonce",
                    key_id="key-1",
                    version=1,
                )
            }
        )
        self.replays: list[ModelRoutingReplayRecord] = []
        self.routes: dict[str, ModelRouteRecord] = {}
        self.failed_route_ids: set[str] = set()
        self.mutation_scopes: list[list[str]] = []

    def mutation_scope(self, connection_ids: list[str]):
        self.mutation_scopes.append(connection_ids)
        return nullcontext()

    def default_route_scope(
        self, _idempotency_key: str, _route_id: str, _purpose: str
    ):
        return nullcontext()

    def is_system_admin(self, _actor: UserRecord) -> bool:
        return True

    def fingerprint_request(self, canonical_payload: bytes) -> str:
        return hashlib.sha256(canonical_payload).hexdigest()

    def get_replay(self, *_args):
        return None

    def get_connection(self, connection_id: str):
        connection = self.connections.get(connection_id)
        return deepcopy(connection) if connection is not None else None

    def list_connections(self):
        return [deepcopy(item) for item in self.connections.values()]

    def get_secret(self, connection_id: str):
        secret = self.secrets.get(connection_id)
        return deepcopy(secret) if secret is not None else None

    def get_route(self, route_id: str):
        route = self.routes.get(route_id)
        return deepcopy(route) if route is not None else None

    def list_routes(self):
        return [deepcopy(item) for item in self.routes.values()]

    def linked_routes(self, connection_id: str):
        return [
            deepcopy(route)
            for route in self.routes.values()
            if route.connection_id == connection_id
        ]

    def default_route(self, purpose: str):
        route = next(
            (
                route
                for route in self.routes.values()
                if getattr(route, f"is_{purpose}_default")
            ),
            None,
        )
        return deepcopy(route) if route is not None else None

    def discover_models(self, connection, _api_key: str):
        if connection.provider_type in {"azure_openai", "anthropic"}:
            raise ProviderError(
                "provider_discovery_unavailable",
                "model.provider_model_discovery_is_unavailable",
            )
        return []
    def validate_route(self, _connection, _api_key: str, route):
        if route.route_id in self.failed_route_ids:
            raise ProviderError(
                "provider_connection_validation_failed",
                "provider.connection_validation_failed",
            )
        return object()

    def encrypt_secret(
        self,
        *,
        connection_id: str,
        provider_type: str,
        version: int,
        plaintext: str,
    ) -> ProviderConnectionSecretRecord:
        assert provider_type in {
            "openai_compatible",
            "azure_openai",
            "anthropic",
        }
        assert plaintext
        return ProviderConnectionSecretRecord(
            connection_id=connection_id,
            ciphertext="ciphertext",
            nonce="nonce",
            key_id="key-1",
            version=version,
        )

    def decrypt_secret(self, _connection, _secret) -> str:
        return "secret"

    def commit_configuration(
        self,
        *,
        connections,
        secrets,
        routes,
        audits,
        replay_factory=None,
    ):
        for connection in connections:
            self.connections[connection.connection_id] = deepcopy(connection)
        for secret in secrets:
            self.secrets[secret.connection_id] = deepcopy(secret)
        for route in routes:
            self.routes[route.route_id] = deepcopy(route)
        events = [
            AuditEventRecord(
                event_id=f"audit-{index}",
                event_type=audit.event_type,
                actor_id=audit.actor_id,
                target_ref=audit.target_ref,
                project_id=None,
                message_code=audit.message_code,
                metadata=audit.metadata,
                created_at=utc_now_iso(),
            )
            for index, audit in enumerate(audits, start=1)
        ]
        if replay_factory is not None:
            self.replays.append(replay_factory(events))
        return events


def _create_payload(
    provider_type: str,
    endpoint_url: str,
    api_version: str | None,
) -> ProviderConnectionCreateRequest:
    return ProviderConnectionCreateRequest(
        connection_id=f"connection-{provider_type}",
        display_name=provider_type,
        provider_type=provider_type,
        endpoint_url=endpoint_url,
        api_version=api_version,
        api_key="secret",
        idempotency_key=f"create-{provider_type}",
    )


@pytest.mark.parametrize(
    ("provider_type", "endpoint_url", "api_version", "expected_endpoint"),
    [
        (
            "openai_compatible",
            "https://provider.example/custom/v1/chat/completions",
            None,
            "https://provider.example/custom/v1",
        ),
        (
            "azure_openai",
            "https://example.openai.azure.com/",
            " 2024-10-21 ",
            "https://example.openai.azure.com",
        ),
        (
            "anthropic",
            "https://api.anthropic.com/",
            None,
            "https://api.anthropic.com",
        ),
    ],
)
def test_closed_provider_profiles_normalize_connection_fields(
    provider_type: str,
    endpoint_url: str,
    api_version: str | None,
    expected_endpoint: str,
) -> None:
    endpoint, version = normalize_provider_connection(
        provider_type,
        endpoint_url,
        api_version,
    )

    assert endpoint == expected_endpoint
    assert version == ("2024-10-21" if provider_type == "azure_openai" else None)


@pytest.mark.parametrize(
    ("provider_type", "endpoint_url", "api_version"),
    [
        ("azure_openai", "https://example.openai.azure.com", None),
        (
            "azure_openai",
            "https://example.openai.azure.com/openai/v1",
            "2024-10-21",
        ),
        ("openai_compatible", "https://provider.example/v1", "2024-10-21"),
        ("anthropic", "https://api.anthropic.com/v1", None),
        ("anthropic", "https://anthropic.example", None),
    ],
)
def test_provider_profile_connection_fields_fail_closed(
    provider_type: str,
    endpoint_url: str,
    api_version: str | None,
) -> None:
    with pytest.raises(ProviderError) as error:
        normalize_provider_connection(provider_type, endpoint_url, api_version)

    assert error.value.message_code in {
        "provider.connection_fields_are_invalid",
        "provider.endpoint_is_invalid",
    }


def test_provider_type_contract_rejects_unknown_profile() -> None:
    with pytest.raises(ValidationError):
        _create_payload("unknown", "https://provider.example/v1", None)


def test_admin_connection_status_round_trips_azure_version() -> None:
    repository = ConnectionRepository()
    service = ModelRoutingService(repository)

    outcome = service.create_connection(
        ADMIN,
        _create_payload(
            "azure_openai",
            "https://example.openai.azure.com/",
            " 2024-10-21 ",
        ),
    )

    assert outcome.success_status_code == 201
    assert outcome.result.provider_type == "azure_openai"
    assert outcome.result.endpoint_url == "https://example.openai.azure.com"
    assert outcome.result.api_version == "2024-10-21"
    assert outcome.result.credential_configured is True
    assert "secret" not in repr(outcome.result)
    assert repository.connections["connection-azure_openai"].api_version == "2024-10-21"


def test_omitted_update_does_not_clear_azure_version() -> None:
    connection = ProviderConnectionRecord(
        connection_id="connection-azure",
        display_name="Azure",
        provider_type="azure_openai",
        endpoint_url="https://example.openai.azure.com",
        api_version="2024-10-21",
        status="verified",
        enabled=True,
        last_verified_at="2026-08-14T00:00:00Z",
        revision=4,
    )
    repository = ConnectionRepository(connection)
    service = ModelRoutingService(repository)
    payload = ProviderConnectionUpdateRequest(
        display_name="Azure renamed",
        expected_revision=4,
        idempotency_key="update-azure-name",
    )

    outcome = service.update_connection(ADMIN, connection.connection_id, payload)
    assert outcome.result.status == "verified"
    assert outcome.result.enabled is True
    assert outcome.result.last_verified_at == "2026-08-14T00:00:00Z"
    assert "api_version" not in payload.model_fields_set
    assert outcome.result.revision == 5
    assert outcome.result.api_version == "2024-10-21"
    assert repository.connections[connection.connection_id].api_version == "2024-10-21"


@pytest.mark.parametrize(
    ("provider_type", "endpoint_url", "api_version", "updated_api_version"),
    [
        (
            "azure_openai",
            "https://example.openai.azure.com",
            "2024-10-21",
            "2025-01-01",
        ),
        (
            "anthropic",
            "https://api.anthropic.com",
            None,
            None,
        ),
    ],
)
def test_unlinked_manual_profile_update_stays_configured_and_disabled(
    provider_type: str,
    endpoint_url: str,
    api_version: str | None,
    updated_api_version: str | None,
) -> None:
    connection = ProviderConnectionRecord(
        connection_id=f"connection-{provider_type}",
        display_name="Original",
        provider_type=provider_type,
        endpoint_url=endpoint_url,
        api_version=api_version,
        status="verified",
        enabled=True,
        last_verified_at="2026-08-14T00:00:00Z",
        revision=4,
    )
    repository = ConnectionRepository(connection)
    service = ModelRoutingService(repository)

    outcome = service.update_connection(
        ADMIN,
        connection.connection_id,
        ProviderConnectionUpdateRequest(
            display_name="Updated",
            endpoint_url=f"{endpoint_url}/",
            api_version=updated_api_version,
            api_key="rotated-secret",
            enabled=True,
            expected_revision=4,
            idempotency_key=f"update-{provider_type}",
        ),
    )

    stored = repository.connections[connection.connection_id]
    assert outcome.result.status == "configured"
    assert outcome.result.enabled is False
    assert outcome.result.endpoint_url == endpoint_url
    assert outcome.result.api_version == updated_api_version
    assert stored.status == "configured"
    assert stored.enabled is False
    assert stored.api_version == updated_api_version
    assert stored.last_verified_at is None
    assert repository.secrets[connection.connection_id].version == 2


def test_explicit_null_update_rejects_azure_version() -> None:
    connection = ProviderConnectionRecord(
        connection_id="connection-azure",
        display_name="Azure",
        provider_type="azure_openai",
        endpoint_url="https://example.openai.azure.com",
        api_version="2024-10-21",
        status="configured",
        enabled=False,
        revision=4,
    )
    repository = ConnectionRepository(connection)
    service = ModelRoutingService(repository)

    with pytest.raises(ModelRoutingError) as error:
        service.update_connection(
            ADMIN,
            connection.connection_id,
            ProviderConnectionUpdateRequest(
                api_version=None,
                expected_revision=4,
                idempotency_key="clear-azure-version",
            ),
        )

    assert error.value.error_code == "provider_connection_fields_invalid"
    assert repository.connections[connection.connection_id].api_version == "2024-10-21"


def _verified_connection(
    connection_id: str,
    provider_type: str,
) -> ProviderConnectionRecord:
    return ProviderConnectionRecord(
        connection_id=connection_id,
        display_name=connection_id,
        provider_type=provider_type,
        endpoint_url=(
            "https://api.anthropic.com"
            if provider_type == "anthropic"
            else f"https://{connection_id}.example/v1"
        ),
        status="verified",
        enabled=True,
    )


def _ready_route(
    route_id: str,
    connection: ProviderConnectionRecord,
    *,
    is_text_default: bool,
    is_vision_default: bool = False,
    supports_vision: bool = False,
) -> ModelRouteRecord:
    return ModelRouteRecord(
        route_id=route_id,
        display_name=route_id,
        provider_type=connection.provider_type,
        model_name=f"model-{route_id}",
        connection_id=connection.connection_id,
        runtime_policy=ModelRouteRuntimePolicy(
            **model_route_runtime_policy(),
            revision=1,
        ),
        status="test_passed",
        enabled=True,
        supports_vision=supports_vision,
        revision=1,
        is_text_default=is_text_default,
        is_vision_default=is_vision_default,
        readiness_schema_name=ROUTE_READINESS_SCHEMA.name,
        readiness_schema_digest=ROUTE_READINESS_SCHEMA.digest,
    )


@pytest.mark.parametrize("fallback_provider_type", ["openai_compatible", "anthropic"])
def test_failed_default_does_not_select_or_rewrite_another_ready_route(
    fallback_provider_type: str,
) -> None:
    default_connection = _verified_connection(
        "connection-default",
        "openai_compatible",
    )
    fallback_connection = _verified_connection(
        "connection-fallback",
        fallback_provider_type,
    )
    repository = ConnectionRepository(default_connection)
    repository.connections[fallback_connection.connection_id] = fallback_connection
    repository.secrets[fallback_connection.connection_id] = (
        ProviderConnectionSecretRecord(
            connection_id=fallback_connection.connection_id,
            ciphertext="ciphertext",
            nonce="nonce",
            key_id="key-1",
            version=1,
        )
    )
    default_route = _ready_route(
        "route-default",
        default_connection,
        is_text_default=True,
    )
    fallback_route = _ready_route(
        "route-fallback",
        fallback_connection,
        is_text_default=False,
        supports_vision=True,
    )
    repository.routes = {
        default_route.route_id: default_route,
        fallback_route.route_id: fallback_route,
    }
    repository.failed_route_ids.add(default_route.route_id)
    service = ModelRoutingService(repository)

    failed = service.test_route(
        ADMIN,
        default_route.route_id,
        ModelRouteTestRequest(
            expected_revision=1,
            idempotency_key="test-default-route",
        ),
    )

    assert failed.result.status == "test_failed"
    assert repository.routes[default_route.route_id].is_text_default is True
    assert repository.routes[fallback_route.route_id].is_text_default is False
    assert repository.mutation_scopes[-1] == [
        default_connection.connection_id,
        "idempotency:test-default-route",
    ]

    selected = service.set_default(
        ADMIN,
        fallback_route.route_id,
        "text",
        ModelRouteDefaultRequest(
            expected_revision=1,
            idempotency_key="select-fallback-route",
        ),
    )

    assert selected.result.is_text_default is True
    assert repository.routes[default_route.route_id].is_text_default is False
    assert repository.routes[fallback_route.route_id].is_text_default is True



def test_text_and_vision_defaults_are_independent_and_may_share_a_route() -> None:
    connection = _verified_connection("connection-shared", "openai_compatible")
    repository = ConnectionRepository(connection)
    route_a = _ready_route(
        "route-a",
        connection,
        is_text_default=True,
        supports_vision=True,
    )
    route_b = _ready_route(
        "route-b",
        connection,
        is_text_default=False,
        supports_vision=True,
    )
    repository.routes = {route.route_id: route for route in (route_a, route_b)}
    service = ModelRoutingService(repository)

    service.set_default(
        ADMIN,
        route_b.route_id,
        "text",
        ModelRouteDefaultRequest(
            expected_revision=1,
            idempotency_key="select-text-b",
        ),
    )
    service.set_default(
        ADMIN,
        route_a.route_id,
        "vision",
        ModelRouteDefaultRequest(
            expected_revision=1,
            idempotency_key="select-vision-a",
        ),
    )
    listed = service.list_routes(ADMIN)
    assert listed.text_default_route_id == route_b.route_id
    assert listed.vision_default_route_id == route_a.route_id

    service.set_default(
        ADMIN,
        route_b.route_id,
        "vision",
        ModelRouteDefaultRequest(
            expected_revision=1,
            idempotency_key="select-vision-b",
        ),
    )
    assert repository.routes[route_b.route_id].is_text_default is True
    assert repository.routes[route_b.route_id].is_vision_default is True
    assert repository.routes[route_a.route_id].is_text_default is False
    assert repository.routes[route_a.route_id].is_vision_default is False
    assert repository.routes[route_a.route_id].revision == 1
    assert repository.routes[route_b.route_id].revision == 1


@pytest.mark.parametrize(
    ("status", "readiness_schema_name", "readiness_schema_digest", "error_code"),
    [
        (
            "test_passed",
            ROUTE_READINESS_SCHEMA.name,
            ROUTE_READINESS_SCHEMA.digest,
            "invalid_request",
        ),
        ("configured", None, None, "invalid_request"),
        (
            "test_passed",
            ROUTE_READINESS_SCHEMA.name,
            "stale-digest",
            "invalid_request",
        ),
    ],
)
def test_invalid_vision_default_selection_is_typed_and_atomic(
    status: str,
    readiness_schema_name: str | None,
    readiness_schema_digest: str | None,
    error_code: str,
) -> None:
    connection = _verified_connection("connection-vision", "openai_compatible")
    repository = ConnectionRepository(connection)
    text_route = _ready_route(
        "route-text",
        connection,
        is_text_default=True,
    )
    vision_route = _ready_route(
        "route-vision",
        connection,
        is_text_default=False,
        is_vision_default=True,
        supports_vision=True,
    )
    candidate = _ready_route(
        "route-candidate",
        connection,
        is_text_default=False,
        supports_vision=status != "test_passed" or readiness_schema_digest == "stale-digest",
    )
    candidate.status = status
    candidate.readiness_schema_name = readiness_schema_name
    candidate.readiness_schema_digest = readiness_schema_digest
    repository.routes = {
        route.route_id: route for route in (text_route, vision_route, candidate)
    }
    service = ModelRoutingService(repository)

    with pytest.raises(ModelRoutingError) as raised:
        service.set_default(
            ADMIN,
            candidate.route_id,
            "vision",
            ModelRouteDefaultRequest(
                expected_revision=1,
                idempotency_key=f"reject-{status}-{readiness_schema_digest}",
            ),
        )

    assert raised.value.error_code == error_code
    assert raised.value.status_code == 422
    assert repository.routes[text_route.route_id].is_text_default is True
    assert repository.routes[vision_route.route_id].is_vision_default is True
    assert repository.routes[candidate.route_id].revision == 1