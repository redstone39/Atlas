from __future__ import annotations

import re

from .errors import ArtifactFenceRejected, ArtifactStorageError
from .records import ArtifactRecord, StorageBlobRecord, StorageControlRecord, StorageFence


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_valid_sha256(value: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ArtifactStorageError(
            "artifact_checksum_invalid", 'artifact.checksum_metadata_is_invalid', 422
        )


def require_current_fence(control: StorageControlRecord, fence: StorageFence) -> None:
    current = control.active_fence()
    if current is None or current != fence:
        raise ArtifactFenceRejected()


def require_readable_metadata(
    control: StorageControlRecord,
    artifact: ArtifactRecord,
    blob: StorageBlobRecord,
    *,
    parent_status: str,
    active_processing_generation: int | None = None,
    allowed_parent_statuses: frozenset[str] = frozenset({"active"}),
) -> None:
    require_current_fence(control, blob.fence)
    if parent_status not in allowed_parent_statuses:
        raise ArtifactStorageError(
            "artifact_parent_inactive", 'common.the_parent_resource_is_not_active', 409
        )
    if artifact.lifecycle_status != "active" or blob.status != "committed":
        raise ArtifactStorageError(
            "artifact_not_readable", 'artifact.the_artifact_is_not_available', 409
        )
    if artifact.blob_id != blob.blob_id:
        raise ArtifactStorageError(
            "artifact_blob_identity_mismatch", 'artifact.the_artifact_is_not_available', 409
        )
    if (
        artifact.checksum_value != blob.checksum_value
        or artifact.byte_size != blob.byte_size
        or artifact.content_type != blob.content_type
    ):
        raise ArtifactStorageError(
            "artifact_blob_metadata_mismatch", 'artifact.the_artifact_is_not_available', 409
        )
    if (
        artifact.processing_generation is not None
        and artifact.processing_generation != active_processing_generation
    ):
        raise ArtifactStorageError(
            "artifact_generation_inactive", 'artifact.the_artifact_generation_is_not_active', 409
        )
