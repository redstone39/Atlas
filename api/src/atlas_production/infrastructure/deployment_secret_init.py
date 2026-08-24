from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_CREDENTIAL_DIRECTORY = Path("/run/atlas/secrets/credentials")
_NOTES_TRANSPORT_DIRECTORY = Path("/run/atlas/secrets/notes-transport")
_NOTES_TICKET_DIRECTORY = Path("/run/atlas/secrets/notes-ticket")
_NODE_UID = 1000
_NODE_GID = 1000


class DeploymentSecretError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretProjectionPaths:
    credential_directory: Path = _CREDENTIAL_DIRECTORY
    notes_transport_directory: Path = _NOTES_TRANSPORT_DIRECTORY
    notes_ticket_directory: Path = _NOTES_TICKET_DIRECTORY


def _non_empty(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name)
    return value if value is not None and value.strip() else None


def _validate_credential(key: str, key_id: str) -> tuple[str, str]:
    try:
        decoded = base64.b64decode(key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DeploymentSecretError("credential secret projection is invalid") from exc
    if len(decoded) != 32 or not key_id.strip():
        raise DeploymentSecretError("credential secret projection is invalid")
    return key, key_id.strip()


def _validate_note_secret(value: str) -> str:
    if not value.strip():
        raise DeploymentSecretError("Notes secret projection is invalid")
    return value


def _prepare_directory(path: Path, *, uid: int = 0, gid: int = 0) -> None:
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise DeploymentSecretError("secret projection directory is invalid")
        os.chmod(path, 0o700, follow_symlinks=False)
        os.chown(path, uid, gid, follow_symlinks=False)
    except DeploymentSecretError:
        raise
    except OSError as exc:
        raise DeploymentSecretError("secret projection directory is unavailable") from exc


def _read_regular_secret(path: Path, *, uid: int, gid: int) -> str | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentSecretError("secret projection is unavailable") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid
            or info.st_gid != gid
        ):
            raise DeploymentSecretError("secret projection is invalid")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 4096:
                raise DeploymentSecretError("secret projection is invalid")
        try:
            return b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentSecretError("secret projection is invalid") from exc
    finally:
        os.close(fd)


def _create_secret(path: Path, value: str, *, uid: int = 0, gid: int = 0) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise DeploymentSecretError("secret projection could not be created") from exc
    created = True
    try:
        payload = value.encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise DeploymentSecretError("secret projection write was interrupted")
            view = view[written:]
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        os.fsync(fd)
        created = False
    except Exception:
        try:
            os.close(fd)
        finally:
            if created:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        raise
    else:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_credential_projection(
    directory: Path,
    *,
    uid: int,
    gid: int,
) -> tuple[str, str] | None:
    key = _read_regular_secret(directory / "active_key", uid=uid, gid=gid)
    key_id = _read_regular_secret(directory / "key_id", uid=uid, gid=gid)
    if (key is None) != (key_id is None):
        raise DeploymentSecretError("credential secret projection is partial")
    return None if key is None else _validate_credential(key, key_id or "")


def _persist_credential_projection(
    directory: Path,
    credential: tuple[str, str],
    *,
    uid: int,
    gid: int,
) -> None:
    created: list[Path] = []
    try:
        for name, value in (("active_key", credential[0]), ("key_id", credential[1])):
            path = directory / name
            _create_secret(path, value, uid=uid, gid=gid)
            created.append(path)
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def _load_note_projection(directory: Path, *, uid: int, gid: int) -> str | None:
    value = _read_regular_secret(directory / "secret", uid=uid, gid=gid)
    return None if value is None else _validate_note_secret(value)


def materialize_deployment_secrets(
    *,
    environment: Mapping[str, str],
    paths: SecretProjectionPaths,
    root_uid: int = 0,
    root_gid: int = 0,
    node_uid: int = _NODE_UID,
    node_gid: int = _NODE_GID,
) -> dict[str, bool]:
    environment_key = _non_empty(environment, "ATLAS_CREDENTIAL_MASTER_KEY")
    environment_key_id = _non_empty(environment, "ATLAS_CREDENTIAL_MASTER_KEY_ID")
    if (environment_key is None) != (environment_key_id is None):
        raise DeploymentSecretError("credential environment override is partial")
    environment_credential = (
        _validate_credential(environment_key, environment_key_id)
        if environment_key is not None and environment_key_id is not None
        else None
    )

    environment_transport = _non_empty(
        environment, "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET"
    )
    environment_ticket = _non_empty(
        environment, "ATLAS_NOTES_COLLABORATION_TICKET_SECRET"
    )
    if environment_transport is not None:
        environment_transport = _validate_note_secret(environment_transport)
    if environment_ticket is not None:
        environment_ticket = _validate_note_secret(environment_ticket)

    _prepare_directory(
        paths.credential_directory,
        uid=root_uid,
        gid=root_gid,
    )
    _prepare_directory(
        paths.notes_transport_directory,
        uid=node_uid,
        gid=node_gid,
    )
    _prepare_directory(
        paths.notes_ticket_directory,
        uid=root_uid,
        gid=root_gid,
    )

    persisted_credential = _load_credential_projection(
        paths.credential_directory,
        uid=root_uid,
        gid=root_gid,
    )
    persisted_transport = _load_note_projection(
        paths.notes_transport_directory,
        uid=node_uid,
        gid=node_gid,
    )
    persisted_ticket = _load_note_projection(
        paths.notes_ticket_directory,
        uid=root_uid,
        gid=root_gid,
    )

    generated = {
        "credential": persisted_credential is None,
        "notes_transport": persisted_transport is None,
        "notes_ticket": persisted_ticket is None,
    }
    fallback_credential = persisted_credential or environment_credential or (
        base64.b64encode(os.urandom(32)).decode("ascii"),
        f"atlas-{secrets.token_hex(16)}",
    )
    fallback_transport = (
        persisted_transport or environment_transport or secrets.token_urlsafe(48)
    )
    fallback_ticket = (
        persisted_ticket or environment_ticket or secrets.token_urlsafe(48)
    )
    resolved_transport = environment_transport or fallback_transport
    resolved_ticket = environment_ticket or fallback_ticket
    if resolved_transport == resolved_ticket:
        raise DeploymentSecretError("Notes secret projections must be distinct")

    if persisted_credential is None:
        _persist_credential_projection(
            paths.credential_directory,
            fallback_credential,
            uid=root_uid,
            gid=root_gid,
        )
    if persisted_transport is None:
        _create_secret(
            paths.notes_transport_directory / "secret",
            fallback_transport,
            uid=node_uid,
            gid=node_gid,
        )
    if persisted_ticket is None:
        _create_secret(
            paths.notes_ticket_directory / "secret",
            fallback_ticket,
            uid=root_uid,
            gid=root_gid,
        )

    return generated


def main() -> int:
    try:
        generated = materialize_deployment_secrets(
            environment=os.environ,
            paths=SecretProjectionPaths(),
        )
    except DeploymentSecretError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"ready": True, "generated": generated},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
