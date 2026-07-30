from __future__ import annotations

from dataclasses import asdict, replace
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
import tempfile
from typing import Callable
from uuid import uuid4
from zipfile import ZipFile

from atlas_production.infrastructure.postgres_owner.model_routing import ModelRoutingReadModel
from atlas_production.infrastructure.postgres_owner.processing_registry import (
    BeginPluginLifecycleIntentCommand,
    BeginPluginPackageIntentCommand,
    BeginProcessingProfileIntentCommand,
    BeginProcessingRunIntentCommand,
    FinalizePluginLifecycleCommand,
    FinalizePluginLifecycleInput,
    FinalizePluginPackageCommand,
    FinalizePluginPackageInput,
    FinalizeProcessingProfileCommand,
    FinalizeProcessingProfileInput,
    FinalizeProcessingRunCommand,
    FinalizeProcessingRunInput,
    PluginActivationDependency,
    PluginDisablePrecondition,
    PluginVersionWrite,
    ProcessingProfileRevisionWrite,
    ProcessingRegistryReadModel,
    ProcessingRunWrite,
    ProfileActivationPrecondition,
    SessionFactory,
)
from atlas_production.modules.processing_pipeline.records import (
    PluginPackageRecord,
    PluginVersionRecord,
    PluginVersionRef,
    ProcessingIdempotencyRecord,
    ProcessingProfile,
    ProcessingProfileRevision,
)
from atlas_production.modules.processing_pipeline.registries import (
    ACTIVE_CHANNEL_REGISTRIES,
    ACTIVE_OUTPUT_CONTRACTS,
    ACTIVE_TRAIT_REGISTRIES,
    PRE_KPEL_ELEMENT_HINTS,
)
from atlas_production.modules.processing_pipeline.service import ProcessingRegistryError
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


