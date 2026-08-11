from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "infra/scripts/audit_no_phase_dependency"


def run_audit(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(AUDIT), "--repo-root", str(repo_root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_audit_accepts_current_sources_and_ignores_hidden_build_cache(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "api/current.py").write_text("CURRENT = True\n", encoding="utf-8")
    historical_marker = "phase" + "15"
    for relative_path in (
        "api/.hidden.py",
        "api/.pytest_cache/seed.py",
        "api/__pycache__/seed.py",
        "web/node_modules/seed.ts",
        "web/dist/seed.ts",
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"dependency = '{historical_marker}'\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_audit_reports_seeded_phase_dependency_with_location(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    bad_source = tmp_path / "web/src/bad.ts"
    bad_source.parent.mkdir(parents=True)
    historical_endpoint = "/api/" + "phase"
    bad_source.write_text(
        f"export const endpoint = '{historical_endpoint}';\n", encoding="utf-8"
    )

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert result.stdout == (
        f"web/src/bad.ts:1:export const endpoint = '{historical_endpoint}';\n"
    )
    assert result.stderr == ""
