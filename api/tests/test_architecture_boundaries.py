from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "infra/scripts/architecture_audit.py"
WRAPPER = REPO_ROOT / "infra/scripts/audit_architecture_boundaries"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2) + "\n")


def base_registry() -> dict:
    return {
        "schema_version": 1,
        "source_roots": {
            "backend": {
                "path": "api/src/atlas_production",
                "extensions": [".py"],
                "exclude_path_prefixes": [],
                "exclude_filename_suffixes": [],
            },
            "frontend": {
                "path": "web/src",
                "extensions": [".ts", ".tsx"],
                "exclude_path_prefixes": [],
                "exclude_filename_suffixes": [],
            },
            "collaboration": {
                "path": "collaboration-server/src",
                "extensions": [".ts", ".tsx"],
                "exclude_path_prefixes": [],
                "exclude_filename_suffixes": [],
            },
        },
        "owners": [
            {
                "id": "backend_transport",
                "role": "transport",
                "path_prefixes": ["api/src/atlas_production/routes"],
                "public_contracts": [],
            },
            {
                "id": "frontend_pages",
                "role": "transport",
                "path_prefixes": ["web/src/pages"],
                "public_contracts": [],
            },
            {
                "id": "frontend_composition",
                "role": "composition",
                "path_prefixes": ["web/src/app"],
                "public_contracts": [],
            },
            {
                "id": "collaboration_carrier",
                "role": "infrastructure",
                "path_prefixes": ["collaboration-server/src"],
                "public_contracts": [],
            },
        ],
        "dependency_rules": [
            {
                "id": "backend-routes-do-not-import-routes",
                "language": "python",
                "kind": "forbid_prefix",
                "source_prefixes": ["atlas_production.routes"],
                "forbidden_target_prefixes": ["atlas_production.routes"],
                "allowed_target_ids": [],
            },
            {
                "id": "frontend-pages-do-not-import-app",
                "language": "typescript",
                "kind": "forbid_prefix",
                "source_prefixes": ["pages"],
                "forbidden_target_prefixes": ["app"],
                "allowed_target_ids": [],
            },
        ],
        "ownership_exceptions": [],
    }


def base_baseline() -> dict:
    return {
        "schema_version": 1,
        "frozen_violations": [
            {
                "rule_id": "backend-routes-do-not-import-routes",
                "source": "atlas_production.routes.a",
                "target": "atlas_production.routes.b",
            },
            {
                "rule_id": "frontend-pages-do-not-import-app",
                "source": "pages/Page",
                "target": "app/Nav",
            },
        ],
    }


def fixture_repo(tmp_path: Path) -> tuple[Path, dict, dict]:
    repo = tmp_path / "repo"
    write(repo / "api/src/atlas_production/routes/a.py", "from . import b\n")
    write(repo / "api/src/atlas_production/routes/b.py", "VALUE = 1\n")
    write(repo / "web/src/pages/Page.tsx", 'import { Nav } from "../app/Nav";\nexport const Page = Nav;\n')
    write(repo / "web/src/app/Nav.tsx", "export const Nav = () => null;\n")
    write_json(
        repo / "web/tsconfig.json",
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "ESNext",
                "moduleResolution": "Bundler",
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
                "jsx": "react-jsx",
            },
            "include": ["src"],
        },
    )
    write(repo / "collaboration-server/src/index.ts", "export {};\n")
    write_json(
        repo / "collaboration-server/tsconfig.json",
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "ESNext",
                "moduleResolution": "Bundler",
                "baseUrl": ".",
            },
            "include": ["src"],
        },
    )
    registry = base_registry()
    baseline = base_baseline()
    save_contracts(repo, registry, baseline)
    return repo, registry, baseline


def save_contracts(repo: Path, registry: dict, baseline: dict) -> None:
    write_json(repo / "architecture-boundaries.json", registry)
    write_json(repo / "architecture-baseline.json", baseline)


def run_audit(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), "--repo-root", str(repo)],
        text=True,
        capture_output=True,
        check=False,
    )


def assert_failed(result: subprocess.CompletedProcess[str], message: str) -> None:
    assert result.returncode != 0, result.stdout
    assert message in result.stderr


def valid_exception() -> dict:
    return {
        "id": "temporary-route-owner",
        "owner": "backend_transport",
        "reason": "The fixture keeps one temporary transport aggregate.",
        "allowed_scope": ["api/src/atlas_production/routes/a.py"],
        "exit_condition": "The fixture route moves behind a public contract.",
        "stale_check": "all_scopes_exist",
        "removal_slice": "fixture convergence",
    }


