from dataclasses import asdict, dataclass
from typing import Any, Mapping, get_args

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRouteRuntimePolicy,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.modules.model_routing.api_models import (
    ModelRouteStatus,
    ProviderConnectionStatus,
    ProviderConnectionTestResult,
)
from atlas_production.modules.turn_runtime.public import SchemaRetryOriginCode
from atlas_production.providers import ROUTE_READINESS_SCHEMA

from .base import OrmBase
from .payload_policy import (
    MODEL_RUNTIME_POLICY_FIELDS,
    RUNTIME_POLICY_MAX_BYTES,
    PersistedPayloadPolicyError,
    serialize_typed_dataclass,
    validate_typed_patch,
)


_MODEL_ROUTE_FIELDS = frozenset(
    {
        "route_id",
        "display_name",
        "provider_type",
        "model_name",
        "connection_id",
        "runtime_policy",
        "supports_vision",
        "status",
        "enabled",
        "revision",
        "last_tested_at",
        "is_default",
        "readiness_schema_name",
        "readiness_schema_digest",
    }
)
_MODEL_INVOCATION_FIELDS = frozenset(
    {
        "invocation_id",
        "route_id",
        "provider_type",
        "model_name",
        "status",
        "created_at",
        "prompt_snapshot_ref",
        "response_schema_name",
        "response_schema_digest",
        "token_usage",
        "error_code",
        "route_revision",
        "runtime_policy_schema_version",
        "runtime_policy_revision",
        "runtime_policy_snapshot",
        "invocation_purpose",
        "subject_kind",
        "subject_ref",
        "request_artifact_ref",
        "response_artifact_ref",
        "execution_key",
        "prompt_digest",
        "input_digest",
        "input_content_type",
        "input_width",
        "input_height",
        "started_at",
        "completed_at",
        "duration_ms",
        "attempt_ordinal",
        "repair_origin_error_codes",
    }
)
_MODEL_TOKEN_USAGE_FIELDS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
    }
)
_ANSWER_REPAIR_ORIGIN_ERROR_CODES = frozenset(
    {
        "empty_terminal_answer",
        "invalid_segment_mix",
        "clarification_segment_count",
        "empty_segment_text",
        "controlled_claim_id_invalid",
        "controlled_provenance_missing",
    }
)
_SCHEMA_RETRY_ORIGIN_ERROR_CODES = frozenset(
    str(code) for code in get_args(SchemaRetryOriginCode)
)
_REPAIR_ORIGIN_ERROR_CODES = (
    _ANSWER_REPAIR_ORIGIN_ERROR_CODES | _SCHEMA_RETRY_ORIGIN_ERROR_CODES
)
_MODEL_INVOCATION_STATUSES = frozenset(
    {"planned", "denied", "skipped", "started", "completed", "failed"}
)


@dataclass(frozen=True, slots=True)
class ModelRoutingChangeSet:
    """Primary keys owned by one model-routing configuration mutation."""

    provider_connection_ids: tuple[str, ...] = ()
    provider_connection_secret_ids: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    replay_keys: tuple[str, ...] = ()
    audit_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "provider_connection_ids",
            "provider_connection_secret_ids",
            "route_ids",
            "replay_keys",
            "audit_event_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                tuple(sorted(set(getattr(self, field_name)))),
            )


class AtlasProviderConnectionRow(OrmBase):
    __tablename__ = "atlas_provider_connections"

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_method: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    last_verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_rotated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasProviderConnectionSecretRow(OrmBase):
    __tablename__ = "atlas_provider_connection_secrets"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_provider_connections.connection_id", ondelete="CASCADE"),
        primary_key=True,
    )
    storage_backend: Mapped[str] = mapped_column(String, nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String, nullable=False)
    algorithm: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasModelRouteRow(OrmBase):
    __tablename__ = "atlas_model_routes"
    __table_args__ = (
        Index(
            "ux_atlas_model_routes_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )

    route_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_provider_connections.connection_id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_tested_at: Mapped[str | None] = mapped_column(String, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    readiness_schema_name: Mapped[str | None] = mapped_column(String, nullable=True)
    readiness_schema_digest: Mapped[str | None] = mapped_column(String, nullable=True)


class AtlasModelInvocationRow(OrmBase):
    __tablename__ = "atlas_model_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'denied', 'skipped', 'started', 'completed', 'failed')",
            name="ck_atlas_model_invocation_status",
        ),
    )

    invocation_id: Mapped[str] = mapped_column(String, primary_key=True)
    route_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    prompt_snapshot_ref: Mapped[str] = mapped_column(String, nullable=False)
    response_schema_name: Mapped[str] = mapped_column(String, nullable=False)
    response_schema_digest: Mapped[str] = mapped_column(String, nullable=False)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    route_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_policy_schema_version: Mapped[str] = mapped_column(String, nullable=False)
    runtime_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    invocation_purpose: Mapped[str] = mapped_column(String, nullable=False)
    subject_kind: Mapped[str] = mapped_column(String, nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    request_artifact_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    response_artifact_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_key: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True, unique=True
    )
    prompt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    input_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repair_origin_error_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False
    )


