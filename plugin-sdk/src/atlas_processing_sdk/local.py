"""Isolated local runner and explicit deterministic conformance checks."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
from dataclasses import asdict
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import shutil
import sys
import tempfile
from typing import Any
import venv
import zipfile

from .contracts import ParserInput, PluginContext, RegionInput, validate_plugin_output_payload
from .package import (
    PackageError,
    _parse_json,
    _validate_json_schema,
    build_package,
    validate_manifest,
    verify_package,
)


class LocalExecutionError(PackageError):
    """Safe typed error returned by the isolated local plugin process."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Logger:
    def info(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass
    def error(self, *_args, **_kwargs): pass


class _ArtifactBroker:
    def __init__(self, artifact: bytes, assets: dict[str, str]) -> None:
        self.artifact = artifact
        self.assets = dict(assets)
        self.outputs: dict[str, str] = {}

    async def read_bytes(self, _ref: str) -> bytes:
        return self.artifact

    async def read_text(self, ref: str) -> str:
        encoded = self.outputs.get(ref, self.assets.get(ref))
        if encoded is None:
            raise ValueError("artifact reference is unavailable")
        return base64.b64decode(encoded, validate=True).decode("utf-8")

    async def parsed_pdf_pages(self, _ref: str, unit_start: int, unit_end: int):
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(self.artifact))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("encrypted PDF requires a password")
        if unit_end > len(reader.pages):
            raise ValueError("requested page range exceeds PDF page count")
        pages = []
        for number in range(unit_start, unit_end + 1):
            page = reader.pages[number - 1]
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((number, self.put_text(text)))
        return pages

    def put_text(self, value: str) -> str:
        ref = f"local-output:{len(self.outputs) + 1}"
        self.outputs[ref] = base64.b64encode(value.encode()).decode("ascii")
        return ref


def _load_plugin(project: Path, entrypoint: str):
    module_name, separator, object_name = entrypoint.partition(":")
    if not separator or module_name != "plugin" or not object_name:
        raise PackageError("local runner requires plugin:<object> entrypoint")
    source = project / "src" / "plugin.py"
    spec = importlib.util.spec_from_file_location("atlas_isolated_plugin", source)
    if spec is None or spec.loader is None:
        raise PackageError("plugin source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin_type = getattr(module, object_name, None)
    if not isinstance(plugin_type, type):
        raise PackageError("plugin entrypoint must name a class")
    return plugin_type()


def _compatible_fixture(manifest: dict[str, Any], fixture: dict[str, Any]) -> None:
    request = fixture.get("request")
    if not isinstance(request, dict):
        raise PackageError("smoke fixture must contain a request object")
    if request.get("media_type") not in manifest["accepted_media_types"]:
        raise PackageError("smoke fixture media_type is unsupported by the plugin manifest")
    if manifest["kind"] == "region_processor":
        if request.get("region_kind") not in manifest["accepted_region_kinds"]:
            raise PackageError("smoke fixture region_kind is unsupported by the plugin manifest")
        if request.get("content_kind_hint") not in manifest["accepted_content_kind_hints"]:
            raise PackageError("smoke fixture content_kind_hint is unsupported by the plugin manifest")
        element = request.get("element_kind_hint")
        if element is not None and element not in manifest["accepted_element_kind_hints"]:
            raise PackageError("smoke fixture element_kind_hint is unsupported by the plugin manifest")
        _validate_locator(fixture["request"].get("locator_draft"), "smoke request locator")


def _validate_locator(value: Any, name: str) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("selector_kind"), str) or not value["selector_kind"]:
        raise PackageError(f"{name} requires selector_kind")
    if value["selector_kind"] == "page_region":
        page_number = value.get("page_number")
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number <= 0:
            raise PackageError(f"{name} page_region requires positive page_number")


