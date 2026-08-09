from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from atlas_production.infrastructure.persistence.audit_events import (
    _audit_event_payload,
    _audit_metadata_payload,
)
from atlas_production.infrastructure.persistence.base import OrmBase
from atlas_production.infrastructure.persistence import schema as _schema  # noqa: F401
from atlas_production.infrastructure.persistence.model_routing import (
    _model_invocation_payload,
    _model_invocation_record,
    _model_route_payload,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    _processing_payload,
)
from atlas_production.infrastructure.persistence.payload_policy import (
    JSONB_PAYLOAD_REGISTRY,
    PersistedPayloadPolicyError,
    serialize_typed_dataclass,
    validate_typed_payload,
)
from atlas_production.infrastructure.persistence.result_governance import (
    _assessment_payload,
    _claim_evidence_payload,
    _valid_persistable_claim_graph,
    _segment_payload,
)
from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
)
from atlas_production.modules.processing_pipeline.records import (
    ProcessingIdempotencyRecord,
)
from atlas_production.modules.result_governance.records import (
    ClaimEvidenceLink,
    ClaimRecord,
    ClaimSupportAssessment,
    ResponseSegmentRecord,
)
from atlas_production.modules.turn_runtime.public import SchemaRetryOriginCode
from atlas_production.shared.public import AuditEventRecord


def test_named_serializers_externalize_authoritative_governance_content() -> None:
    segment = ResponseSegmentRecord(
        segment_id="segment-1",
        assistant_turn_id="turn-1",
        kind="controlled",
        segment_canonical_text="complete canonical segment",
        canonical_text_digest="b" * 64,
        normalization_version="nfc-v1",
        created_at="2026-07-13T00:00:00+00:00",
        artifact_id="artifact-segment",
    )
    persisted_segment = _segment_payload(segment)
    assert persisted_segment["segment_canonical_text"] == ""
    assert persisted_segment["artifact_id"] == "artifact-segment"

    link = ClaimEvidenceLink(
        link_id="link-1",
        claim_id="claim-1",
        evidence_id="evidence-1",
        relation="supports",
        support_scope="complete canonical claim text",
        evidence_stance_summary="complete evidence text",
        conflict_field="canonical field text",
        confidence_score=None,
        confidence_method=None,
        calibration_reference=None,
        created_at="2026-07-13T00:00:00+00:00",
    )
    persisted_link = _claim_evidence_payload(link)
    assert persisted_link["support_scope"] is None
    assert persisted_link["evidence_stance_summary"] == ""
    assert persisted_link["conflict_field"] is None

    assessment = ClaimSupportAssessment(
        claim_id="claim-1",
        status="supported",
        supported_scope="complete canonical claim text",
        unsupported_scope=None,
        conflict_summary=None,
        assessment_method="claim-graph-v1",
        assessment_version="1",
        created_at="2026-07-13T00:00:00+00:00",
    )
    persisted_assessment = _assessment_payload(assessment)
    assert persisted_assessment["supported_scope"] is None
    assert persisted_assessment["unsupported_scope"] is None
    assert persisted_assessment["conflict_summary"] is None