class AtlasModelRoutingReplayRow(OrmBase):
    __tablename__ = "atlas_model_routing_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    target_ref: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    response_model: Mapped[str] = mapped_column(String, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


def serialize_model_runtime_policy(
    policy: ModelRouteRuntimePolicy | Mapping[str, Any],
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    if isinstance(policy, Mapping):
        values = dict(policy)
        if allow_empty and not values:
            return {}
        try:
            policy = ModelRouteRuntimePolicy(**values)
        except (TypeError, ValueError) as exc:
            raise PersistedPayloadPolicyError(
                "model runtime policy does not match its typed contract"
            ) from exc
    if not isinstance(policy, ModelRouteRuntimePolicy):
        raise PersistedPayloadPolicyError(
            "model runtime policy does not match its typed contract"
        )
    return serialize_typed_dataclass(
        policy,
        family="model runtime policy",
        allowed_fields=MODEL_RUNTIME_POLICY_FIELDS,
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )


def _model_token_usage_payload(token_usage: Mapping[str, Any]) -> dict[str, int]:
    values = dict(token_usage)
    if not values:
        return {}
    for key, value in values.items():
        if (
            key not in _MODEL_TOKEN_USAGE_FIELDS
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise PersistedPayloadPolicyError(
                "model token usage does not match its typed allowlist"
            )
    return validate_typed_patch(
        values,
        family="model token usage",
        allowed_fields=_MODEL_TOKEN_USAGE_FIELDS,
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )


def _model_route_payload(record: ModelRouteRecord) -> dict[str, Any]:
    payload = asdict(record)
    if frozenset(payload) != _MODEL_ROUTE_FIELDS:
        raise PersistedPayloadPolicyError(
            "model route fields do not match the persistence contract"
        )
    payload["runtime_policy"] = serialize_model_runtime_policy(record.runtime_policy)
    return payload


def _model_invocation_payload(record: ModelInvocationRecord) -> dict[str, Any]:
    payload = asdict(record)
    if frozenset(payload) != _MODEL_INVOCATION_FIELDS:
        raise PersistedPayloadPolicyError(
            "model invocation fields do not match the persistence contract"
        )
    if record.status not in _MODEL_INVOCATION_STATUSES:
        raise PersistedPayloadPolicyError("model invocation status is invalid")
    if record.duration_ms is not None and (
        isinstance(record.duration_ms, bool) or record.duration_ms < 0
    ):
        raise PersistedPayloadPolicyError("model invocation duration is invalid")
    payload["token_usage"] = _model_token_usage_payload(record.token_usage)
    payload["runtime_policy_snapshot"] = serialize_model_runtime_policy(
        record.runtime_policy_snapshot,
        allow_empty=True,
    )
    if (
        len(record.repair_origin_error_codes) > len(_REPAIR_ORIGIN_ERROR_CODES)
        or any(
            code not in _REPAIR_ORIGIN_ERROR_CODES
            for code in record.repair_origin_error_codes
        )
    ):
        raise PersistedPayloadPolicyError(
            "model repair origin error codes are invalid"
        )
    payload["repair_origin_error_codes"] = sorted(
        set(record.repair_origin_error_codes)
    )
    return payload


def _model_invocation_record(item: dict[str, Any]) -> ModelInvocationRecord:
    record = ModelInvocationRecord(**item)
    _model_invocation_payload(record)
    return record


def _replay_record_payload(record: ModelRoutingReplayRecord) -> dict[str, Any]:
    allowed_models = {
        "ProviderConnectionStatus": ProviderConnectionStatus,
        "ProviderConnectionTestResult": ProviderConnectionTestResult,
        "ModelRouteStatus": ModelRouteStatus,
    }
    if record.response_model not in allowed_models:
        raise ValueError("model-routing replay response model is not allowlisted")
    response_model = allowed_models[record.response_model]
    validated_response = response_model.model_validate(
        record.response_payload
    ).model_dump(mode="json")
    return serialize_typed_dataclass(
        record,
        family="model-routing idempotency replay",
        allowed_fields={
            "idempotency_key", "operation", "target_ref", "request_fingerprint",
            "response_model", "response_payload", "status_code", "created_at",
        },
        overrides={"response_payload": validated_response},
        max_bytes=16_384,
    )


def _selected_records(
    records: Mapping[str, Any],
    record_ids: tuple[str, ...],
    *,
    owner: str,
) -> list[Any]:
    missing = sorted(set(record_ids) - set(records))
    if missing:
        raise RuntimeError(
            f"model-routing {owner} change set references missing records: {missing}"
        )
    return [records[record_id] for record_id in record_ids]


def write_invocation_row(
    session: Session,
    invocation: ModelInvocationRecord,
) -> None:
    """Persist one invocation without replaying another process's stale rows."""

    payload = _model_invocation_payload(invocation)
    if invocation.execution_key is not None and invocation.status == "planned":
        session.add(AtlasModelInvocationRow(**payload))
        session.flush()
        return
    if invocation.execution_key is not None:
        values = {key: value for key, value in payload.items() if key != "invocation_id"}
        expected_statuses = (
            ["planned"]
            if invocation.status == "started"
            else (["planned", "started"] if invocation.status == "failed" else ["started"])
        )
        changed = session.execute(
            update(AtlasModelInvocationRow)
            .where(
                AtlasModelInvocationRow.invocation_id == invocation.invocation_id,
                AtlasModelInvocationRow.execution_key == invocation.execution_key,
                AtlasModelInvocationRow.status.in_(expected_statuses),
            )
            .values(**values)
        ).rowcount
        if changed != 1:
            raise RuntimeError("model_invocation_transition_conflict")
        return
    session.merge(AtlasModelInvocationRow(**payload))


def runtime_joined_snapshot(
    session: Session,
    route_id: str | None = None,
) -> tuple[
    ModelRouteRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
] | None:
    query = (
        session.query(
            AtlasModelRouteRow,
            AtlasProviderConnectionRow,
            AtlasProviderConnectionSecretRow,
        )
        .join(
            AtlasProviderConnectionRow,
            AtlasProviderConnectionRow.connection_id
            == AtlasModelRouteRow.connection_id,
        )
        .join(
            AtlasProviderConnectionSecretRow,
            AtlasProviderConnectionSecretRow.connection_id
            == AtlasProviderConnectionRow.connection_id,
        )
        .filter(
            AtlasModelRouteRow.enabled.is_(True),
            AtlasModelRouteRow.status == "test_passed",
            AtlasModelRouteRow.readiness_schema_name == ROUTE_READINESS_SCHEMA.name,
            AtlasModelRouteRow.readiness_schema_digest == ROUTE_READINESS_SCHEMA.digest,
            AtlasProviderConnectionRow.enabled.is_(True),
            AtlasProviderConnectionRow.status == "verified",
            AtlasModelRouteRow.provider_type
            == AtlasProviderConnectionRow.provider_type,
        )
    )
    if route_id is not None:
        query = query.filter(AtlasModelRouteRow.route_id == route_id)
    route_row, connection_row, secret_row = query.order_by(
        AtlasModelRouteRow.is_default.desc(),
        AtlasModelRouteRow.route_id,
    ).first() or (None, None, None)
    if route_row is None:
        return None
    return (
        _route_record(
            {
                column.name: getattr(route_row, column.name)
                for column in AtlasModelRouteRow.__table__.columns
            }
        ),
        ProviderConnectionRecord(
            **{
                column.name: getattr(connection_row, column.name)
                for column in AtlasProviderConnectionRow.__table__.columns
            }
        ),
        ProviderConnectionSecretRecord(
            **{
                column.name: getattr(secret_row, column.name)
                for column in AtlasProviderConnectionSecretRow.__table__.columns
            }
        ),
    )


def _route_record(payload: dict[str, Any]) -> ModelRouteRecord:
    values = dict(payload)
    policy = values["runtime_policy"]
    if not isinstance(policy, dict):
        raise ValueError("model route runtime policy must be an object")
    values["runtime_policy"] = ModelRouteRuntimePolicy(**policy)
    return ModelRouteRecord(**values)