def add_public_api_only_fixture(repo: Path, registry: dict) -> None:
    write(
        repo / "api/src/atlas_production/modules/documents/public.py",
        "from .service import VALUE\n",
    )
    write(
        repo / "api/src/atlas_production/modules/documents/service.py",
        "VALUE = 1\n",
    )
    write(
        repo / "web/src/features/documents/index.ts",
        'export { VALUE } from "./service";\n',
    )
    write(
        repo / "web/src/features/documents/service.ts",
        "export const VALUE = 1;\n",
    )
    registry["owners"].extend(
        [
            {
                "id": "document_module",
                "role": "module",
                "path_prefixes": [
                    "api/src/atlas_production/modules/documents"
                ],
                "public_contracts": [
                    "api/src/atlas_production/modules/documents/public.py"
                ],
            },
            {
                "id": "document_feature",
                "role": "module",
                "path_prefixes": ["web/src/features/documents"],
                "public_contracts": [
                    "web/src/features/documents/index.ts"
                ],
            },
        ]
    )
    registry["dependency_rules"].extend(
        [
            {
                "id": "backend-routes-use-public-modules",
                "language": "python",
                "kind": "public_api_only",
                "source_prefixes": ["atlas_production.routes"],
                "target_root": "atlas_production.modules",
                "public_target_suffixes": [".public"],
            },
            {
                "id": "frontend-consumers-use-public-features",
                "language": "typescript",
                "kind": "public_api_only",
                "source_prefixes": ["pages", "api", "types"],
                "target_root": "features",
                "public_target_suffixes": ["/index"],
            },
        ]
    )


