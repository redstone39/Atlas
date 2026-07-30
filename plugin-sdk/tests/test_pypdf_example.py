import base64
import json
from pathlib import Path
import tempfile

import pytest

from atlas_processing_sdk.local import LocalExecutionError, run_local


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/atlas-pypdf"


def test_official_pypdf_emits_two_one_based_page_number_locators():
    result = run_local(EXAMPLE)
    assert [draft["locator_draft"]["page_number"] for draft in result["drafts"]] == [1, 2]
    assert [draft["source_region_identity"] for draft in result["drafts"]] == ["page:1", "page:2"]


def test_official_pypdf_malformed_pdf_fails_closed_without_drafts():
    fixture = json.loads((EXAMPLE / "fixtures/smoke-input.json").read_text())
    fixture["artifact_base64"] = base64.b64encode(b"not a valid PDF").decode()
    with tempfile.TemporaryDirectory() as temp:
        malformed = Path(temp) / "malformed.json"
        malformed.write_text(json.dumps(fixture))
        with pytest.raises(LocalExecutionError) as raised:
            run_local(EXAMPLE, malformed)
    assert raised.value.code == "plugin_execution_failed"
    assert str(raised.value) == "Plugin execution failed safely."