def test_persistable_claim_graph_accepts_restarted_and_fresh_turns_together() -> None:
    fresh_text = "The board family is Atlas-One."
    fresh_digest = __import__("hashlib").sha256(fresh_text.encode()).hexdigest()
    hydrated_old_text = "Previously hydrated answer."
    old_segment = ResponseSegmentRecord(
        segment_id="segment-old", assistant_turn_id="turn-old", kind="controlled",
        segment_canonical_text=hydrated_old_text,
        canonical_text_digest=__import__("hashlib").sha256(
            hydrated_old_text.encode()
        ).hexdigest(),
        normalization_version="nfc-v1", created_at="now",
        artifact_id="artifact-segment-old",
    )
    fresh_segment = ResponseSegmentRecord(
        segment_id="segment-fresh", assistant_turn_id="turn-fresh", kind="controlled",
        segment_canonical_text=fresh_text, canonical_text_digest=fresh_digest,
        normalization_version="nfc-v1", created_at="now",
    )
    old_claim = ClaimRecord(
        claim_id="claim-old", provider_claim_id="provider-old",
        segment_id=old_segment.segment_id, start_codepoint_offset=0,
        end_codepoint_offset=4, claim_text_digest="b" * 64, created_at="now",
    )
    fresh_claim = ClaimRecord(
        claim_id="claim-fresh", provider_claim_id="provider-fresh",
        segment_id=fresh_segment.segment_id, start_codepoint_offset=0,
        end_codepoint_offset=len(fresh_text), claim_text_digest=fresh_digest,
        created_at="now",
    )
    links = {
        ("claim-old", "evidence-old"): ClaimEvidenceLink(
            link_id="link-old", claim_id="claim-old", evidence_id="evidence-old",
            relation="supports", support_scope=None, evidence_stance_summary="",
            conflict_field=None, confidence_score=None, confidence_method=None,
            calibration_reference=None, created_at="now",
        ),
        ("claim-fresh", "evidence-fresh"): ClaimEvidenceLink(
            link_id="link-fresh", claim_id="claim-fresh",
            evidence_id="evidence-fresh", relation="supports",
            support_scope=fresh_text, evidence_stance_summary="supports",
            conflict_field=None, confidence_score=None, confidence_method=None,
            calibration_reference=None, created_at="now",
        ),
    }
    assessments = {
        "claim-old": ClaimSupportAssessment(
            claim_id="claim-old", status="supported", supported_scope=None,
            unsupported_scope=None, conflict_summary=None,
            assessment_method="atlas-claim-validation-v1",
            assessment_version="v1", created_at="now",
        ),
        "claim-fresh": ClaimSupportAssessment(
            claim_id="claim-fresh", status="supported",
            supported_scope=fresh_text, unsupported_scope=None,
            conflict_summary=None, assessment_method="atlas-claim-validation-v1",
            assessment_version="v1", created_at="now",
        ),
    }

    assert _valid_persistable_claim_graph(SimpleNamespace(
        response_segment_records={
            old_segment.segment_id: old_segment,
            fresh_segment.segment_id: fresh_segment,
        },
        claim_records={"claim-old": old_claim, "claim-fresh": fresh_claim},
        claim_evidence_links=links,
        claim_support_assessments=assessments,
    ))


@dataclass
class _MetadataFixture:
    record_id: str
    metadata: dict[str, object]


def test_typed_payload_policy_rejects_schema_drift_canonical_content_and_chunks() -> None:
    with pytest.raises(PersistedPayloadPolicyError, match="fields do not match"):
        validate_typed_payload(
            {"record_id": "record-1", "unexpected": True},
            family="test metadata",
            allowed_fields={"record_id"},
        )

    with pytest.raises(PersistedPayloadPolicyError, match="prohibited canonical content"):
        serialize_typed_dataclass(
            _MetadataFixture("record-1", {"tool_output": "complete output"}),
            family="test metadata",
            allowed_fields={"record_id", "metadata"},
        )

    with pytest.raises(PersistedPayloadPolicyError, match="base64-like"):
        serialize_typed_dataclass(
            _MetadataFixture("record-1", {"encoded": "YWFh" * 1024}),
            family="test metadata",
            allowed_fields={"record_id", "metadata"},
        )


def _runtime_policy() -> ModelRouteRuntimePolicy:
    return ModelRouteRuntimePolicy(
        schema_version="model-route-runtime-policy-v8",
        tokenizer_profile="cl100k_base",
        max_tool_executions=4,
        max_provider_invocations=20,
        max_reasoning_revision_cycles=2,
        max_catalog_pages=5,
        max_search_rounds=6,
        max_model_visible_items_per_turn=40,
        max_retrieval_repairs=3,
        max_schema_retries_per_turn=3,
        max_selected_anchor_pages_per_round=20,
        provider_invocation_timeout_seconds=30,
        tool_execution_timeout_seconds=10,
        turn_timeout_seconds=60,
        context_window_tokens=16_384,
        max_input_tokens_per_invocation=8_192,
        max_output_tokens_per_invocation=2_048,
        max_tool_result_tokens_per_execution=2_048,
        max_total_tokens_per_conversation=20_000,
        revision=1,
    )


