from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import model_routing
from atlas_production.infrastructure.persistence.model_routing import (
    AtlasModelInvocationRow,
    AtlasModelRouteRow,
    AtlasModelRoutingReplayRow,
    AtlasProviderConnectionRow,
    AtlasProviderConnectionSecretRow,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
    ModelRoutingReplayRecord,
    ProviderConnectionRecord,
    ProviderConnectionSecretRecord,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]
_MAX_READ_LIMIT = 500


def _bounded_limit(limit: int, *, family: str) -> int:
    if limit < 1 or limit > _MAX_READ_LIMIT:
        raise ValueError(f"{family} limit must be between 1 and 500")
    return limit


def _route_record(row: AtlasModelRouteRow) -> ModelRouteRecord:
    return model_routing._route_record(
        {
            column.name: getattr(row, column.name)
            for column in AtlasModelRouteRow.__table__.columns
        }
    )


def _invocation_record(row: AtlasModelInvocationRow) -> ModelInvocationRecord:
    return cast(
        ModelInvocationRecord,
        model_routing._model_invocation_record(
            {
                column.name: getattr(row, column.name)
                for column in AtlasModelInvocationRow.__table__.columns
            }
        ),
    )


@dataclass(frozen=True, slots=True)
class ProviderConnectionWrite:
    record: ProviderConnectionRecord
    expected_revision: int | None

    def __post_init__(self) -> None:
        if self.expected_revision is None:
            if self.record.revision != 1:
                raise ValueError("new Provider connection revision must start at 1")
            return
        if self.expected_revision < 1 or self.record.revision != self.expected_revision + 1:
            raise ValueError("Provider connection write must advance the revision")


@dataclass(frozen=True, slots=True)
class ProviderConnectionSecretWrite:
    record: ProviderConnectionSecretRecord
    expected_version: int | None

    def __post_init__(self) -> None:
        if self.expected_version is None:
            if self.record.version != 1:
                raise ValueError("new Provider secret version must start at 1")
            return
        if self.expected_version < 1 or self.record.version != self.expected_version + 1:
            raise ValueError("Provider secret write must advance the version")


@dataclass(frozen=True, slots=True)
class ModelRouteWrite:
    record: ModelRouteRecord
    expected_revision: int | None
    preserve_revision: bool = False
    expected_default: bool | None = None
    expected_other_default: bool | None = None

    def __post_init__(self) -> None:
        if self.expected_revision is None:
            if (
                self.preserve_revision
                or self.expected_default is not None
                or self.expected_other_default is not None
            ):
                raise ValueError(
                    "new model route cannot preserve a revision or default preimage"
                )
            if self.record.revision != 1:
                raise ValueError("new model route revision must start at 1")
            return
        expected_record_revision = (
            self.expected_revision
            if self.preserve_revision
            else self.expected_revision + 1
        )
        if (
            self.expected_revision < 1
            or self.record.revision != expected_record_revision
        ):
            action = "preserve" if self.preserve_revision else "advance"
            raise ValueError(f"model route write must {action} the revision")
        if self.preserve_revision != (
            self.expected_default is not None
            and self.expected_other_default is not None
        ):
            raise ValueError(
                "revision-preserving route write requires both default preimages"
            )


@dataclass(frozen=True, slots=True)
class ModelInvocationWrite:
    record: ModelInvocationRecord
    expected: ModelInvocationRecord | None


@dataclass(frozen=True, slots=True)
class DefaultRouteConnectionPrecondition:
    connection_id: str
    expected_revision: int
    expected_enabled: bool
    expected_status: str


@dataclass(frozen=True, slots=True)
class ConnectionDisablePrecondition:
    connection_id: str