def test_checked_in_tree_passes_and_wrapper_is_cwd_safe(tmp_path: Path) -> None:
    result = subprocess.run([str(WRAPPER)], cwd=tmp_path, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "architecture boundary audit passed"


def test_normal_audit_does_not_modify_contracts(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    registry_path = repo / "architecture-boundaries.json"
    baseline_path = repo / "architecture-baseline.json"
    before = (registry_path.read_bytes(), baseline_path.read_bytes())
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr
    assert (registry_path.read_bytes(), baseline_path.read_bytes()) == before


def test_new_forbidden_dependency_fails(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/c.py", "from . import b\n")
    assert_failed(run_audit(repo), "new architecture violations")


def test_python_public_api_only_accepts_public_and_rejects_deep_import(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    add_public_api_only_fixture(repo, registry)
    write(
        repo / "api/src/atlas_production/routes/a.py",
        "from . import b\nfrom atlas_production.modules.documents.public import VALUE\n",
    )
    save_contracts(repo, registry, baseline)
    assert run_audit(repo).returncode == 0

    write(
        repo / "api/src/atlas_production/routes/a.py",
        "from . import b\nfrom atlas_production.modules.documents.service import VALUE\n",
    )
    assert_failed(run_audit(repo), "backend-routes-use-public-modules")


@pytest.mark.parametrize(
    "source",
    [
        "from . import b\nimport atlas_production.modules\n",
        "from . import b\nfrom atlas_production import modules\n",
    ],
)
def test_python_public_api_only_rejects_module_root_and_namespace_alias(
    tmp_path: Path,
    source: str,
) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    add_public_api_only_fixture(repo, registry)
    write(repo / "api/src/atlas_production/routes/a.py", source)
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "backend-routes-use-public-modules")


def test_typescript_public_api_only_accepts_index_and_rejects_deep_import(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    add_public_api_only_fixture(repo, registry)
    write(
        repo / "web/src/pages/Page.tsx",
        'import { Nav } from "../app/Nav";\nimport { VALUE } from "../features/documents/index";\nexport const Page = () => Nav() ?? VALUE;\n',
    )
    save_contracts(repo, registry, baseline)
    assert run_audit(repo).returncode == 0

    write(
        repo / "web/src/pages/Page.tsx",
        'import { Nav } from "../app/Nav";\nimport { VALUE } from "../features/documents/service";\nexport const Page = () => Nav() ?? VALUE;\n',
    )
    assert_failed(run_audit(repo), "frontend-consumers-use-public-features")


@pytest.mark.parametrize(
    ("rule_index", "field", "value"),
    [
        (-2, "source_prefixes", ["atlas_production..routes"]),
        (-2, "target_root", "atlas_production.modules."),
        (-2, "public_target_suffixes", ["public"]),
        (-1, "source_prefixes", ["pages//admin"]),
        (-1, "target_root", "features/../internal"),
        (-1, "public_target_suffixes", ["index"]),
    ],
)
def test_public_api_only_rejects_non_normalized_identifiers(
    tmp_path: Path,
    rule_index: int,
    field: str,
    value: object,
) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    add_public_api_only_fixture(repo, registry)
    registry["dependency_rules"][rule_index][field] = value
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "registry.dependency_rules")


def test_resolved_baseline_dependency_fails_as_stale(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", "VALUE = 1\n")
    assert_failed(run_audit(repo), "stale architecture baseline entries")


def test_exception_missing_required_field_fails(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    exception = valid_exception()
    exception.pop("reason")
    registry["ownership_exceptions"] = [exception]
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "fields invalid")


@pytest.mark.parametrize("kind", ["owner", "rule", "exception", "baseline"])
def test_duplicate_stable_identities_fail(tmp_path: Path, kind: str) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    if kind == "owner":
        duplicate = copy.deepcopy(registry["owners"][0])
        duplicate["path_prefixes"] = ["api/src/atlas_production/routes/a.py"]
        registry["owners"].append(duplicate)
    elif kind == "rule":
        registry["dependency_rules"].append(copy.deepcopy(registry["dependency_rules"][0]))
    elif kind == "exception":
        registry["ownership_exceptions"] = [valid_exception(), valid_exception()]
    else:
        baseline["frozen_violations"].append(copy.deepcopy(baseline["frozen_violations"][0]))
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "duplicate")


def test_exception_unknown_owner_fails(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    exception = valid_exception()
    exception["owner"] = "missing_owner"
    registry["ownership_exceptions"] = [exception]
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "unknown owner")


def test_partially_missing_exception_scope_fails_as_stale(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    exception = valid_exception()
    exception["allowed_scope"].append("api/src/atlas_production/routes/missing.py")
    registry["ownership_exceptions"] = [exception]
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "stale ownership exception")


def test_unowned_runtime_file_fails(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/loose.py", "VALUE = 1\n")
    assert_failed(run_audit(repo), "unowned runtime file")


def test_equal_length_owner_match_fails_as_ambiguous(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    registry["owners"].append(
        {
            "id": "duplicate_transport_scope",
            "role": "transport",
            "path_prefixes": ["api/src/atlas_production/routes"],
            "public_contracts": [],
        }
    )
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "ambiguous runtime owner")


@pytest.mark.parametrize("contract", ["registry", "baseline"])
def test_unknown_manifest_field_fails(tmp_path: Path, contract: str) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    if contract == "registry":
        registry["unexpected"] = True
    else:
        baseline["unexpected"] = True
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "unknown=['unexpected']")


def test_nonliteral_python_dynamic_import_fails(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", 'name = "atlas_production.routes.b"\n__import__(name)\n')
    assert_failed(run_audit(repo), "non-literal dynamic Python import")


@pytest.mark.parametrize(
    "source",
    [
        'import importlib as il\nname = "atlas_production.routes.b"\nil.import_module(name)\n',
        'from importlib import import_module as load\nname = "atlas_production.routes.b"\nload(name)\n',
    ],
)
def test_nonliteral_python_dynamic_import_aliases_fail(tmp_path: Path, source: str) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", source)
    assert_failed(run_audit(repo), "non-literal dynamic Python import")


@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib as loader\n"
            "def call_user_object(loader, target):\n"
            "    return loader.import_module(target)\n"
        ),
        (
            "from importlib import import_module as load\n"
            "def call_user_function(load, target):\n"
            "    return load(target)\n"
        ),
        (
            "import importlib as loader\n"
            "loader = object()\n"
            "target = 'atlas_production.routes.b'\n"
            "loader.import_module(target)\n"
        ),
    ],
)
def test_shadowed_importlib_alias_is_not_treated_as_dynamic_import(tmp_path: Path, source: str) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", source)
    baseline = base_baseline()
    baseline["frozen_violations"] = baseline["frozen_violations"][1:]
    write_json(repo / "architecture-baseline.json", baseline)
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib as loader\n"
            "def load(name):\n"
            "    loader.import_module(name)\n"
            "    return [loader for loader in ()]\n"
        ),
        (
            "import importlib as loader\n"
            "if False:\n"
            "    loader = object()\n"
            "loader.import_module(name)\n"
        ),
        (
            "import importlib as loader\n"
            "for item in ():\n"
            "    loader = item\n"
            "loader.import_module(name)\n"
        ),
        (
            "import importlib as loader\n"
            "flag and (loader := object())\n"
            "loader.import_module(name)\n"
        ),
        (
            "import importlib as loader\n"
            "try:\n"
            "    loader = object()\n"
            "except Exception:\n"
            "    pass\n"
            "loader.import_module(name)\n"
        ),
    ],
)
def test_conditional_or_comprehension_binding_cannot_hide_dynamic_import(tmp_path: Path, source: str) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", source)
    assert_failed(run_audit(repo), "non-literal dynamic Python import")


