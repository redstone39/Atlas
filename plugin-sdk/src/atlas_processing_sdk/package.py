"""Canonical `.atlas-plugin` build and verification (`atlas-plugin-digest-v1`)."""

from __future__ import annotations

import base64
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import json
import os
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


DIGEST_ALGORITHM = "atlas-plugin-digest-v1"
MAX_FILES = 256
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
REQUIRED_PAYLOADS = {
    "manifest.yaml", "schemas/config.schema.json", "schemas/output.schema.json",
    "fixtures/smoke-input.json", "expected/smoke-output.json",
    "requirements.lock", "sbom.spdx.json",
}
RUNTIME_PACKAGES = {
    "atlas-python-v1": {"pypdf": "6.0.0"},
    "atlas-docling-cpu-v1": {"docling": "2.111.0", "pypdf": "6.0.0"},
}
RUNTIME_PACKAGE_LICENSES = {
    "pypdf": "BSD-3-Clause",
    "docling": "MIT",
}
REQUIRED_MANIFEST = {
    "plugin_id", "plugin_version", "sdk_api_version", "runtime_profile",
    "kind", "entrypoint", "accepted_media_types", "accepted_region_kinds",
    "accepted_element_kind_hints", "accepted_content_kind_hints",
    "produced_channels", "declared_capabilities", "config_schema_ref",
    "output_contract_version", "timeout_policy_ref", "network_access",
    "license_expression",
}
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationResult:
    package_digest: str
    plugin_id: str
    plugin_version: str
    signed: bool
    trusted: bool
    files: tuple[str, ...]


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PackageError(f"value is not canonical JSON: {exc}") from exc


def _parse_json(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageError(f"invalid JSON-compatible {name}: {exc}") from exc


def _normalize_path(raw: str) -> str:
    value = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    path = PurePosixPath(value)
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value) or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PackageError(f"unsafe archive path: {raw!r}")
    return str(path)


def _validate_names(names: list[str]) -> list[str]:
    if len(names) > MAX_FILES:
        raise PackageError("archive contains too many files")
    normalized: list[str] = []
    seen: set[str] = set()
    folded: set[str] = set()
    for raw in names:
        name = _normalize_path(raw)
        key = name.casefold()
        if name in seen or key in folded:
            raise PackageError(f"duplicate or case-normalized collision: {name}")
        seen.add(name)
        folded.add(key)
        normalized.append(name)
    return normalized


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_MANIFEST - set(manifest))
    if missing:
        raise PackageError(f"manifest missing fields: {', '.join(missing)}")
    for key in (
        "plugin_id", "plugin_version", "runtime_profile", "kind", "entrypoint",
        "config_schema_ref", "output_contract_version", "timeout_policy_ref",
        "license_expression",
    ):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise PackageError(f"manifest {key} must be a non-empty string")
    if not SEMVER.fullmatch(manifest["plugin_version"]):
        raise PackageError("manifest plugin_version must be SemVer")
    if manifest["sdk_api_version"] != 1 or isinstance(manifest["sdk_api_version"], bool):
        raise PackageError("manifest sdk_api_version must be exactly 1")
    if manifest["kind"] not in {"base_parser", "region_processor"}:
        raise PackageError("manifest kind must be base_parser or region_processor")
    for key in (
        "accepted_media_types", "accepted_region_kinds",
        "accepted_element_kind_hints", "accepted_content_kind_hints",
        "produced_channels", "declared_capabilities",
    ):
        values = manifest[key]
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise PackageError(f"manifest {key} must be a string list")
    if not manifest["accepted_media_types"]:
        raise PackageError("manifest accepted_media_types must not be empty")
    if manifest["network_access"] is not False:
        raise PackageError("manifest network_access must be false")
    if manifest.get("digest_algorithm", DIGEST_ALGORITHM) != DIGEST_ALGORITHM:
        raise PackageError("unsupported manifest digest_algorithm")


def _manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    clean = dict(manifest)
    clean.pop("package_digest", None)
    clean.pop("signature_key_id", None)
    validate_manifest(clean)
    return _canonical_json(clean)


def _checksums(payloads: Mapping[str, bytes]) -> bytes:
    body = {
        path: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for path, data in sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))
    }
    return _canonical_json(body)


