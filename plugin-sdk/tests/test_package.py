import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atlas_processing_sdk.package import (
    PackageError,
    _checksums,
    _digest,
    _manifest_payload,
    _signed_payload,
    _zip_bytes,
    build_package,
    verify_package,
)
from atlas_processing_sdk.scaffold import init_project


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "plugin"
        init_project(self.project, "com.example.tables")

    def tearDown(self):
        self.temp.cleanup()

    def _rewrite_semantic_package(self, package: Path, destination: Path, mutate) -> None:
        with zipfile.ZipFile(package) as archive:
            files = {info.filename: archive.read(info) for info in archive.infolist()}
        mutate(files)
        manifest = json.loads(files["manifest.yaml"])
        canonical = {name: value for name, value in files.items() if name not in {"checksums.json", "signature.json"}}
        canonical["manifest.yaml"] = _manifest_payload(manifest)
        checksums = _checksums(canonical)
        digest = _digest({**canonical, "checksums.json": checksums})
        manifest["package_digest"] = digest
        files["manifest.yaml"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        files["checksums.json"] = checksums
        files["signature.json"] = json.dumps({
            "algorithm": "unsigned-local-development",
            "signed_payload": _signed_payload(manifest, digest),
        }, sort_keys=True, separators=(",", ":")).encode()
        destination.write_bytes(_zip_bytes(files))

    def test_build_is_byte_reproducible_and_verifiable(self):
        first, second = self.root / "one.atlas-plugin", self.root / "two.atlas-plugin"
        a = build_package(self.project, first)
        b = build_package(self.project, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(a.package_digest, b.package_digest)
        self.assertEqual(verify_package(first, allow_unsigned=True).plugin_id, "com.example.tables")
        with self.assertRaisesRegex(PackageError, "unsigned"):
            verify_package(first)

    def test_builder_supplies_default_digest_algorithm_consistently(self):
        manifest_path = self.project / "manifest.yaml"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("digest_algorithm")
        manifest_path.write_text(json.dumps(manifest))
        package = self.root / "default-digest.atlas-plugin"
        build_package(self.project, package)
        self.assertEqual(verify_package(package, allow_unsigned=True).plugin_id, "com.example.tables")

    def test_tampering_fails_closed(self):
        package = self.root / "valid.atlas-plugin"
        build_package(self.project, package)
        broken = self.root / "broken.atlas-plugin"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(broken, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "fixtures/smoke-input.json":
                    data += b" "
                target.writestr(info, data)
        with self.assertRaisesRegex(PackageError, "checksums"):
            verify_package(broken, allow_unsigned=True)

    def test_ed25519_signature_requires_and_accepts_trusted_key(self):
        private_key = Ed25519PrivateKey.generate()
        private_path = self.root / "signer.pem"
        private_path.write_bytes(private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        package = self.root / "signed.atlas-plugin"
        built = build_package(
            self.project,
            package,
            signing_key=private_path,
            signing_key_id="team-key-1",
        )
        self.assertTrue(built.signed)
        self.assertTrue(built.trusted)
        with self.assertRaisesRegex(PackageError, "not trusted"):
            verify_package(package, require_trusted_signature=True)
        checked = verify_package(
            package,
            trusted_public_keys={"team-key-1": public_pem},
            require_trusted_signature=True,
        )
        self.assertTrue(checked.trusted)

    def test_signature_file_is_outside_semantic_digest_but_tampering_is_rejected(self):
        private_key = Ed25519PrivateKey.generate()
        private_path = self.root / "signer.pem"
        private_path.write_bytes(private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        package = self.root / "signed.atlas-plugin"
        original = build_package(self.project, package, signing_key=private_path, signing_key_id="key-1")
        tampered = self.root / "tampered.atlas-plugin"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "signature.json":
                    value = json.loads(data)
                    value["signature"] = "AAAA"
                    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                target.writestr(info, data)
        with self.assertRaisesRegex(PackageError, "signature"):
            verify_package(tampered, trusted_public_keys={"key-1": public_pem}, require_trusted_signature=True)
        with zipfile.ZipFile(tampered) as archive:
            manifest = json.loads(archive.read("manifest.yaml"))
        self.assertEqual(manifest["package_digest"], original.package_digest)

    def test_archive_metadata_does_not_change_semantic_digest(self):
        package = self.root / "normal.atlas-plugin"
        original = build_package(self.project, package)
        repacked = self.root / "repacked.atlas-plugin"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(repacked, "w", compression=zipfile.ZIP_STORED) as target:
            for info in reversed(source.infolist()):
                changed = zipfile.ZipInfo(info.filename, (2026, 7, 12, 12, 0, 0))
                changed.external_attr = (stat.S_IFREG | 0o600) << 16
                target.writestr(changed, source.read(info))
        checked = verify_package(repacked, allow_unsigned=True)
        self.assertEqual(checked.package_digest, original.package_digest)

    def test_traversal_and_case_collisions_are_rejected(self):
        for filename, members, expected in (
            ("traversal.atlas-plugin", [("../manifest.yaml", b"{}")], "unsafe"),
            ("collision.atlas-plugin", [("A", b"1"), ("a", b"2")], "collision"),
        ):
            path = self.root / filename
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members:
                    archive.writestr(name, data)
            with self.assertRaisesRegex(PackageError, expected):
                verify_package(path, allow_unsigned=True)

    def test_sdk_api_version_must_be_exactly_one_and_schema_validates_fixture(self):
        manifest_path = self.project / "manifest.yaml"
        manifest = json.loads(manifest_path.read_text())
        manifest["sdk_api_version"] = 2
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PackageError, "exactly 1"):
            build_package(self.project, self.root / "v2.atlas-plugin")
        manifest["sdk_api_version"] = 1
        manifest_path.write_text(json.dumps(manifest))
        schema_path = self.project / "schemas/config.schema.json"
        schema = json.loads(schema_path.read_text())
        schema["required"] = ["mode"]
        schema_path.write_text(json.dumps(schema))
        with self.assertRaisesRegex(PackageError, "required property"):
            build_package(self.project, self.root / "schema.atlas-plugin")
        schema_path.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}))
        output_path = self.project / "schemas/output.schema.json"
        output_path.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": 42}))
        with self.assertRaisesRegex(PackageError, "not a valid Draft 2020-12 schema"):
            build_package(self.project, self.root / "invalid-output-schema.atlas-plugin")

    def test_wheel_metadata_must_exactly_match_manifest_lock_and_license(self):
        package = self.root / "valid.atlas-plugin"
        build_package(self.project, package)
        for label, metadata_line, expected in (
            ("name", b"Name: attacker", "name/version"),
            ("license", b"License-Expression: Proprietary", "license"),
            ("marker", b"Requires-Dist: pypdf==6.0.0; python_version > '3'", "unmarked"),
            ("unknown", b"Requires-Dist: requests==999.0.0", "requirements.lock"),
        ):
            destination = self.root / f"{label}.atlas-plugin"
            def mutate(files, line=metadata_line, kind=label):
                with zipfile.ZipFile(__import__("io").BytesIO(files["plugin.whl"])) as wheel:
                    entries = {info.filename: wheel.read(info) for info in wheel.infolist()}
                metadata = next(name for name in entries if name.endswith(".dist-info/METADATA"))
                if kind == "name":
                    entries[metadata] = entries[metadata].replace(b"Name: com.example.tables", line)
                elif kind == "license":
                    entries[metadata] = entries[metadata].replace(b"License-Expression: Apache-2.0", line)
                else:
                    entries[metadata] += line + b"\n"
                files["plugin.whl"] = _zip_bytes(entries)
            self._rewrite_semantic_package(package, destination, mutate)
            with self.assertRaisesRegex(PackageError, expected):
                verify_package(destination, allow_unsigned=True)

    def test_builder_generates_sbom_and_verifier_rejects_stale_or_tampered_sbom(self):
        sbom_path = self.project / "sbom.spdx.json"
        sbom_path.write_text('{"stale":"developer-maintained content is ignored"}')
        package = self.root / "generated.atlas-plugin"
        build_package(self.project, package)
        with zipfile.ZipFile(package) as archive:
            generated = json.loads(archive.read("sbom.spdx.json"))
        self.assertEqual(generated["name"], "com.example.tables")
        self.assertEqual(generated["packages"][0]["licenseDeclared"], "Apache-2.0")
        tampered = self.root / "tampered-sbom.atlas-plugin"
        def mutate(files):
            sbom = json.loads(files["sbom.spdx.json"])
            sbom["packages"][0]["licenseDeclared"] = "Proprietary"
            files["sbom.spdx.json"] = json.dumps(sbom, sort_keys=True, separators=(",", ":")).encode()
        self._rewrite_semantic_package(package, tampered, mutate)
        with self.assertRaisesRegex(PackageError, "license|stale|canonical"):
            verify_package(tampered, allow_unsigned=True)

    def test_hostile_archive_names_types_counts_sizes_and_nonfinite_json_fail_closed(self):
        hostile = (
            ("absolute", [("/manifest.yaml", b"{}", None)], "unsafe"),
            ("windows-absolute", [("C:\\manifest.yaml", b"{}", None)], "unsafe"),
            ("nfc", [("caf\u00e9", b"1", None), ("cafe\u0301", b"2", None)], "collision"),
            ("symlink", [("manifest.yaml", b"target", stat.S_IFLNK)], "forbidden"),
            ("device", [("manifest.yaml", b"", stat.S_IFCHR)], "forbidden"),
        )
        for label, entries, expected in hostile:
            path = self.root / f"{label}.atlas-plugin"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data, kind in entries:
                    info = zipfile.ZipInfo(name)
                    if kind is not None:
                        info.create_system = 3
                        info.external_attr = (kind | 0o644) << 16
                    archive.writestr(info, data)
            with self.assertRaisesRegex(PackageError, expected):
                verify_package(path, allow_unsigned=True)
        count_path = self.root / "count.atlas-plugin"
        with zipfile.ZipFile(count_path, "w") as archive:
            archive.writestr("one", b"1")
            archive.writestr("two", b"2")
        with mock.patch("atlas_processing_sdk.package.MAX_FILES", 1), self.assertRaisesRegex(PackageError, "too many"):
            verify_package(count_path, allow_unsigned=True)
        size_path = self.root / "size.atlas-plugin"
        with zipfile.ZipFile(size_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("large")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, b"12")
        with mock.patch("atlas_processing_sdk.package.MAX_FILE_BYTES", 1), self.assertRaisesRegex(PackageError, "size"):
            verify_package(size_path, allow_unsigned=True)
        package = self.root / "valid.atlas-plugin"
        build_package(self.project, package)
        for constant in (b"NaN", b"Infinity"):
            destination = self.root / f"{constant.decode()}.atlas-plugin"
            with zipfile.ZipFile(package) as source, zipfile.ZipFile(destination, "w") as target:
                for info in source.infolist():
                    data = source.read(info)
                    if info.filename == "manifest.yaml":
                        data = data[:-1] + b',"nonfinite":' + constant + b"}"
                    target.writestr(info, data)
            with self.assertRaisesRegex(PackageError, "invalid JSON"):
                verify_package(destination, allow_unsigned=True)


if __name__ == "__main__":
    unittest.main()
