import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.request

from atlas_processing_sdk.admin import AdminClient
from atlas_processing_sdk.cli import _local_sdk_source, main


class _Response:
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return b'{"data":{"status":"ok"}}'


class _LoginResponse(_Response):
    headers = {"Set-Cookie": "atlas_session=session-secret; HttpOnly; Path=/; SameSite=lax"}


class CliAdminTests(unittest.TestCase):
    def test_cli_golden_path_and_invalid_unsigned_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            package = Path(temp) / "plugin.atlas-plugin"
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["init", str(project), "--plugin-id", "com.example.cli"]), 0)
                self.assertEqual(main(["test", str(project)]), 0)
                self.assertEqual(main(["build", str(project), "--output", str(package)]), 0)
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(main(["verify", str(package)]), 2)
                self.assertIn("unsigned", stderr.getvalue())
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["verify", str(package), "--allow-unsigned"]), 0)

    def test_cli_init_supports_the_documented_zero_config_command(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "hardware-table-parser"
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(
                    main(["init", str(project), "--kind", "region-processor"]),
                    0,
                )
            self.assertEqual(json.loads(stdout.getvalue())["plugin_id"], "hardware-table-parser")
            self.assertEqual(
                json.loads((project / "manifest.yaml").read_text())["kind"],
                "region_processor",
            )
            self.assertTrue((project / "tests/fixtures/sample.json").is_file())
            self.assertIn("[tool.uv.sources]", (project / "pyproject.toml").read_text())

    def test_build_uses_deterministic_default_output_path(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "default-output"
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["init", str(project)]), 0)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(main(["build", str(project)]), 0)
            output = Path(json.loads(stdout.getvalue())["output"])
            self.assertEqual(
                output,
                project / "dist/default-output-0.1.0.atlas-plugin",
            )
            self.assertTrue(output.is_file())

    def test_source_checkout_is_available_to_scaffolded_uv_project(self):
        source = _local_sdk_source()
        self.assertIsNotNone(source)
        self.assertTrue((source / "pyproject.toml").is_file())

    @mock.patch("urllib.request.urlopen", return_value=_Response())
    def test_admin_mutation_sends_auth_idempotency_and_expected_revision(self, urlopen):
        client = AdminClient("https://atlas.example", "secret")
        result = client.request("POST", "/api/v1/admin/processing-profiles/p/revisions", body={"name": "next"}, idempotency_key="idem-1", expected_revision=3)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("Idempotency-key"), "idem-1")
        self.assertEqual(request.get_header("If-match"), "3")
        self.assertEqual(result["data"]["status"], "ok")

    @mock.patch("urllib.request.urlopen", return_value=_Response())
    def test_plugin_lifecycle_cli_requires_and_sends_expected_revision(self, urlopen):
        with mock.patch.dict(
            os.environ,
            {"ATLAS_BASE_URL": "https://atlas.example", "ATLAS_TOKEN": "secret"},
            clear=True,
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main([
                "admin", "package", "validate", "com.example.table", "1.0.0",
                "--expected-revision", "7",
            ]), 0)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("If-match"), "7")

    def test_admin_cli_rejects_missing_credentials_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"ATLAS_PLUGIN_CONFIG": str(Path(temp) / "missing.json")}, clear=True), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(main(["admin", "package", "list"]), 2)
                self.assertIn("base URL", stderr.getvalue())

    @mock.patch("urllib.request.urlopen", return_value=_LoginResponse())
    def test_admin_login_reads_password_from_stdin_and_writes_private_local_config(self, _urlopen):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "atlas" / "plugin.json"
            with mock.patch.dict(os.environ, {"ATLAS_PLUGIN_CONFIG": str(config)}, clear=True), mock.patch("sys.stdin", io.StringIO("password\n")), mock.patch("sys.stdout", new_callable=io.StringIO):
                assert main(["admin", "--base-url", "https://atlas.example", "login", "--email", "admin@example.test", "--password-stdin"]) == 0
            payload = json.loads(config.read_text())
            self.assertEqual(payload["token"], "session-secret")
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