def _digest(entries: Mapping[str, bytes]) -> str:
    lines = []
    for path, data in sorted(entries.items(), key=lambda item: item[0].encode("utf-8")):
        lines.append(path.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
    return "sha256:" + hashlib.sha256(b"".join(lines)).hexdigest()


def _signed_payload(manifest: Mapping[str, Any], digest: str) -> dict[str, str]:
    return {key: value for key, value in (
        ("package_digest", digest), ("plugin_id", manifest["plugin_id"]),
        ("plugin_version", manifest["plugin_version"]), ("sdk_api_version", manifest["sdk_api_version"]),
        ("runtime_profile", manifest["runtime_profile"]),
    )}


def _project_payloads(project: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    manifest_path = project / "manifest.yaml"
    if not manifest_path.is_file():
        raise PackageError("project is missing manifest.yaml")
    manifest = _parse_json(manifest_path.read_bytes(), "manifest.yaml")
    if not isinstance(manifest, dict):
        raise PackageError("manifest.yaml must contain an object")
    manifest.setdefault("digest_algorithm", DIGEST_ALGORITHM)
    validate_manifest(manifest)
    payloads: dict[str, bytes] = {"manifest.yaml": _manifest_payload(manifest)}
    for rel in sorted(REQUIRED_PAYLOADS - {"manifest.yaml", "sbom.spdx.json"}):
        path = project / rel
        if not path.is_file():
            raise PackageError(f"project is missing {rel}")
        payloads[rel] = path.read_bytes()
    payloads["sbom.spdx.json"] = _canonical_sbom(
        manifest,
        _locked_requirements(payloads["requirements.lock"], str(manifest["runtime_profile"])),
    )
    wheel = _build_plugin_wheel(project, manifest)
    payloads["plugin.whl"] = wheel
    return manifest, payloads


def _locked_requirements(data: bytes, runtime_profile: str) -> list[tuple[str, str]]:
    available = RUNTIME_PACKAGES.get(runtime_profile)
    if available is None:
        raise PackageError(f"unknown runtime profile: {runtime_profile}")
    result = []
    for raw in data.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if not match:
            raise PackageError("requirements.lock permits only exact name==version entries")
        name, version = _normalized_distribution_name(match.group(1)), match.group(2)
        if available.get(name) != version:
            raise PackageError(f"runtime profile does not provide {name}=={version}")
        if any(existing == name for existing, _ in result):
            raise PackageError(f"requirements.lock contains duplicate dependency {name}")
        result.append((name, version))
    return result


def _normalized_distribution_name(value: str) -> str:
    if not PACKAGE_NAME.fullmatch(value):
        raise PackageError(f"invalid Python distribution name: {value!r}")
    return re.sub(r"[-_.]+", "-", value).lower()


def _canonical_sbom(manifest: Mapping[str, Any], locked: list[tuple[str, str]]) -> bytes:
    plugin_license = str(manifest["license_expression"])
    packages = [{
        "SPDXID": "SPDXRef-Package-plugin",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": plugin_license,
        "licenseDeclared": plugin_license,
        "name": manifest["plugin_id"],
        "versionInfo": manifest["plugin_version"],
    }]
    for name, version in sorted(locked):
        license_expression = RUNTIME_PACKAGE_LICENSES.get(name)
        if license_expression is None:
            raise PackageError(f"runtime package {name} is missing governed license metadata")
        packages.append({
            "SPDXID": "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", name),
            "downloadLocation": f"https://pypi.org/project/{name}/{version}/",
            "filesAnalyzed": False,
            "licenseConcluded": license_expression,
            "licenseDeclared": license_expression,
            "name": name,
            "versionInfo": version,
        })
    return _canonical_json({
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {"created": "1980-01-01T00:00:00Z", "creators": ["Tool: atlas-processing-sdk"]},
        "dataLicense": "CC0-1.0",
        "documentDescribes": ["SPDXRef-Package-plugin"],
        "documentNamespace": f"https://atlas.local/spdx/{manifest['plugin_id']}/{manifest['plugin_version']}",
        "name": manifest["plugin_id"],
        "packages": packages,
        "spdxVersion": "SPDX-2.3",
    })


def _check_json_schema(schema: Any, name: str) -> None:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise PackageError(f"{name} is not a valid Draft 2020-12 schema: {exc.message}") from exc


def _validate_json_schema(schema: Any, instance: Any, name: str, path: str = "$") -> None:
    """Compile a Draft 2020-12 schema and validate a real fixture/output instance."""
    _check_json_schema(schema, name)
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError
        Draft202012Validator(schema).validate(instance)
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path)
        raise PackageError(f"{name} rejected {path}{'.' + location if location else ''}: {exc.message}") from exc