def _model_invocation_with_repair_origin(origin: str) -> ModelInvocationRecord:
    policy = _runtime_policy()
    return ModelInvocationRecord(
        invocation_id="invocation-repair-1",
        route_id="route-1",
        provider_type="openai_compatible",
        model_name="model-1",
        status="planned",
        created_at="2026-08-04T00:00:00+00:00",
        prompt_snapshot_ref="prompt-invocation-repair-1",
        response_schema_name="answer-v1",
        response_schema_digest="a" * 64,
        token_usage={},
        route_revision=1,
        runtime_policy_schema_version=policy.schema_version,
        runtime_policy_revision=policy.revision,
        runtime_policy_snapshot=vars(policy),
        repair_origin_error_codes=[origin],
    )


@pytest.mark.parametrize(
    "origin",
    [*get_args(SchemaRetryOriginCode), "empty_terminal_answer"],
)
def test_model_invocation_accepts_owned_repair_origin_contract(origin: str) -> None:
    payload = _model_invocation_payload(_model_invocation_with_repair_origin(origin))

    assert payload["repair_origin_error_codes"] == [origin]


def test_model_invocation_rejects_unknown_repair_origin() -> None:
    with pytest.raises(
        PersistedPayloadPolicyError,
        match="model repair origin error codes are invalid",
    ):
        _model_invocation_payload(
            _model_invocation_with_repair_origin("unknown_retry_origin")
        )


