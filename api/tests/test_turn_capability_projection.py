from __future__ import annotations

from datetime import datetime, timedelta, timezone

from atlas_production.infrastructure.turn_capability_projection import (
    project_turn_model_capabilities,
)
from atlas_production.modules.retrieval.public import (
    DocumentNavigationResultV1,
    EvidenceDescriptorV1,
    KnowledgeCatalogPageV1,
    KnowledgeDocumentDescriptorV1,
    KnowledgeInspectionItemV1,
    KnowledgeInspectionResultV1,
    KnowledgeSearchResultV1,
    NavigationTargetV1,
    RelevantDocumentCandidateV1,
    RelevantDocumentDiscoveryResultV1,
    VisualInspectionResultV1,
)
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    RoutePolicyV1,
)
from tests.turn_runtime_fixtures import route_snapshot


NOW = datetime.now(timezone.utc)


def _budget(**changes: int) -> BudgetSnapshotV1:
    values = {
        "tool_invocations": 0,
        "catalog_pages": 0,
        "document_candidates": 0,
        "search_rounds": 0,
        "model_visible_items": 0,
        "provider_invocations": 0,
        "context_tokens": 0,
        "tool_tokens": 0,
        "retrieval_repairs": 0,
        "schema_retries": 0,
    }
    values.update(changes)
    return BudgetSnapshotV1(**values)


def _snapshot(
    *,
    budget: BudgetSnapshotV1 | None = None,
    policy: RoutePolicyV1 | None = None,
) -> ExecutionSnapshotV1:
    return ExecutionSnapshotV1(
        execution_id="execution-1",
        turn_id="turn-1",
        conversation_id="conversation-1",
        actor_id="actor-1",
        state="awaiting_model_action",
        version=4,
        policy=policy or RoutePolicyV1(max_retrieval_repairs=1),
        route=route_snapshot(),
        input_digest="0" * 64,
        response_language="zh-TW",
        applied_guidance_revision=0,
        applied_guidance_digest=None,
        lease=ExecutionLeaseV1(
            execution_id="execution-1",
            holder_id="worker-1",
            lease_version=1,
            fencing_token=1,
            acquired_at=NOW,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
        budget=budget or _budget(),
        grant_ref="grant-1",
        catalog_ref="catalog-1",
        context_pack_ref="context-1",
        deadline_at=NOW + timedelta(minutes=2),
        created_at=NOW,
        updated_at=NOW,
    )


def _search_observation() -> KnowledgeSearchResultV1:
    return KnowledgeSearchResultV1(
        result_type="knowledge_search_result",
        evidence=[
            EvidenceDescriptorV1(
                evidence_handle="kh_evidence_A",
                document_handle="kh_document_A",
                document_display_name="Policy A.pdf",
                locator_label="p. 12",
                snippet="The retention period is seven years.",
                modalities=["text"],
                page_handle="kh_page_A",
                page_number=12,
            )
        ],
        next_cursor=None,
    )


def test_initial_projection_exposes_complete_semantics_without_guessable_handles() -> None:
    result = project_turn_model_capabilities(
        _snapshot(),
        catalog_document_count=2,
        observations=[],
        contract_repair_remaining=1,
    )

    assert result.allowed_actions == [
        "list_knowledge_documents",
        "find_knowledge_documents",
        "discover_relevant_documents",
        "finalize_answer",
    ]
    assert result.documents == []
    assert result.evidence == []
    assert result.visuals == []
    assert result.catalog_wide_search_allowed is False
    assert result.allowed_modalities == ["text", "table", "figure"]
    assert result.limits.max_search_limit == 0
    assert result.limits.max_discovery_limit == 20
    assert result.limits.max_output_tokens == 64_000


def test_discovery_candidates_become_selection_only_document_options() -> None:
    discovery = RelevantDocumentDiscoveryResultV1(
        result_type="relevant_document_discovery_result",
        candidates=[
            RelevantDocumentCandidateV1(
                document_handle="kh_document_discovered",
                document_display_name="Policy.pdf",
                media_type="application/pdf",
                modalities=["text"],
                preview="Sensitive selection preview",
                locator_label="Policy.pdf · p. 3",
                page_number=3,
            )
        ],
        ranking_contract="equal-reciprocal-rank-v1",
        channels=["lexical", "vector"],
        degraded=False,
        vector_coverage=2,
        catalog_document_count=2,
        truncated_by_budget=False,
    )

    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(catalog_pages=1, document_candidates=1)),
        catalog_document_count=2,
        observations=[discovery],
        contract_repair_remaining=1,
    )

    assert "search_knowledge" in result.allowed_actions
    assert [item.document_handle for item in result.documents] == [
        "kh_document_discovered"
    ]
    assert result.documents[0].display_name == "Policy.pdf"
    assert "Sensitive selection preview" not in result.model_dump_json()


