from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
import re
from time import monotonic
from typing import Callable, Iterable
from urllib.parse import quote

from atlas_production.infrastructure.postgres_owner.artifact import (
    BeginArtifactWriteCommand,
    BeginArtifactWriteInput,
    CompleteArtifactReadInput,
    ClaimArtifactReconciliationCommand,
    ClaimArtifactReconciliationInput,
    CommandResult,
    FinalizeArtifactReconciliationCommand,
    FinalizeArtifactReconciliationInput,
    FinalizeArtifactWriteCommand,
    FinalizeArtifactWriteInput,
    HeartbeatArtifactWriteCommand,
    HeartbeatArtifactWriteInput,
    HeartbeatArtifactReadInput,
    PostCommitArtifactOpener,
    ProtectedArtifactOpenCommand,
    ProtectedArtifactOpenInput,
    ReconciliationClaim,
    TargetControlCommand,
    TargetControlInput,
)
from atlas_production.modules.artifact_storage.ports import ArtifactFilesystemPort
from atlas_production.modules.artifact_storage.records import (
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageRequestLeaseRecord,
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)


@dataclass(frozen=True, slots=True)
class ArtifactHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: Iterable[bytes]


@dataclass(frozen=True, slots=True)
class ArtifactWriteJourneyInput:
    begin: BeginArtifactWriteInput
    finalize: FinalizeArtifactWriteInput
    chunks: Iterable[bytes]
    max_bytes: int


@dataclass(frozen=True, slots=True)
class ArtifactWriteJourneyPlan:
    begin: BeginArtifactWriteInput
    chunks: Iterable[bytes] = field(repr=False)
    max_bytes: int
    finalize: Callable[
        [
            int,
            str,
            ArtifactWriteAttemptRecord,
            StorageRequestLeaseRecord,
            str,
        ],
        FinalizeArtifactWriteInput,
    ]


@dataclass(frozen=True, slots=True)
class OfflineTargetInput:
    command: TargetControlInput
    committed_blobs: tuple[StorageBlobRecord, ...]


@dataclass(frozen=True, slots=True)
class PortainerTargetInput:
    command: TargetControlInput
    committed_blobs: tuple[StorageBlobRecord, ...]
    generation: int
    generation_prefix: str
    switch_mode: str
    risk_acknowledgement: str | None = None


def _range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("invalid byte range")
    raw = value[6:]
    if "-" not in raw:
        raise ValueError("invalid byte range")
    first, last = raw.split("-", 1)
    if not first:
        if not last.isdigit() or int(last) <= 0:
            raise ValueError("invalid byte range")
        length = min(int(last), size)
        return size - length, size - 1
    if not first.isdigit() or (last and not last.isdigit()):
        raise ValueError("invalid byte range")
    start = int(first)
    end = size - 1 if not last else min(int(last), size - 1)
    if start >= size or start > end:
        raise ValueError("unsatisfied byte range")
    return start, end


def _empty_body() -> Iterable[bytes]:
    return ()


def _safe_header_filename(filename: str) -> str:
    candidate = filename.replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(
        character
        for character in candidate
        if ord(character) >= 32 and character != '"'
    ).strip()
    return candidate[:180].rstrip(". ") or "atlas-document"


