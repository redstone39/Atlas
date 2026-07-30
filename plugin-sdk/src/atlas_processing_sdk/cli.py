from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from .admin import AdminClient, AdminClientError, add_query
from .package import PackageError, build_package, verify_package
from .local import LocalExecutionError, run_conformance, run_local
from .config import load_config, login_and_store
from .scaffold import init_project


def _local_sdk_source() -> Path | None:
    """Return a source checkout only for a local-directory SDK install."""
    try:
        direct_url = importlib.metadata.distribution(
            "atlas-processing-sdk"
        ).read_text("direct_url.json")
        payload = json.loads(direct_url) if direct_url else {}
        parsed = urlparse(payload.get("url", ""))
        candidate = Path(unquote(parsed.path)) if parsed.scheme == "file" else None
        if (
            candidate is not None
            and payload.get("dir_info") is not None
            and candidate.is_dir()
            and (candidate / "pyproject.toml").is_file()
        ):
            return candidate.resolve()
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        pass

    for root in (Path.cwd(), *Path.cwd().parents):
        candidate = root / "production" / "plugin-sdk"
        if (candidate / "pyproject.toml").is_file():
            return candidate.resolve()

    module_source = Path(__file__).resolve()
    candidate = module_source.parents[2]
    if (
        candidate.name == "plugin-sdk"
        and (candidate / "pyproject.toml").is_file()
    ):
        return candidate
    return None


def _default_package_output(project: Path) -> Path:
    try:
        manifest = json.loads((project / "manifest.yaml").read_text(encoding="utf-8"))
        plugin_id = manifest["plugin_id"]
        plugin_version = manifest["plugin_version"]
        if not isinstance(plugin_id, str) or not isinstance(plugin_version, str):
            raise ValueError
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("manifest must define plugin_id and plugin_version") from exc
    return project / "dist" / f"{plugin_id}-{plugin_version}.atlas-plugin"


def _json_arg(value: str) -> object:
    try:
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            return json.loads(value)
        path = Path(value)
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"expected JSON or a JSON file: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    config = load_config()
    parser = argparse.ArgumentParser(prog="atlas-plugin")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("destination", type=Path)
    init.add_argument("--plugin-id")
    init.add_argument(
        "--kind",
        choices=("base-parser", "region-processor", "base_parser", "region_processor"),
        default="region-processor",
    )
    for name in ("dev", "test"):
        cmd = sub.add_parser(name)
        cmd.add_argument("project", nargs="?", type=Path, default=Path("."))
        if name == "dev":
            cmd.add_argument("--fixture", type=Path)
    build = sub.add_parser("build")
    build.add_argument("project", nargs="?", type=Path, default=Path("."))
    build.add_argument("--output", type=Path)
    build.add_argument("--signing-key", type=Path)
    build.add_argument("--key-id")
    verify = sub.add_parser("verify")
    verify.add_argument("package", type=Path)
    verify.add_argument("--allow-unsigned", action="store_true")
    admin = sub.add_parser("admin")
    admin.add_argument("--base-url", default=os.getenv("ATLAS_BASE_URL") or config.get("base_url"))
    admin.add_argument("--token", default=os.getenv("ATLAS_TOKEN") or config.get("token"))
    admin.add_argument("--idempotency-key")
    family = admin.add_subparsers(dest="family", required=True)
    login = family.add_parser("login")
    login.add_argument("--email", required=True)
    login.add_argument("--password-stdin", action="store_true", required=True)
    package = family.add_parser("package").add_subparsers(dest="action", required=True)
    upload = package.add_parser("upload"); upload.add_argument("package", type=Path)
    show = package.add_parser("show"); show.add_argument("plugin_id"); show.add_argument("version")
    list_cmd = package.add_parser("list"); list_cmd.add_argument("--status")
    for action in ("validate", "canary", "disable"):
        cmd = package.add_parser(action); cmd.add_argument("plugin_id"); cmd.add_argument("version"); cmd.add_argument("--expected-revision", required=True, type=int)
    profile = family.add_parser("profile").add_subparsers(dest="action", required=True)
    profile.add_parser("list")
    create = profile.add_parser("create"); create.add_argument("--body", required=True, type=_json_arg)
    revise = profile.add_parser("revise"); revise.add_argument("profile_id"); revise.add_argument("--body", required=True, type=_json_arg); revise.add_argument("--expected-revision", required=True, type=int)
    activate = profile.add_parser("activate"); activate.add_argument("profile_id"); activate.add_argument("revision", type=int); activate.add_argument("--expected-revision", required=True, type=int)
    run = family.add_parser("run").add_subparsers(dest="action", required=True)
    run_list = run.add_parser("list"); run_list.add_argument("--status")
    run_show = run.add_parser("show"); run_show.add_argument("run_id")
    retry = run.add_parser("retry"); retry.add_argument("run_id")
    return parser


