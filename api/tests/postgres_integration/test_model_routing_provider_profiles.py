from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import delete, select

from atlas_production.infrastructure.persistence.model_routing import (
    AtlasModelRouteRow,
    AtlasProviderConnectionRow,
    AtlasProviderConnectionSecretRow,
    runtime_joined_snapshot,
)
from atlas_production.modules.model_routing.records import (
    ModelRouteRuntimePolicy,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.providers import ROUTE_READINESS_SCHEMA
from tests import model_route_runtime_policy


CONNECTION_IDS = (
    "profile-roundtrip-openai",
    "profile-roundtrip-azure",
    "profile-roundtrip-anthropic",
)
ROUTING_CONNECTION_IDS = (
    "fixed-default-connection-a",
    "fixed-default-connection-b",
    "fixed-default-connection-c",
)
ROUTING_ROUTE_IDS = (
    "fixed-default-route-a",
    "fixed-default-route-b",
    "fixed-default-route-c",
)


def test_fresh_baseline_round_trips_closed_provider_profiles_and_azure_version(
    postgres_runtime,
) -> None:
    postgres_runtime.bootstrap_schema()
    records = [
        ProviderConnectionRecord(
            connection_id=CONNECTION_IDS[0],
            display_name="OpenAI compatible",
            provider_type="openai_compatible",
            endpoint_url="https://provider.example/v1",
            api_version=None,
        ),
        ProviderConnectionRecord(
            connection_id=CONNECTION_IDS[1],
            display_name="Azure OpenAI",
            provider_type="azure_openai",
            endpoint_url="https://example.openai.azure.com",
            api_version="2024-10-21",
        ),
        ProviderConnectionRecord(
            connection_id=CONNECTION_IDS[2],
            display_name="Anthropic",
            provider_type="anthropic",
            endpoint_url="https://api.anthropic.com",
            api_version=None,
        ),
    ]

    try:
        with postgres_runtime.session_factory() as session, session.begin():
            session.add_all(
                AtlasProviderConnectionRow(**asdict(record)) for record in records
            )

        with postgres_runtime.session_factory() as session:
            rows = session.scalars(
                select(AtlasProviderConnectionRow)
                .where(AtlasProviderConnectionRow.connection_id.in_(CONNECTION_IDS))
                .order_by(AtlasProviderConnectionRow.connection_id)
            ).all()

        observed = {
            row.provider_type: (row.endpoint_url, row.api_version) for row in rows
        }
        assert observed == {
            "openai_compatible": ("https://provider.example/v1", None),
            "azure_openai": (
                "https://example.openai.azure.com",
                "2024-10-21",
            ),
            "anthropic": ("https://api.anthropic.com", None),
        }
    finally:
        with postgres_runtime.session_factory() as session, session.begin():
            session.execute(
                delete(AtlasProviderConnectionRow).where(
                    AtlasProviderConnectionRow.connection_id.in_(CONNECTION_IDS)
                )
            )


def test_route_less_snapshot_never_falls_back_until_admin_changes_default(
    postgres_runtime,
) -> None:
    postgres_runtime.bootstrap_schema()
    connections = [
        ProviderConnectionRecord(
            connection_id=ROUTING_CONNECTION_IDS[0],
            display_name="Failed default",
            provider_type="openai_compatible",
            endpoint_url="https://provider-a.example/v1",
            status="verified",
            enabled=True,
        ),
        ProviderConnectionRecord(
            connection_id=ROUTING_CONNECTION_IDS[1],
            display_name="Same-provider candidate",
            provider_type="openai_compatible",
            endpoint_url="https://provider-b.example/v1",
            status="verified",
            enabled=True,
        ),
        ProviderConnectionRecord(
            connection_id=ROUTING_CONNECTION_IDS[2],
            display_name="Cross-provider candidate",
            provider_type="anthropic",
            endpoint_url="https://api.anthropic.com",
            status="verified",
            enabled=True,
        ),
    ]
    secrets = [
        ProviderConnectionSecretRecord(
            connection_id=connection_id,
            ciphertext=f"ciphertext-{index}",
            nonce=f"nonce-{index}",
            key_id="key-1",
            version=1,
        )
        for index, connection_id in enumerate(ROUTING_CONNECTION_IDS, start=1)
    ]
    policy = asdict(
        ModelRouteRuntimePolicy(
            **model_route_runtime_policy(),
            revision=1,
        )
    )
    routes = [
        AtlasModelRouteRow(
            route_id=ROUTING_ROUTE_IDS[0],
            display_name="Failed default",
            provider_type="openai_compatible",
            model_name="model-a",
            connection_id=ROUTING_CONNECTION_IDS[0],
            status="test_failed",
            enabled=True,
            revision=2,
            runtime_policy=policy,
            supports_vision=False,
            last_tested_at="2026-08-09T00:00:00+00:00",
            is_default=True,
            readiness_schema_name=None,
            readiness_schema_digest=None,
        ),
        AtlasModelRouteRow(
            route_id=ROUTING_ROUTE_IDS[1],
            display_name="Same-provider candidate",
            provider_type="openai_compatible",
            model_name="model-b",
            connection_id=ROUTING_CONNECTION_IDS[1],
            status="test_passed",
            enabled=True,
            revision=1,
            runtime_policy=policy,
            supports_vision=False,
            last_tested_at="2026-08-09T00:00:00+00:00",
            is_default=False,
            readiness_schema_name=ROUTE_READINESS_SCHEMA.name,
            readiness_schema_digest=ROUTE_READINESS_SCHEMA.digest,
        ),
        AtlasModelRouteRow(
            route_id=ROUTING_ROUTE_IDS[2],
            display_name="Cross-provider candidate",
            provider_type="anthropic",
            model_name="model-c",
            connection_id=ROUTING_CONNECTION_IDS[2],
            status="test_passed",
            enabled=True,
            revision=1,
            runtime_policy=policy,
            supports_vision=False,
            last_tested_at="2026-08-09T00:00:00+00:00",
            is_default=False,
            readiness_schema_name=ROUTE_READINESS_SCHEMA.name,
            readiness_schema_digest=ROUTE_READINESS_SCHEMA.digest,
        ),
    ]

    try:
        with postgres_runtime.session_factory() as session, session.begin():
            session.add_all(
                AtlasProviderConnectionRow(**asdict(connection))
                for connection in connections
            )
            session.flush()
            session.add_all(
                AtlasProviderConnectionSecretRow(**asdict(secret))
                for secret in secrets
            )
            session.flush()
            session.add_all(routes)

        with postgres_runtime.session_factory() as session:
            assert runtime_joined_snapshot(session) is None
            same_provider = runtime_joined_snapshot(session, ROUTING_ROUTE_IDS[1])
            cross_provider = runtime_joined_snapshot(session, ROUTING_ROUTE_IDS[2])
            assert same_provider is not None
            assert same_provider[0].route_id == ROUTING_ROUTE_IDS[1]
            assert cross_provider is not None
            assert cross_provider[0].route_id == ROUTING_ROUTE_IDS[2]

        with postgres_runtime.session_factory() as session, session.begin():
            failed_default = session.get(AtlasModelRouteRow, ROUTING_ROUTE_IDS[0])
            selected = session.get(AtlasModelRouteRow, ROUTING_ROUTE_IDS[1])
            assert failed_default is not None
            assert selected is not None
            failed_default.is_default = False
            selected.is_default = True

        with postgres_runtime.session_factory() as session:
            selected = runtime_joined_snapshot(session)
            assert selected is not None
            assert selected[0].route_id == ROUTING_ROUTE_IDS[1]
    finally:
        with postgres_runtime.session_factory() as session, session.begin():
            session.execute(
                delete(AtlasModelRouteRow).where(
                    AtlasModelRouteRow.route_id.in_(ROUTING_ROUTE_IDS)
                )
            )
            session.execute(
                delete(AtlasProviderConnectionSecretRow).where(
                    AtlasProviderConnectionSecretRow.connection_id.in_(
                        ROUTING_CONNECTION_IDS
                    )
                )
            )
            session.execute(
                delete(AtlasProviderConnectionRow).where(
                    AtlasProviderConnectionRow.connection_id.in_(
                        ROUTING_CONNECTION_IDS
                    )
                )
            )
