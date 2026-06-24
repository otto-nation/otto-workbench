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


def _is_python_bin_script(p: Path) -> bool:
    """Check if a bin file is a Python script (by .py suffix or python3 shebang)."""
    if p.name.startswith("_") and p.suffix == ".py":
        return True
    if not p.is_file() or p.suffix:
        return False
    try:
        first_line = p.read_text().split("\n", 1)[0]
    except (UnicodeDecodeError, OSError):
        return False
    return "python3" in first_line


def _discover_bin_sources(bin_dir: Path) -> list[Path]:
    """Find Python scripts in a single bin directory."""
    if not bin_dir.exists():
        return []
    return [p for p in sorted(bin_dir.iterdir()) if _is_python_bin_script(p)]


def _discover_python_sources() -> list[Path]:
    """Find all Python source files (lib .py + bin scripts with python3 shebang)."""
    sources: list[Path] = list(sorted(LIB_DIR.glob("*.py")))

    bin_dirs = [
        REPO_ROOT / "ai" / "claude" / "bin",
        REPO_ROOT / "bin" / "local",
    ]
    for bin_dir in bin_dirs:
        sources.extend(_discover_bin_sources(bin_dir))

    return sources


def _has_wildcard_import(tree: ast.Module) -> bool:
    """Check if the module uses any ``from X import *`` statements."""
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if any(alias.name == "*" for alias in node.names):
            return True
    return False


def _names_from_import(node: ast.ImportFrom | ast.Import) -> set[str]:
    """Extract bound names from an import statement, excluding wildcard imports."""
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    return {alias.asname or alias.name for alias in node.names}


def _names_from_assign_targets(targets: list[ast.AST]) -> set[str]:
    """Extract names from assignment targets (Name and Tuple unpacking)."""
    result: set[str] = set()
    for target in targets:
        result |= _extract_assigned_names(target)
    return result


def _collect_available_names(tree: ast.Module) -> set[str]:
    """Collect names available at module scope (imports, definitions, assignments).

    Walks into top-level control flow (if/else, try/except) since those
    assignments are still module-scoped in Python.
    """
    names: set[str] = set()

    def _visit(node: ast.AST) -> None:
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            names.update(_names_from_import(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(_names_from_assign_targets(node.targets))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try, ast.ExceptHandler)):
            if isinstance(node, ast.ExceptHandler) and node.name:
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
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_names_from_import(node))

    return names


def _methods_in_class(cls_node: ast.ClassDef) -> list[ast.AST]:
    """Extract function/method definitions from a class body."""
    return [
        child for child in ast.iter_child_nodes(cls_node)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _discover_func_nodes(tree: ast.Module) -> list[ast.AST]:
    """Find all function/method definitions at module and class scope."""
    func_nodes: list[ast.AST] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            func_nodes.extend(_methods_in_class(node))
    return func_nodes


def _unresolved_refs_in_func(func: ast.AST) -> set[str]:
    """Collect bare Name(Load) references not bound locally in a function."""
    locals_ = _collect_function_locals(func)
    refs: set[str] = set()
    for node in _walk_scope(func):
        if not isinstance(node, ast.Name):
            continue
        if isinstance(node.ctx, ast.Load) and node.id not in locals_:
            refs.add(node.id)
    return refs


def _collect_bare_refs(tree: ast.Module) -> set[str]:
    """Collect bare Name references in function/method bodies that need module scope."""
    refs: set[str] = set()
    for func in _discover_func_nodes(tree):
        refs |= _unresolved_refs_in_func(func)
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
        if not isinstance(node, ast.ImportFrom):
            continue
        if any(alias.name == "*" for alias in node.names):
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
