from __future__ import annotations

import base64
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.parser import BytesHeaderParser
from email.policy import default as email_policy
import json
import logging
import os
from pathlib import Path
import resource
import signal
import sys
import tempfile
from zipfile import ZipFile

from atlas_processing_sdk.package import PackageError, verify_package
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invocation_id: str
    runtime_profile: str
    kind: str
    entrypoint: str
    request: dict
    input_assets: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)


STDOUT_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 1024 * 1024
READ_CHUNK = 64 * 1024
ARTIFACT_LIMIT = 256 * 1024 * 1024
INPUT_ASSETS_LIMIT = 128 * 1024 * 1024
METADATA_LIMIT = INPUT_ASSETS_LIMIT + 1024 * 1024
MULTIPART_HEADER_LIMIT = 64 * 1024


class InvalidMultipartEnvelope(ValueError):
    pass


@dataclass(frozen=True)
class MultipartPart:
    path: Path
    size: int


def _multipart_boundary(content_type: str | None) -> bytes:
    if not content_type:
        raise InvalidMultipartEnvelope
    headers = BytesHeaderParser(policy=email_policy).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode("latin-1")
    )
    if headers.get_content_type() != "multipart/form-data":
        raise InvalidMultipartEnvelope
    boundary = headers.get_boundary()
    if (
        not isinstance(boundary, str)
        or not 1 <= len(boundary) <= 70
        or "\r" in boundary
        or "\n" in boundary
    ):
        raise InvalidMultipartEnvelope
    try:
        return boundary.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidMultipartEnvelope from exc


async def _spool_request(request: Request, path: Path) -> None:
    with path.open("wb") as output:
        async for chunk in request.stream():
            output.write(chunk)


def _part_name(source) -> str:
    header_bytes = bytearray()
    while True:
        line = source.readline(MULTIPART_HEADER_LIMIT + 1)
        if not line or len(line) > MULTIPART_HEADER_LIMIT:
            raise InvalidMultipartEnvelope
        header_bytes.extend(line)
        if len(header_bytes) > MULTIPART_HEADER_LIMIT:
            raise InvalidMultipartEnvelope
        if line == b"\r\n":
            break
    try:
        headers = BytesHeaderParser(policy=email_policy).parsebytes(bytes(header_bytes))
    except Exception as exc:
        raise InvalidMultipartEnvelope from exc
    if headers.get_content_disposition() != "form-data":
        raise InvalidMultipartEnvelope
    name = headers.get_param("name", header="content-disposition")
    if name not in {"metadata", "artifact", "package"}:
        raise InvalidMultipartEnvelope
    return name


def _copy_part(source, output, boundary: bytes, limit: int | None) -> tuple[int, bool]:
    next_marker = b"\r\n--" + boundary + b"\r\n"
    closing_marker = b"\r\n--" + boundary + b"--"
    keep = max(len(next_marker), len(closing_marker)) - 1
    buffered = bytearray()
    size = 0
    while True:
        chunk = source.read(READ_CHUNK)
        if not chunk:
            raise InvalidMultipartEnvelope
        buffered.extend(chunk)
        next_index = buffered.find(next_marker)
        closing_index = buffered.find(closing_marker)
        candidates = [
            (index, closing)
            for index, closing in ((next_index, False), (closing_index, True))
            if index >= 0
        ]
        if candidates:
            index, closing = min(candidates, key=lambda candidate: candidate[0])
            content = bytes(buffered[:index])
            size += len(content)
            if limit is not None and size > limit:
                raise InvalidMultipartEnvelope
            output.write(content)
            consumed = index + len(closing_marker if closing else next_marker)
            unread = len(buffered) - consumed
            if unread:
                source.seek(-unread, os.SEEK_CUR)
            return size, closing
        if len(buffered) > keep:
            content = bytes(buffered[:-keep])
            size += len(content)
            if limit is not None and size > limit:
                raise InvalidMultipartEnvelope
            output.write(content)
            del buffered[:-keep]