@dataclass(frozen=True, slots=True)
class _ModelRoutingWriteBatch:
    provider_connections: tuple[ProviderConnectionWrite, ...] = ()
    provider_secrets: tuple[ProviderConnectionSecretWrite, ...] = ()
    routes: tuple[ModelRouteWrite, ...] = ()
    replays: tuple[ModelRoutingReplayRecord, ...] = ()
    invocations: tuple[ModelInvocationWrite, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()
    default_route_connection_precondition: (
        DefaultRouteConnectionPrecondition | None
    ) = None
    default_purpose: Literal["text", "vision"] | None = None
    connection_disable_preconditions: tuple[
        ConnectionDisablePrecondition, ...
    ] = ()

    def __post_init__(self) -> None:
        if any(
            (
                self.provider_connections,
                self.provider_secrets,
                self.routes,
                self.replays,
                self.invocations,
            )
        ) and not self.audit_events:
            raise ValueError("model-routing mutation requires audit events")
        identities = (
            *(
                f"connection:{write.record.connection_id}"
                for write in self.provider_connections
            ),
            *(
                f"secret:{write.record.connection_id}"
                for write in self.provider_secrets
            ),
            *(f"route:{write.record.route_id}" for write in self.routes),
            *(f"replay:{record.idempotency_key}" for record in self.replays),
            *(
                f"invocation:{write.record.invocation_id}"
                for write in self.invocations
            ),
        )
        if len(identities) != len(set(identities)):
            raise ValueError("model-routing change set contains duplicate owners")


class ModelRoutingCurrentnessConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderAttemptSnapshot:
    """One detached route/connection/credential-policy selection."""

    route: ModelRouteRecord
    connection: ProviderConnectionRecord
    secret: ProviderConnectionSecretRecord = field(repr=False)


_INVOCATION_LINEAGE_FIELDS = (
    "invocation_id", "route_id", "provider_type", "model_name", "created_at",
    "prompt_snapshot_ref", "response_schema_name", "response_schema_digest",
    "route_revision", "runtime_policy_schema_version", "runtime_policy_revision",
    "runtime_policy_snapshot", "invocation_purpose", "subject_kind", "subject_ref",
    "request_artifact_ref", "execution_key", "prompt_digest", "input_digest",
    "input_content_type", "input_width", "input_height", "attempt_ordinal",
    "repair_origin_error_codes",
)
_INVOCATION_TRANSITIONS = {
    # Conversation result publication owns an atomic terminal-create path: the
    # provider call has already completed outside SQL and the result checkpoint
    # persists its immutable invocation lineage in the same transaction as the
    # conversation result and audit evidence.  The ordinary model-runtime path
    # still uses planned -> started -> terminal transitions.
    None: {"planned", "completed", "failed"},
    "planned": {"started", "failed"},
    "started": {"completed", "failed"},
}
_LOCKED_PRIOR_NOT_PROVIDED = object()


def _validate_invocation_transition(
    prior: ModelInvocationRecord | None,
    candidate: ModelInvocationRecord,
) -> None:
    if prior is not None and any(
        getattr(prior, field_name) != getattr(candidate, field_name)
        for field_name in _INVOCATION_LINEAGE_FIELDS
    ):
        raise ModelRoutingCurrentnessConflict(
            "model invocation immutable lineage changed"
        )
    prior_status = prior.status if prior is not None else None
    if candidate.status not in _INVOCATION_TRANSITIONS.get(prior_status, set()):
        raise ModelRoutingCurrentnessConflict(
            "model invocation lifecycle transition is not monotonic"
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationWriter:
    """Closed cross-owner writer bound to its coordinator's exact Session."""

    _session: Session

    def write(
        self,
        invocation: ModelInvocationRecord,
        *,
        locked_prior: ModelInvocationRecord | None | object = _LOCKED_PRIOR_NOT_PROVIDED,
    ) -> None:
        if locked_prior is _LOCKED_PRIOR_NOT_PROVIDED:
            current = self._session.scalar(
                select(AtlasModelInvocationRow)
                .where(
                    AtlasModelInvocationRow.invocation_id
                    == invocation.invocation_id
                )
                .with_for_update()
            )
            current_record = (
                _invocation_record(current) if current is not None else None
            )
        else:
            # Named coordinators already acquired the row lock and pass their
            # exact preimage; cross-owner callers that omit it are fenced here.
            current_record = locked_prior  # type: ignore[assignment]
        if (
            current_record == invocation
            and invocation.status in {"completed", "failed"}
        ):
            return
        _validate_invocation_transition(current_record, invocation)
        model_routing.write_invocation_row(self._session, invocation)


@dataclass(frozen=True, slots=True)
class _ModelRoutingCommandCoordinator:
    session_factory: SessionFactory

    def _finalize(self, change_set: _ModelRoutingWriteBatch) -> bool:
        session = self.session_factory()
        with session:
            try:
                configuration_change = any(
                    (
                        change_set.provider_connections,
                        change_set.provider_secrets,
                        change_set.routes,
                    )
                )
                acquire_owner_locks(
                    session,
                    domain_keys=("model-routing:configuration-control",)
                    if configuration_change
                    else (),
                    identity_keys=self._identity_keys(change_set),
                )
                self._validate_configuration_preconditions(session, change_set)
                replayed_invocations = self._lock_current_rows(session, change_set)
                self._write_rows(session, change_set, replayed_invocations)
                if not (
                    change_set.invocations
                    and len(replayed_invocations) == len(change_set.invocations)
                    and not any((change_set.provider_connections, change_set.provider_secrets, change_set.routes, change_set.replays))
                ):
                    AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
                return bool(replayed_invocations)
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _validate_configuration_preconditions(
        session: Session, change_set: _ModelRoutingWriteBatch
    ) -> None:
        default_connection = change_set.default_route_connection_precondition
        if default_connection is not None:
            current = session.scalar(
                select(AtlasProviderConnectionRow)
                .where(
                    AtlasProviderConnectionRow.connection_id
                    == default_connection.connection_id
                )
                .with_for_update()
            )
            if (
                current is None
                or current.revision != default_connection.expected_revision
                or current.enabled != default_connection.expected_enabled
                or current.status != default_connection.expected_status
                or not current.enabled
                or current.status != "verified"
            ):
                raise ModelRoutingCurrentnessConflict(
                    "default route Provider connection changed or is unavailable"
                )
        for disable in change_set.connection_disable_preconditions:
            current_default = session.scalar(
                select(AtlasModelRouteRow)
                .where(
                    AtlasModelRouteRow.connection_id == disable.connection_id,
                    (
                        AtlasModelRouteRow.is_text_default.is_(True)
                        | AtlasModelRouteRow.is_vision_default.is_(True)
                    ),
                )
                .order_by(AtlasModelRouteRow.route_id)
                .with_for_update()
            )
            if current_default is not None:
                raise ModelRoutingCurrentnessConflict(
                    "Provider connection became linked to the current default route"
                )

    @staticmethod
    def _identity_keys(change_set: _ModelRoutingWriteBatch) -> tuple[str, ...]:
        return (
            *(
                f"model-routing:connection:{write.record.connection_id}"
                for write in change_set.provider_connections
            ),
            *(
                f"model-routing:connection-secret:{write.record.connection_id}"
                for write in change_set.provider_secrets
            ),
            *(
                f"model-routing:route:{write.record.route_id}"
                for write in change_set.routes
            ),
            *(
                f"model-routing:replay:{record.idempotency_key}"
                for record in change_set.replays
            ),
            *(
                f"model-routing:invocation:{write.record.invocation_id}"
                for write in change_set.invocations
            ),
        )

    @staticmethod
    def _lock_current_rows(
        session: Session,
        change_set: _ModelRoutingWriteBatch,
    ) -> set[str]:
        replayed_invocations: set[str] = set()
        for write in change_set.provider_connections:
            current = session.scalar(
                select(AtlasProviderConnectionRow)
                .where(
                    AtlasProviderConnectionRow.connection_id
                    == write.record.connection_id
                )
                .with_for_update()
            )
            if write.expected_revision is None:
                if current is not None:
                    raise ModelRoutingCurrentnessConflict(
                        "Provider connection already exists"
                    )
            elif current is None or current.revision != write.expected_revision:
                raise ModelRoutingCurrentnessConflict(
                    "Provider connection revision changed"
                )

        for write in change_set.provider_secrets:
            current = session.scalar(
                select(AtlasProviderConnectionSecretRow)
                .where(
                    AtlasProviderConnectionSecretRow.connection_id
                    == write.record.connection_id
                )
                .with_for_update()
            )
            if write.expected_version is None:
                if current is not None:
                    raise ModelRoutingCurrentnessConflict(
                        "Provider connection secret already exists"
                    )
            elif current is None or current.version != write.expected_version:
                raise ModelRoutingCurrentnessConflict(
                    "Provider connection secret version changed"
                )

        purpose = change_set.default_purpose
        for write in change_set.routes:
            current = session.scalar(
                select(AtlasModelRouteRow)
                .where(AtlasModelRouteRow.route_id == write.record.route_id)
                .with_for_update()
            )
            if write.expected_revision is None:
                if current is not None:
                    raise ModelRoutingCurrentnessConflict(
                        "model route already exists"
                    )
            elif current is None or current.revision != write.expected_revision:
                raise ModelRoutingCurrentnessConflict("model route revision changed")
            if write.expected_revision is not None:
                if write.preserve_revision:
                    if purpose is None:
                        raise ModelRoutingCurrentnessConflict(
                            "revision-preserving route write requires a default purpose"
                        )
                    other_purpose = "vision" if purpose == "text" else "text"
                    if (
                        getattr(current, f"is_{purpose}_default")
                        != write.expected_default
                        or getattr(current, f"is_{other_purpose}_default")
                        != write.expected_other_default
                    ):
                        raise ModelRoutingCurrentnessConflict(
                            "model route default preimage changed"
                        )
                elif (
                    current.is_text_default != write.record.is_text_default
                    or current.is_vision_default != write.record.is_vision_default
                ):
                    raise ModelRoutingCurrentnessConflict(
                        "model route default selection changed"
                    )

        if purpose is not None:
            default_field = f"is_{purpose}_default"
            selected_defaults = [
                write.record
                for write in change_set.routes
                if getattr(write.record, default_field)
            ]
            if len(selected_defaults) > 1:
                raise ModelRoutingCurrentnessConflict(
                    f"model-routing change set contains multiple {purpose} defaults"
                )
            if selected_defaults:
                default_column = (
                    AtlasModelRouteRow.is_text_default
                    if purpose == "text"
                    else AtlasModelRouteRow.is_vision_default
                )
                current_default = session.scalar(
                    select(AtlasModelRouteRow)
                    .where(default_column.is_(True))
                    .with_for_update()
                )
                desired = selected_defaults[0].route_id
                route_ids = {write.record.route_id for write in change_set.routes}
                if (
                    current_default is not None
                    and current_default.route_id != desired
                    and current_default.route_id not in route_ids
                ):
                    raise ModelRoutingCurrentnessConflict(
                        f"{purpose} default route change omits the prior default"
                    )

        for replay in change_set.replays:
            current = session.scalar(
                select(AtlasModelRoutingReplayRow)
                .where(
                    AtlasModelRoutingReplayRow.idempotency_key
                    == replay.idempotency_key
                )
                .with_for_update()
            )
            if current is not None:
                raise ModelRoutingCurrentnessConflict(
                    "model-routing idempotency key already exists"
                )

        for write in change_set.invocations:
            current = session.scalar(
                select(AtlasModelInvocationRow)
                .where(
                    AtlasModelInvocationRow.invocation_id
                    == write.record.invocation_id
                )
                .with_for_update()
            )
            current_record = _invocation_record(current) if current is not None else None
            if current_record == write.record and current_record is not None:
                if current_record.status not in {"completed", "failed"}:
                    raise ModelRoutingCurrentnessConflict(
                        "non-terminal invocation replay is forbidden"
                    )
                replayed_invocations.add(write.record.invocation_id)
                continue
            if current_record != write.expected:
                raise ModelRoutingCurrentnessConflict(
                    "model invocation exact preimage changed"
                )
            _validate_invocation_transition(write.expected, write.record)
        return replayed_invocations

    @staticmethod
    def _write_rows(
        session: Session,
        change_set: _ModelRoutingWriteBatch,
        replayed_invocations: set[str],
    ) -> None:
        for write in change_set.provider_connections:
            session.merge(AtlasProviderConnectionRow(**asdict(write.record)))
        for write in change_set.provider_secrets:
            session.merge(AtlasProviderConnectionSecretRow(**asdict(write.record)))

        purpose = change_set.default_purpose
        if purpose is None:
            ordered_routes = sorted(
                (write.record for write in change_set.routes),
                key=lambda record: record.route_id,
            )
            for route in ordered_routes:
                session.merge(
                    AtlasModelRouteRow(**model_routing._model_route_payload(route))
                )
                session.flush()
        else:
            default_field = f"is_{purpose}_default"
            ordered_routes = sorted(
                (write.record for write in change_set.routes),
                key=lambda record: (
                    getattr(record, default_field),
                    record.route_id,
                ),
            )
            for route in ordered_routes:
                session.execute(
                    update(AtlasModelRouteRow)
                    .where(AtlasModelRouteRow.route_id == route.route_id)
                    .values(**{default_field: getattr(route, default_field)})
                )
                session.flush()

        for replay in change_set.replays:
            session.add(
                AtlasModelRoutingReplayRow(
                    **model_routing._replay_record_payload(replay)
                )
            )
        invocation_writer = ModelInvocationWriter(session)
        for write in change_set.invocations:
            if write.record.invocation_id not in replayed_invocations:
                invocation_writer.write(write.record, locked_prior=write.expected)

@dataclass(frozen=True, slots=True)
class ModelRoutingReadModel:
    session_factory: SessionFactory

    def get_connection(self, connection_id: str) -> ProviderConnectionRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProviderConnectionRow).where(
                    AtlasProviderConnectionRow.connection_id == connection_id
                )
            )
            return (
                ProviderConnectionRecord(
                    **{
                        column.name: getattr(row, column.name)
                        for column in AtlasProviderConnectionRow.__table__.columns
                    }
                )
                if row is not None
                else None
            )

    def list_connections(self, *, limit: int = 200) -> list[ProviderConnectionRecord]:
        _bounded_limit(limit, family="Provider connection")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProviderConnectionRow)
                .order_by(AtlasProviderConnectionRow.connection_id)
                .limit(limit)
            ).all()
            return [
                ProviderConnectionRecord(
                    **{
                        column.name: getattr(row, column.name)
                        for column in AtlasProviderConnectionRow.__table__.columns
                    }
                )
                for row in rows
            ]

    def get_secret(
        self,
        connection_id: str,
    ) -> ProviderConnectionSecretRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProviderConnectionSecretRow).where(
                    AtlasProviderConnectionSecretRow.connection_id
                    == connection_id
                )
            )
            return (
                ProviderConnectionSecretRecord(
                    **{
                        column.name: getattr(row, column.name)
                        for column in AtlasProviderConnectionSecretRow.__table__.columns
                    }
                )
                if row is not None
                else None
            )

    def get_route(self, route_id: str) -> ModelRouteRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasModelRouteRow).where(
                    AtlasModelRouteRow.route_id == route_id
                )
            )
            if row is None:
                return None
            return _route_record(row)

    def list_routes(self, *, limit: int = 200) -> list[ModelRouteRecord]:
        _bounded_limit(limit, family="model route")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasModelRouteRow)
                .order_by(AtlasModelRouteRow.route_id)
                .limit(limit)
            ).all()
            return [_route_record(row) for row in rows]

    def linked_routes(
        self,
        connection_id: str,
        *,
        limit: int = 200,
    ) -> list[ModelRouteRecord]:
        _bounded_limit(limit, family="linked model route")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasModelRouteRow)
                .where(AtlasModelRouteRow.connection_id == connection_id)
                .order_by(AtlasModelRouteRow.route_id)
                .limit(limit)
            ).all()
            return [_route_record(row) for row in rows]

    def default_route(
        self, purpose: Literal["text", "vision"]
    ) -> ModelRouteRecord | None:
        default_column = (
            AtlasModelRouteRow.is_text_default
            if purpose == "text"
            else AtlasModelRouteRow.is_vision_default
        )
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasModelRouteRow)
                .where(default_column.is_(True))
                .order_by(AtlasModelRouteRow.route_id)
                .limit(1)
            )
            return _route_record(row) if row is not None else None

    def tested_route(self) -> ModelRouteRecord | None:
        with self.session_factory() as session:
            snapshot = model_routing.runtime_joined_snapshot(session)
            return snapshot[0] if snapshot is not None else None
    def tested_vision_default_route(self) -> ModelRouteRecord | None:
        with self.session_factory() as session:
            snapshot = model_routing.runtime_joined_snapshot(
                session, default_purpose="vision"
            )
            if snapshot is None or not snapshot[0].supports_vision:
                return None
            return snapshot[0]

    def provider_attempt_snapshot(
        self,
        route_id: str | None = None,
    ) -> ProviderAttemptSnapshot | None:
        with self.session_factory() as session:
            snapshot = model_routing.runtime_joined_snapshot(session, route_id)
            if snapshot is None:
                return None
            route, connection, secret = snapshot
            return ProviderAttemptSnapshot(route, connection, secret)

    def get_replay(
        self,
        idempotency_key: str,
    ) -> ModelRoutingReplayRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasModelRoutingReplayRow).where(
                    AtlasModelRoutingReplayRow.idempotency_key
                    == idempotency_key
                )
            )
            return (
                ModelRoutingReplayRecord(
                    **{
                        column.name: getattr(row, column.name)
                        for column in AtlasModelRoutingReplayRow.__table__.columns
                    }
                )
                if row is not None
                else None
            )

    def get_invocation(self, invocation_id: str) -> ModelInvocationRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasModelInvocationRow).where(
                    AtlasModelInvocationRow.invocation_id == invocation_id
                )
            )
            if row is None:
                return None
            return _invocation_record(row)

    def invocation_for_execution_key(
        self,
        execution_key: str,
    ) -> ModelInvocationRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasModelInvocationRow).where(
                    AtlasModelInvocationRow.execution_key == execution_key
                )
            )
            return _invocation_record(row) if row is not None else None

    def list_invocations(
        self,
        *,
        limit: int = 200,
    ) -> list[ModelInvocationRecord]:
        _bounded_limit(limit, family="model invocation")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasModelInvocationRow)
                .order_by(
                    AtlasModelInvocationRow.created_at.desc(),
                    AtlasModelInvocationRow.invocation_id,
                )
                .limit(limit)
            ).all()
            return [_invocation_record(row) for row in rows]


