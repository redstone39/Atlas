from atlas_production.shared.user_messages import MessageParams, validate_message_reference


class ArtifactStorageError(RuntimeError):
    def __init__(self, error_code: str, message_code: str, status_code: int = 503, *, message_params: MessageParams | None = None):
        validated_params = validate_message_reference(message_code, message_params or {})
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.message_params = validated_params
        self.status_code = status_code


class ArtifactStorageUnavailable(ArtifactStorageError):
    def __init__(self, message_code: str = "artifact.storage_is_unavailable"):
        super().__init__("artifact_storage_unavailable", message_code, 503)


class ArtifactUploadPending(ArtifactStorageError):
    def __init__(self):
        super().__init__(
            "artifact_upload_pending",
            "artifact.upload_is_pending",
            409,
        )


class ArtifactFenceRejected(ArtifactStorageError):
    def __init__(self):
        super().__init__(
            "artifact_storage_fence_rejected",
            "artifact.storage_target_changed_before_commit",
            409,
        )


class ArtifactIntegrityError(ArtifactStorageError):
    def __init__(self, error_code: str = "artifact_integrity_mismatch"):
        super().__init__(
            error_code,
            "artifact.bytes_do_not_match_committed_metadata",
            409,
        )