def _parse_multipart(raw_path: Path, parts_dir: Path, boundary: bytes) -> dict[str, MultipartPart]:
    limits = {
        "metadata": METADATA_LIMIT,
        "artifact": ARTIFACT_LIMIT,
        # Package verification owns package validity. Preserve the existing
        # package-size contract while spooling its bytes instead of buffering.
        "package": None,
    }
    parts: dict[str, MultipartPart] = {}
    with raw_path.open("rb") as source:
        if source.read(len(boundary) + 4) != b"--" + boundary + b"\r\n":
            raise InvalidMultipartEnvelope
        for index in range(3):
            name = _part_name(source)
            if name in parts:
                raise InvalidMultipartEnvelope
            path = parts_dir / f"part-{index}"
            with path.open("wb") as output:
                size, closing = _copy_part(source, output, boundary, limits[name])
            parts[name] = MultipartPart(path=path, size=size)
            if closing:
                if source.read(2) not in {b"", b"\r\n"} or source.read(1):
                    raise InvalidMultipartEnvelope
                break
        else:
            raise InvalidMultipartEnvelope
    if set(parts) not in ({"metadata", "artifact"}, {"metadata", "artifact", "package"}):
        raise InvalidMultipartEnvelope
    return parts


class ChildOutputLimitExceeded(RuntimeError):
    pass


LOGGER = logging.getLogger("atlas_plugin_runner")


def _safe_child_diagnostic(stderr: bytes) -> str:
    message = stderr.decode("utf-8", errors="ignore").lower()
    if any(value in message for value in (
        "libgomp", "thread creation failed", "pthread_create",
        "resource temporarily unavailable",
    )):
        return "thread_creation_failed"
    if any(value in message for value in (
        "std::bad_alloc", "cannot allocate memory", "out of memory",
    )):
        return "memory_limit"
    return "process_exit"


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024 * 1024 * 1024, 8 * 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _trusted_keys() -> dict[str, str]:
    raw = os.environ.get("ATLAS_PLUGIN_TRUSTED_KEYS_JSON", "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(pem, str) for key, pem in value.items()):
        raise ValueError("invalid trust store")
    return value


def _host_command(host_path: Path, _workspace: Path) -> list[str]:
    return [sys.executable, "-I", str(host_path)]


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> bytes:
    output = bytearray()
    while chunk := await stream.read(READ_CHUNK):
        output.extend(chunk)
        if len(output) > limit:
            raise ChildOutputLimitExceeded
    return bytes(output)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            process.kill()
        await process.wait()


async def _run_host(
    host_path: Path,
    workspace: Path,
    host_payload: dict,
    timeout_seconds: int,
    disconnected: Callable[[], Awaitable[bool]],
) -> tuple[str | None, str | None]:
    runtime_profile = host_payload.get("runtime_profile")
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "ATLAS_DOCLING_ARTIFACTS_PATH": os.environ.get(
            "ATLAS_DOCLING_ARTIFACTS_PATH", ""
        ),
        "TMPDIR": str(workspace),
    }
    if runtime_profile == "atlas-docling-cpu-v1":
        child_env.update({
            "OMP_NUM_THREADS": "4",
            "OMP_THREAD_LIMIT": "8",
            "OMP_STACKSIZE": "1M",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "4",
            "KMP_STACKSIZE": "1m",
            "MALLOC_ARENA_MAX": "2",
            "DOCLING_NUM_THREADS": "4",
            "DOCLING_DEVICE": "cpu",
        })
    process = await asyncio.create_subprocess_exec(
        *_host_command(host_path, workspace),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace,
        env=child_env,
        preexec_fn=_limits,
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_capped(process.stdout, STDOUT_LIMIT))
    stderr_task = asyncio.create_task(_read_capped(process.stderr, STDERR_LIMIT))
    wait_task = asyncio.create_task(process.wait())
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    try:
        encoded = json.dumps(host_payload, separators=(",", ":")).encode()
        process.stdin.write(encoded)
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()
        while not wait_task.done():
            if stdout_task.done() and isinstance(stdout_task.exception(), ChildOutputLimitExceeded):
                raise ChildOutputLimitExceeded
            if stderr_task.done() and isinstance(stderr_task.exception(), ChildOutputLimitExceeded):
                raise ChildOutputLimitExceeded
            if await disconnected():
                await _terminate(process)
                return None, "plugin_cancelled"
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await _terminate(process)
                return None, "plugin_timeout"
            await asyncio.wait({wait_task, stdout_task, stderr_task}, timeout=min(0.05, remaining), return_when=asyncio.FIRST_COMPLETED)
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        if process.returncode != 0:
            LOGGER.warning(
                "plugin child failed category=%s returncode=%s",
                _safe_child_diagnostic(stderr),
                process.returncode,
            )
        if process.returncode != 0 and not stdout:
            return None, "plugin_crashed"
        return stdout.decode("utf-8", errors="strict"), None
    except ChildOutputLimitExceeded:
        await _terminate(process)
        return None, "plugin_output_limit_exceeded"
    except (BrokenPipeError, ConnectionResetError):
        await _terminate(process)
        return None, "plugin_crashed"
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    finally:
        for task in (stdout_task, stderr_task, wait_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)