@dataclass(frozen=True, slots=True)
class ProviderConnectionIntent:
    replay: ModelRoutingReplayRecord | None
    connections: tuple[ProviderConnectionRecord, ...]
    secrets: tuple[ProviderConnectionSecretRecord, ...]
    routes: tuple[ModelRouteRecord, ...]


@dataclass(frozen=True, slots=True)
class BeginProviderConnectionIntentCommand:
    session_factory: SessionFactory

    def execute(
        self, connection_ids: tuple[str, ...], idempotency_key: str
    ) -> ProviderConnectionIntent:
        reader = ModelRoutingReadModel(self.session_factory)
        connections = tuple(
            connection
            for connection_id in sorted(set(connection_ids))
            if (connection := reader.get_connection(connection_id)) is not None
        )
        return ProviderConnectionIntent(
            replay=reader.get_replay(idempotency_key),
            connections=connections,
            secrets=tuple(
                secret
                for connection in connections
                if (secret := reader.get_secret(connection.connection_id)) is not None
            ),
            routes=tuple(
                route
                for connection in connections
                for route in reader.linked_routes(connection.connection_id, limit=_MAX_READ_LIMIT)
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteConfigurationIntent:
    replay: ModelRoutingReplayRecord | None
    route: ModelRouteRecord | None
    connection: ProviderConnectionRecord | None
    secret: ProviderConnectionSecretRecord | None


@dataclass(frozen=True, slots=True)
class BeginRouteConfigurationIntentCommand:
    session_factory: SessionFactory

    def execute(self, idempotency_key: str, route_id: str) -> RouteConfigurationIntent:
        reader = ModelRoutingReadModel(self.session_factory)
        route = reader.get_route(route_id)
        connection = reader.get_connection(route.connection_id) if route else None
        secret = reader.get_secret(route.connection_id) if route else None
        return RouteConfigurationIntent(
            reader.get_replay(idempotency_key), route, connection, secret
        )


@dataclass(frozen=True, slots=True)
class DefaultRouteIntent:
    replay: ModelRoutingReplayRecord | None
    selected: ModelRouteRecord | None
    current_default: ModelRouteRecord | None
    purpose: Literal["text", "vision"]


@dataclass(frozen=True, slots=True)
class BeginDefaultRouteIntentCommand:
    session_factory: SessionFactory

    def execute(
        self,
        idempotency_key: str,
        route_id: str,
        purpose: Literal["text", "vision"],
    ) -> DefaultRouteIntent:
        reader = ModelRoutingReadModel(self.session_factory)
        return DefaultRouteIntent(
            reader.get_replay(idempotency_key),
            reader.get_route(route_id),
            reader.default_route(purpose),
            purpose,
        )


@dataclass(frozen=True, slots=True)
class InvocationLifecycleIntent:
    invocation: ModelInvocationRecord | None


@dataclass(frozen=True, slots=True)
class BeginInvocationLifecycleIntentCommand:
    session_factory: SessionFactory

    def execute(self, invocation_id: str) -> InvocationLifecycleIntent:
        return InvocationLifecycleIntent(
            ModelRoutingReadModel(self.session_factory).get_invocation(invocation_id)
        )


@dataclass(frozen=True, slots=True)
class FinalizeProviderConfigurationInput:
    connections: tuple[ProviderConnectionWrite, ...]
    secrets: tuple[ProviderConnectionSecretWrite, ...]
    routes: tuple[ModelRouteWrite, ...]
    replay: ModelRoutingReplayRecord
    audit_events: tuple[AuditEventRecord, ...]
    connection_disable_preconditions: tuple[
        ConnectionDisablePrecondition, ...
    ] = ()


@dataclass(frozen=True, slots=True)
class FinalizeProviderConfigurationCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeProviderConfigurationInput) -> None:
        _ModelRoutingCommandCoordinator(self.session_factory)._finalize(
            _ModelRoutingWriteBatch(
                provider_connections=request.connections,
                provider_secrets=request.secrets,
                routes=request.routes,
                replays=(request.replay,),
                audit_events=request.audit_events,
                connection_disable_preconditions=(
                    request.connection_disable_preconditions
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizeRouteConfigurationInput:
    routes: tuple[ModelRouteWrite, ...]
    replay: ModelRoutingReplayRecord
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class FinalizeRouteConfigurationCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeRouteConfigurationInput) -> None:
        _ModelRoutingCommandCoordinator(self.session_factory)._finalize(
            _ModelRoutingWriteBatch(
                routes=request.routes,
                replays=(request.replay,),
                audit_events=request.audit_events,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizeDefaultRouteInput:
    routes: tuple[ModelRouteWrite, ...]
    audit_events: tuple[AuditEventRecord, ...]
    replay: ModelRoutingReplayRecord | None = None
    connection_precondition: DefaultRouteConnectionPrecondition | None = None
    purpose: Literal["text", "vision"] = "text"


@dataclass(frozen=True, slots=True)
class FinalizeDefaultRouteCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeDefaultRouteInput) -> None:
        _ModelRoutingCommandCoordinator(self.session_factory)._finalize(
            _ModelRoutingWriteBatch(
                routes=request.routes,
                replays=(() if request.replay is None else (request.replay,)),
                audit_events=request.audit_events,
                default_route_connection_precondition=(
                    request.connection_precondition
                ),
                default_purpose=request.purpose,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizeInvocationLifecycleInput:
    invocation: ModelInvocationWrite
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class FinalizeInvocationLifecycleCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeInvocationLifecycleInput) -> bool:
        return _ModelRoutingCommandCoordinator(self.session_factory)._finalize(
            _ModelRoutingWriteBatch(
                invocations=(request.invocation,),
                audit_events=request.audit_events,
            )
        )


__all__ = [
    "BeginProviderConnectionIntentCommand",
    "BeginRouteConfigurationIntentCommand",
    "BeginDefaultRouteIntentCommand",
    "BeginInvocationLifecycleIntentCommand",
    "FinalizeDefaultRouteCommand",
    "FinalizeDefaultRouteInput",
    "FinalizeInvocationLifecycleCommand",
    "FinalizeInvocationLifecycleInput",
    "FinalizeProviderConfigurationCommand",
    "FinalizeProviderConfigurationInput",
    "FinalizeRouteConfigurationCommand",
    "FinalizeRouteConfigurationInput",
    "ModelInvocationWriter",
    "ModelInvocationWrite",
    "ModelRouteWrite",
    "ModelRoutingCurrentnessConflict",
    "ProviderConnectionIntent",
    "ProviderAttemptSnapshot",
    "RouteConfigurationIntent",
    "DefaultRouteIntent",
    "InvocationLifecycleIntent",
    "ModelRoutingReadModel",
    "ProviderConnectionSecretWrite",
    "ProviderConnectionWrite",
]
