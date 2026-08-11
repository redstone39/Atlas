#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"atlas_api|/api/phase|/api/poc|phase2|phase3|phase4|phase15|apps/api|apps/web|infra/phase"
)
SOURCE_ROOTS = ("api", "web")
EXCLUDED_DIRECTORIES = frozenset(
    {"node_modules", "dist", ".pytest_cache", "__pycache__"}
)


def _is_excluded(path: Path, source_root: Path) -> bool:
    relative_parts = path.relative_to(source_root).parts
    return any(part.startswith(".") or part in EXCLUDED_DIRECTORIES for part in relative_parts)


def _scan_file(path: Path, display_path: Path) -> list[str]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"cannot read {display_path}: {exc}") from exc

    if b"\0" in content:
        return []

    text = content.decode("utf-8", errors="replace")
    return [
        f"{display_path.as_posix()}:{line_number}:{line}"
        for line_number, line in enumerate(text.splitlines(), start=1)
        if PATTERN.search(line)
    ]


def audit(repo_root: Path) -> list[str]:
    matches: list[str] = []
    for root_name in SOURCE_ROOTS:
        source_root = repo_root / root_name
        if not source_root.is_dir():
            raise RuntimeError(f"missing source root: {source_root}")
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _is_excluded(path, source_root):
                continue
            matches.extend(_scan_file(path, path.relative_to(repo_root)))
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject Current runtime dependencies on historical phase paths."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root containing api/ and web/",
    )
    args = parser.parse_args()

    try:
        matches = audit(args.repo_root.resolve())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if matches:
        print("\n".join(matches))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