def test_model_routing_jsonb_serializers_use_closed_typed_payloads() -> None:
    policy = _runtime_policy()
    route = ModelRouteRecord(
        route_id="route-1",
        display_name="Primary",
        provider_type="openai_compatible",
        model_name="model-1",
        connection_id="connection-1",
        runtime_policy=policy,
    )
    persisted_route = _model_route_payload(route)
    assert persisted_route["runtime_policy"]["schema_version"] == (
        "model-route-runtime-policy-v8"
    )
    assert set(persisted_route["runtime_policy"]) == {
        "schema_version",
        "tokenizer_profile",
        "max_tool_executions",
        "max_provider_invocations",
        "max_reasoning_revision_cycles",
        "max_catalog_pages",
        "max_search_rounds",
        "max_model_visible_items_per_turn",
        "max_retrieval_repairs",
        "max_schema_retries_per_turn",
        "max_selected_anchor_pages_per_round",
        "provider_invocation_timeout_seconds",
        "tool_execution_timeout_seconds",
        "turn_timeout_seconds",
        "context_window_tokens",
        "max_input_tokens_per_invocation",
        "max_output_tokens_per_invocation",
        "max_tool_result_tokens_per_execution",
        "max_total_tokens_per_conversation",
        "revision",
    }

    invocation = ModelInvocationRecord(
        invocation_id="invocation-1",
        route_id=route.route_id,
        provider_type=route.provider_type,
        model_name=route.model_name,
        status="completed",
        created_at="2026-07-13T00:00:00+00:00",
        prompt_snapshot_ref="prompt-invocation-1",
        response_schema_name="answer-v1",
        response_schema_digest="a" * 64,
        token_usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        route_revision=1,
        runtime_policy_schema_version=policy.schema_version,
        runtime_policy_revision=policy.revision,
        runtime_policy_snapshot=vars(policy),
        started_at="2026-07-13T00:00:00+00:00",
        completed_at="2026-07-13T00:00:01+00:00",
        duration_ms=1000,
    )
    persisted_invocation = _model_invocation_payload(invocation)
    assert persisted_invocation["token_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }

    invocation.status = "succeeded"
    with pytest.raises(PersistedPayloadPolicyError, match="status is invalid"):
        _model_invocation_payload(invocation)
    with pytest.raises(PersistedPayloadPolicyError, match="status is invalid"):
        _model_invocation_record(vars(invocation))
    invocation.status = "completed"

    invocation.token_usage = {"cached_tokens": 12}
    with pytest.raises(PersistedPayloadPolicyError, match="typed allowlist"):
        _model_invocation_payload(invocation)

    invocation.token_usage = {}
    invocation.runtime_policy_snapshot = {**vars(policy), "prompt": "full prompt"}
    with pytest.raises(PersistedPayloadPolicyError, match="typed contract"):
        _model_invocation_payload(invocation)


def test_audit_jsonb_serializer_closes_metadata_and_bounds_safe_summaries() -> None:
    policy = _runtime_policy()
    metadata = _audit_metadata_payload(
        {
            "reason": "authorized_scope",
            "api_version": "2024-10-21",
            "change_id": "local-pilot-target-v1",
            "evidence_count": 2,
            "logical_identity": "conversation-1:turn-request-1:input",
            "request_fingerprint": "a" * 64,
            "runtime_policy": vars(policy),
            "tag_refs": [{"tag_type": "project", "tag_id": "project-1"}],
            "trace_id": "trace-model-step-1",
            "verification_mode": "full_hash",
            "viewer_item_id": "viewer-item-1",
        }
    )
    assert metadata["change_id"] == "local-pilot-target-v1"
    assert metadata["api_version"] == "2024-10-21"
    assert metadata["request_fingerprint"] == "a" * 64
    assert metadata["verification_mode"] == "full_hash"
    assert metadata["viewer_item_id"] == "viewer-item-1"
    assert metadata["logical_identity"] == "conversation-1:turn-request-1:input"
    assert metadata["runtime_policy"]["revision"] == 1
    assert metadata["tag_refs"] == [
        {"tag_type": "project", "tag_id": "project-1"}
    ]
    assert metadata["trace_id"] == "trace-model-step-1"

    with pytest.raises(PersistedPayloadPolicyError, match="allowlist"):
        _audit_metadata_payload({"prompt": "full prompt"})
    with pytest.raises(PersistedPayloadPolicyError, match="base64-like"):
        _audit_metadata_payload({"reason": "YWFh" * 1024})
    with pytest.raises(PersistedPayloadPolicyError, match="65536-byte"):
        _audit_metadata_payload({"reason": "!" * 65_536})

    with pytest.raises(ValueError, match="unknown message_code"):
        AuditEventRecord(
            event_id="audit-1",
            event_type="processing_run.failed",
            actor_id="user-1",
            target_ref="processing-run:run-1",
            project_id=None,
            message_code="x" * 1_025,
            metadata={"failure_code": "processing_failed"},
            created_at="2026-07-13T00:00:00+00:00",
        )


def test_processing_error_replay_persists_bounded_message_params() -> None:
    record = ProcessingIdempotencyRecord(
        idempotency_key="profile-route-rejected",
        operation="profile.revise",
        request_digest="a" * 64,
        response_payload={
            "error_code": "visual_route_not_vision_capable",
            "message_code": (
                "processing.visual_profiles_require_an_enabled_tested_"
                "image_capable_model_route"
            ),
            "message_params": {},
            "correlation_id": "processing-plugin-admin",
            "audit_event_ref": "audit-rejected",
        },
        status_code=422,
        created_at="2026-07-15T00:00:00+00:00",
    )

    persisted = _processing_payload(record)

    assert persisted["response_payload"]["message_params"] == {}


def test_production_orm_and_baseline_have_no_legacy_artifact_byte_columns() -> None:
    root = Path(__file__).parents[1] / "src" / "atlas_production"
    assert OrmBase.metadata.tables
    baseline = (
        root
        / "migrations"
        / "versions"
        / "20260711_0001_development_baseline.py"
    ).read_text(encoding="utf-8")
    for prohibited_column in (
        '"content_base64"',
        '"source_text_snapshot"',
        '"storage_path_ref"',
        '"raw_path"',
    ):
        assert prohibited_column not in baseline
    prohibited_names = {
        "content_base64", "source_text_snapshot", "storage_path_ref", "raw_path"
    }
    assert not {
        column.name
        for table in OrmBase.metadata.tables.values()
        for column in table.columns
    }.intersection(prohibited_names)


def test_every_postgresql_jsonb_column_has_an_explicit_payload_policy() -> None:
    actual = {
        f"{table.name}.{column.name}"
        for table in OrmBase.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB)
    }
    assert actual == set(JSONB_PAYLOAD_REGISTRY)
