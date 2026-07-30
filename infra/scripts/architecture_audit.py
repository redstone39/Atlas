#!/usr/bin/env python3
"""Read-only architecture fitness audit for Atlas Production."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
TYPESCRIPT_DEPENDENCY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_@.-]+$")
OWNER_ROLES = {"module", "composition", "transport", "shared", "infrastructure", "compatibility"}
LANGUAGES = {"python", "typescript"}
ROOT_KEYS = {"backend", "frontend"}


class AuditError(Exception):
    """Raised when the architecture contract cannot be proven."""


@dataclass(frozen=True, order=True)
class Dependency:
    language: str
    source: str
    target: str


@dataclass(frozen=True, order=True)
class Violation:
    rule_id: str
    source: str
    target: str


def fail(message: str) -> None:
    raise AuditError(message)


def exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        fail(f"{context} fields invalid; missing={missing}, unknown={unknown}")


def require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{context} must be an object")
    return value


def require_identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        fail(f"{context} must match {IDENTIFIER_RE.pattern}")
    return value


def require_nonempty_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        fail(f"{context} must be a whitespace-trimmed non-empty string")
    return value


def require_dependency_identifier(value: Any, context: str, language: str) -> str:
    identifier = require_nonempty_text(value, context)
    separator = "." if language == "python" else "/"
    parts = identifier.split(separator)
    if any(not part or part in {".", ".."} for part in parts) or "\\" in identifier:
        fail(f"{context} must be a normalized {language} dependency identifier")
    if language == "python" and any(not part.isidentifier() for part in parts):
        fail(f"{context} must be a normalized Python module identifier")
    if language == "typescript" and any(
        not TYPESCRIPT_DEPENDENCY_SEGMENT_RE.fullmatch(part) for part in parts
    ):
        fail(f"{context} must be a normalized TypeScript dependency identifier")
    return identifier


def require_public_suffix(value: Any, context: str, language: str) -> str:
    if value == "":
        return ""
    suffix = require_nonempty_text(value, context)
    separator = "." if language == "python" else "/"
    if not suffix.startswith(separator):
        fail(f"{context} must start with {separator!r} or be an explicit empty suffix")
    require_dependency_identifier(suffix[len(separator) :], context, language)
    return suffix


def require_string_array(
    value: Any,
    context: str,
    *,
    allow_empty: bool,
    allow_empty_item: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        fail(f"{context} must be {qualifier} of unique strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            fail(f"{context}[{index}] must be a string")
        if not allow_empty_item and not item:
            fail(f"{context}[{index}] must be non-empty")
        if item and item != item.strip():
            fail(f"{context}[{index}] must not have surrounding whitespace")
        result.append(item)
    if len(set(result)) != len(result):
        fail(f"{context} must contain unique strings")
    return result


def require_repo_path(value: Any, context: str) -> str:
    path = require_nonempty_text(value, context)
    pure = PurePosixPath(path)
    if (
        path.startswith("./")
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in path
        or any(character in path for character in "*?[]{}")
    ):
        fail(f"{context} must be a literal repo-relative POSIX path")
    return path


def resolve_contract_path(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def load_json(path: Path, context: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{context} not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"{context} is not valid JSON: {exc}")
    return require_object(raw, context)


def path_prefix_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def identifier_prefix_matches(identifier: str, prefix: str, language: str) -> bool:
    separator = "." if language == "python" else "/"
    return identifier == prefix or identifier.startswith(prefix + separator)


def is_excluded(repo_path: str, root: dict[str, Any]) -> bool:
    return any(path_prefix_matches(repo_path, prefix) for prefix in root["exclude_path_prefixes"]) or any(
        Path(repo_path).name.endswith(suffix) for suffix in root["exclude_filename_suffixes"]
    )


def scan_runtime_files(repo_root: Path, root: dict[str, Any]) -> list[Path]:
    root_path = repo_root / root["path"]
    if not root_path.is_dir():
        fail(f"source root is not a directory: {root['path']}")
    extensions = set(root["extensions"])
    files = []
    for path in sorted(candidate for candidate in root_path.rglob("*") if candidate.is_file()):
        repo_path = path.relative_to(repo_root).as_posix()
        if any(path.name.endswith(extension) for extension in extensions) and not is_excluded(repo_path, root):
            files.append(path)
    return files


def validate_registry(raw: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], dict[str, list[Path]]]:
    exact_keys(raw, {"schema_version", "source_roots", "owners", "dependency_rules", "ownership_exceptions"}, "registry")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        fail("registry.schema_version must be integer 1")

    source_roots = require_object(raw["source_roots"], "registry.source_roots")
    exact_keys(source_roots, ROOT_KEYS, "registry.source_roots")
    scanned: dict[str, list[Path]] = {}
    for name in sorted(ROOT_KEYS):
        root = require_object(source_roots[name], f"registry.source_roots.{name}")
        exact_keys(root, {"path", "extensions", "exclude_path_prefixes", "exclude_filename_suffixes"}, f"registry.source_roots.{name}")
        root["path"] = require_repo_path(root["path"], f"registry.source_roots.{name}.path")
        root["extensions"] = require_string_array(root["extensions"], f"registry.source_roots.{name}.extensions", allow_empty=False)
        if any(not extension.startswith(".") for extension in root["extensions"]):
            fail(f"registry.source_roots.{name}.extensions entries must start with '.'")
        root["exclude_path_prefixes"] = require_string_array(
            root["exclude_path_prefixes"], f"registry.source_roots.{name}.exclude_path_prefixes", allow_empty=True
        )
        root["exclude_path_prefixes"] = [
            require_repo_path(item, f"registry.source_roots.{name}.exclude_path_prefixes")
            for item in root["exclude_path_prefixes"]
        ]
        root["exclude_filename_suffixes"] = require_string_array(
            root["exclude_filename_suffixes"], f"registry.source_roots.{name}.exclude_filename_suffixes", allow_empty=True
        )
        scanned[name] = scan_runtime_files(repo_root, root)

    runtime_repo_paths = {
        path.relative_to(repo_root).as_posix()
        for path in scanned["backend"] + scanned["frontend"]
    }
    owners = raw["owners"]
    if not isinstance(owners, list) or not owners:
        fail("registry.owners must be a non-empty array")
    owner_ids: set[str] = set()
    owner_prefix_uses: dict[str, int] = {}
    declared_public_contracts: list[tuple[str, str]] = []
    for index, owner_value in enumerate(owners):
        context = f"registry.owners[{index}]"
        owner = require_object(owner_value, context)
        exact_keys(owner, {"id", "role", "path_prefixes", "public_contracts"}, context)
        owner_id = require_identifier(owner["id"], f"{context}.id")
        if owner_id in owner_ids:
            fail(f"duplicate owner id: {owner_id}")
        owner_ids.add(owner_id)
        if owner["role"] not in OWNER_ROLES:
            fail(f"{context}.role must be one of {sorted(OWNER_ROLES)}")
        prefixes = require_string_array(owner["path_prefixes"], f"{context}.path_prefixes", allow_empty=False)
        owner["path_prefixes"] = [require_repo_path(item, f"{context}.path_prefixes") for item in prefixes]
        for prefix in owner["path_prefixes"]:
            owner_prefix_uses[prefix] = 0
        contracts = require_string_array(owner["public_contracts"], f"{context}.public_contracts", allow_empty=True)
        owner["public_contracts"] = [require_repo_path(item, f"{context}.public_contracts") for item in contracts]
        for contract in owner["public_contracts"]:
            declared_public_contracts.append((owner_id, contract))

    all_runtime = sorted(scanned["backend"] + scanned["frontend"])
    owner_for_path: dict[str, str] = {}
    for path in all_runtime:
        repo_path = path.relative_to(repo_root).as_posix()
        matches: list[tuple[int, str, str]] = []
        for owner in owners:
            for prefix in owner["path_prefixes"]:
                if path_prefix_matches(repo_path, prefix):
                    matches.append((len(prefix), owner["id"], prefix))
        if not matches:
            fail(f"unowned runtime file: {repo_path}")
        longest = max(length for length, _, _ in matches)
        winners = {(owner_id, prefix) for length, owner_id, prefix in matches if length == longest}
        if len(winners) != 1:
            fail(f"ambiguous runtime owner for {repo_path}: {sorted(winners)}")
        owner_id, prefix = next(iter(winners))
        owner_for_path[repo_path] = owner_id
        owner_prefix_uses[prefix] += 1
    unused_prefixes = sorted(prefix for prefix, uses in owner_prefix_uses.items() if uses == 0)
    if unused_prefixes:
        fail(f"owner prefixes match no runtime file: {unused_prefixes}")
    for owner_id, contract in declared_public_contracts:
        package_entries = {
            f"{contract}/__init__.py",
            f"{contract}/index.ts",
            f"{contract}/index.tsx",
        }
        resolved_entries = ({contract} if contract in runtime_repo_paths else package_entries.intersection(runtime_repo_paths))
        if not resolved_entries:
            fail(f"public contract is not a scanned runtime file or package entry point: {contract}")
        if len(resolved_entries) != 1:
            fail(f"public contract package entry point is ambiguous: {contract} -> {sorted(resolved_entries)}")
        entry = next(iter(resolved_entries))
        if owner_for_path[entry] != owner_id:
            fail(f"public contract is not owned by {owner_id}: {contract} -> {owner_for_path[entry]}")

    rules = raw["dependency_rules"]
    if not isinstance(rules, list) or not rules:
        fail("registry.dependency_rules must be a non-empty array")
    rule_ids: set[str] = set()
    for index, rule_value in enumerate(rules):
        context = f"registry.dependency_rules[{index}]"
        rule = require_object(rule_value, context)
        kind = rule.get("kind")
        if kind == "forbid_prefix":
            exact_keys(
                rule,
                {"id", "language", "kind", "source_prefixes", "forbidden_target_prefixes", "allowed_target_ids"},
                context,
            )
            for key in ("source_prefixes", "forbidden_target_prefixes"):
                rule[key] = require_string_array(rule[key], f"{context}.{key}", allow_empty=False)
            rule["allowed_target_ids"] = require_string_array(
                rule["allowed_target_ids"], f"{context}.allowed_target_ids", allow_empty=True
            )
        elif kind == "cross_slice_public_api":
            exact_keys(rule, {"id", "language", "kind", "slice_root", "public_target_suffixes"}, context)
            rule["slice_root"] = require_nonempty_text(rule["slice_root"], f"{context}.slice_root")
            rule["public_target_suffixes"] = require_string_array(
                rule["public_target_suffixes"],
                f"{context}.public_target_suffixes",
                allow_empty=False,
                allow_empty_item=True,
            )
        elif kind == "public_api_only":
            exact_keys(
                rule,
                {
                    "id",
                    "language",
                    "kind",
                    "source_prefixes",
                    "target_root",
                    "public_target_suffixes",
                },
                context,
            )
            rule["source_prefixes"] = require_string_array(
                rule["source_prefixes"],
                f"{context}.source_prefixes",
                allow_empty=False,
            )
            rule["target_root"] = require_nonempty_text(
                rule["target_root"],
                f"{context}.target_root",
            )
            rule["public_target_suffixes"] = require_string_array(
                rule["public_target_suffixes"],
                f"{context}.public_target_suffixes",
                allow_empty=False,
                allow_empty_item=True,
            )
        else:
            fail(
                f"{context}.kind must be forbid_prefix, cross_slice_public_api, "
                "or public_api_only"
            )
        rule_id = require_identifier(rule["id"], f"{context}.id")
        if rule_id in rule_ids:
            fail(f"duplicate dependency rule id: {rule_id}")
        rule_ids.add(rule_id)
        if rule["language"] not in LANGUAGES:
            fail(f"{context}.language must be one of {sorted(LANGUAGES)}")
        language = rule["language"]
        if kind == "forbid_prefix":
            for key in ("source_prefixes", "forbidden_target_prefixes", "allowed_target_ids"):
                rule[key] = [
                    require_dependency_identifier(item, f"{context}.{key}", language)
                    for item in rule[key]
                ]
        elif kind == "cross_slice_public_api":
            rule["slice_root"] = require_dependency_identifier(
                rule["slice_root"],
                f"{context}.slice_root",
                language,
            )
            rule["public_target_suffixes"] = [
                require_public_suffix(item, f"{context}.public_target_suffixes", language)
                for item in rule["public_target_suffixes"]
            ]
        else:
            rule["source_prefixes"] = [
                require_dependency_identifier(item, f"{context}.source_prefixes", language)
                for item in rule["source_prefixes"]
            ]
            rule["target_root"] = require_dependency_identifier(
                rule["target_root"],
                f"{context}.target_root",
                language,
            )
            rule["public_target_suffixes"] = [
                require_public_suffix(item, f"{context}.public_target_suffixes", language)
                for item in rule["public_target_suffixes"]
            ]

    exceptions = raw["ownership_exceptions"]
    if not isinstance(exceptions, list):
        fail("registry.ownership_exceptions must be an array")
    exception_ids: set[str] = set()
    for index, exception_value in enumerate(exceptions):
        context = f"registry.ownership_exceptions[{index}]"
        exception = require_object(exception_value, context)
        exact_keys(
            exception,
            {"id", "owner", "reason", "allowed_scope", "exit_condition", "stale_check", "removal_slice"},
            context,
        )
        exception_id = require_identifier(exception["id"], f"{context}.id")
        if exception_id in exception_ids:
            fail(f"duplicate ownership exception id: {exception_id}")
        exception_ids.add(exception_id)
        owner_id = require_nonempty_text(exception["owner"], f"{context}.owner")
        if owner_id not in owner_ids:
            fail(f"{context}.owner references unknown owner: {owner_id}")
        require_nonempty_text(exception["reason"], f"{context}.reason")
        require_nonempty_text(exception["exit_condition"], f"{context}.exit_condition")
        require_nonempty_text(exception["removal_slice"], f"{context}.removal_slice")
        if exception["stale_check"] != "all_scopes_exist":
            fail(f"{context}.stale_check must be all_scopes_exist")
        scopes = require_string_array(exception["allowed_scope"], f"{context}.allowed_scope", allow_empty=False)
        exception["allowed_scope"] = [require_repo_path(item, f"{context}.allowed_scope") for item in scopes]
        for scope in exception["allowed_scope"]:
            scope_path = repo_root / scope
            if not scope_path.exists():
                fail(f"stale ownership exception {exception_id}; scope does not exist: {scope}")
            scoped_owners = (
                {owner_for_path[scope]}
                if scope_path.is_file() and scope in owner_for_path
                else {
                    scoped_owner
                    for runtime_path, scoped_owner in owner_for_path.items()
                    if path_prefix_matches(runtime_path, scope)
                }
            )
            if scoped_owners != {owner_id}:
                fail(f"ownership exception {exception_id} scope is not owned by {owner_id}: {scope}")

    return raw, scanned


def validate_baseline(raw: dict[str, Any], rule_ids: set[str]) -> set[Violation]:
    exact_keys(raw, {"schema_version", "frozen_violations"}, "baseline")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        fail("baseline.schema_version must be integer 1")
    entries = raw["frozen_violations"]
    if not isinstance(entries, list):
        fail("baseline.frozen_violations must be an array")
    result: set[Violation] = set()
    for index, entry_value in enumerate(entries):
        context = f"baseline.frozen_violations[{index}]"
        entry = require_object(entry_value, context)
        exact_keys(entry, {"rule_id", "source", "target"}, context)
        rule_id = require_nonempty_text(entry["rule_id"], f"{context}.rule_id")
        source = require_nonempty_text(entry["source"], f"{context}.source")
        target = require_nonempty_text(entry["target"], f"{context}.target")
        if rule_id not in rule_ids:
            fail(f"{context}.rule_id references unknown rule: {rule_id}")
        violation = Violation(rule_id, source, target)
        if violation in result:
            fail(f"duplicate baseline violation: {violation}")
        result.add(violation)
    return result


def python_source_id(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([source_root.name, *parts]) if parts else source_root.name


def resolve_python_relative(source_id: str, source_path: Path, level: int, module: str | None) -> str:
    source_parts = source_id.split(".")
    package_parts = source_parts if source_path.name == "__init__.py" else source_parts[:-1]
    keep = len(package_parts) - max(level - 1, 0)
    if keep < 0:
        return ""
    parts = package_parts[:keep]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


class LocalBindingCollector(ast.NodeVisitor):
    """Collect names Python treats as local without descending nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def _visit_comprehension(self, generators: list[ast.comprehension], outputs: list[ast.AST]) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)


