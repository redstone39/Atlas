from __future__ import annotations

import base64
import os


TEST_PROVIDER_CONNECTION_ID = "conn-test-provider"
TEST_PROVIDER_KEY = "test-provider-key"
TEST_MASTER_KEY = base64.b64encode(bytes(range(32))).decode("ascii")

os.environ.setdefault("ATLAS_CREDENTIAL_MASTER_KEY", TEST_MASTER_KEY)
os.environ.setdefault("ATLAS_CREDENTIAL_MASTER_KEY_ID", "test-master-key")


def model_route_runtime_policy(**overrides):
    policy = {
        "schema_version": "model-route-runtime-policy-v7",
        "tokenizer_profile": "cl100k_base",
        "max_tool_executions": 12,
        "max_provider_invocations": 26,
        "max_reasoning_revision_cycles": 2,
        "max_catalog_pages": 5,
        "max_search_rounds": 6,
        "max_unique_evidence": 40,
        "max_retrieval_repairs": 3,
        "max_schema_retries_per_turn": 3,
        "max_selected_anchor_pages_per_round": 20,
        "provider_invocation_timeout_seconds": 60,
        "tool_execution_timeout_seconds": 45,
        "turn_timeout_seconds": 240,
        "context_window_tokens": 400_000,
        "max_input_tokens_per_invocation": 272_000,
        "max_output_tokens_per_invocation": 16_000,
        "max_tool_result_tokens_per_execution": 64_000,
        "max_total_tokens_per_conversation": 1_000_000,
    }
    policy.update(overrides)
    return policy


def install_test_provider_connection(store, connection_id: str = TEST_PROVIDER_CONNECTION_ID):
    from atlas_production.infrastructure.provider_key_cipher import AesGcmCredentialCipher
    from atlas_production.modules.model_routing.records import ProviderConnectionRecord

    connection = ProviderConnectionRecord(
        connection_id=connection_id,
        display_name="Test Provider",
        provider_type="openai_compatible",
        endpoint_url="https://provider.example/v1",
        status="verified",
        enabled=True,
    )
    store.provider_connections[connection_id] = connection
    store.provider_connection_secrets[connection_id] = (
        AesGcmCredentialCipher.from_environment().encrypt(
            connection_id=connection_id,
            provider_type=connection.provider_type,
            secret_version=1,
            plaintext=TEST_PROVIDER_KEY,
        )
    )
    return connection
