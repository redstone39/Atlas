from __future__ import annotations

import asyncio
import base64
import builtins
import ctypes
from dataclasses import asdict
from datetime import datetime
import errno
import importlib
from importlib import abc as importlib_abc, machinery as importlib_machinery
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any

from atlas_processing_sdk import ParserInput, PluginContext, RegionInput


class SafeLogger:
    def info(self, *_args: object, **_kwargs: object) -> None: pass
    def warning(self, *_args: object, **_kwargs: object) -> None: pass
    def error(self, *_args: object, **_kwargs: object) -> None: pass


class ArtifactBroker:
    def __init__(self, artifact_path: Path, assets: dict[str, str]) -> None:
        self.artifact_path = artifact_path
        self.assets = dict(assets)
        self.output_assets: dict[str, str] = {}

    async def read_bytes(self, _artifact_ref: str) -> bytes:
        encoded = self.output_assets.get(_artifact_ref, self.assets.get(_artifact_ref))
        if encoded is not None:
            return base64.b64decode(encoded, validate=True)
        return self.artifact_path.read_bytes()

    async def parsed_pdf_pages(
        self, _artifact_ref: str, unit_start: int, unit_end: int
    ):
        from pypdf import PdfReader
        reader = PdfReader(self.artifact_path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("encrypted PDF requires a password")
        if unit_end > len(reader.pages):
            raise ValueError("requested page range exceeds PDF page count")
        result = []
        for index in range(unit_start, unit_end + 1):
            page = reader.pages[index - 1]
            text = (page.extract_text() or "").strip()
            ref = self.put_text(text) if text else None
            result.append((index, ref))
        return result

    async def read_text(self, ref: str) -> str:
        encoded = self.output_assets.get(ref, self.assets.get(ref))
        if encoded is None:
            raise ValueError("artifact reference is unavailable")
        return base64.b64decode(encoded).decode("utf-8")

    def put_text(self, value: str) -> str:
        ref = f"runner-text:{len(self.output_assets) + 1}"
        self.output_assets[ref] = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return ref

    def put_bytes(self, value: bytes) -> str:
        if not isinstance(value, bytes) or not value:
            raise ValueError("binary artifact output must be non-empty bytes")
        ref = f"runner-bytes:{len(self.output_assets) + 1}"
        self.output_assets[ref] = base64.b64encode(value).decode("ascii")
        return ref


def _deny_network() -> None:
    def denied(*_args: object, **_kwargs: object):
        raise PermissionError("plugin network access is denied")
    socket.create_connection = denied
    socket.socket.connect = denied


def _deny_network_syscalls() -> None:
    if not sys.platform.startswith("linux"):
        return
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(_SeccompArgCompare),
    ]
    seccomp.seccomp_rule_add_array.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("seccomp initialization failed")
    try:
        deny = 0x00050000 | errno.EPERM
        deny_as_unimplemented = 0x00050000 | errno.ENOSYS
        for syscall in (b"socket", b"socketpair", b"connect", b"bind", b"listen", b"accept", b"accept4", b"sendto", b"sendmsg", b"recvfrom", b"recvmsg", b"execve", b"execveat", b"fork", b"vfork"):
            number = seccomp.seccomp_syscall_resolve_name(syscall)
            if number >= 0 and seccomp.seccomp_rule_add(context, deny, number, 0) != 0:
                raise RuntimeError("seccomp isolation rule failed")
        clone3 = seccomp.seccomp_syscall_resolve_name(b"clone3")
        if clone3 >= 0 and seccomp.seccomp_rule_add(context, deny_as_unimplemented, clone3, 0) != 0:
            raise RuntimeError("seccomp clone3 isolation rule failed")
        clone = seccomp.seccomp_syscall_resolve_name(b"clone")
        if clone >= 0:
            comparison = _SeccompArgCompare(
                0, _SCMP_COMPARE_MASKED_EQ, _CLONE_THREAD, 0,
            )
            if seccomp.seccomp_rule_add_array(
                context, deny, clone, 1, ctypes.byref(comparison),
            ) != 0:
                raise RuntimeError("seccomp clone isolation rule failed")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("seccomp load failed")
    finally:
        seccomp.seccomp_release(context)


