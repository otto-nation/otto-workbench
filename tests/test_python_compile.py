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


def _walk_scope(node: ast.AST) -> list[ast.AST]:
    """Walk all descendants of node, stopping at nested scope boundaries.

    Yields every node inside the given function/method body but does NOT
    descend into nested functions, async functions, or lambdas — those
    are separate scopes.
    """
    result: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        result.append(child)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(child))
    return result


def _extract_assigned_names(node: ast.AST) -> set[str]:
    """Extract names bound by a single AST node (assignment target, loop var, etc.)."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return {elt.id for elt in node.elts if isinstance(elt, ast.Name)}
    return set()


def _collect_function_locals(func_node: ast.AST) -> set[str]:
    """Collect all names bound inside a function scope."""
    names: set[str] = set()

    args = func_node.args
    for arg in args.args + args.posonlyargs + args.kwonlyargs:
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)

    for node in _walk_scope(func_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            names |= _extract_assigned_names(node.target)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)

    return names


def _collect_bare_refs(tree: ast.Module) -> set[str]:
    """Collect bare Name references in function/method bodies that need module scope."""
    func_nodes: list[ast.AST] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_nodes.append(child)

    refs: set[str] = set()
    for func in func_nodes:
        locals_ = _collect_function_locals(func)
        for node in _walk_scope(func):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in locals_:
                    refs.add(node.id)

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