class PostgresProcessingAdapter:
    """Typed route provider over exact processing intent/finalize commands.

    Each method loads only its named owner preimages, performs external work with
    no SQL Session open, and submits one typed finalize input.  It never creates
    a detached registry snapshot or a generic unit of work.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    @property
    def _reads(self) -> ProcessingRegistryReadModel:
        return ProcessingRegistryReadModel(self.session_factory)

    @staticmethod
    def _admin(actor) -> None:
        if actor is None:
            raise ProcessingRegistryError("unauthenticated", "auth.please_sign_in_before_using_admin_tools", 401)
        if actor.system_role != "admin":
            raise ProcessingRegistryError("access_denied", "permission.admin_permission_is_required", 403)

    @staticmethod
    def _digest(operation: str, payload: object) -> str:
        encoded = json.dumps([operation, payload], sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _audit(event_type: str, actor_id: str, target_ref: str, message_code: str, metadata: dict) -> AuditEventRecord:
        return AuditEventRecord(
            f"audit-{uuid4().hex}", event_type, actor_id, target_ref, None,
            message_code, metadata, utc_now_iso(),
        )

    @staticmethod
    def _replay(intent, operation: str, digest: str):
        replay = intent.replay
        if replay is None:
            return None
        if replay.operation != operation or replay.request_digest != digest:
            raise ProcessingRegistryError(
                "idempotency_conflict",
                "request.idempotency_key_was_already_used_for_a_different_request",
                409,
            )
        return replay.response_payload, replay.status_code

    @staticmethod
    def _idempotency(key: str, operation: str, digest: str, payload: object, status: int) -> ProcessingIdempotencyRecord:
        return ProcessingIdempotencyRecord(key, operation, digest, payload, status, utc_now_iso())

    @staticmethod
    def _version_view(record: PluginVersionRecord, active_refs: set[PluginVersionRef] | None = None) -> dict:
        descriptor = record.descriptor
        ref = PluginVersionRef(record.plugin_id, record.plugin_version, record.package_digest, record.runtime_profile)
        active = ref in (active_refs or set())
        return {
            **asdict(record), "active": active,
            "entrypoint": descriptor.get("entrypoint"),
            "accepted_media_types": descriptor.get("accepted_media_types", []),
            "produced_channels": descriptor.get("produced_channels", []),
            "output_contract_version": descriptor.get("output_contract_version"),
            "network_access": descriptor.get("network_access", False),
            "signature_key_id": descriptor.get("signature_key_id"),
            "sbom_present": bool(descriptor.get("sbom_present", False)),
            "sbom_spdx_version": descriptor.get("sbom_spdx_version"),
            "checksums_verified": bool(descriptor.get("checksums_verified", False)),
        }

    def _active_refs(self) -> set[PluginVersionRef]:
        refs: set[PluginVersionRef] = set()
        for revision in self._reads.active_profile_revisions():
            refs.update((revision.base_parser_plugin_ref, *revision.mandatory_processor_plugin_refs, *revision.eligible_processor_plugin_refs))
        return refs

    def list_plugins(self, actor):
        self._admin(actor)
        active = self._active_refs()
        return [self._version_view(item, active) for item in self._reads.list_plugin_versions(limit=500)]

    def get_plugin(self, actor, plugin_id: str, version: str):
        self._admin(actor)
        record = self._reads.get_plugin_version(plugin_id, version)
        if record is None:
            raise ProcessingRegistryError("plugin_not_found", "processing.plugin_version_was_not_found", 404)
        return self._version_view(record, self._active_refs())

    def upload_package(self, actor, filename: str, payload: bytes, key: str, *, artifact_store):
        self._admin(actor)
        if not filename.endswith(".atlas-plugin") or not payload:
            raise ProcessingRegistryError("invalid_package", "processing.exactly_one_non_empty_atlas_plugin_package_is_required", 422)
        fingerprint = hashlib.sha256(payload).hexdigest()
        digest = self._digest("package.upload", [filename, fingerprint])
        intent = BeginPluginPackageIntentCommand(self.session_factory).execute(key)
        if (replay := self._replay(intent, "package.upload", digest)) is not None:
            return replay

        # Package parsing/verification is external work and intentionally occurs
        # after the read-only intent and before the finalize transaction.
        try:
            from atlas_processing_sdk.package import verify_package
            with tempfile.NamedTemporaryFile(suffix=".atlas-plugin") as package_file:
                package_file.write(payload); package_file.flush()
                checked = verify_package(package_file.name, allow_unsigned=True)
            with ZipFile(BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.yaml"))
                config_schema = json.loads(archive.read("schemas/config.schema.json"))
                output_schema = json.loads(archive.read("schemas/output.schema.json"))
                sbom = json.loads(archive.read("sbom.spdx.json"))
                requirements = archive.read("requirements.lock").decode("utf-8")
            plugin_id, version = checked.plugin_id, checked.plugin_version
            package_digest = checked.package_digest
            runtime_profile = str(manifest["runtime_profile"])
            plugin_kind = str(manifest["kind"])
            runtime = self._reads.get_runtime_profile(runtime_profile)
            if runtime is None or not runtime.enabled:
                raise ValueError("unknown runtime profile")
            if not isinstance(config_schema, dict) or not isinstance(output_schema, dict) or sbom.get("spdxVersion") != "SPDX-2.3":
                raise ValueError("schema or SBOM invalid")
            for line in requirements.splitlines():
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                name, separator, package_version = value.partition("==")
                if not separator or runtime.available_packages.get(name.casefold()) != package_version:
                    raise ValueError("runtime dependency mismatch")
            status, trust, diagnostic = "uploaded", ("structurally_signed_pending_validation" if checked.signed else "unsigned_pending_validation"), "validation_required"
            descriptor = {name: manifest.get(name) for name in (
                "entrypoint", "accepted_media_types", "accepted_region_kinds",
                "accepted_element_kind_hints", "accepted_content_kind_hints",
                "produced_channels", "declared_capabilities", "output_contract_version",
                "network_access", "license_expression", "sdk_api_version", "signature_key_id",
            )}
            descriptor.update({
                "sbom_present": True,
                "sbom_spdx_version": sbom.get("spdxVersion"),
                "checksums_verified": True,
            })
        except Exception:
            plugin_id, version = f"quarantined-{fingerprint[:12]}", "0.0.0-invalid"
            package_digest, runtime_profile, plugin_kind = f"sha256:{fingerprint}", "unresolved", "unknown"
            status, trust, diagnostic, descriptor = "quarantined", "untrusted", "package_validation_failed", {}
        existing = self._reads.get_plugin_version(plugin_id, version)
        if existing is not None:
            if existing.package_digest != package_digest:
                raise ProcessingRegistryError("package_identity_conflict", "processing.plugin_id_and_version_already_exist_with_a_different_digest", 409)
            result = self._version_view(existing)
            replay_record = self._idempotency(key, "package.upload", digest, result, 200)
            audit = self._audit("processing_plugin.upload_replayed", actor.actor_id, f"processing-plugin:{plugin_id}:{version}", "processing.package_is_accepted_for_validation", {"plugin_id": plugin_id, "status": existing.status})
            FinalizePluginPackageCommand(self.session_factory).execute(FinalizePluginPackageInput((), (), replay_record, (audit,)))
            return result, 200
        artifact_ref = artifact_store.put(payload)
        now = utc_now_iso()
        package = PluginPackageRecord(f"pkg-{uuid4().hex}", plugin_id, version, package_digest, artifact_ref, len(payload), actor.actor_id, now)
        record = PluginVersionRecord(plugin_id, version, package_digest, runtime_profile, plugin_kind, status, trust, 1, now, now, diagnostic, descriptor=descriptor)
        result, status_code = self._version_view(record), 201
        replay_record = self._idempotency(key, "package.upload", digest, result, status_code)
        audit = self._audit("processing_plugin.quarantined" if status == "quarantined" else "processing_plugin.uploaded", actor.actor_id, f"processing-plugin:{plugin_id}:{version}", "processing.invalid_package_is_quarantined" if status == "quarantined" else "processing.package_is_accepted_for_validation", {"plugin_id": plugin_id, "plugin_version": version, "status": status})
        try:
            FinalizePluginPackageCommand(self.session_factory).execute(FinalizePluginPackageInput((package,), (PluginVersionWrite(record, None),), replay_record, (audit,)))
        except Exception:
            artifact_store.delete(artifact_ref)
            raise
        return result, status_code

    def mutate_plugin(self, actor, plugin_id: str, version: str, operation: str, key: str, expected_revision: int | None, *, artifact_store, runner):
        self._admin(actor)
        digest = self._digest(f"package.{operation}", [plugin_id, version, expected_revision])
        intent = BeginPluginLifecycleIntentCommand(self.session_factory).execute(key, plugin_id, version)
        if (replay := self._replay(intent, f"package.{operation}", digest)) is not None:
            return replay
        record = intent.plugin_version
        if record is None:
            raise ProcessingRegistryError("plugin_not_found", "processing.plugin_version_was_not_found", 404)
        if expected_revision is None:
            raise ProcessingRegistryError("revision_required", "request.if_match_or_expected_revision_is_required", 422)
        if record.revision != expected_revision:
            raise ProcessingRegistryError("revision_conflict", "processing.plugin_revision_changed", 412)
        updated = replace(record)
        if operation == "validate":
            if record.status not in {"uploaded", "validating"}:
                raise ProcessingRegistryError("invalid_lifecycle_transition", "processing.plugin_cannot_be_validated_from_its_current_state", 409)
            if intent.package is None:
                raise ProcessingRegistryError("package_artifact_missing", "artifact.plugin_package_artifact_is_unavailable", 409)
            # Validation reads package bytes outside SQL. A failed validation is
            # itself a durable quarantined lifecycle result.
            try:
                from atlas_processing_sdk.package import verify_package
                allow_unsigned = os.getenv("ATLAS_ALLOW_UNSIGNED_PLUGINS") == "true"
                with tempfile.NamedTemporaryFile(suffix=".atlas-plugin") as package_file:
                    package_file.write(artifact_store.get(intent.package.artifact_ref)); package_file.flush()
                    checked = verify_package(
                        package_file.name,
                        allow_unsigned=allow_unsigned,
                        trusted_public_keys=self._trusted_public_keys(),
                        require_trusted_signature=not allow_unsigned,
                    )
                updated.status = "verified"
                updated.trust_provenance = "trusted_signature" if checked.trusted else "unsigned_local_development"
                updated.diagnostic_code = None
            except Exception:
                updated.status, updated.trust_provenance = "quarantined", "untrusted"
                updated.diagnostic_code = "signature_or_package_validation_failed"
        elif operation == "canary":
            if record.status != "verified":
                raise ProcessingRegistryError("plugin_not_verified", "processing.only_verified_plugin_versions_may_run_a_canary", 409)
            if record.trust_provenance != "platform_builtin":
                if runner is None or intent.package is None:
                    updated.status, updated.diagnostic_code = "quarantined", "canary_failed"
                else:
                    try:
                        self._run_canary(updated, intent.package, artifact_store, runner)
                        updated.canary_passed_at, updated.diagnostic_code = utc_now_iso(), None
                    except Exception:
                        updated.status, updated.diagnostic_code = "quarantined", "canary_failed"
            else:
                updated.canary_passed_at, updated.diagnostic_code = utc_now_iso(), None
        elif operation == "disable":
            if record.status != "verified":
                raise ProcessingRegistryError("invalid_lifecycle_transition", "processing.only_verified_plugin_versions_may_be_disabled", 409)
            updated.status = "disabled"
        else:
            raise ProcessingRegistryError("invalid_operation", "common.rejected", 422)
        updated.revision += 1; updated.updated_at = utc_now_iso()
        result, status_code = self._version_view(updated), 200
        replay_record = self._idempotency(key, f"package.{operation}", digest, result, status_code)
        audit = self._audit(f"processing_plugin.{operation}", actor.actor_id, f"processing-plugin:{plugin_id}:{version}", "processing.plugin_mutation_is_recorded", {"plugin_id": plugin_id, "plugin_version": version, "status": updated.status, "operation": operation})
        FinalizePluginLifecycleCommand(self.session_factory).execute(FinalizePluginLifecycleInput((PluginVersionWrite(updated, record.revision),), replay_record, (audit,), PluginDisablePrecondition(plugin_id, version) if operation == "disable" else None))
        return result, status_code

    def create_profile(self, actor, request):
        self._admin(actor)
        operation, digest = "profile.create", self._digest("profile.create", request.model_dump())
        intent = BeginProcessingProfileIntentCommand(self.session_factory).execute(request.idempotency_key, request.profile_id)
        if (replay := self._replay(intent, operation, digest)) is not None: return replay
        if intent.profile is not None:
            raise ProcessingRegistryError("profile_exists", "processing.profile_already_exists", 409)
        record = ProcessingProfile(request.profile_id, request.display_name, actor.actor_id, utc_now_iso())
        result, status = asdict(record), 201
        audit = self._audit("processing_profile.created", actor.actor_id, f"processing-profile:{record.profile_id}", "processing.profile_is_created", {"profile_id": record.profile_id})
        FinalizeProcessingProfileCommand(self.session_factory).execute(FinalizeProcessingProfileInput((record,), (), self._idempotency(request.idempotency_key, operation, digest, result, status), (audit,)))
        return result, status

    def list_profiles(self, actor):
        self._admin(actor)
        profiles = self._reads.list_processing_profiles(limit=500)
        revisions = self._reads.list_profile_revisions(limit=500)
        grouped: dict[str, list[dict]] = {}
        for item in revisions: grouped.setdefault(item.profile_id, []).append(asdict(item))
        return [{**asdict(item), "revisions": grouped.get(item.profile_id, [])} for item in profiles]

    @staticmethod
    def _ref(value) -> PluginVersionRef:
        return PluginVersionRef(value.plugin_id, value.plugin_version, value.package_digest, value.runtime_profile)

    def _plugin_dependency(self, ref: PluginVersionRef, kind: str, *, canary: bool = False):
        record = self._reads.get_plugin_version(ref.plugin_id, ref.plugin_version)
        runtime = self._reads.get_runtime_profile(ref.runtime_profile)
        if not record or record.package_digest != ref.package_digest or record.runtime_profile != ref.runtime_profile or record.status != "verified" or record.plugin_kind != kind or not runtime or not runtime.enabled:
            raise ProcessingRegistryError("plugin_not_verified", "processing.profile_references_must_resolve_to_verified_exact_plugin_versions_with_the_required_kind_and_enabled_runtime", 422)
        if canary and record.trust_provenance != "platform_builtin" and not record.canary_passed_at:
            raise ProcessingRegistryError("plugin_canary_required", "processing.external_plugin_versions_require_a_passing_canary_before_activation", 422)
        return record

    def create_revision(self, actor, profile_id: str, request, expected_revision: int):
        self._admin(actor)
        operation, digest = "profile.revise", self._digest("profile.revise", [profile_id, expected_revision, request.model_dump()])
        intent = BeginProcessingProfileIntentCommand(self.session_factory).execute(request.idempotency_key, profile_id)
        if (replay := self._replay(intent, operation, digest)) is not None: return replay
        if intent.profile is None: raise ProcessingRegistryError("profile_not_found", "processing.profile_was_not_found", 404)
        current = max((item.revision for item in intent.revisions), default=0)
        if expected_revision != current: raise ProcessingRegistryError("revision_conflict", "processing.profile_revision_changed", 412)
        base = self._ref(request.base_parser_plugin_ref); mandatory = tuple(self._ref(v) for v in request.mandatory_processor_plugin_refs); eligible = tuple(self._ref(v) for v in request.eligible_processor_plugin_refs); priority = tuple(self._ref(v) for v in request.plugin_priority)
        if len({ref.plugin_id for ref in eligible}) != len(eligible): raise ProcessingRegistryError("duplicate_logical_plugin", "processing.a_profile_may_pin_only_one_version_of_each_processor_plugin_id", 422)
        if len(set(mandatory)) != len(mandatory) or not set(mandatory) <= set(eligible): raise ProcessingRegistryError("invalid_mandatory_subset", "common.mandatory_processors_must_be_a_unique_subset_of_eligible_processors", 422)
        if len(set(priority)) != len(priority) or set(priority) != set(eligible) or base in priority: raise ProcessingRegistryError("invalid_plugin_priority", "processing.plugin_priority_must_contain_every_eligible_processor_exactly_once_and_exclude_the_base_parser", 422)
        base_record = self._plugin_dependency(base, "base_parser")
        processor_records = [self._plugin_dependency(ref, "region_processor") for ref in eligible]
        channels = ACTIVE_CHANNEL_REGISTRIES.get(request.channel_registry_version)
        if channels is None or request.trait_registry_version not in ACTIVE_TRAIT_REGISTRIES: raise ProcessingRegistryError("processing_registry_version_inactive", "processing.profile_registry_version_is_not_active", 422)
        accepted = set(request.accepted_media_types)
        if not accepted <= set(base_record.descriptor.get("accepted_media_types", [])) or any(not accepted <= set(item.descriptor.get("accepted_media_types", [])) for item in processor_records): raise ProcessingRegistryError("profile_media_incompatible", "processing.base_parser_does_not_accept_every_profile_media_type", 422)
        for item in (base_record, *processor_records):
            if item.descriptor.get("output_contract_version") not in ACTIVE_OUTPUT_CONTRACTS or not set(item.descriptor.get("produced_channels", [])) <= set(channels) or not set(item.descriptor.get("accepted_element_kind_hints", [])) <= PRE_KPEL_ELEMENT_HINTS: raise ProcessingRegistryError("plugin_output_contract_inactive", "processing.profile_plugin_output_contract_is_not_active", 422)
        if request.planner_enabled and (not request.planner_model_route_id or not self.tested_model_route(request.planner_model_route_id)): raise ProcessingRegistryError("planner_route_not_tested", "model.planner_enabled_profiles_require_an_enabled_tested_model_route", 422)
        record = ProcessingProfileRevision(profile_id, current + 1, "draft", tuple(sorted(accepted)), base, mandatory, eligible, priority, request.planner_enabled, request.planner_model_route_id, request.channel_registry_version, request.trait_registry_version, request.max_regions_per_plan, request.max_modules_per_region, request.max_total_plugin_invocations, "mandatory_only", actor.actor_id, utc_now_iso())
        result, status = asdict(record), 201
        audit = self._audit("processing_profile.revision_created", actor.actor_id, f"processing-profile:{profile_id}:{record.revision}", "processing.profile_revision_is_created", {"profile_id": profile_id, "revision": record.revision})
        FinalizeProcessingProfileCommand(self.session_factory).execute(FinalizeProcessingProfileInput((), (ProcessingProfileRevisionWrite(record, None),), self._idempotency(request.idempotency_key, operation, digest, result, status), (audit,)))
        return result, status

    def activate_revision(self, actor, profile_id: str, revision: int, request):
        self._admin(actor)
        operation, digest = "profile.activate", self._digest("profile.activate", [profile_id, revision, request.model_dump()])
        intent = BeginProcessingProfileIntentCommand(self.session_factory).execute(request.idempotency_key, profile_id)
        if (replay := self._replay(intent, operation, digest)) is not None: return replay
        selected = next((item for item in intent.revisions if item.revision == revision), None)
        if selected is None: raise ProcessingRegistryError("profile_revision_not_found", "processing.profile_revision_was_not_found", 404)
        current = max((item.revision for item in intent.revisions), default=0)
        if request.expected_revision != current: raise ProcessingRegistryError("revision_conflict", "processing.profile_revision_changed", 412)
        records = [self._plugin_dependency(selected.base_parser_plugin_ref, "base_parser", canary=True), *(self._plugin_dependency(ref, "region_processor", canary=True) for ref in selected.eligible_processor_plugin_refs)]
        writes = [ProcessingProfileRevisionWrite(replace(selected, status="active", activated_at=utc_now_iso()), selected.status)]
        for prior in intent.revisions:
            if prior.status == "active" and prior.revision != revision: writes.append(ProcessingProfileRevisionWrite(replace(prior, status="deprecated"), prior.status))
        result, status = asdict(writes[0].record), 200
        deps = tuple(PluginActivationDependency(item.plugin_id, item.plugin_version, item.revision, item.status, item.trust_provenance, item.canary_passed_at) for item in records)
        precondition = ProfileActivationPrecondition(profile_id, revision, tuple(selected.accepted_media_types), deps)
        audit = self._audit("processing_profile.activated", actor.actor_id, f"processing-profile:{profile_id}:{revision}", "processing.profile_revision_is_activated", {"profile_id": profile_id, "revision": revision})
        FinalizeProcessingProfileCommand(self.session_factory).execute(FinalizeProcessingProfileInput((), tuple(writes), self._idempotency(request.idempotency_key, operation, digest, result, status), (audit,), precondition))
        return result, status

    def list_runs(self, actor):
        self._admin(actor)
        return [asdict(item) for item in sorted(self._reads.list_runs(limit=500), key=lambda item: item.created_at, reverse=True)]

    def get_run(self, actor, run_id: str):
        self._admin(actor); run = self._reads.get_run(run_id)
        if run is None: raise ProcessingRegistryError("run_not_found", "processing.run_was_not_found", 404)
        return {**asdict(run), "invocations": [asdict(item) for item in self._reads.list_parser_invocations(run_id)], "routing_decisions": [asdict(item) for item in self._reads.list_routing_decisions(run_id)], "trace": next((asdict(item) for item in self._reads.list_evidence_traces(run_id)), None)}

    def run_ingestion(self, actor, request, document_version_id: str, executor: Callable, *, after_run=None, scope_authorized=False):
        if scope_authorized:
            if actor is None: raise ProcessingRegistryError("unauthenticated", "processing.please_sign_in_before_processing_content", 401)
        else: self._admin(actor)
        operation, digest = "ingestion.run", self._digest("ingestion.run", [request.document_id, document_version_id])
        intent = BeginProcessingRunIntentCommand(self.session_factory).execute(request.idempotency_key, None)
        if (replay := self._replay(intent, operation, digest)) is not None: return replay
        try: run = executor()
        except Exception as exc:
            raise ProcessingRegistryError(getattr(exc, "safe_code", "processing_failed"), getattr(exc, "message_code", "processing.failed_safely"), getattr(exc, "status_code", 422), preserve_mutations=True) from exc
        result = asdict(run)
        if after_run is not None: result["operation_audit_event_ref"] = getattr(after_run(run), "event_id", None)
        audit = self._audit("processing_run.completed", actor.actor_id, f"processing-run:{run.run_id}", "processing.admin_action_was_recorded", {"run_id": run.run_id})
        FinalizeProcessingRunCommand(self.session_factory).execute(FinalizeProcessingRunInput((ProcessingRunWrite(run),), self._idempotency(request.idempotency_key, operation, digest, result, 200), (audit,)))
        return result, 200

    def retry_run(self, actor, run_id: str, request, *, retry_executor):
        self._admin(actor)
        operation, digest = "run.retry", self._digest("run.retry", run_id)
        intent = BeginProcessingRunIntentCommand(self.session_factory).execute(request.idempotency_key, run_id)
        if (replay := self._replay(intent, operation, digest)) is not None: return replay
        prior = intent.run
        if prior is None: raise ProcessingRegistryError("run_not_found", "processing.run_was_not_found", 404)
        if prior.status != "failed": raise ProcessingRegistryError("run_not_retryable", "processing.only_failed_processing_runs_may_be_retried", 409)
        if retry_executor is None: raise ProcessingRegistryError("processing_retry_unavailable", "processing.retry_is_unavailable", 503)
        try: retry = retry_executor(prior, actor)
        except ProcessingRegistryError: raise
        except Exception as exc: raise ProcessingRegistryError(getattr(exc, "safe_code", "processing_retry_failed"), getattr(exc, "message_code", "processing.retry_failed_safely"), getattr(exc, "status_code", 422)) from exc
        retry.attempt = prior.attempt + 1
        result, status = asdict(retry), 200
        audit = self._audit("processing_run.retried", actor.actor_id, f"processing-run:{retry.run_id}", "processing.retry_is_completed", {"prior_run_id": run_id, "run_id": retry.run_id, "status": retry.status})
        FinalizeProcessingRunCommand(self.session_factory).execute(FinalizeProcessingRunInput((ProcessingRunWrite(retry),), self._idempotency(request.idempotency_key, operation, digest, result, status), (audit,)))
        return result, status

    def tested_model_route(self, route_id: str) -> bool:
        route = ModelRoutingReadModel(self.session_factory).get_route(route_id)
        return bool(route and route.enabled and route.status == "test_passed")

    def tested_vision_model_route(self, route_id: str) -> bool:
        return ModelRoutingReadModel(self.session_factory).tested_vision_route(route_id) is not None

    @staticmethod
    def _trusted_public_keys() -> dict[str, str]:
        try:
            parsed = json.loads(os.getenv("ATLAS_PLUGIN_TRUSTED_KEYS_JSON", "{}"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {
            key: value for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str) and key and value
        }

    @staticmethod
    def _run_canary(record, package, artifact_store, runner) -> None:
        payload = artifact_store.get(package.artifact_ref)
        with ZipFile(BytesIO(payload)) as archive:
            fixture = json.loads(archive.read("fixtures/smoke-input.json"))
            expected = json.loads(archive.read("expected/smoke-output.json"))
        request = fixture.get("request")
        encoded_artifact = fixture.get("artifact_base64")
        if not isinstance(request, dict) or not isinstance(encoded_artifact, str):
            raise RuntimeError("canary fixture incomplete")
        artifact = base64.b64decode(encoded_artifact, validate=True)
        request = dict(request)
        request["deadline_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat()
        result = runner.invoke({
            "invocation_id": request.get("invocation_id", f"canary-{uuid4().hex}"),
            "runtime_profile": record.runtime_profile,
            "kind": record.plugin_kind,
            "entrypoint": record.descriptor["entrypoint"],
            "request": request,
            "artifact": artifact,
            "package": payload,
            "input_assets": fixture.get("input_assets", {}),
            "timeout_seconds": 60,
        })
        drafts = result.get("drafts")
        if not isinstance(drafts, list):
            raise RuntimeError("canary output mismatch")
        if "drafts" in expected and expected["drafts"] != drafts:
            raise RuntimeError("canary output mismatch")
        if len(drafts) < int(expected.get("minimum_drafts", 0)):
            raise RuntimeError("canary output count mismatch")
        subset = expected.get("draft_subset")
        if isinstance(subset, dict) and not any(
            all(draft.get(key) == value for key, value in subset.items())
            for draft in drafts
        ):
            raise RuntimeError("canary output subset mismatch")


__all__ = ["PostgresProcessingAdapter"]