def test_disclosed_documents_can_accumulate_beyond_twenty_candidates() -> None:
    catalogs = [
        KnowledgeCatalogPageV1(
            result_type="knowledge_catalog_page",
            documents=[
                KnowledgeDocumentDescriptorV1(
                    document_handle=f"kh_document_{page}_{index:02d}",
                    display_name=f"Candidate {page}-{index:02d}.pdf",
                    media_type="application/pdf",
                    modalities=["text"],
                    tags=[],
                    version_label=None,
                )
                for index in range(10)
            ],
            next_cursor=f"cursor-{page}" if page < 2 else None,
        )
        for page in range(3)
    ]
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(catalog_pages=3, document_candidates=30)),
        catalog_document_count=30,
        observations=catalogs,
        contract_repair_remaining=1,
    )

    assert "list_knowledge_documents" in result.allowed_actions
    assert "find_knowledge_documents" in result.allowed_actions
    assert "search_knowledge" in result.allowed_actions
    assert result.limits.max_page_size == 10
    assert result.limits.max_search_limit == 20
    assert len(result.documents) == 30


def test_candidate_count_does_not_close_scoped_search_or_expansion() -> None:
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(document_candidates=20, model_visible_items=2)),
        catalog_document_count=20,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert "search_knowledge" in result.allowed_actions
    assert "inspect_knowledge" in result.allowed_actions
    assert "expand_knowledge" in result.allowed_actions
    assert result.limits.max_search_limit == 19
    assert result.limits.max_expand_limit == 19


def test_admitted_tool_projects_configured_max_not_remaining_budget() -> None:
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(tool_tokens=4_000, model_visible_items=2)),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert result.limits.max_output_tokens == 64_000
    assert "inspect_visual" in result.allowed_actions


def test_prior_usage_below_threshold_still_projects_full_configured_max() -> None:
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(tool_tokens=49_054, model_visible_items=2)),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert result.limits.max_output_tokens == 64_000


def test_prior_usage_at_threshold_closes_all_tool_actions() -> None:
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(tool_tokens=64_000, model_visible_items=2)),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert result.allowed_actions == ["finalize_answer"]
    assert result.limits.max_output_tokens == 0


def test_visual_action_closes_when_model_visible_items_budget_is_exhausted() -> None:
    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(model_visible_items=2)),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert "inspect_visual" in result.allowed_actions

    result = project_turn_model_capabilities(
        _snapshot(
            budget=_budget(model_visible_items=2),
        ).model_copy(
            update={
                    "policy": RoutePolicyV1(
                        max_model_visible_items_per_turn=2,
                        max_retrieval_repairs=1,
                    ),
            }
        ),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=1,
    )

    assert "inspect_visual" not in result.allowed_actions


def test_observations_accumulate_stable_semantic_document_and_evidence_choices() -> None:
    catalog = KnowledgeCatalogPageV1(
        result_type="knowledge_catalog_page",
        documents=[
            KnowledgeDocumentDescriptorV1(
                document_handle="kh_document_A",
                display_name="Policy A.pdf",
                media_type="application/pdf",
                modalities=["text", "table"],
                tags=["policy"],
                version_label="2026",
            )
        ],
        next_cursor=None,
    )
    inspection = KnowledgeInspectionResultV1(
        result_type="knowledge_inspection_result",
        items=[
            KnowledgeInspectionItemV1(
                evidence_handle="kh_evidence_A",
                document_handle="kh_document_A",
                document_display_name="Policy A.pdf",
                locator_label="p. 12",
                content="The complete inspected passage.",
                modalities=["text"],
            )
        ],
    )
    observations = [catalog, _search_observation(), inspection]

    first = project_turn_model_capabilities(
        _snapshot(budget=_budget(model_visible_items=2)),
        catalog_document_count=2,
        observations=observations,
        contract_repair_remaining=1,
    )
    second = project_turn_model_capabilities(
        _snapshot(budget=_budget(model_visible_items=2)),
        catalog_document_count=2,
        observations=observations,
        contract_repair_remaining=1,
    )

    assert first == second
    assert first.documents[0].model_dump() == {
        "document_handle": "kh_document_A",
        "display_name": "Policy A.pdf",
        "media_type": "application/pdf",
        "modalities": ["text", "table"],
        "tags": ["policy"],
        "version_label": "2026",
    }
    assert first.evidence[0].snippet == "The complete inspected passage."
    assert {"inspect_knowledge", "inspect_visual", "expand_knowledge"}.issubset(
        first.allowed_actions
    )
    assert first.visuals[0].handle == "kh_page_A"


