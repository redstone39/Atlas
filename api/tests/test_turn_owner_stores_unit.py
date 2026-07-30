from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from atlas_production.infrastructure.postgres_owner.authorization import CreateGrantInput
from atlas_production.infrastructure.postgres_owner.context_engineering import (
    ContextMessageInput,
    MaterializeContextInput,
    RecentExchangeInput,
    SourceLineageInput,
    SummaryInput,
    SummarySourceInput,
    _semantic_payload,
)
from atlas_production.infrastructure.postgres_owner.conversation_v1 import CreateConversationInput
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    PersistInvocationResultInput,
)


OWNER_DIR = Path(__file__).parents[1] / "src/atlas_production/infrastructure/postgres_owner"
STORE_FILES = (
    "conversation_v1.py",
    "authorization.py",
    "context_engineering.py",
    "retrieval_v1.py",
)


@pytest.mark.parametrize("filename", STORE_FILES)
def test_turn_owner_store_has_no_cross_owner_collaborator_import(filename: str) -> None:
    tree = ast.parse((OWNER_DIR / filename).read_text())
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(module.startswith("atlas_production.modules.") for module in imports)
    persistence_imports = {
        module for module in imports if ".infrastructure.persistence." in module
    }
    expected_owner = filename.removesuffix("_v1.py").removesuffix(".py")
    assert persistence_imports == {
        f"atlas_production.infrastructure.persistence.{expected_owner}"
    }


def test_owner_inputs_are_typed_immutable_values() -> None:
    command = CreateConversationInput(
        "conversation-1", "actor-1", "Title", "key-1", "zh-TW"
    )
    with pytest.raises(FrozenInstanceError):
        command.title = "changed"  # type: ignore[misc]
    assert CreateGrantInput.__dataclass_fields__["schema_version"].default == "turn-access-grant-v1"


def test_context_materialization_payload_preserves_full_multi_resource_lineage() -> None:
    command = MaterializeContextInput(
        context_pack_ref="context-pack-1",
        execution_id="execution-1",
        input_projection_ref="input-projection-1",
        conversation_id="conversation-1",
        dependent_turn_id="turn-current",
        model_user_input="Compare the documents",
        recent_tail=(
            RecentExchangeInput(
                "root-2",
                "turn-2",
                "a" * 64,
                ContextMessageInput("user", "question"),
                ContextMessageInput("assistant", "prior", "verified"),
            ),
        ),
        summary=SummaryInput(
            "summary-1",
            None,
            "older",
            2,
            (
                SummarySourceInput("root-0", "turn-0", "b" * 64),
                SummarySourceInput("root-1", "turn-1", "c" * 64),
            ),
        ),
        source_lineage=(
            SourceLineageInput("turn-2", None, "turn", "recent_turn"),
            SourceLineageInput("turn-0", "summary-1", "summary", "summary_source"),
            SourceLineageInput(
                "turn-1", "document-version-a", "document", "knowledge_hint", 3,
                "document-version-a", "index-generation-a",
            ),
            SourceLineageInput(
                "turn-1", "evidence-a", "evidence", "knowledge_hint", 3,
                "document-version-a", "index-generation-a",
            ),
        ),
        token_budget=16000,
        idempotency_key="context-key-1",
    )
    payload = _semantic_payload(command)
    lineage = payload["source_lineage"]
    assert isinstance(lineage, list)
    assert {edge["source_resource_kind"] for edge in lineage} == {
        "turn", "summary", "document", "evidence"
    }
    assert payload["schema_version"] == "context-pack-v3"


def test_retrieval_replay_input_carries_action_and_schema_identity() -> None:
    command = PersistInvocationResultInput(
        invocation_id="invocation-1",
        result_ref="result-1",
        execution_id="execution-1",
        catalog_ref="catalog-1",
        invocation_ordinal=1,
        action="search_knowledge",
        schema_version="search-knowledge-v1",
        canonical_arguments={"action": "search_knowledge", "query_text": "alpha"},
        result_type="knowledge_search_result",
        observation={"result_type": "knowledge_search_result", "evidence": [], "next_cursor": None},
        error_code=None,
    )
    assert command.canonical_arguments["action"] == command.action
    assert command.schema_version == "search-knowledge-v1"
