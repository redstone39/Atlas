from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from atlas_production.modules.artifact_storage.errors import ArtifactStorageUnavailable


_TARGET_ID_RE = re.compile(r"^target-[a-z0-9-]{1,64}$")
_CONFIG_NAME_RE = re.compile(r"^(target-[a-z0-9-]{1,64})\.r([1-9][0-9]*)\.json$")


class RootOnlyStorageTargetConfig:
    """Immutable per-target raw-path records in an owner-only shared directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def config_key(target_id: str, revision: int) -> str:
        if not _TARGET_ID_RE.fullmatch(target_id) or revision < 1:
            raise ArtifactStorageUnavailable(
                "artifact.storage_reference_is_invalid"
            )
        return f"{target_id}.r{revision}.json"

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.directory.exists():
            return {}
        try:
            info = self.directory.stat()
            if not self.directory.is_dir() or info.st_mode & 0o077:
                raise ArtifactStorageUnavailable(
                    "artifact.storage_reference_is_invalid"
                )
            result: dict[str, dict[str, Any]] = {}
            for path in sorted(self.directory.iterdir()):
                match = _CONFIG_NAME_RE.fullmatch(path.name)
                if match is None:
                    continue
                file_info = path.stat(follow_symlinks=False)
                if path.is_symlink() or not path.is_file() or file_info.st_mode & 0o077:
                    raise ArtifactStorageUnavailable(
                        "artifact.storage_reference_is_invalid"
                    )
                payload = json.loads(path.read_text(encoding="utf-8"))
                target_id = match.group(1)
                revision = int(match.group(2))
                expected = {
                    "target_id": target_id,
                    "revision": revision,
                    "kind": payload.get("kind"),
                    "raw_path": payload.get("raw_path"),
                }
                if payload != expected:
                    raise ArtifactStorageUnavailable(
                        "artifact.storage_reference_is_invalid"
                    )
                raw_path = expected["raw_path"]
                if (
                    expected["kind"] not in {"local", "smb_mount"}
                    or not isinstance(raw_path, str)
                    or not Path(raw_path).is_absolute()
                    or target_id in result
                ):
                    raise ArtifactStorageUnavailable(
                        "artifact.storage_reference_is_invalid"
                    )
                result[target_id] = {
                    **expected,
                    "config_key": path.name,
                }
            return result
        except ArtifactStorageUnavailable:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ArtifactStorageUnavailable(
                "artifact.storage_is_temporarily_unavailable"
            ) from exc

    def put_target(
        self,
        *,
        target_id: str,
        revision: int,
        kind: str,
        raw_path: str,
    ) -> str:
        key = self.config_key(target_id, revision)
        if kind not in {"local", "smb_mount"} or not Path(raw_path).is_absolute():
            raise ArtifactStorageUnavailable("artifact.storage_reference_is_invalid")
        payload = {
            "target_id": target_id,
            "revision": revision,
            "kind": kind,
            "raw_path": raw_path,
        }
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            path = self.directory / key
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(path, flags, 0o600)
            except FileExistsError:
                existing = path.read_bytes()
                if existing != data:
                    raise ArtifactStorageUnavailable(
                        "artifact.target_configuration_identity_already_exists"
                    )
                return key
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short config write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return key
        except ArtifactStorageUnavailable:
            raise
        except OSError as exc:
            raise ArtifactStorageUnavailable(
                "artifact.storage_is_temporarily_unavailable"
            ) from exc