def test_visual_observation_adds_recursive_visual_handle() -> None:
    visual = VisualInspectionResultV1(
        result_type="visual_inspection_result",
        visual_handle="kh_visual_A",
        source_handle="kh_page_A",
        page_handle="kh_page_A",
        document_handle="kh_document_A",
        page_number=12,
        scope="rect",
        bbox={"left": 100, "top": 200, "right": 9_000, "bottom": 8_000},
        image_ref="image:abc",
        image_digest="a" * 64,
        width=800,
        height=600,
    )

    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(model_visible_items=3)), catalog_document_count=1,
        observations=[_search_observation(), visual],
        contract_repair_remaining=1,
    )

    assert [item.handle for item in result.visuals] == ["kh_page_A", "kh_visual_A"]
    assert "inspect_visual" in result.allowed_actions


def test_navigation_observation_adds_location_and_page_without_evidence() -> None:
    catalog = KnowledgeCatalogPageV1(
        result_type="knowledge_catalog_page",
        documents=[
            KnowledgeDocumentDescriptorV1(
                document_handle="kh_document_A",
                display_name="Chip.pdf",
                media_type="application/pdf",
                modalities=["text", "figure"],
                tags=[],
                version_label=None,
            )
        ],
        next_cursor=None,
    )
    navigation = DocumentNavigationResultV1(
        result_type="document_navigation_result",
        mode="search",
        map_digest="a" * 64,
        targets=[
            NavigationTargetV1(
                navigation_handle="kh_navigation_A",
                document_handle="kh_document_A",
                document_display_name="Chip.pdf",
                kind="figure",
                label="Figure 1. Pin Assignments",
                structure_path=["第 9 頁", "Figure 1. Pin Assignments"],
                page_number=9,
                content_traits=["figure"],
                page_handle="kh_page_9",
            )
        ],
        next_cursor=None,
    )

    result = project_turn_model_capabilities(
        _snapshot(budget=_budget(model_visible_items=2)),
        catalog_document_count=1,
        observations=[catalog, navigation],
        contract_repair_remaining=1,
    )

    assert result.navigation[0].navigation_handle == "kh_navigation_A"
    assert result.visuals[0].handle == "kh_page_9"
    assert result.evidence == []
    assert {"navigate_document", "inspect_visual"}.issubset(result.allowed_actions)


def test_budget_exhaustion_closes_tool_choices_but_preserves_surfaced_semantics() -> None:
    exhausted = _budget(
        tool_invocations=12,
        tool_tokens=32000,
        model_visible_items=2,
        retrieval_repairs=1,
    )
    result = project_turn_model_capabilities(
        _snapshot(budget=exhausted),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=0,
    )

    assert result.allowed_actions == ["finalize_answer"]
    assert result.limits.model_dump() == {
        "max_page_size": 0,
        "max_discovery_limit": 0,
        "max_search_limit": 0,
        "max_expand_limit": 0,
        "max_expand_anchor_handles": 0,
        "max_navigation_limit": 0,
        "max_output_tokens": 0,
    }
    assert result.evidence[0].evidence_handle == "kh_evidence_A"
    assert result.contract_repair_remaining == 0


def test_repair_state_is_bound_into_capability_digest() -> None:
    before = project_turn_model_capabilities(
        _snapshot(), catalog_document_count=1, observations=[], contract_repair_remaining=1
    )
    after = project_turn_model_capabilities(
        _snapshot(budget=_budget(retrieval_repairs=1)),
        catalog_document_count=1,
        observations=[],
        contract_repair_remaining=0,
    )

    assert before.digest != after.digest


def test_policy_projects_independent_expand_anchor_cardinality_and_repair_remaining() -> None:
    policy = RoutePolicyV1(
        max_retrieval_repairs=3,
        max_selected_anchor_pages_per_round=7,
    )
    result = project_turn_model_capabilities(
        _snapshot(
            policy=policy,
            budget=_budget(retrieval_repairs=1, model_visible_items=2),
        ),
        catalog_document_count=2,
        observations=[_search_observation()],
        contract_repair_remaining=2,
    )

    assert result.contract_repair_remaining == 2
    assert result.limits.max_expand_anchor_handles == 7
    assert result.limits.max_expand_limit == 19