async def _execute_once(project: Path, fixture_path: Path) -> dict[str, Any]:
    manifest = _parse_json((project / "manifest.yaml").read_bytes(), "manifest.yaml")
    if not isinstance(manifest, dict):
        raise PackageError("manifest.yaml must contain an object")
    validate_manifest(manifest)
    fixture = _parse_json(fixture_path.read_bytes(), "smoke fixture")
    if not isinstance(fixture, dict):
        raise PackageError("smoke fixture must contain an object")
    _compatible_fixture(manifest, fixture)
    request_payload = dict(fixture["request"])
    try:
        request_payload["deadline_at"] = datetime.fromisoformat(request_payload["deadline_at"])
        artifact = base64.b64decode(fixture["artifact_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("smoke fixture contains invalid deadline or artifact_base64") from exc
    broker = _ArtifactBroker(artifact, fixture.get("input_assets", {}))
    plugin = _load_plugin(project, manifest["entrypoint"])
    context = PluginContext(broker, _Logger(), request_payload["deadline_at"])
    request = ParserInput(**request_payload) if manifest["kind"] == "base_parser" else RegionInput(**request_payload)
    iterator = plugin.parse(request, context) if manifest["kind"] == "base_parser" else plugin.process(request, context)
    drafts = [
        json.loads(json.dumps(asdict(item), allow_nan=False))
        async for item in iterator
    ]
    output_schema = _parse_json((project / "schemas/output.schema.json").read_bytes(), "output schema")
    for index, draft in enumerate(drafts):
        validate_plugin_output_payload(draft, location=f"drafts[{index}]")
        _validate_json_schema(output_schema, draft, "output schema", f"$[{index}]")
        if manifest["kind"] == "base_parser":
            _validate_locator(draft.get("locator_draft"), f"drafts[{index}] locator")
            if draft.get("region_kind") not in manifest["accepted_region_kinds"] and manifest["accepted_region_kinds"]:
                raise PackageError("parser emitted an undeclared region kind")
        else:
            if draft.get("channel_id") not in manifest["produced_channels"]:
                raise PackageError("processor emitted an undeclared channel")
            if draft.get("output_contract_version") != manifest["output_contract_version"]:
                raise PackageError("processor emitted an unexpected output contract version")
    return {"drafts": drafts, "assets": broker.outputs}


async def _worker_execute(project: Path, fixture: Path, replay: bool) -> dict[str, Any]:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def denied(*_args, **_kwargs):
        raise PermissionError("plugin network access is denied")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    try:
        first = await _execute_once(project, fixture)
        if replay:
            second = await _execute_once(project, fixture)
            if first != second:
                raise PackageError("deterministic replay produced different output")
        return first
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create


def _worker_main(project: Path, fixture: Path, replay: bool) -> int:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(_worker_execute(project, fixture, replay))
        envelope = {"ok": True, "result": result}
    except ModuleNotFoundError:
        envelope = {"ok": False, "error": {"code": "plugin_runtime_prerequisite_missing", "message": "A declared runtime dependency is not installed in the local plugin environment."}}
    except PermissionError:
        envelope = {"ok": False, "error": {"code": "plugin_network_denied", "message": "Plugin network access is denied by local conformance policy."}}
    except (PackageError, ValueError, TypeError, AttributeError) as exc:
        envelope = {"ok": False, "error": {"code": "plugin_contract_error", "message": str(exc)}}
    except BaseException:
        envelope = {"ok": False, "error": {"code": "plugin_execution_failed", "message": "Plugin execution failed safely."}}
    sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if envelope["ok"] else 2


def _isolated_python(directory: Path) -> Path:
    venv.EnvBuilder(with_pip=False, clear=True, symlinks=True, system_site_packages=True).create(directory)
    executable = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        raise LocalExecutionError("venv_failed", "isolated plugin environment could not be created")
    return executable


def _safe_worker_environment() -> dict[str, str]:
    sdk_source = str(Path(__file__).resolve().parents[1])
    runtime_roots = [
        value for value in sys.path
        if value and (value.endswith("site-packages") or value.endswith("dist-packages"))
    ]
    return {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join(dict.fromkeys([sdk_source, *runtime_roots])),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "LC_ALL": "C.UTF-8",
    }


def _invoke_isolated(project: Path, fixture: Path, timeout_seconds: float, replay: bool) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    with tempfile.TemporaryDirectory(prefix="atlas-plugin-venv-") as temp:
        python = _isolated_python(Path(temp) / "venv")
        command = [str(python), "-m", "atlas_processing_sdk.local", "--worker", str(project.resolve()), str(fixture.resolve())]
        if replay:
            command.append("--replay")
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=_safe_worker_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalExecutionError("plugin_timeout", "plugin smoke execution timed out and was cancelled") from exc
    try:
        envelope = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LocalExecutionError("invalid_worker_response", "isolated plugin worker returned an invalid safe envelope") from exc
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
        code = error.get("code", "plugin_execution_failed") if isinstance(error, dict) else "plugin_execution_failed"
        message = error.get("message", "Plugin execution failed safely.") if isinstance(error, dict) else "Plugin execution failed safely."
        raise LocalExecutionError(str(code), str(message))
    result = envelope.get("result")
    if completed.returncode != 0 or not isinstance(result, dict):
        raise LocalExecutionError("invalid_worker_response", "isolated plugin worker returned an invalid safe envelope")
    return result


def run_local(project: Path, fixture: Path | None = None, *, check_replay: bool = False, timeout_seconds: float = 30) -> dict[str, Any]:
    project = project.resolve()
    fixture_path = (fixture or project / "fixtures/smoke-input.json").resolve()
    return _invoke_isolated(project, fixture_path, timeout_seconds, check_replay)


def _matches_expected(result: dict[str, Any], expected: dict[str, Any]) -> None:
    if "drafts" in expected and result["drafts"] != expected["drafts"]:
        raise PackageError("smoke output does not match expected/smoke-output.json")
    if len(result["drafts"]) < int(expected.get("minimum_drafts", 0)):
        raise PackageError("smoke output does not meet minimum_drafts")
    subset = expected.get("draft_subset")
    if subset is not None:
        if not isinstance(subset, dict) or not any(all(draft.get(key) == value for key, value in subset.items()) for draft in result["drafts"]):
            raise PackageError("smoke output does not contain the expected draft_subset")
    page_numbers = expected.get("locator_page_numbers")
    if page_numbers is not None:
        actual = [draft.get("locator_draft", {}).get("page_number") for draft in result["drafts"]]
        if not isinstance(page_numbers, list) or actual != page_numbers:
            raise PackageError("smoke output locator page numbers do not match locator_page_numbers")


def _hostile_source(kind: str, scenario: str) -> str:
    method = "parse" if kind == "base_parser" else "process"
    if scenario == "network":
        imports = "import socket"
        body = 'socket.create_connection(("127.0.0.1", 9))\n        if False:\n            yield None'
    elif scenario == "exception":
        imports = ""
        body = 'raise RuntimeError("/private/customer/secret.txt")\n        if False:\n            yield None'
    elif scenario == "timeout":
        imports = "import asyncio"
        body = 'await asyncio.sleep(60)\n        if False:\n            yield None'
    elif kind == "base_parser":
        imports = "from atlas_processing_sdk import SourceRegionDraft"
        if scenario == "owned":
            body = '''yield SourceRegionDraft(
            source_region_identity="page:1", region_kind="page", content_kind_hint="text",
            locator_draft={"selector_kind":"page_region","page_number":1,"nested":{"raw_filename":"secret.pdf"}},
        )'''
        else:
            body = '''yield SourceRegionDraft(
            source_region_identity="page:1", region_kind="page", content_kind_hint="text",
            locator_draft={"selector_kind":"page_region","page_number":1},
            native_artifact_ref="artifact:x/etc/passwd",
        )'''
    else:
        imports = "from atlas_processing_sdk import CandidateDraft"
        if scenario == "owned":
            body = '''yield CandidateDraft(
            source_region_ids=(request.region_id,), channel_id="generic_text",
            output_contract_version="eir-draft-v1", candidate_payload_ref="artifact:safe",
            table_grid={"nested":{"raw_filename":"secret.pdf"}},
        )'''
        else:
            body = '''yield CandidateDraft(
            source_region_ids=(request.region_id,), channel_id="generic_text",
            output_contract_version="eir-draft-v1", candidate_payload_ref="artifact:x/etc/passwd",
        )'''
    return f'''{imports}
class Plugin:
    async def {method}(self, request, context):
        {body}
'''


def _run_hostile_conformance(project: Path, manifest: dict[str, Any]) -> list[str]:
    completed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="atlas-plugin-hostile-") as temp:
        hostile = Path(temp) / "plugin"
        shutil.copytree(project, hostile, ignore=shutil.ignore_patterns(".atlas-plugin-test-*", "__pycache__"))
        fixture_path = hostile / "fixtures/smoke-input.json"
        fixture = _parse_json(fixture_path.read_bytes(), "smoke fixture")
        fixture["request"]["media_type"] = "application/x-atlas-unsupported"
        unsupported = Path(temp) / "unsupported.json"
        unsupported.write_text(json.dumps(fixture, sort_keys=True, separators=(",", ":")))
        try:
            run_local(hostile, unsupported)
        except LocalExecutionError as exc:
            if exc.code != "plugin_contract_error":
                raise PackageError("unsupported input did not fail with plugin_contract_error") from exc
        else:
            raise PackageError("unsupported input was not rejected")
        completed.append("unsupported_input_fail_closed")

        for scenario, expected_code, check_name in (
            ("owned", "plugin_contract_error", "nested_owned_field_rejection"),
            ("path", "plugin_contract_error", "raw_filename_path_rejection"),
            ("network", "plugin_network_denied", "network_deny"),
            ("exception", "plugin_execution_failed", "safe_exception_conversion"),
        ):
            (hostile / "src/plugin.py").write_text(_hostile_source(manifest["kind"], scenario))
            try:
                run_local(hostile)
            except LocalExecutionError as exc:
                if exc.code != expected_code:
                    raise PackageError(f"{check_name} returned unexpected safe error {exc.code}") from exc
                if scenario == "exception" and ("secret" in str(exc).lower() or "/private" in str(exc)):
                    raise PackageError("safe exception conversion leaked plugin exception content")
            else:
                raise PackageError(f"{check_name} hostile plugin was not rejected")
            completed.append(check_name)

        (hostile / "src/plugin.py").write_text(_hostile_source(manifest["kind"], "timeout"))
        try:
            run_local(hostile, timeout_seconds=0.2)
        except LocalExecutionError as exc:
            if exc.code != "plugin_timeout":
                raise PackageError("timeout canary returned an unexpected safe error") from exc
        else:
            raise PackageError("timeout canary did not terminate the plugin child")
        completed.append("timeout_cancellation_canary")
    return completed


def _run_archive_safety_canary() -> None:
    with tempfile.TemporaryDirectory(prefix="atlas-plugin-archive-canary-") as temp:
        for label, name, mode in (
            ("traversal", "../manifest.yaml", stat.S_IFREG),
            ("absolute", "/manifest.yaml", stat.S_IFREG),
            ("symlink", "manifest.yaml", stat.S_IFLNK),
        ):
            package = Path(temp) / f"{label}.atlas-plugin"
            with zipfile.ZipFile(package, "w") as archive:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (mode | 0o644) << 16
                archive.writestr(info, b"unsafe")
            try:
                verify_package(package, allow_unsigned=True)
            except PackageError:
                continue
            raise PackageError(f"archive safety canary accepted {label}")


def run_conformance(project: Path) -> dict[str, Any]:
    first_path = project / ".atlas-plugin-test-one.atlas-plugin"
    second_path = project / ".atlas-plugin-test-two.atlas-plugin"
    signed_path = project / ".atlas-plugin-test-signed.atlas-plugin"
    key_fd, key_name = tempfile.mkstemp(prefix="atlas-plugin-test-key-", suffix=".pem")
    os.close(key_fd)
    key_path = Path(key_name)
    checks: list[str] = []
    try:
        first = build_package(project, first_path)
        second = build_package(project, second_path)
        if first_path.read_bytes() != second_path.read_bytes() or first.package_digest != second.package_digest:
            raise PackageError("package build is not reproducible")
        verify_package(first_path, allow_unsigned=True)
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key_path.write_bytes(Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        signed = build_package(project, signed_path, signing_key=key_path, signing_key_id="local-conformance-key")
        if not signed.signed or not signed.trusted or signed.package_digest != first.package_digest:
            raise PackageError("signed conformance package did not verify with its generated Ed25519 key")
        _run_archive_safety_canary()
        checks.extend(["manifest_and_json_schema", "sdk_api_v1", "archive_and_path_safety", "runtime_dependency_wheel_sbom", "checksum_digest_signature"])
        result = run_local(project, check_replay=True)
        checks.extend(["isolated_subprocess_venv", "deterministic_replay", "output_contract", "locator_kpel_handoff_structure"])
        expected = _parse_json((project / "expected/smoke-output.json").read_bytes(), "smoke expectation")
        if not isinstance(expected, dict):
            raise PackageError("smoke expectation must be an object")
        _matches_expected(result, expected)
        manifest = _parse_json((project / "manifest.yaml").read_bytes(), "manifest.yaml")
        checks.extend(_run_hostile_conformance(project, manifest))
        return {"package_digest": first.package_digest, "draft_count": len(result["drafts"]), "checks": checks}
    finally:
        first_path.unlink(missing_ok=True)
        second_path.unlink(missing_ok=True)
        signed_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)


def _module_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("project", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    if not args.worker:
        return 2
    return _worker_main(args.project, args.fixture, args.replay)


if __name__ == "__main__":
    raise SystemExit(_module_main())
