from __future__ import annotations

from atlas_production.shared.user_messages import MessageParams, validate_message_reference


class ProcessingRegistryError(ValueError):
    """Stable public error returned by the processing administration routes."""

    def __init__(
        self,
        error_code: str,
        message_code: str,
        status_code: int = 400,
        *,
        preserve_mutations: bool = False,
        message_params: MessageParams | None = None,
    ) -> None:
        validated = validate_message_reference(message_code, message_params or {})
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code
        self.message_params = validated
        self.preserve_mutations = preserve_mutations


class ProcessingRegistryService:
    """Narrow route-facing facade over the PostgreSQL processing provider.

    Business state and mutation planning belong to the concrete provider.  The
    facade deliberately has no operation ContextVar, detached state, hydration,
    snapshot diff, or generic persistence scope.
    """

    def __init__(self, repository, artifact_store, runner=None) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.runner = runner
        self._retry_executor = None

    @property
    def retry_executor(self):
        return self._retry_executor

    @retry_executor.setter
    def retry_executor(self, value) -> None:
        self._retry_executor = value

    def upload_package(self, actor, filename, payload, idempotency_key):
        return self.repository.upload_package(
            actor, filename, payload, idempotency_key,
            artifact_store=self.artifact_store,
        )

    def list_plugins(self, actor):
        return self.repository.list_plugins(actor)

    def get_plugin(self, actor, plugin_id, version):
        return self.repository.get_plugin(actor, plugin_id, version)

    def mutate_plugin(self, actor, plugin_id, version, operation, key, expected_revision):
        return self.repository.mutate_plugin(
            actor, plugin_id, version, operation, key, expected_revision,
            artifact_store=self.artifact_store, runner=self.runner,
        )

    def create_profile(self, actor, request):
        return self.repository.create_profile(actor, request)

    def list_profiles(self, actor):
        return self.repository.list_profiles(actor)

    def create_revision(self, actor, profile_id, request, expected_revision):
        return self.repository.create_revision(actor, profile_id, request, expected_revision)

    def activate_revision(self, actor, profile_id, revision, request):
        return self.repository.activate_revision(actor, profile_id, revision, request)

    def run_ingestion(
        self, actor, request, document_version_id, executor, *, after_run=None,
        scope_authorized=False,
    ):
        return self.repository.run_ingestion(
            actor, request, document_version_id, executor,
            after_run=after_run, scope_authorized=scope_authorized,
        )

    def list_runs(self, actor):
        return self.repository.list_runs(actor)

    def get_run(self, actor, run_id):
        return self.repository.get_run(actor, run_id)

    def retry_run(self, actor, run_id, request):
        return self.repository.retry_run(
            actor, run_id, request, retry_executor=self._retry_executor,
        )


__all__ = ["ProcessingRegistryError", "ProcessingRegistryService"]