def _safe_plugin_output(value: object) -> bool:
    forbidden = {
        "acl_decision", "canonical_status", "evidence_id", "citation_id",
        "audit_event", "index_operation", "database_write", "db_write",
        "storage_path", "raw_filename", "credential", "secret",
    }
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and key not in forbidden
            and _safe_plugin_output(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_safe_plugin_output(item) for item in value)
    if isinstance(value, str):
        return not value.startswith(("/", "file://")) and "../" not in value
    return value is None or isinstance(value, (bool, int, float))


def create_app() -> FastAPI:
    app = FastAPI(title="Atlas Plugin Runner", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "healthy", "runtime_profiles": ["atlas-python-v1", "atlas-docling-cpu-v1"]}

    @app.post("/internal/v1/invocations")
    async def invoke(request: Request):
        with tempfile.TemporaryDirectory(prefix="atlas-plugin-run-") as temp:
            temp_root = Path(temp)
            raw_path = temp_root / "invocation.multipart"
            parts_dir = temp_root / "parts"
            parts_dir.mkdir(mode=0o700)
            try:
                boundary = _multipart_boundary(request.headers.get("content-type"))
                await _spool_request(request, raw_path)
                parts = _parse_multipart(raw_path, parts_dir, boundary)
                metadata = json.loads(parts["metadata"].path.read_bytes())
                payload = InvocationRequest.model_validate(metadata)
                if sum(len(value) for value in payload.input_assets.values()) > INPUT_ASSETS_LIMIT:
                    raise InvalidMultipartEnvelope
            except ValidationError as exc:
                raise RequestValidationError(exc.errors()) from exc
            except Exception:
                return {"ok": False, "error": {"code": "invalid_artifact_envelope"}}
            workspace = Path(temp) / "workspace"
            workspace.mkdir(mode=0o700)
            artifact_path = workspace / "artifact.bin"
            os.replace(parts["artifact"].path, artifact_path)
            artifact_path.chmod(0o400)
            host_payload = payload.model_dump()
            host_payload["artifact_path"] = str(artifact_path)
            output_validator: Draft202012Validator | None = None
            package_part = parts.get("package")
            if package_part is not None:
                package_path = Path(temp) / "plugin.atlas-plugin"
                os.replace(package_part.path, package_path)
                package_path.chmod(0o400)
                try:
                    allow_unsigned = os.environ.get("ATLAS_ALLOW_UNSIGNED_PLUGINS", "false").lower() == "true"
                    checked = verify_package(
                        package_path,
                        allow_unsigned=allow_unsigned,
                        trusted_public_keys=_trusted_keys(),
                        require_trusted_signature=not allow_unsigned,
                    )
                    with ZipFile(package_path) as archive:
                        manifest = json.loads(archive.read("manifest.yaml"))
                        output_schema = json.loads(
                            archive.read("schemas/output.schema.json")
                        )
                        requirements = archive.read("requirements.lock").decode("utf-8")
                    output_validator = Draft202012Validator(output_schema)
                    host_payload["allowed_plugin_import_roots"] = sorted({
                        line.split("==", 1)[0].strip().replace("-", "_")
                        for line in requirements.splitlines()
                        if line.strip() and not line.lstrip().startswith("#")
                    } | {"atlas_processing_sdk", manifest["entrypoint"].split(":", 1)[0]})
                    if (
                        checked.package_digest != manifest.get("package_digest")
                        or payload.runtime_profile != manifest.get("runtime_profile")
                        or payload.kind != manifest.get("kind")
                        or payload.entrypoint != manifest.get("entrypoint")
                    ):
                        raise ValueError("invocation does not match package identity")
                except (PackageError, ValueError, json.JSONDecodeError):
                    return {"ok": False, "error": {"code": "plugin_package_untrusted"}}
                with ZipFile(package_path) as archive:
                    wheel_path = Path(temp) / "plugin.whl"
                    wheel_path.write_bytes(archive.read("plugin.whl"))
                    wheel_path.chmod(0o444)
                host_payload["wheel_path"] = str(wheel_path)
            else:
                builtin_contracts = {
                    "atlas_plugin_runner.builtin_plugins:PypdfPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:InlineTextPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:CsvPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:DocxPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:PptxPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:XlsxPlugin": ("atlas-python-v1", "base_parser"),
                    "atlas_plugin_runner.builtin_plugins:GenericTextPlugin": ("atlas-python-v1", "region_processor"),
                    "atlas_plugin_runner.builtin_plugins:RapidOcrPlugin": ("atlas-python-v1", "region_processor"),
                    "atlas_plugin_runner.builtin_plugins:DoclingLayoutPlugin": ("atlas-docling-cpu-v1", "region_processor"),
                }
                if builtin_contracts.get(payload.entrypoint) != (payload.runtime_profile, payload.kind):
                    return {"ok": False, "error": {"code": "builtin_contract_mismatch"}}
                builtin = Path(os.environ.get("ATLAS_BUILTIN_PLUGIN_ROOT", "/app/builtin"))
                host_payload["wheel_path"] = str(builtin / f"{payload.entrypoint.split(':', 1)[0]}.whl") if builtin.exists() else None
                # Derived only after the entrypoint matches the runner-owned
                # allowlist.  InvocationRequest forbids callers from setting it.
                host_payload["trusted_builtin"] = True
            try:
                output, child_error = await _run_host(
                    Path(__file__).with_name("host.py"), workspace, host_payload,
                    payload.timeout_seconds, request.is_disconnected,
                )
            except UnicodeDecodeError:
                return {"ok": False, "error": {"code": "runner_protocol_error"}}
            if child_error:
                return {"ok": False, "error": {"code": child_error}}
            assert output is not None
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                return {"ok": False, "error": {"code": "runner_protocol_error"}}
            if not isinstance(result, dict):
                return {"ok": False, "error": {"code": "runner_protocol_error"}}
            if result.get("ok") is True:
                drafts = result.get("drafts")
                assets = result.get("assets")
                if not isinstance(drafts, list) or len(drafts) > 10_000 or not isinstance(assets, dict):
                    return {"ok": False, "error": {"code": "runner_output_contract_invalid"}}
                if not _safe_plugin_output(drafts):
                    return {"ok": False, "error": {"code": "runner_output_contract_invalid"}}
                if output_validator is not None and any(
                    not output_validator.is_valid(draft) for draft in drafts
                ):
                    return {"ok": False, "error": {"code": "runner_output_schema_invalid"}}
                try:
                    decoded_size = sum(len(base64.b64decode(value, validate=True)) for value in assets.values() if isinstance(value, str))
                except Exception:
                    return {"ok": False, "error": {"code": "runner_output_contract_invalid"}}
                if len(assets) > 10_000 or decoded_size > 128 * 1024 * 1024 or len(assets) != sum(isinstance(value, str) for value in assets.values()):
                    return {"ok": False, "error": {"code": "plugin_output_limit_exceeded"}}
            return result

    return app
