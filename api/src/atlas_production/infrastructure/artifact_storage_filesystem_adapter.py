from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import BinaryIO, Callable, Iterable, Iterator

from atlas_production.modules.artifact_storage.errors import (
    ArtifactIntegrityError,
    ArtifactStorageError,
    ArtifactStorageUnavailable,
)


_TEMP_RE = re.compile(r"^[0-9a-f]{32}\.tmp$")
_SHARD_RE = re.compile(r"^[0-9a-f]{2}$")
_BLOB_REF_RE = re.compile(
    r"^blobs/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}\.blob$"
)
_ROOT_MARKER_NAME = ".atlas-root-identity"


def opaque_blob_ref(opaque_name: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", opaque_name):
        raise ArtifactStorageError(
            "artifact_ref_invalid", 'artifact.storage_reference_is_invalid', 422
        )
    return f"blobs/{opaque_name[:2]}/{opaque_name[2:4]}/{opaque_name}.blob"


class LocalArtifactFilesystemAdapter:
    """Dirfd-anchored filesystem adapter for immutable opaque artifacts.

    Every component below the allowlisted anchor is opened with
    O_DIRECTORY|O_NOFOLLOW. Reads, creates, stats and unlinks then use *at-style
    dir_fd operations, removing the check/use symlink race of path-based IO.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        allowlisted_parents: tuple[str | Path, ...],
        create_layout: bool = True,
    ) -> None:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        required_dir_fd = {os.open, os.stat, os.unlink, os.mkdir}
        if not required_dir_fd.issubset(os.supports_dir_fd):
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        raw_root = Path(root).expanduser()
        if not raw_root.is_absolute():
            raise ArtifactStorageUnavailable("artifact.storage_reference_is_invalid")
        try:
            resolved = raw_root.resolve(strict=True)
            allowed = [
                Path(item).expanduser().resolve(strict=True)
                for item in allowlisted_parents
            ]
        except OSError as exc:
            raise ArtifactStorageUnavailable(
                "artifact.storage_mount_is_unavailable"
            ) from exc
        matching = [
            parent
            for parent in allowed
            if resolved == parent or resolved.is_relative_to(parent)
        ]
        if not matching:
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        self._anchor = max(matching, key=lambda item: len(item.parts))
        self._root_parts = resolved.relative_to(self._anchor).parts
        self._root = resolved
        try:
            with self._root_dir_fd() as root_fd:
                info = os.fstat(root_fd)
                if not stat.S_ISDIR(info.st_mode):
                    raise ArtifactStorageUnavailable(
                        "artifact.storage_reference_is_invalid"
                    )
                if create_layout:
                    for name in ("tmp", "blobs"):
                        child = self._open_child_dir(root_fd, name, create=True)
                        os.close(child)
                    self._ensure_root_identity_marker(root_fd)
                    self._sync_directory_fd(root_fd)
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_identity_digest(self) -> str:
        try:
            with self._root_dir_fd() as root_fd:
                fd = self._open_regular_at(root_fd, _ROOT_MARKER_NAME)
                try:
                    marker = os.read(fd, 33)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise self._translate_io(exc) from exc
        if len(marker) != 32:
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        return hashlib.sha256(b"atlas-root-v1\0" + marker).hexdigest()

    def probe_capabilities(self) -> dict[str, bool]:
        name = f".atlas-crud-probe-{os.urandom(16).hex()}"
        created = False
        removed = False
        try:
            with self._root_dir_fd() as root_fd:
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=root_fd,
                )
                created = True
                try:
                    self._write_all_fd(fd, b"created")
                finally:
                    os.close(fd)
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
                try:
                    self._write_all_fd(fd, b"modified")
                finally:
                    os.close(fd)
                fd = self._open_regular_at(root_fd, name)
                try:
                    observed = bytearray()
                    while chunk := os.read(fd, 9 - len(observed)):
                        observed.extend(chunk)
                        if len(observed) >= 9:
                            break
                    if observed != b"modified":
                        raise ArtifactStorageUnavailable(
                            "artifact.storage_is_not_writable"
                        )
                finally:
                    os.close(fd)
                os.unlink(name, dir_fd=root_fd)
                removed = True
            return {
                "create_file": True,
                "modify_file": True,
                "remove_file": True,
            }
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc
        finally:
            if created and not removed:
                try:
                    with self._root_dir_fd() as root_fd:
                        os.unlink(name, dir_fd=root_fd)
                except OSError:
                    pass

    def write_temp(
        self,
        opaque_temp_name: str,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        progress_callback: Callable[[int], None] | None = None,
    ) -> tuple[int, str]:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            raise ArtifactStorageError(
                "artifact_temp_name_invalid", 'artifact.temporary_name_is_invalid', 422
            )
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        fd: int | None = None
        total = 0
        digest = hashlib.sha256()
        try:
            with self._root_dir_fd() as root_fd:
                temp_fd = self._open_temp_parent(
                    root_fd, opaque_temp_name, create=True
                )
                try:
                    fd = os.open(
                        opaque_temp_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=temp_fd,
                    )
                    for chunk in chunks:
                        if not isinstance(chunk, (bytes, bytearray, memoryview)):
                            raise TypeError("artifact chunks must be bytes")
                        payload = memoryview(chunk)
                        if total + len(payload) > max_bytes:
                            raise ArtifactStorageError(
                                "artifact_too_large",
                                'artifact.exceeds_the_upload_size_limit',
                                413,
                            )
                        digest.update(payload)
                        while payload:
                            written = os.write(fd, payload)
                            if written <= 0:
                                raise OSError(errno.EIO, "short artifact write")
                            payload = payload[written:]
                            total += written
                        if progress_callback is not None:
                            progress_callback(total)
                    self._sync_file_fd(fd)
                    os.close(fd)
                    fd = None
                    self._sync_directory_fd(temp_fd)
                    # A post-write heartbeat prevents a slow controlled mount
                    # from expiring the lease immediately before publication.
                    if progress_callback is not None:
                        progress_callback(total)
                finally:
                    if fd is not None:
                        os.close(fd)
                    os.close(temp_fd)
            return total, digest.hexdigest()
        except ArtifactStorageError:
            self.remove_temp(opaque_temp_name)
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc

    def publish_no_overwrite(self, opaque_temp_name: str, opaque_ref: str) -> None:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            raise ArtifactStorageError(
                "artifact_temp_name_invalid", 'artifact.temporary_name_is_invalid', 422
            )
        try:
            with self._root_dir_fd() as root_fd:
                temp_fd = self._open_temp_parent(root_fd, opaque_temp_name)
                parent_fd, filename = self._open_ref_parent(
                    root_fd, opaque_ref, create=True
                )
                source_fd: int | None = None
                final_fd: int | None = None
                created_final = False
                temp_removed = False
                try:
                    source_fd = self._open_regular_at(temp_fd, opaque_temp_name)
                    final_fd = os.open(
                        filename,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=parent_fd,
                    )
                    created_final = True
                    while chunk := os.read(source_fd, 1024 * 1024):
                        self._write_all_fd(final_fd, chunk)
                    self._sync_file_fd(final_fd)
                    os.close(final_fd)
                    final_fd = None
                    self._sync_directory_fd(parent_fd)
                    os.unlink(opaque_temp_name, dir_fd=temp_fd)
                    temp_removed = True
                    self._sync_directory_fd(temp_fd)
                except Exception:
                    if created_final and not temp_removed:
                        try:
                            os.unlink(filename, dir_fd=parent_fd)
                        except OSError:
                            pass
                    raise
                finally:
                    if source_fd is not None:
                        os.close(source_fd)
                    if final_fd is not None:
                        os.close(final_fd)
                    os.close(parent_fd)
                    os.close(temp_fd)
        except FileExistsError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc

    def verify_full(
        self, opaque_ref: str, *, expected_size: int, expected_sha256: str
    ) -> None:
        digest = hashlib.sha256()
        size = 0
        try:
            with self.open_read(opaque_ref, expected_size=expected_size) as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc
        if size != expected_size:
            raise ArtifactIntegrityError("artifact_size_mismatch")
        if digest.hexdigest() != expected_sha256:
            raise ArtifactIntegrityError("artifact_checksum_mismatch")

    def final_exists(self, opaque_ref: str) -> bool:
        try:
            with self._root_dir_fd() as root_fd:
                parent_fd, filename = self._open_ref_parent(root_fd, opaque_ref)
                try:
                    info = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
                finally:
                    os.close(parent_fd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise self._translate_io(exc) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        return True

    def temp_exists(self, opaque_temp_name: str) -> bool:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            return False
        try:
            with self._root_dir_fd() as root_fd:
                temp_fd = self._open_temp_parent(root_fd, opaque_temp_name)
                try:
                    info = os.stat(
                        opaque_temp_name, dir_fd=temp_fd, follow_symlinks=False
                    )
                finally:
                    os.close(temp_fd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise self._translate_io(exc) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        return True

    def verify_temp_full(
        self,
        opaque_temp_name: str,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            raise ArtifactStorageError(
                "artifact_temp_name_invalid", 'artifact.temporary_name_is_invalid', 422
            )
        digest = hashlib.sha256()
        size = 0
        try:
            with self._root_dir_fd() as root_fd:
                temp_fd = self._open_temp_parent(root_fd, opaque_temp_name)
                try:
                    fd = self._open_regular_at(temp_fd, opaque_temp_name)
                finally:
                    os.close(temp_fd)
            with os.fdopen(fd, "rb", closefd=True) as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise self._translate_io(exc) from exc
        if size != expected_size:
            raise ArtifactIntegrityError("artifact_size_mismatch")
        if digest.hexdigest() != expected_sha256:
            raise ArtifactIntegrityError("artifact_checksum_mismatch")

    def cleanup_orphan_temps(
        self,
        eligible_names: set[str],
        *,
        older_than_epoch: float,
        max_objects: int = 1000,
        max_bytes: int = 10 * 1024 * 1024 * 1024,
        max_scan_objects: int = 5000,
    ) -> int:
        if max_objects < 1 or max_bytes < 0 or max_scan_objects < max_objects:
            raise ValueError("cleanup bounds must be positive")
        removed = 0
        removed_bytes = 0
        scanned = 0
        try:
            with self._root_dir_fd() as root_fd:
                for name in sorted(eligible_names):
                    if scanned >= max_scan_objects or removed >= max_objects:
                        break
                    scanned += 1
                    if not _TEMP_RE.fullmatch(name):
                        continue
                    try:
                        temp_fd = self._open_temp_parent(root_fd, name)
                    except FileNotFoundError:
                        continue
                    try:
                        try:
                            info = os.stat(name, dir_fd=temp_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_mtime > older_than_epoch
                            or removed_bytes + info.st_size > max_bytes
                        ):
                            continue
                        os.unlink(name, dir_fd=temp_fd)
                        removed += 1
                        removed_bytes += info.st_size
                        self._sync_directory_fd(temp_fd)
                    finally:
                        os.close(temp_fd)
        except OSError as exc:
            raise self._translate_io(exc) from exc
        return removed

    def open_read(self, opaque_ref: str, *, expected_size: int) -> BinaryIO:
        try:
            fd = self._open_ref_fd(opaque_ref)
            info = os.fstat(fd)
            if info.st_size != expected_size:
                os.close(fd)
                raise ArtifactIntegrityError("artifact_size_mismatch")
            return os.fdopen(fd, "rb", closefd=True)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact_final_missing") from exc
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc

    def remove_temp(self, opaque_temp_name: str) -> None:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            return
        try:
            with self._root_dir_fd() as root_fd:
                temp_fd = self._open_temp_parent(root_fd, opaque_temp_name)
                try:
                    try:
                        os.unlink(opaque_temp_name, dir_fd=temp_fd)
                    except FileNotFoundError:
                        return
                    self._sync_directory_fd(temp_fd)
                finally:
                    os.close(temp_fd)
        except OSError:
            return

    def list_blob_refs(self, *, max_refs: int | None = None) -> set[str]:
        refs: set[str] = set()
        try:
            with self._root_dir_fd() as root_fd:
                blobs_fd = self._open_child_dir(root_fd, "blobs")
                try:
                    for first in os.listdir(blobs_fd):
                        if not _SHARD_RE.fullmatch(first):
                            raise ArtifactStorageUnavailable(
                                "artifact.storage_reference_is_invalid"
                            )
                        first_fd = self._open_child_dir(blobs_fd, first)
                        try:
                            for second in os.listdir(first_fd):
                                if not _SHARD_RE.fullmatch(second):
                                    raise ArtifactStorageUnavailable(
                                        "artifact.storage_reference_is_invalid"
                                    )
                                second_fd = self._open_child_dir(first_fd, second)
                                try:
                                    for filename in os.listdir(second_fd):
                                        ref = f"blobs/{first}/{second}/{filename}"
                                        if not _BLOB_REF_RE.fullmatch(ref):
                                            raise ArtifactStorageUnavailable(
                                                "artifact.storage_reference_is_invalid"
                                            )
                                        info = os.stat(
                                            filename,
                                            dir_fd=second_fd,
                                            follow_symlinks=False,
                                        )
                                        if not stat.S_ISREG(info.st_mode):
                                            raise ArtifactStorageUnavailable(
                                                "artifact.storage_reference_is_invalid"
                                            )
                                        refs.add(ref)
                                        if max_refs is not None and len(refs) >= max_refs:
                                            return refs
                                finally:
                                    os.close(second_fd)
                        finally:
                            os.close(first_fd)
                finally:
                    os.close(blobs_fd)
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc
        return refs

    def remove_committed(self, opaque_ref: str) -> None:
        self._unlink_ref(opaque_ref)

    def check_available(self) -> None:
        try:
            with self._root_dir_fd() as root_fd:
                if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                    raise ArtifactStorageUnavailable(
                        "artifact.storage_reference_is_invalid"
                    )
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise self._translate_io(exc) from exc

    @contextmanager
    def _root_dir_fd(self) -> Iterator[int]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self._anchor, flags)
        try:
            for part in self._root_parts:
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def _open_child_dir(self, parent_fd: int, name: str, *, create: bool = False) -> int:
        if "/" in name or name in {"", ".", ".."}:
            raise ArtifactStorageUnavailable("artifact.storage_reference_is_invalid")
        created = False
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
        child_fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        if created:
            self._sync_directory_fd(parent_fd)
        return child_fd

    @staticmethod
    def _open_regular_at(parent_fd: int, name: str) -> int:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        return fd

    def _open_ref_parent(
        self, root_fd: int, opaque_ref: str, *, create: bool = False
    ) -> tuple[int, str]:
        parts = self._ref_parts(opaque_ref)
        current_fd: int | None = None
        parent_fd = root_fd
        try:
            for part in parts[:-1]:
                next_fd = self._open_child_dir(parent_fd, part, create=create)
                if current_fd is not None:
                    os.close(current_fd)
                current_fd = next_fd
                parent_fd = next_fd
            assert current_fd is not None
            return current_fd, parts[-1]
        except Exception:
            if current_fd is not None:
                os.close(current_fd)
            raise

    def _open_temp_parent(
        self,
        root_fd: int,
        opaque_temp_name: str,
        *,
        create: bool = False,
    ) -> int:
        if not _TEMP_RE.fullmatch(opaque_temp_name):
            raise ArtifactStorageError(
                "artifact_temp_name_invalid", 'artifact.temporary_name_is_invalid', 422
            )
        tmp_fd = self._open_child_dir(root_fd, "tmp")
        try:
            return self._open_child_dir(
                tmp_fd, opaque_temp_name[:2], create=create
            )
        finally:
            os.close(tmp_fd)

    def _open_ref_fd(self, opaque_ref: str) -> int:
        with self._root_dir_fd() as root_fd:
            parent_fd, filename = self._open_ref_parent(root_fd, opaque_ref)
            try:
                return self._open_regular_at(parent_fd, filename)
            finally:
                os.close(parent_fd)

    @staticmethod
    def _ref_parts(opaque_ref: str) -> tuple[str, ...]:
        if not _BLOB_REF_RE.fullmatch(opaque_ref):
            raise ArtifactStorageError(
                "artifact_ref_invalid", 'artifact.storage_reference_is_invalid', 422
            )
        pure = PurePosixPath(opaque_ref)
        if pure.is_absolute() or ".." in pure.parts:
            raise ArtifactStorageError(
                "artifact_ref_invalid", 'artifact.storage_reference_is_invalid', 422
            )
        return tuple(pure.parts)

    def _ensure_root_identity_marker(self, root_fd: int) -> None:
        try:
            fd = self._open_regular_at(root_fd, _ROOT_MARKER_NAME)
        except FileNotFoundError:
            fd = os.open(
                _ROOT_MARKER_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            try:
                marker = os.urandom(32)
                written = os.write(fd, marker)
                if written != len(marker):
                    raise OSError(errno.EIO, "short root identity marker write")
                self._sync_file_fd(fd)
            finally:
                os.close(fd)
            self._sync_directory_fd(root_fd)
            return
        try:
            marker = os.read(fd, 33)
            if len(marker) != 32:
                raise ArtifactStorageUnavailable(
                    "artifact.storage_reference_is_invalid"
                )
        finally:
            os.close(fd)

    def _unlink_ref(self, opaque_ref: str) -> None:
        try:
            with self._root_dir_fd() as root_fd:
                parent_fd, filename = self._open_ref_parent(root_fd, opaque_ref)
                try:
                    try:
                        os.unlink(filename, dir_fd=parent_fd)
                    except FileNotFoundError:
                        return
                    self._sync_directory_fd(parent_fd)
                finally:
                    os.close(parent_fd)
        except FileNotFoundError:
            # Idempotent cleanup: a crash may leave metadata for a blob whose
            # final path hierarchy was never published.
            return
        except OSError as exc:
            raise self._translate_io(exc) from exc

    @staticmethod
    def _sync_directory_fd(fd: int) -> None:
        LocalArtifactFilesystemAdapter._sync_fd_best_effort(
            fd, allow_directory_bad_fd=True
        )

    @staticmethod
    def _sync_file_fd(fd: int) -> None:
        LocalArtifactFilesystemAdapter._sync_fd_best_effort(fd)

    @staticmethod
    def _sync_fd_best_effort(
        fd: int, *, allow_directory_bad_fd: bool = False
    ) -> None:
        try:
            os.fsync(fd)
        except OSError as exc:
            unsupported = {
                errno.EACCES,
                errno.EINVAL,
                errno.EPERM,
                getattr(errno, "ENOSYS", -1),
                getattr(errno, "ENOTSUP", -1),
                getattr(errno, "EOPNOTSUPP", -1),
            }
            if allow_directory_bad_fd:
                unsupported.add(errno.EBADF)
            if exc.errno not in unsupported:
                raise

    @staticmethod
    def _write_all_fd(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short artifact write")
            view = view[written:]

    @staticmethod
    def _translate_io(exc: OSError) -> ArtifactStorageUnavailable:
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
            message = 'artifact.storage_has_no_available_capacity'
        elif exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}:
            message = 'artifact.storage_is_not_writable'
        elif exc.errno in {
            errno.ENOENT,
            errno.ENODEV,
            errno.ESTALE,
            errno.EIO,
            errno.ELOOP,
            errno.ENOTDIR,
        }:
            message = 'artifact.storage_mount_is_unavailable'
        else:
            message = 'artifact.storage_i_o_failed'
        return ArtifactStorageUnavailable(message)