def _wheel_metadata(wheel: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(wheel)) as archive:
            infos = archive.infolist()
            names = _validate_names([info.filename for info in infos])
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise PackageError("plugin.whl must contain exactly one METADATA file")
            for info, name in zip(infos, names):
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                    raise PackageError(f"plugin.whl contains unsafe entry: {name}")
                if info.file_size > MAX_FILE_BYTES or info.compress_size == 0 and info.file_size > 0:
                    raise PackageError(f"plugin.whl contains unsafe member size: {name}")
                if info.compress_size and info.file_size / info.compress_size > 200:
                    raise PackageError(f"plugin.whl contains unsafe compression ratio: {name}")
            message = BytesParser(policy=compat32).parsebytes(archive.read(metadata_names[0]))
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise PackageError("plugin.whl is not a valid safe wheel") from exc
    name, version, license_expression = message.get("Name"), message.get("Version"), message.get("License-Expression")
    if not all(isinstance(value, str) and value.strip() for value in (name, version, license_expression)):
        raise PackageError("plugin.whl METADATA requires Name, Version, and License-Expression")
    requirements: list[tuple[str, str]] = []
    for requirement in message.get_all("Requires-Dist", []):
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)\s*", requirement)
        if not match:
            raise PackageError("plugin.whl Requires-Dist permits only exact unmarked name==version entries")
        requirements.append((_normalized_distribution_name(match.group(1)), match.group(2)))
    if len({name for name, _ in requirements}) != len(requirements):
        raise PackageError("plugin.whl contains duplicate Requires-Dist dependencies")
    return _normalized_distribution_name(name), version, license_expression, requirements


def _validate_support_files(files: Mapping[str, bytes], manifest: Mapping[str, Any]) -> None:
    fixture = _parse_json(files["fixtures/smoke-input.json"], "smoke fixture")
    expected = _parse_json(files["expected/smoke-output.json"], "smoke expectation")
    if not isinstance(fixture, dict) or not isinstance(fixture.get("request"), dict):
        raise PackageError("smoke fixture must contain a request object")
    if not isinstance(expected, dict):
        raise PackageError("smoke expectation must be an object")
    schemas: dict[str, dict[str, Any]] = {}
    for name in ("schemas/config.schema.json", "schemas/output.schema.json"):
        schema = _parse_json(files[name], name)
        if not isinstance(schema, dict) or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise PackageError(f"{name} must be a Draft 2020-12 JSON Schema")
        _check_json_schema(schema, name)
        schemas[name] = schema
    _validate_json_schema(schemas["schemas/config.schema.json"], fixture["request"].get("plugin_config", {}), "config schema")
    for index, draft in enumerate(expected.get("drafts", [])):
        _validate_json_schema(schemas["schemas/output.schema.json"], draft, "output schema", f"$[{index}]")
    locked = _locked_requirements(files["requirements.lock"], str(manifest["runtime_profile"]))
    wheel_name, wheel_version, wheel_license, wheel_requirements = _wheel_metadata(files["plugin.whl"])
    if wheel_name != _normalized_distribution_name(str(manifest["plugin_id"])) or wheel_version != manifest["plugin_version"]:
        raise PackageError("plugin.whl name/version does not match manifest")
    if wheel_license != manifest["license_expression"]:
        raise PackageError("plugin.whl license does not match manifest")
    if sorted(wheel_requirements) != sorted(locked):
        raise PackageError("plugin.whl Requires-Dist does not exactly match requirements.lock")
    sbom = _parse_json(files["sbom.spdx.json"], "SPDX SBOM")
    if not isinstance(sbom, dict) or sbom.get("spdxVersion") != "SPDX-2.3" or not isinstance(sbom.get("packages"), list):
        raise PackageError("sbom.spdx.json must be an SPDX 2.3 document")
    if sbom.get("name") != manifest["plugin_id"] or not str(sbom.get("documentNamespace", "")).endswith(f"/{manifest['plugin_id']}/{manifest['plugin_version']}"):
        raise PackageError("SBOM document identity does not match manifest")
    plugin_packages = [item for item in sbom["packages"] if isinstance(item, dict) and item.get("name") == manifest["plugin_id"]]
    if len(plugin_packages) != 1 or plugin_packages[0].get("versionInfo") != manifest["plugin_version"] or plugin_packages[0].get("licenseDeclared") != manifest["license_expression"]:
        raise PackageError("SBOM plugin identity/license does not match manifest")
    dependencies = []
    for item in sbom["packages"]:
        if not isinstance(item, dict):
            raise PackageError("SBOM packages must be objects")
        if item.get("name") == manifest["plugin_id"]:
            continue
        if not all(isinstance(item.get(key), str) and item[key] for key in ("name", "versionInfo", "licenseDeclared")):
            raise PackageError("SBOM dependency requires name, versionInfo, and licenseDeclared")
        dependencies.append((_normalized_distribution_name(item["name"]), item["versionInfo"]))
    if sorted(dependencies) != sorted(locked):
        raise PackageError("SBOM dependencies do not exactly match requirements.lock")
    if files["sbom.spdx.json"] != _canonical_sbom(manifest, locked):
        raise PackageError("SBOM is stale or non-canonical for manifest and requirements.lock")


