"""Regression tests for the proxy-module import pattern.

Python's ``from module import *`` skips names starting with ``_``.
The proxy module's ``__getattr__`` handles external access
(``module.name``), but bare name lookups inside functions use
``globals()`` — which only contains explicitly imported names.

These tests parse the script ASTs and verify that every ``_``-prefixed
bare name used inside a function body is either explicitly imported or
defined at module level.  This catches the class of bug where a
function references ``_some_helper`` that was only available via
wildcard import (which silently skips it) or the proxy fallback
(which doesn't apply to in-module global lookups).
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    REPO_ROOT / "ai" / "claude" / "bin" / "review-post",
    REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate",
]

BUILTINS = {"__name__", "__file__", "__class__", "__spec__"}


def _collect_explicit_names(tree: ast.Module) -> set[str]:
    """Collect names explicitly available in module scope."""
    names: set[str] = set()

    for node in ast.iter_child_nodes(tree):
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

    return names


def _collect_bare_underscore_refs(tree: ast.Module) -> set[str]:
    """Collect _-prefixed bare Name references inside function bodies."""
    refs: set[str] = set()

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id.startswith("_"):
                refs.add(child.id)

    return refs - BUILTINS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_underscore_names_explicitly_imported(script):
    """Every _-prefixed bare name in a function must be in module scope.

    Wildcard imports (``from mod import *``) skip ``_``-prefixed names.
    The proxy module ``__getattr__`` handles ``module.name`` access from
    outside, but bare name lookups inside functions resolve via
    ``globals()`` — which requires an explicit import or module-level
    assignment.
    """
    source = script.read_text()
    tree = ast.parse(source, filename=str(script))

    available = _collect_explicit_names(tree)
    referenced = _collect_bare_underscore_refs(tree)
    missing = sorted(referenced - available)

    assert not missing, (
        f"{script.name}: these _-prefixed names are used as bare references "
        f"in function bodies but are not explicitly imported or defined at "
        f"module level (wildcard import skips them):\n"
        + "\n".join(f"  - {name}" for name in missing)
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_proxy_submodules_importable(script):
    """Every module listed in _SUBMODULES must be importable."""
    source = script.read_text()
    tree = ast.parse(source, filename=str(script))

    submodule_aliases: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SUBMODULES":
                    if isinstance(node.value, ast.Tuple):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Name):
                                submodule_aliases.append(elt.id)

    import_map: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                import_map[name] = alias.name

    for alias in submodule_aliases:
        assert alias in import_map, (
            f"{script.name}: _SUBMODULES references '{alias}' but no "
            f"'import ... as {alias}' found"
        )


@pytest.mark.parametrize(
    "script,fixture",
    [
        (SCRIPTS[0], "rp"),
        (SCRIPTS[1], "ro"),
    ],
    ids=["review-post", "review-orchestrate"],
)
def test_main_callable(script, fixture, request):
    """The main() function must be resolvable without NameError."""
    mod = request.getfixturevalue(fixture)
    assert hasattr(mod, "main"), f"{script.name} has no main() function"
    assert callable(mod.main)