@pytest.mark.parametrize(
    "source",
    [
        (
            "loader = custom_loader\n"
            "while loader.import_module(name):\n"
            "    import importlib as loader\n"
        ),
        (
            "loader = custom_loader\n"
            "try:\n"
            "    import importlib as loader\n"
            "    raise RuntimeError\n"
            "except RuntimeError:\n"
            "    loader.import_module(name)\n"
        ),
    ],
)
def test_loop_recheck_and_try_partial_state_cannot_hide_dynamic_import(tmp_path: Path, source: str) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/a.py", source)
    assert_failed(run_audit(repo), "non-literal dynamic Python import")


def test_first_bool_operand_rebinding_is_committed(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(
        repo / "api/src/atlas_production/routes/a.py",
        (
            "import importlib as loader\n"
            "(loader := object()) and True\n"
            "loader.import_module(name)\n"
        ),
    )
    baseline = base_baseline()
    baseline["frozen_violations"] = baseline["frozen_violations"][1:]
    write_json(repo / "architecture-baseline.json", baseline)
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_nonliteral_typescript_dynamic_import_fails(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "web/src/pages/Page.tsx", 'const target = "../app/Nav";\nvoid import(target);\n')
    assert_failed(run_audit(repo), "non-literal dynamic import")


def test_unresolved_internal_typescript_import_fails(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "web/src/pages/Page.tsx", 'import "../app/Missing";\n')
    assert_failed(run_audit(repo), "unresolved internal import")


def test_dotted_typescript_basename_is_resolved_before_asset_classification(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "web/src/app/Nav.client.ts", "export const Nav = 1;\n")
    write(repo / "web/src/pages/Page.tsx", 'import { Nav } from "../app/Nav.client";\nvoid Nav;\n')
    baseline = base_baseline()
    baseline["frozen_violations"][1]["target"] = "app/Nav.client"
    write_json(repo / "architecture-baseline.json", baseline)
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_malformed_typescript_fails_instead_of_yielding_a_partial_graph(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "web/src/pages/Page.tsx", 'import { Nav from "../app/Nav";\n')
    assert_failed(run_audit(repo), "TypeScript parse error")


def test_typescript_import_type_node_is_in_dependency_graph(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(repo / "web/src/app/Nav.tsx", "export type Nav = string;\n")
    write(repo / "web/src/pages/Page.tsx", 'export type PageNav = import("../app/Nav").Nav;\n')
    baseline = base_baseline()
    baseline["frozen_violations"] = baseline["frozen_violations"][:1]
    write_json(repo / "architecture-baseline.json", baseline)
    assert_failed(run_audit(repo), "new architecture violations")


def test_typescript_alias_type_reexport_import_equals_require_and_dynamic_are_normalized(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    targets = ["Typed", "Exported", "Equals", "Required", "Dynamic"]
    for target in targets:
        write(repo / f"web/src/app/{target}.ts", "export const value = 1;\nexport type T = number;\n")
    write(
        repo / "web/src/pages/Page.tsx",
        "\n".join(
            [
                'import type { T } from "../app/Typed";',
                'export { value } from "@/app/Exported";',
                'import Equals = require("../app/Equals");',
                'const required = require("../app/Required");',
                'void import("../app/Dynamic");',
                "export type Page = T;",
                "void Equals;",
                "void required;",
            ]
        )
        + "\n",
    )
    baseline = {
        "schema_version": 1,
        "frozen_violations": [
            {
                "rule_id": "backend-routes-do-not-import-routes",
                "source": "atlas_production.routes.a",
                "target": "atlas_production.routes.b",
            },
            *[
                {
                    "rule_id": "frontend-pages-do-not-import-app",
                    "source": "pages/Page",
                    "target": f"app/{target}",
                }
                for target in targets
            ],
        ],
    }
    write_json(repo / "architecture-baseline.json", baseline)
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_literal_python_dynamic_import_is_in_dependency_graph(tmp_path: Path) -> None:
    repo, _, _ = fixture_repo(tmp_path)
    write(
        repo / "api/src/atlas_production/routes/a.py",
        'import importlib\nimportlib.import_module("atlas_production.routes.b")\n',
    )
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_python_cross_slice_public_api_rejects_deep_import_and_allows_package_public_and_same_slice(
    tmp_path: Path,
) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    registry["owners"][0]["path_prefixes"] = ["api/src/atlas_production"]
    registry["dependency_rules"][0] = {
        "id": "backend-module-public-api",
        "language": "python",
        "kind": "cross_slice_public_api",
        "slice_root": "atlas_production.modules",
        "public_target_suffixes": ["", ".public"],
    }
    baseline["frozen_violations"] = baseline["frozen_violations"][1:]
    write(repo / "api/src/atlas_production/modules/alpha/helper.py", "VALUE = 1\n")
    write(repo / "api/src/atlas_production/modules/beta/__init__.py", "VALUE = 1\n")
    write(repo / "api/src/atlas_production/modules/beta/public.py", "VALUE = 1\n")
    write(repo / "api/src/atlas_production/modules/beta/internal.py", "VALUE = 1\n")
    source = repo / "api/src/atlas_production/modules/alpha/use.py"
    write(
        source,
        "from . import helper\nfrom .. import beta\nfrom ..beta import public\nfrom ..beta import internal\n",
    )
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "backend-module-public-api")

    write(source, "from . import helper\nfrom .. import beta\nfrom ..beta import public\n")
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_typescript_cross_slice_public_api_rejects_deep_import_and_allows_index_and_same_slice(
    tmp_path: Path,
) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    registry["owners"].append(
        {
            "id": "frontend_features",
            "role": "module",
            "path_prefixes": ["web/src/features"],
            "public_contracts": ["web/src/features/beta"],
        }
    )
    registry["dependency_rules"][1] = {
        "id": "frontend-feature-public-api",
        "language": "typescript",
        "kind": "cross_slice_public_api",
        "slice_root": "features",
        "public_target_suffixes": ["/index"],
    }
    baseline["frozen_violations"] = baseline["frozen_violations"][:1]
    write(repo / "web/src/features/alpha/local.ts", "export const local = 1;\n")
    write(repo / "web/src/features/beta/index.ts", "export const publicValue = 1;\n")
    write(repo / "web/src/features/beta/internal.ts", "export const internal = 1;\n")
    source = repo / "web/src/features/alpha/feature.ts"
    write(
        source,
        (
            'import { local } from "./local";\n'
            'import { publicValue } from "../beta";\n'
            'import { internal } from "../beta/internal";\n'
            "void local; void publicValue; void internal;\n"
        ),
    )
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "frontend-feature-public-api")

    write(
        source,
        'import { local } from "./local";\nimport { publicValue } from "../beta";\nvoid local; void publicValue;\n',
    )
    result = run_audit(repo)
    assert result.returncode == 0, result.stderr


def test_public_contract_must_be_a_scanned_runtime_entry_point(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    write(repo / "api/src/atlas_production/routes/notes.md", "not a runtime contract\n")
    registry["owners"][0]["public_contracts"] = [
        "api/src/atlas_production/routes/notes.md"
    ]
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "not a scanned runtime file or package entry point")


def test_public_contract_must_be_owned_by_its_declaring_owner(tmp_path: Path) -> None:
    repo, registry, baseline = fixture_repo(tmp_path)
    registry["owners"][0]["public_contracts"] = ["web/src/pages/Page.tsx"]
    save_contracts(repo, registry, baseline)
    assert_failed(run_audit(repo), "public contract is not owned by backend_transport")


def test_audit_implementation_has_no_source_size_decision_path() -> None:
    sources = [
        AUDIT.read_text(encoding="utf-8"),
        (REPO_ROOT / "infra/scripts/typescript_dependency_graph.mjs").read_text(encoding="utf-8"),
        WRAPPER.read_text(encoding="utf-8"),
    ]
    for source in sources:
        assert "line_count" not in source
        assert "splitlines(" not in source
        assert "endpoint_count" not in source