def _build_plugin_wheel(project: Path, manifest: Mapping[str, Any]) -> bytes:
    import io
    source = project / "src" / "plugin.py"
    if not source.is_file():
        raise PackageError("project is missing src/plugin.py")
    module = str(manifest["entrypoint"]).partition(":")[0]
    if module != "plugin":
        raise PackageError("first SDK release requires an entrypoint in plugin:<object>")
    dist = str(manifest["plugin_id"]).replace("-", "_").replace(".", "_")
    version = str(manifest["plugin_version"])
    requirements = _locked_requirements(
        (project / "requirements.lock").read_bytes(), str(manifest["runtime_profile"])
    )
    metadata = f"Metadata-Version: 2.4\nName: {manifest['plugin_id']}\nVersion: {version}\nLicense-Expression: {manifest['license_expression']}\n"
    metadata += "".join(f"Requires-Dist: {name}=={locked}\n" for name, locked in requirements)
    files = {
        "plugin.py": source.read_bytes(),
        f"{dist}-{version}.dist-info/METADATA": metadata.encode(),
        f"{dist}-{version}.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: atlas-processing-sdk\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    return _zip_bytes(files)


def _zip_bytes(files: Mapping[str, bytes]) -> bytes:
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def build_package(
    project: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    signing_key: str | os.PathLike[str] | None = None,
    signing_key_id: str | None = None,
) -> VerificationResult:
    root = Path(project)
    manifest, payloads = _project_payloads(root)
    checksums = _checksums(payloads)
    digest = _digest({**payloads, "checksums.json": checksums})
    archive_manifest = dict(manifest)
    archive_manifest["digest_algorithm"] = DIGEST_ALGORITHM
    archive_manifest["package_digest"] = digest
    payloads["manifest.yaml"] = _canonical_json(archive_manifest)
    signed_payload = _signed_payload(manifest, digest)
    trusted_keys = None
    if signing_key is not None:
        if not signing_key_id:
            raise PackageError("signing_key_id is required with signing_key")
        try:
            from cryptography.hazmat.primitives import serialization
            private_key = serialization.load_pem_private_key(
                Path(signing_key).read_bytes(), password=None
            )
            signature_bytes = private_key.sign(_canonical_json(signed_payload))
            public_pem = private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise PackageError("signing key must be an unencrypted Ed25519 PEM key") from exc
        signature = {
            "algorithm": "ed25519",
            "key_id": signing_key_id,
            "signature": base64.b64encode(signature_bytes).decode("ascii"),
            "signed_payload": signed_payload,
        }
        signature_key_id = signing_key_id
        trusted_keys = {signing_key_id: public_pem}
    else:
        signature = {"algorithm": "unsigned-local-development", "signed_payload": signed_payload}
        signature_key_id = None
    archive_manifest["signature_key_id"] = signature_key_id
    payloads["manifest.yaml"] = _canonical_json(archive_manifest)
    files = {**payloads, "checksums.json": checksums, "signature.json": _canonical_json(signature)}
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_zip_bytes(files))
    return verify_package(
        destination,
        allow_unsigned=True,
        trusted_public_keys=trusted_keys,
    )