_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13
_FS_TRUNCATE = 1 << 14
_READ_ACCESS = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
_WRITE_ACCESS = (
    _READ_ACCESS | _FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE
    | _FS_MAKE_DIR | _FS_MAKE_REG | _FS_MAKE_SOCK | _FS_MAKE_FIFO
    | _FS_MAKE_SYM | _FS_REFER | _FS_TRUNCATE
)


_SCMP_COMPARE_MASKED_EQ = 7
_CLONE_THREAD = 0x00010000


class _SeccompArgCompare(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _landlock_syscalls() -> tuple[int, int, int]:
    machine = os.uname().machine
    if machine in {"x86_64", "aarch64", "arm64"}:
        return 444, 445, 446
    raise RuntimeError("unsupported Landlock architecture")


def _landlock_add_path(libc, add_rule: int, ruleset_fd: int, path: Path, access: int) -> None:
    if not path.exists():
        return
    if path.is_file():
        access &= ~(_FS_READ_DIR | _FS_EXECUTE)
    flags = os.O_PATH | os.O_CLOEXEC
    path_fd = os.open(path, flags)
    try:
        attr = _PathBeneathAttr(access, path_fd)
        if libc.syscall(add_rule, ruleset_fd, _LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(attr), 0) < 0:
            raise OSError(ctypes.get_errno(), f"Landlock rule failed for {path}")
    finally:
        os.close(path_fd)


def _apply_filesystem_isolation(payload: dict[str, Any]) -> None:
    if not sys.platform.startswith("linux"):
        return
    create_ruleset, add_rule, restrict_self = _landlock_syscalls()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(create_ruleset, 0, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        raise RuntimeError("Landlock is unavailable")
    handled = _WRITE_ACCESS
    if abi < 2:
        handled &= ~_FS_REFER
    if abi < 3:
        handled &= ~_FS_TRUNCATE
    attr = _RulesetAttr(handled)
    ruleset_fd = libc.syscall(create_ruleset, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset_fd < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")
    try:
        read_roots = {
            Path(sys.base_prefix), Path(sys.prefix), Path(__file__).resolve().parents[1],
            Path("/usr/lib"), Path("/usr/local/lib"), Path("/lib"), Path("/lib64"),
            # Native numerical runtimes inspect only these public CPU topology
            # files while selecting safe kernels; do not expose broader /proc or /sys.
            Path("/dev"), Path("/proc/cpuinfo"), Path("/sys/devices/system/cpu"),
        }
        wheel_path = payload.get("wheel_path")
        if isinstance(wheel_path, str) and wheel_path:
            read_roots.add(Path(wheel_path).resolve())
        model_path = os.environ.get("ATLAS_DOCLING_ARTIFACTS_PATH", "")
        if model_path:
            read_roots.add(Path(model_path).resolve())
        for path in read_roots:
            _landlock_add_path(libc, add_rule, ruleset_fd, path, _READ_ACCESS & handled)
        _landlock_add_path(libc, add_rule, ruleset_fd, Path.cwd().resolve(), _WRITE_ACCESS & handled)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "no_new_privs failed")
        if libc.syscall(restrict_self, ruleset_fd, 0) < 0:
            raise OSError(ctypes.get_errno(), "Landlock restrict_self failed")
    finally:
        os.close(ruleset_fd)


def _preload_trusted_runtime(runtime_profile: object) -> None:
    # Runtime dependencies are image-pinned Atlas code, not package-controlled
    # modules. Load them before adding the untrusted wheel to sys.path so native
    # runtimes can initialize without widening the plugin filesystem boundary.
    if runtime_profile == "atlas-python-v1":
        import pypdf  # noqa: F401
        import docx  # noqa: F401
        import openpyxl  # noqa: F401
        import pptx  # noqa: F401
        import PIL  # noqa: F401
        import rapidocr  # noqa: F401
    elif runtime_profile == "atlas-docling-cpu-v1":
        import torch

        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        from docling.datamodel.base_models import DocumentStream  # noqa: F401
        from docling.document_converter import DocumentConverter  # noqa: F401
    elif runtime_profile is not None:
        raise ValueError("unsupported runtime profile")


def _preload_trusted_builtin(payload: dict[str, Any]) -> None:
    if payload.get("trusted_builtin") is not True:
        return
    entrypoint = payload.get("entrypoint")
    allowed = {
        "atlas_plugin_runner.builtin_plugins:PypdfPlugin",
        "atlas_plugin_runner.builtin_plugins:InlineTextPlugin",
        "atlas_plugin_runner.builtin_plugins:CsvPlugin",
        "atlas_plugin_runner.builtin_plugins:DocxPlugin",
        "atlas_plugin_runner.builtin_plugins:PptxPlugin",
        "atlas_plugin_runner.builtin_plugins:XlsxPlugin",
        "atlas_plugin_runner.builtin_plugins:GenericTextPlugin",
        "atlas_plugin_runner.builtin_plugins:RapidOcrPlugin",
        "atlas_plugin_runner.builtin_plugins:DoclingLayoutPlugin",
    }
    if entrypoint not in allowed:
        raise ValueError("untrusted builtin entrypoint")
    importlib.import_module(entrypoint.split(":", 1)[0])


def _restrict_plugin_imports(
    wheel_path: object, allowed_plugin_import_roots: object
) -> None:
    if not isinstance(wheel_path, str) or not isinstance(
        allowed_plugin_import_roots, list
    ):
        return
    allowed = {
        value for value in allowed_plugin_import_roots
        if isinstance(value, str) and value
    }
    allowed.update(sys.stdlib_module_names)
    plugin_global_ids: set[int] = set()

    class TrackingPluginLoader(importlib_abc.Loader):
        def __init__(self, delegate: object) -> None:
            self.delegate = delegate

        def create_module(self, spec):
            create = getattr(self.delegate, "create_module", None)
            return create(spec) if create is not None else None

        def exec_module(self, module) -> None:
            plugin_global_ids.add(id(module.__dict__))
            execute_module = getattr(self.delegate, "exec_module", None)
            if execute_module is None:
                raise ImportError("plugin loader does not support isolated execution")
            execute_module(module)

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

    class TrackingPluginFinder(importlib_abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            spec = importlib_machinery.PathFinder.find_spec(fullname, path, target)
            origin = getattr(spec, "origin", None) if spec is not None else None
            if (
                spec is not None
                and spec.loader is not None
                and isinstance(origin, str)
                and wheel_path in origin
                and not isinstance(spec.loader, TrackingPluginLoader)
            ):
                spec.loader = TrackingPluginLoader(spec.loader)
            return spec

    sys.meta_path.insert(0, TrackingPluginFinder())

    def plugin_module_globals(namespace: object) -> bool:
        return isinstance(namespace, dict) and id(namespace) in plugin_global_ids

    def direct_audit_plugin_caller(frame) -> bool:
        while frame is not None:
            filename = frame.f_code.co_filename
            if filename != __file__ and "importlib" not in filename:
                return plugin_module_globals(frame.f_globals)
            frame = frame.f_back
        return False

    original_import = builtins.__import__
    original_compile = builtins.compile
    original_eval = builtins.eval
    original_exec = builtins.exec

    def plugin_caller() -> bool:
        return plugin_module_globals(sys._getframe(2).f_globals)

    def guarded_compile(
        source, filename, mode, flags=0, dont_inherit=False, optimize=-1,
        *, _feature_version=-1,
    ):
        if plugin_caller():
            raise ImportError("plugin dependency dynamic code compilation is not permitted")
        return original_compile(
            source, filename, mode, flags, dont_inherit, optimize,
            _feature_version=_feature_version,
        )

    def guarded_eval(source, globals=None, locals=None):
        if plugin_caller():
            raise ImportError("plugin dependency dynamic code evaluation is not permitted")
        return original_eval(source, globals, locals)

    def guarded_exec(source, globals=None, locals=None, *, closure=None):
        if plugin_caller():
            raise ImportError("plugin dependency dynamic code execution is not permitted")
        if closure is None:
            return original_exec(source, globals, locals)
        return original_exec(source, globals, locals, closure=closure)

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.partition(".")[0] if isinstance(name, str) else ""
        if root not in allowed and plugin_module_globals(globals):
            raise ImportError(
                f"plugin dependency is not declared by its runtime profile: {root}"
            )
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import
    builtins.compile = guarded_compile
    builtins.eval = guarded_eval
    builtins.exec = guarded_exec

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event != "import" or not args or not isinstance(args[0], str):
            return
        root = args[0].partition(".")[0]
        if root in allowed:
            return
        if direct_audit_plugin_caller(sys._getframe(1)):
            raise ImportError(
                f"plugin dependency is not declared by its runtime profile: {root}"
            )

    sys.addaudithook(audit)


async def execute(payload: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    _preload_trusted_runtime(payload.get("runtime_profile"))
    _preload_trusted_builtin(payload)
    wheel_path = payload.get("wheel_path")
    if wheel_path:
        sys.path.insert(0, str(Path(wheel_path)))
    module_name, separator, object_name = payload["entrypoint"].partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("invalid plugin entrypoint")
    # Kernel boundaries must precede import because module top-level statements
    # and constructors are untrusted plugin execution too.
    _apply_filesystem_isolation(payload)
    _deny_network_syscalls()
    _restrict_plugin_imports(
        wheel_path, payload.get("allowed_plugin_import_roots")
    )
    module = importlib.import_module(module_name)
    plugin = getattr(module, object_name)()
    artifact_path = Path(payload["artifact_path"]).resolve()
    if artifact_path.parent != Path.cwd().resolve() or not artifact_path.is_file():
        raise ValueError("invalid runner artifact path")
    broker = ArtifactBroker(artifact_path, payload.get("input_assets", {}))
    deadline = datetime.fromisoformat(payload["request"]["deadline_at"])
    request_payload = dict(payload["request"])
    request_payload["deadline_at"] = deadline
    if payload["kind"] == "base_parser":
        request = ParserInput(**request_payload)
        iterator = plugin.parse(request, PluginContext(broker, SafeLogger(), deadline))
    else:
        request = RegionInput(**request_payload)
        iterator = plugin.process(request, PluginContext(broker, SafeLogger(), deadline))
    drafts = [asdict(draft) async for draft in iterator]
    return {"drafts": drafts, "assets": broker.output_assets}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        _deny_network()
        result = asyncio.run(execute(payload))
        sys.stdout.write(json.dumps({"ok": True, **result}, separators=(",", ":")))
        return 0
    except BaseException as exc:
        if isinstance(exc, PermissionError):
            message = str(exc).lower()
            code = "plugin_network_denied" if "network" in message else "plugin_filesystem_denied"
        elif isinstance(exc, KeyboardInterrupt):
            # SIGINT/KeyboardInterrupt is an execution-carrier interruption,
            # not evidence that the document or plugin output is invalid.
            code = "plugin_interrupted"
        elif isinstance(exc, RuntimeError) and "landlock" in str(exc).lower():
            code = "runner_isolation_unavailable"
        elif isinstance(exc, ImportError) and "dependency" in str(exc).lower():
            code = "plugin_dependency_not_declared"
        else:
            code = "plugin_execution_failed"
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": {
                "code": code,
                "type": type(exc).__name__,
            },
        }, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