def _admin(args: argparse.Namespace) -> object:
    if args.family == "login":
        return login_and_store(args.base_url or "", args.email, sys.stdin.readline().rstrip("\n"))
    client = AdminClient(args.base_url or "", args.token or "")
    idem = args.idempotency_key
    if args.family == "package":
        root = "/api/v1/admin/processing-plugins"
        if args.action == "upload": return client.upload(args.package, idempotency_key=idem)
        if args.action == "list": return client.request("GET", add_query(root, {"status": args.status}))
        path = f"{root}/{args.plugin_id}/versions/{args.version}"
        if args.action == "show": return client.request("GET", path)
        return client.request(
            "POST", f"{path}/{args.action}", body={},
            idempotency_key=idem, expected_revision=args.expected_revision,
        )
    if args.family == "profile":
        root = "/api/v1/admin/processing-profiles"
        if args.action == "list": return client.request("GET", root)
        if args.action == "create": return client.request("POST", root, body=args.body, idempotency_key=idem)
        if args.action == "revise": return client.request("POST", f"{root}/{args.profile_id}/revisions", body=args.body, idempotency_key=idem, expected_revision=args.expected_revision)
        return client.request("POST", f"{root}/{args.profile_id}/revisions/{args.revision}/activate", body={}, idempotency_key=idem, expected_revision=args.expected_revision)
    root = "/api/v1/admin/processing-runs"
    if args.action == "list": return client.request("GET", add_query(root, {"status": args.status}))
    if args.action == "show": return client.request("GET", f"{root}/{args.run_id}")
    return client.request("POST", f"{root}/{args.run_id}/retry", body={}, idempotency_key=idem)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            plugin_id = args.plugin_id or args.destination.name
            init_project(
                args.destination,
                plugin_id,
                args.kind.replace("-", "_"),
                sdk_source=_local_sdk_source(),
            ); result = {"project": str(args.destination), "plugin_id": plugin_id, "status": "created"}
        elif args.command == "dev":
            executed = run_local(args.project, args.fixture)
            result = {"status": "executed", **executed}
        elif args.command == "test":
            checked = run_conformance(args.project)
            result = {"status": "conformant", **checked}
        elif args.command == "build":
            output = args.output or _default_package_output(args.project)
            built = build_package(
                args.project,
                output,
                signing_key=args.signing_key,
                signing_key_id=args.key_id,
            ); result = {"output": str(output), "package_digest": built.package_digest, "signed": built.signed}
        elif args.command == "verify":
            checked = verify_package(args.package, allow_unsigned=args.allow_unsigned); result = {"status": "verified", "package_digest": checked.package_digest, "signed": checked.signed}
        else:
            result = _admin(args)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except LocalExecutionError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, sort_keys=True), file=sys.stderr)
        return 2
    except (PackageError, AdminClientError, ValueError, OSError) as exc:
        print(json.dumps({"error": {"code": "atlas_plugin_error", "message": str(exc)}}, sort_keys=True), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error": {"code": "plugin_execution_failed", "message": "Plugin execution failed safely."}}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
