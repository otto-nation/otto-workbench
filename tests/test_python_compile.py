"""Verify all Python source files have no unresolved bare name references.

Catches missing imports (e.g., using ``sys.stderr`` without ``import sys``)
via AST analysis. Unlike runtime imports, this detects errors in code paths
that aren't executed at import time.

Dynamically discovers all Python files — adding a new module or script
automatically adds coverage. Files using wildcard imports are skipped
(they're covered by test_review_imports.py).
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"

BUILTIN_NAMES = set(dir(builtins)) | {
    "__name__", "__file__", "__class__", "__spec__",
    "__doc__", "__all__", "__path__", "__loader__",
    "__package__", "__builtins__", "__cached__",
    "annotations",
}


def _discover_python_sources() -> list[Path]:
    """Find all Python source files (lib .py + bin scripts with python3 shebang)."""
    sources: list[Path] = []

    for p in sorted(LIB_DIR.glob("*.py")):
        sources.append(p)

    bin_dirs = [
        REPO_ROOT / "ai" / "claude" / "bin",
        REPO_ROOT / "bin" / "local",
    ]
    for bin_dir in bin_dirs:
        if not bin_dir.exists():
            continue
        for p in sorted(bin_dir.iterdir()):
            if p.name.startswith("_") and p.suffix == ".py":
                sources.append(p)
                continue
            if not p.is_file() or p.suffix:
                continue
            try:
                first_line = p.read_text().split("\n", 1)[0]
            except (UnicodeDecodeError, OSError):
                continue
            if "python3" in first_line:
                sources.append(p)

    return sources


def _has_wildcard_import(tree: ast.Module) -> bool:
    """Check if the module uses any ``from X import *`` statements."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return True
    return False


def _collect_available_names(tree: ast.Module) -> set[str]:
    """Collect names available at module scope (imports, definitions, assignments).

    Walks into top-level control flow (if/else, try/except) since those
    assignments are still module-scoped in Python.
    """
    names: set[str] = set()

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name != "*":
                    names.add(name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try)):
            for child in ast.iter_child_nodes(node):
                _visit(child)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
            for child in ast.iter_child_nodes(node):
                _visit(child)

    for node in ast.iter_child_nodes(tree):
        _visit(node)

    return names


def _collect_function_locals(func_node: ast.AST) -> set[str]:
    """Collect names defined locally within a function (params, assignments, loops)."""
    local_names: set[str] = set()
    for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
        local_names.add(arg.arg)
    if func_node.args.vararg:
        local_names.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        local_names.add(func_node.args.kwarg.arg)

    def _walk_shallow(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child is not func_node:
                    local_names.add(child.name)
                    continue
            if isinstance(child, ast.Lambda):
                for a in child.args.args:
                    local_names.add(a.arg)
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    local_names.add(alias.asname or alias.name)
            elif isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    name = alias.asname or alias.name
                    if name != "*":
                        local_names.add(name)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                local_names.add(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                local_names.add(child.name)
            elif isinstance(child, ast.For) and isinstance(child.target, ast.Name):
                local_names.add(child.target.id)
            elif isinstance(child, ast.For) and isinstance(child.target, ast.Tuple):
                for elt in child.target.elts:
                    if isinstance(elt, ast.Name):
                        local_names.add(elt.id)
            elif isinstance(child, ast.comprehension):
                if isinstance(child.target, ast.Name):
                    local_names.add(child.target.id)
                elif isinstance(child.target, ast.Tuple):
                    for elt in child.target.elts:
                        if isinstance(elt, ast.Name):
                            local_names.add(elt.id)
            _walk_shallow(child)

    _walk_shallow(func_node)
    return local_names


def _collect_bare_refs(tree: ast.Module) -> set[str]:
    """Collect bare Name references in function/method bodies that need module scope."""
    refs: set[str] = set()

    func_nodes: list[ast.AST] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_nodes.append(child)

    def _walk_excluding_nested(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child is not node:
                    continue
            if isinstance(child, ast.Lambda):
                continue
            yield child
            yield from _walk_excluding_nested(child)

    for func in func_nodes:
        locals_ = _collect_function_locals(func)
        for child in _walk_excluding_nested(func):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id not in locals_:
                    refs.add(child.id)

    return refs


ALL_SOURCES = _discover_python_sources()


@pytest.mark.parametrize(
    "source_path",
    ALL_SOURCES,
    ids=[p.name for p in ALL_SOURCES],
)
def test_no_wildcard_imports(source_path: Path):
    """No Python file should use ``from X import *``."""
    source = source_path.read_text()
    tree = ast.parse(source, filename=str(source_path))

    wildcards: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    wildcards.append(f"  - from {node.module} import * (line {node.lineno})")

    assert not wildcards, (
        f"{source_path.name}: wildcard imports found:\n"
        + "\n".join(wildcards)
    )


@pytest.mark.parametrize(
    "source_path",
    ALL_SOURCES,
    ids=[p.name for p in ALL_SOURCES],
)
def test_bare_refs_resolvable(source_path: Path):
    """Every bare name in a function body must be a builtin, import, or definition.

    Catches missing stdlib imports like ``sys`` that only fail at runtime
    when the specific code path is hit. Files using wildcard imports are
    skipped — they have their own dedicated import tests.
    """
    source = source_path.read_text()
    tree = ast.parse(source, filename=str(source_path))

    available = _collect_available_names(tree) | BUILTIN_NAMES
    bare_refs = _collect_bare_refs(tree)
    missing = sorted(bare_refs - available)

    assert not missing, (
        f"{source_path.name}: bare names used in function bodies "
        f"without import or definition (will be NameError at runtime):\n"
        + "\n".join(f"  - {name}" for name in missing)
    )