def verify_package(
    path: str | os.PathLike[str],
    *,
    allow_unsigned: bool = False,
    trusted_public_keys: Mapping[str, bytes | str] | None = None,
    require_trusted_signature: bool = False,
) -> VerificationResult:
    package_path = Path(path)
    if package_path.suffix != ".atlas-plugin":
        raise PackageError("package must use the .atlas-plugin extension")
    if package_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("archive exceeds maximum size")
    try:
        with zipfile.ZipFile(package_path) as archive:
            infos = archive.infolist()
            names = _validate_names([info.filename for info in infos])
            total = 0
            files: dict[str, bytes] = {}
            for info, name in zip(infos, names):
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                    raise PackageError(f"links, directories, and device entries are forbidden: {name}")
                if info.file_size > MAX_FILE_BYTES or info.compress_size == 0 and info.file_size > 0:
                    raise PackageError(f"unsafe archive member size/compression: {name}")
                if info.compress_size and info.file_size / info.compress_size > 200:
                    raise PackageError(f"unsafe compression ratio: {name}")
                total += info.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise PackageError("uncompressed archive exceeds maximum size")
                files[name] = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise PackageError("package is not a valid ZIP archive") from exc
    required = REQUIRED_PAYLOADS | {"checksums.json", "signature.json"}
    missing = sorted(required - set(files))
    wheels = [name for name in files if name.endswith(".whl")]
    extras = sorted(set(files) - required - {"plugin.whl"})
    if missing or wheels != ["plugin.whl"] or extras:
        raise PackageError(f"invalid package contents; missing={missing}, wheels={wheels}, extras={extras}")
    manifest = _parse_json(files["manifest.yaml"], "manifest.yaml")
    if not isinstance(manifest, dict):
        raise PackageError("manifest.yaml must contain an object")
    validate_manifest(manifest)
    _validate_support_files(files, manifest)
    stated = manifest.get("package_digest")
    if not isinstance(stated, str):
        raise PackageError("manifest package_digest is required")
    canonical_payloads = {name: data for name, data in files.items() if name not in {"checksums.json", "signature.json"}}
    canonical_payloads["manifest.yaml"] = _manifest_payload(manifest)
    expected_checksums = _checksums(canonical_payloads)
    if files["checksums.json"] != expected_checksums:
        raise PackageError("checksums.json does not match package payloads")
    actual = _digest({**canonical_payloads, "checksums.json": expected_checksums})
    if stated != actual:
        raise PackageError("package digest mismatch")
    signature = _parse_json(files["signature.json"], "signature.json")
    if not isinstance(signature, dict) or signature.get("signed_payload") != _signed_payload(manifest, actual):
        raise PackageError("signature payload does not match package identity")
    unsigned = signature.get("algorithm") == "unsigned-local-development"
    if unsigned and not allow_unsigned:
        raise PackageError("unsigned package is allowed only for explicit local development")
    if not unsigned and not all(isinstance(signature.get(key), str) and signature[key] for key in ("algorithm", "signature", "key_id")):
        raise PackageError("signed package requires algorithm, signature, and key_id")
    if not unsigned and signature.get("algorithm") != "ed25519":
        raise PackageError("signed package algorithm must be ed25519")
    if manifest.get("signature_key_id") != (None if unsigned else signature.get("key_id")):
        raise PackageError("manifest signature_key_id does not match signature")
    if not unsigned:
        try:
            base64.b64decode(signature["signature"], validate=True)
        except Exception as exc:
            raise PackageError("signature must be valid base64") from exc
    trusted = False
    if not unsigned and trusted_public_keys is not None:
        public_pem = trusted_public_keys.get(signature["key_id"])
        if public_pem is not None:
            try:
                from cryptography.hazmat.primitives import serialization
                public_key = serialization.load_pem_public_key(
                    public_pem.encode("utf-8") if isinstance(public_pem, str) else public_pem
                )
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                if not isinstance(public_key, Ed25519PublicKey):
                    raise PackageError("trusted signing key must be Ed25519")
                public_key.verify(
                    base64.b64decode(signature["signature"], validate=True),
                    _canonical_json(signature["signed_payload"]),
                )
                trusted = True
            except Exception as exc:
                raise PackageError("trusted signature verification failed") from exc
    if require_trusted_signature and not trusted:
        raise PackageError("package signature is not trusted")
    return VerificationResult(actual, manifest["plugin_id"], manifest["plugin_version"], not unsigned, trusted, tuple(sorted(files)))