@dataclass
class BindingScope:
    kind: str
    bindings: dict[str, set[str]]
    global_names: set[str]
    nonlocal_names: set[str]


class DynamicImportVisitor(ast.NodeVisitor):
    """Find dynamic imports while respecting Python lexical name shadowing."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.targets: list[str] = []
        self.scopes = [BindingScope("module", {}, set(), set())]

    def current_scope(self) -> BindingScope:
        return self.scopes[-1]

    def bind(self, name: str, binding: str) -> None:
        current = self.current_scope()
        if name in current.global_names:
            self.scopes[0].bindings[name] = {binding}
            return
        if name in current.nonlocal_names:
            for scope in reversed(self.scopes[:-1]):
                if scope.kind != "class" and name in scope.bindings:
                    scope.bindings[name] = {binding}
                    return
            return
        current.bindings[name] = {binding}

    def lookup(self, name: str) -> set[str]:
        current = self.current_scope()
        if name in current.global_names:
            return set(self.scopes[0].bindings.get(name, set()))
        skip_class = current.kind in {"function", "lambda", "comprehension"}
        possible: set[str] = set()
        for scope in reversed(self.scopes):
            if skip_class and scope.kind == "class":
                continue
            if name in scope.bindings:
                values = set(scope.bindings[name])
                possible.update(values - {"absent"})
                if "absent" not in values:
                    return possible
        return possible

    def snapshot(self) -> list[BindingScope]:
        return [
            BindingScope(
                scope.kind,
                {name: set(values) for name, values in scope.bindings.items()},
                set(scope.global_names),
                set(scope.nonlocal_names),
            )
            for scope in self.scopes
        ]

    def restore(self, scopes: list[BindingScope]) -> None:
        self.scopes = [
            BindingScope(
                scope.kind,
                {name: set(values) for name, values in scope.bindings.items()},
                set(scope.global_names),
                set(scope.nonlocal_names),
            )
            for scope in scopes
        ]

    def merge_states(self, states: list[list[BindingScope]]) -> list[BindingScope]:
        merged = self.snapshot() if not states else self._copy_state(states[0])
        for scope_index, merged_scope in enumerate(merged):
            names = set().union(*(set(state[scope_index].bindings) for state in states))
            merged_scope.bindings = {}
            for name in names:
                values: set[str] = set()
                for state in states:
                    values.update(state[scope_index].bindings.get(name, {"absent"}))
                merged_scope.bindings[name] = values
        return merged

    @staticmethod
    def _copy_state(scopes: list[BindingScope]) -> list[BindingScope]:
        return [
            BindingScope(
                scope.kind,
                {name: set(values) for name, values in scope.bindings.items()},
                set(scope.global_names),
                set(scope.nonlocal_names),
            )
            for scope in scopes
        ]

    def bind_target(self, node: ast.AST | None) -> None:
        if isinstance(node, ast.Name):
            self.bind(node.id, "other")
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                self.bind_target(item)
        elif isinstance(node, ast.Starred):
            self.bind_target(node.value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self.bind(name, "importlib" if alias.name == "importlib" else "other")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            if node.level == 0 and node.module == "importlib" and alias.name == "import_module":
                binding = "import_module"
            elif node.level == 0 and node.module == "builtins" and alias.name == "__import__":
                binding = "builtin_import"
            else:
                binding = "other"
            self.bind(name, binding)

    def visit_Call(self, node: ast.Call) -> None:
        binding: str | None = None
        if isinstance(node.func, ast.Name):
            resolved = self.lookup(node.func.id)
            if node.func.id == "__import__" and not resolved:
                binding = "builtin_import"
            elif resolved.intersection({"builtin_import", "import_module"}):
                binding = "dynamic_import"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and "importlib" in self.lookup(node.func.value.id)
        ):
            binding = "import_module"
        if binding:
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                fail(f"unscannable non-literal dynamic Python import in {self.source}")
            self.targets.append(node.args[0].value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            self.bind_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value:
            self.visit(node.value)
        self.visit(node.target)
        self.bind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self.bind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.bind_target(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.visit(target)
            self.bind_target(target)

    def _path_state(self, base: list[BindingScope], statements: Iterable[ast.AST]) -> list[BindingScope]:
        self.restore(base)
        for statement in statements:
            self.visit(statement)
        return self.snapshot()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        base = self.snapshot()
        body_state = self._path_state(base, node.body)
        else_state = self._path_state(base, node.orelse)
        self.restore(self.merge_states([body_state, else_state]))

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        base = self.snapshot()
        body_state = self._path_state(base, [node.body])
        else_state = self._path_state(base, [node.orelse])
        self.restore(self.merge_states([body_state, else_state]))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not node.values:
            return
        self.visit(node.values[0])
        for value in node.values[1:]:
            before = self.snapshot()
            self.visit(value)
            after = self.snapshot()
            self.restore(self.merge_states([before, after]))

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        accumulated = self.snapshot()
        while True:
            self.restore(accumulated)
            self.visit(node.target)
            self.bind_target(node.target)
            for statement in node.body:
                self.visit(statement)
            body_state = self.snapshot()
            candidate = self.merge_states([accumulated, body_state])
            if candidate == accumulated:
                break
            accumulated = candidate
        before_else = self._copy_state(accumulated)
        after_else = self._path_state(accumulated, node.orelse)
        self.restore(self.merge_states([before_else, after_else]))

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        accumulated = self.snapshot()
        while True:
            self.restore(accumulated)
            for statement in node.body:
                self.visit(statement)
            self.visit(node.test)
            iteration_state = self.snapshot()
            candidate = self.merge_states([accumulated, iteration_state])
            if candidate == accumulated:
                break
            accumulated = candidate
        before_else = self._copy_state(accumulated)
        after_else = self._path_state(accumulated, node.orelse)
        self.restore(self.merge_states([before_else, after_else]))

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self.visit(item.optional_vars)
                self.bind_target(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self.bind(node.name, "other")
        for statement in node.body:
            self.visit(statement)

    def visit_Try(self, node: ast.Try) -> None:
        base = self.snapshot()
        self.restore(base)
        body_prefix_states = [base]
        for statement in node.body:
            self.visit(statement)
            body_prefix_states.append(self.snapshot())
        for statement in node.orelse:
            self.visit(statement)
        normal_state = self.snapshot()
        handler_states: list[list[BindingScope]] = []
        if node.handlers:
            handler_entry = self.merge_states(body_prefix_states)
            handler_states = [self._path_state(handler_entry, [handler]) for handler in node.handlers]
        merged = self.merge_states([normal_state, *handler_states])
        self.restore(merged)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_Match(self, node: ast.AST) -> None:
        subject = getattr(node, "subject")
        cases = getattr(node, "cases")
        self.visit(subject)
        base = self.snapshot()
        states = [base]
        for case in cases:
            self.restore(base)
            pattern = getattr(case, "pattern")
            guard = getattr(case, "guard")
            self.visit(pattern)
            self.bind_match_pattern(pattern)
            if guard:
                self.visit(guard)
            for statement in getattr(case, "body"):
                self.visit(statement)
            states.append(self.snapshot())
        self.restore(self.merge_states(states))

    def bind_match_pattern(self, pattern: ast.AST) -> None:
        kind = type(pattern).__name__
        if kind in {"MatchAs", "MatchStar"}:
            name = getattr(pattern, "name", None)
            if name:
                self.bind(name, "other")
        if kind == "MatchMapping":
            rest = getattr(pattern, "rest", None)
            if rest:
                self.bind(rest, "other")
        for child in ast.iter_child_nodes(pattern):
            self.bind_match_pattern(child)

    def visit_TryStar(self, node: ast.AST) -> None:
        self.visit_Try(node)  # type: ignore[arg-type]

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def _visit_comprehension(self, generators: list[ast.comprehension], outputs: list[ast.AST]) -> None:
        if not generators:
            return
        self.visit(generators[0].iter)
        self.scopes.append(BindingScope("comprehension", {}, set(), set()))
        self.visit(generators[0].target)
        self.bind_target(generators[0].target)
        for condition in generators[0].ifs:
            self.visit(condition)
        for generator in generators[1:]:
            self.visit(generator.iter)
            self.visit(generator.target)
            self.bind_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default:
                self.visit(default)
        if node.returns:
            self.visit(node.returns)
        self.bind(node.name, "other")
        collector = LocalBindingCollector()
        for statement in node.body:
            collector.visit(statement)
        argument_names = {
            argument.arg
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        }
        if node.args.vararg:
            argument_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            argument_names.add(node.args.kwarg.arg)
        local_names = (collector.names | argument_names) - collector.global_names - collector.nonlocal_names
        self.scopes.append(
            BindingScope(
                "function",
                {
                    name: {"other"} if name in argument_names else {"unbound"}
                    for name in local_names
                },
                collector.global_names,
                collector.nonlocal_names,
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default:
                self.visit(default)
        argument_names = {
            argument.arg
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        }
        if node.args.vararg:
            argument_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            argument_names.add(node.args.kwarg.arg)
        self.scopes.append(BindingScope("lambda", {name: {"other"} for name in argument_names}, set(), set()))
        self.visit(node.body)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self.bind(node.name, "other")
        self.scopes.append(BindingScope("class", {}, set(), set()))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()


def scan_python_dependencies(files: list[Path], source_root: Path) -> set[Dependency]:
    internal_modules = {python_source_id(path, source_root) for path in files}
    internal_namespaces = {
        ".".join(module.split(".")[:index])
        for module in internal_modules
        for index in range(1, len(module.split(".")))
    }
    dependencies: set[Dependency] = set()
    for path in files:
        source = python_source_id(path, source_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"cannot parse Python source {path}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dependencies.add(Dependency("python", source, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    target = resolve_python_relative(source, path, node.level, node.module)
                else:
                    target = node.module or ""
                if not target:
                    fail(f"unresolvable relative Python import in {source}")
                if node.module is None:
                    for alias in node.names:
                        candidate = f"{target}.{alias.name}"
                        dependencies.add(Dependency("python", source, candidate))
                else:
                    internal_candidates = {
                        f"{target}.{alias.name}"
                        for alias in node.names
                        if f"{target}.{alias.name}" in internal_modules
                        or f"{target}.{alias.name}" in internal_namespaces
                    }
                    if internal_candidates:
                        dependencies.update(Dependency("python", source, candidate) for candidate in internal_candidates)
                    else:
                        dependencies.add(Dependency("python", source, target))
        dynamic_visitor = DynamicImportVisitor(source)
        dynamic_visitor.visit(tree)
        for target in dynamic_visitor.targets:
            if target.startswith("."):
                dots = len(target) - len(target.lstrip("."))
                target = resolve_python_relative(source, path, dots, target[dots:] or None)
            dependencies.add(Dependency("python", source, target))
    return {edge for edge in dependencies if identifier_prefix_matches(edge.target, source_root.name, "python")}


def scan_typescript_dependencies(
    repo_root: Path,
    files: list[Path],
    source_root: Path,
) -> set[Dependency]:
    helper = Path(__file__).with_name("typescript_dependency_graph.mjs")
    tsconfig = repo_root / "web/tsconfig.json"
    if not helper.is_file():
        fail(f"TypeScript dependency helper not found: {helper}")
    if not tsconfig.is_file():
        fail(f"TypeScript config not found: {tsconfig}")
    payload = json.dumps({"files": [str(path.resolve()) for path in files]})
    completed = subprocess.run(
        [
            "node",
            str(helper),
            "--source-root",
            str(source_root.resolve()),
            "--tsconfig",
            str(tsconfig.resolve()),
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        fail(f"TypeScript dependency scan failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fail(f"TypeScript dependency helper returned invalid JSON: {exc}")
    result = require_object(result, "TypeScript dependency result")
    exact_keys(result, {"dependencies", "errors"}, "TypeScript dependency result")
    errors = require_string_array(result["errors"], "TypeScript dependency result.errors", allow_empty=True)
    if errors:
        fail("TypeScript dependency scan errors: " + "; ".join(errors))
    dependencies = result["dependencies"]
    if not isinstance(dependencies, list):
        fail("TypeScript dependency result.dependencies must be an array")
    edges: set[Dependency] = set()
    for index, dependency_value in enumerate(dependencies):
        context = f"TypeScript dependency result.dependencies[{index}]"
        dependency = require_object(dependency_value, context)
        exact_keys(dependency, {"source", "target"}, context)
        edges.add(
            Dependency(
                "typescript",
                require_nonempty_text(dependency["source"], f"{context}.source"),
                require_nonempty_text(dependency["target"], f"{context}.target"),
            )
        )
    return edges


def apply_rules(dependencies: Iterable[Dependency], rules: list[dict[str, Any]]) -> set[Violation]:
    violations: set[Violation] = set()
    for edge in dependencies:
        for rule in rules:
            if edge.language != rule["language"]:
                continue
            if rule["kind"] == "forbid_prefix":
                if edge.target in rule["allowed_target_ids"]:
                    continue
                if any(identifier_prefix_matches(edge.source, prefix, edge.language) for prefix in rule["source_prefixes"]) and any(
                    identifier_prefix_matches(edge.target, prefix, edge.language)
                    for prefix in rule["forbidden_target_prefixes"]
                ):
                    violations.add(Violation(rule["id"], edge.source, edge.target))
                continue

            if rule["kind"] == "public_api_only":
                if not any(
                    identifier_prefix_matches(edge.source, prefix, edge.language)
                    for prefix in rule["source_prefixes"]
                ) or not identifier_prefix_matches(
                    edge.target,
                    rule["target_root"],
                    edge.language,
                ):
                    continue
                separator = "." if edge.language == "python" else "/"
                root = rule["target_root"]
                if edge.target == root:
                    violations.add(Violation(rule["id"], edge.source, edge.target))
                    continue
                target_tail = edge.target[len(root) + 1 :]
                target_slice = target_tail.split(separator, 1)[0]
                if not target_slice:
                    continue
                target_base = root + separator + target_slice
                allowed = any(
                    edge.target == target_base if suffix == "" else edge.target == target_base + suffix
                    for suffix in rule["public_target_suffixes"]
                )
                if not allowed:
                    violations.add(Violation(rule["id"], edge.source, edge.target))
                continue

            separator = "." if edge.language == "python" else "/"
            root = rule["slice_root"]
            if not identifier_prefix_matches(edge.source, root, edge.language) or not identifier_prefix_matches(
                edge.target, root, edge.language
            ):
                continue
            source_tail = edge.source[len(root) + 1 :]
            target_tail = edge.target[len(root) + 1 :]
            source_slice = source_tail.split(separator, 1)[0]
            target_slice = target_tail.split(separator, 1)[0]
            if not source_slice or not target_slice or source_slice == target_slice:
                continue
            target_base = root + separator + target_slice
            allowed = any(
                edge.target == target_base if suffix == "" else edge.target == target_base + suffix
                for suffix in rule["public_target_suffixes"]
            )
            if not allowed:
                violations.add(Violation(rule["id"], edge.source, edge.target))
    return violations


def format_violations(label: str, violations: set[Violation]) -> str:
    rendered = ", ".join(f"{item.rule_id}:{item.source}->{item.target}" for item in sorted(violations))
    return f"{label}: {rendered}"


def run(repo_root: Path, registry_path: Path, baseline_path: Path) -> None:
    registry, scanned = validate_registry(load_json(registry_path, "registry"), repo_root)
    rule_ids = {rule["id"] for rule in registry["dependency_rules"]}
    baseline = validate_baseline(load_json(baseline_path, "baseline"), rule_ids)
    backend_root = repo_root / registry["source_roots"]["backend"]["path"]
    frontend_root = repo_root / registry["source_roots"]["frontend"]["path"]
    dependencies = scan_python_dependencies(scanned["backend"], backend_root)
    dependencies.update(scan_typescript_dependencies(repo_root, scanned["frontend"], frontend_root))
    observed = apply_rules(dependencies, registry["dependency_rules"])
    new = observed - baseline
    stale = baseline - observed
    failures = []
    if new:
        failures.append(format_violations("new architecture violations", new))
    if stale:
        failures.append(format_violations("stale architecture baseline entries", stale))
    if failures:
        fail("; ".join(failures))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--registry", default="architecture-boundaries.json")
    parser.add_argument("--baseline", default="architecture-baseline.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    try:
        run(
            repo_root,
            resolve_contract_path(repo_root, args.registry),
            resolve_contract_path(repo_root, args.baseline),
        )
    except AuditError as exc:
        print(f"architecture audit failed: {exc}", file=sys.stderr)
        return 1
    print("architecture boundary audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
