import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from atlas_processing_sdk.local import LocalExecutionError, run_conformance, run_local
from atlas_processing_sdk.package import PackageError
from atlas_processing_sdk.scaffold import init_project


class LocalConformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "plugin"
        init_project(self.project, "com.example.local")

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, body: str) -> None:
        (self.project / "src/plugin.py").write_text(body)

    def test_conformance_reports_explicit_checks_and_uses_safe_environment(self):
        self._source('''import os
class Plugin:
    async def process(self, request, context):
        if os.getenv("ATLAS_SECRET_FOR_TEST"):
            raise RuntimeError("secret leaked")
        if False:
            yield None
''')
        with mock.patch.dict(os.environ, {"ATLAS_SECRET_FOR_TEST": "must-not-cross"}):
            result = run_conformance(self.project)
        self.assertIn("isolated_subprocess_venv", result["checks"])
        self.assertIn("timeout_cancellation_canary", result["checks"])
        self.assertEqual(result["draft_count"], 0)

    def test_network_access_is_denied_with_typed_safe_error(self):
        self._source('''import socket
class Plugin:
    async def process(self, request, context):
        socket.create_connection(("127.0.0.1", 9))
        if False:
            yield None
''')
        with self.assertRaises(LocalExecutionError) as raised:
            run_local(self.project)
        self.assertEqual(raised.exception.code, "plugin_network_denied")
        self.assertNotIn("127.0.0.1", str(raised.exception))

    def test_plugin_exception_is_converted_without_secret_or_traceback(self):
        self._source('''class Plugin:
    async def process(self, request, context):
        raise RuntimeError("/private/customer/secret.txt")
        if False:
            yield None
''')
        with self.assertRaises(LocalExecutionError) as raised:
            run_local(self.project)
        self.assertEqual(raised.exception.code, "plugin_execution_failed")
        self.assertEqual(str(raised.exception), "Plugin execution failed safely.")

    def test_timeout_terminates_child_and_returns_typed_error(self):
        self._source('''import asyncio
class Plugin:
    async def process(self, request, context):
        await asyncio.sleep(60)
        if False:
            yield None
''')
        with self.assertRaises(LocalExecutionError) as raised:
            run_local(self.project, timeout_seconds=0.2)
        self.assertEqual(raised.exception.code, "plugin_timeout")

    def test_unsupported_fixture_fails_closed_before_plugin_execution(self):
        fixture_path = self.project / "fixtures/smoke-input.json"
        fixture = json.loads(fixture_path.read_text())
        fixture["request"]["media_type"] = "text/html"
        fixture_path.write_text(json.dumps(fixture))
        with self.assertRaisesRegex(LocalExecutionError, "unsupported") as raised:
            run_local(self.project)
        self.assertEqual(raised.exception.code, "plugin_contract_error")

    def test_output_schema_and_manifest_channel_are_enforced(self):
        schema_path = self.project / "schemas/output.schema.json"
        schema_path.write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["channel_id"],
            "properties": {"channel_id": {"const": "table"}},
        }))
        self._source('''from atlas_processing_sdk import CandidateDraft
class Plugin:
    async def process(self, request, context):
        yield CandidateDraft(
            source_region_ids=(request.region_id,), channel_id="generic_text",
            output_contract_version="eir-draft-v1",
            candidate_payload_ref=context.artifact_broker.put_text("safe"),
        )
''')
        with self.assertRaisesRegex(LocalExecutionError, "was expected"):
            run_local(self.project)

    def test_missing_runtime_dependency_is_typed_prerequisite_failure(self):
        self._source('''import atlas_dependency_that_does_not_exist
class Plugin:
    async def process(self, request, context):
        if False:
            yield None
''')
        with self.assertRaises(LocalExecutionError) as raised:
            run_local(self.project)
        self.assertEqual(raised.exception.code, "plugin_runtime_prerequisite_missing")
        self.assertNotIn("atlas_dependency_that_does_not_exist", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