@dataclass(frozen=True, slots=True)
class PostgresArtifactStorageAdapter:
    protected_open_command: ProtectedArtifactOpenCommand
    begin_write_command: BeginArtifactWriteCommand
    finalize_write_command: FinalizeArtifactWriteCommand
    target_control_command: TargetControlCommand
    claim_reconciliation_command: ClaimArtifactReconciliationCommand
    finalize_reconciliation_command: FinalizeArtifactReconciliationCommand
    filesystem: ArtifactFilesystemPort
    heartbeat_write_command: HeartbeatArtifactWriteCommand | None = None

    def open_original(
        self,
        request: ProtectedArtifactOpenInput,
        *,
        method: str,
        filename: str,
        if_match: str | None = None,
        if_none_match: str | None = None,
        if_range: str | None = None,
        range_header: str | None = None,
    ) -> ArtifactHttpResponse:
        if method not in {"GET", "HEAD"}:
            raise ValueError("direct original supports only GET or HEAD")
        if request.record_success_evidence != (method == "GET"):
            raise ValueError("protected evidence mode must match GET or HEAD semantics")
        opener = self.protected_open_command.execute(request)
        if opener.byte_size <= 0:
            self.protected_open_command.complete(
                CompleteArtifactReadInput(opener.read_lease)
            )
            raise ValueError("direct original requires positive committed byte size")
        etag = f'"{opener.checksum_sha256}"'
        safe_filename = _safe_header_filename(filename)
        suffix = PurePath(safe_filename).suffix.casefold()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ""
        headers = {
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Content-Disposition": (
                f'attachment; filename="atlas-document{suffix}"; '
                f"filename*=UTF-8''{quote(safe_filename, safe='')}"
            ),
        }
        if if_match and if_match != etag:
            self.protected_open_command.complete(
                CompleteArtifactReadInput(opener.read_lease)
            )
            return ArtifactHttpResponse(412, headers, _empty_body())
        if if_none_match == etag:
            self.protected_open_command.complete(
                CompleteArtifactReadInput(opener.read_lease)
            )
            return ArtifactHttpResponse(304, headers, _empty_body())
        if range_header and if_range not in {None, etag}:
            range_header = None
        try:
            selected = _range(range_header or None, opener.byte_size)
        except ValueError:
            self.protected_open_command.complete(
                CompleteArtifactReadInput(opener.read_lease)
            )
            return ArtifactHttpResponse(
                416,
                {**headers, "Content-Range": f"bytes */{opener.byte_size}"},
                _empty_body(),
            )
        start, end = selected or (0, opener.byte_size - 1)
        length = end - start + 1
        headers.update(
            {
                "Content-Type": opener.content_type,
                "Content-Length": str(length),
            }
        )
        status = 206 if selected is not None else 200
        if selected is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{opener.byte_size}"

        if method == "HEAD":
            self.protected_open_command.complete(
                CompleteArtifactReadInput(opener.read_lease)
            )
            return ArtifactHttpResponse(status, headers, _empty_body())

        def stream() -> Iterable[bytes]:
            lease = opener.read_lease
            handle = None
            heartbeat_started = monotonic()
            try:
                handle = self.filesystem.open_read(
                    opener.opaque_ref,
                    expected_size=opener.byte_size,
                )
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if monotonic() - heartbeat_started >= 30:
                        now = datetime.now(timezone.utc)
                        next_lease = replace(
                            lease,
                            last_heartbeat_at=now.isoformat(),
                            expires_at=(now + timedelta(seconds=90)).isoformat(),
                        )
                        lease = self.protected_open_command.heartbeat(
                            HeartbeatArtifactReadInput(lease, next_lease)
                        )
                        heartbeat_started = monotonic()
                    yield chunk
                if remaining:
                    raise OSError("artifact bytes ended before committed size")
            finally:
                if handle is not None:
                    handle.close()
                self.protected_open_command.complete(
                    CompleteArtifactReadInput(lease)
                )

        return ArtifactHttpResponse(status, headers, stream())

    def write_artifact(self, request: ArtifactWriteJourneyInput) -> CommandResult:
        if (
            request.begin.attempt != request.finalize.expected_attempt
            or request.begin.lease != request.finalize.expected_lease
        ):
            raise ValueError("artifact write journey begin/finalize preimages must agree")
        begin_result = self.begin_write_command.execute(request.begin)
        if (
            begin_result.replayed
            and not begin_result.continue_external_work
        ):
            return begin_result
        temp_name = request.begin.attempt.opaque_temp_name
        blob = request.finalize.blob
        try:
            size, digest = self.filesystem.write_temp(
                temp_name,
                request.chunks,
                max_bytes=request.max_bytes,
            )
            if size != blob.byte_size or digest != blob.checksum_value:
                raise ValueError("filesystem output does not match final artifact metadata")
            self.filesystem.publish_no_overwrite(temp_name, blob.opaque_ref)
            self.filesystem.verify_full(
                blob.opaque_ref,
                expected_size=blob.byte_size,
                expected_sha256=blob.checksum_value,
            )
        except Exception:
            try:
                self.filesystem.remove_temp(temp_name)
            except Exception:
                pass
            raise
        try:
            return self.finalize_write_command.execute(request.finalize)
        except Exception:
            try:
                self.filesystem.remove_temp(temp_name)
            except Exception:
                pass
            raise

    def heartbeat_write(
        self,
        request: HeartbeatArtifactWriteInput,
    ) -> CommandResult:
        if self.heartbeat_write_command is None:
            raise RuntimeError("artifact write heartbeat command is not configured")
        return self.heartbeat_write_command.execute(request)

    def write_artifact_plan(self, request: ArtifactWriteJourneyPlan) -> CommandResult:
        if self.heartbeat_write_command is None:
            raise RuntimeError("artifact upload plan requires heartbeat command")
        begin_result = self.begin_write_command.execute(request.begin)
        if begin_result.replayed and not begin_result.continue_external_work:
            return begin_result
        temp_name = request.begin.attempt.opaque_temp_name
        expected_attempt = request.begin.attempt
        expected_lease = request.begin.lease

        def heartbeat(_cumulative_bytes: int) -> None:
            nonlocal expected_attempt, expected_lease
            now = datetime.now(timezone.utc)
            observed_at = now.isoformat()
            next_expiry = (now + timedelta(seconds=90)).isoformat()
            attempt = replace(
                expected_attempt,
                lease_expires_at=next_expiry,
                last_heartbeat_at=observed_at,
                updated_at=observed_at,
            )
            lease = replace(
                expected_lease,
                expires_at=next_expiry,
                last_heartbeat_at=observed_at,
            )
            self.heartbeat_write_command.execute(
                HeartbeatArtifactWriteInput(
                    expected_attempt,
                    expected_lease,
                    attempt,
                    lease,
                    observed_at,
                )
            )
            expected_attempt = attempt
            expected_lease = lease

        try:
            size, digest = self.filesystem.write_temp(
                temp_name,
                request.chunks,
                max_bytes=request.max_bytes,
                progress_callback=heartbeat,
            )
            finalize = request.finalize(
                size,
                digest,
                expected_attempt,
                expected_lease,
                datetime.now(timezone.utc).isoformat(),
            )
            if (
                finalize.expected_attempt != expected_attempt
                or finalize.expected_lease != expected_lease
                or finalize.blob.byte_size != size
                or finalize.blob.checksum_value != digest
            ):
                raise ValueError("artifact upload plan finalize facts are cross-wired")
            self.filesystem.publish_no_overwrite(temp_name, finalize.blob.opaque_ref)
            self.filesystem.verify_full(
                finalize.blob.opaque_ref,
                expected_size=size,
                expected_sha256=digest,
            )
        except Exception:
            try:
                self.filesystem.remove_temp(temp_name)
            except Exception:
                pass
            raise
        try:
            return self.finalize_write_command.execute(finalize)
        except Exception:
            try:
                self.filesystem.remove_temp(temp_name)
            except Exception:
                pass
            raise

    def configure_offline_target(self, request: OfflineTargetInput) -> dict[str, object]:
        if request.command.operation.verification_mode != "full_hash":
            raise ValueError("offline target requires full_hash verification")
        if request.command.expected_committed_blobs != request.committed_blobs:
            raise ValueError("offline target must verify the command's exact blob set")
        if self.filesystem.list_blob_refs() != {
            blob.opaque_ref for blob in request.committed_blobs
        }:
            raise ValueError("offline target blob mapping does not match committed metadata")
        for blob in request.committed_blobs:
            self.filesystem.verify_full(
                blob.opaque_ref,
                expected_size=blob.byte_size,
                expected_sha256=blob.checksum_value,
            )
        result = self.target_control_command.execute(request.command)
        operation = request.command.operation
        return {
            "status": "succeeded",
            "operation_id": result.canonical_id or operation.operation_id,
            "committed_blob_count": operation.committed_blob_count,
            "total_bytes": operation.total_bytes,
            "blob_set_digest": operation.blob_set_digest,
            "storage_epoch": operation.fence.storage_epoch,
            "verification_mode": operation.verification_mode,
            "evidence_claim": operation.evidence_claim,
            "replayed": result.replayed,
        }

    def configure_portainer_target(
        self,
        request: PortainerTargetInput,
    ) -> dict[str, object]:
        operation = request.command.operation
        target = request.command.target
        if request.generation <= 0 or target.target_revision != request.generation:
            raise ValueError("Portainer generation must be positive and match target revision")
        if (
            not request.generation_prefix
            or target.target_id != f"{request.generation_prefix}{request.generation}"
            or request.command.generation_prefix != request.generation_prefix
            or request.command.monotonic_generation != request.generation
        ):
            raise ValueError("Portainer target generation identity is invalid")
        if request.switch_mode != "explicit":
            raise ValueError("Portainer target requires explicit switch mode")
        if request.command.expected_committed_blobs != request.committed_blobs:
            raise ValueError("Portainer target must use the command's exact blob set")
        expected = request.command.expected_control
        if (
            expected is not None
            and expected.active_target_revision is not None
            and expected.active_target_revision > request.generation
        ):
            raise ValueError("Portainer generation was superseded")
        if operation.verification_mode == "full_hash":
            if self.filesystem.list_blob_refs() != {
                blob.opaque_ref for blob in request.committed_blobs
            }:
                raise ValueError("Portainer blob mapping does not match committed metadata")
            for blob in request.committed_blobs:
                self.filesystem.verify_full(
                    blob.opaque_ref,
                    expected_size=blob.byte_size,
                    expected_sha256=blob.checksum_value,
                )
        elif (
            operation.verification_mode == "operator_accepted_unverified"
            and request.risk_acknowledgement
            != UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT
        ):
            raise ValueError("Portainer unverified target requires exact risk acknowledgement")
        result = self.target_control_command.execute(request.command)
        return {
            "status": "succeeded",
            "generation": request.generation,
            "verification_mode": operation.verification_mode,
            "evidence_claim": operation.evidence_claim,
            "committed_blob_count": operation.committed_blob_count,
            "storage_epoch": operation.fence.storage_epoch,
            "replayed": result.replayed,
        }

    def claim_reconciliation(
        self,
        request: ClaimArtifactReconciliationInput,
    ) -> ReconciliationClaim:
        return self.claim_reconciliation_command.execute(request)

    def finalize_reconciliation(
        self,
        request: FinalizeArtifactReconciliationInput,
    ) -> CommandResult:
        return self.finalize_reconciliation_command.execute(request)


__all__ = [
    "ArtifactHttpResponse",
    "ArtifactWriteJourneyInput",
    "ArtifactWriteJourneyPlan",
    "OfflineTargetInput",
    "PortainerTargetInput",
    "PostgresArtifactStorageAdapter",
]
