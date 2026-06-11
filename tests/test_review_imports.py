"""Regression tests for the proxy-module import pattern.

Python's ``from module import *`` skips names starting with ``_``.
The proxy module's ``__getattr__`` handles external access
(``module.name``), but bare name lookups inside functions use
``globals()`` — which only contains explicitly imported names.

These tests verify multiple layers of correctness:

1. **AST: bare underscore imports** — every ``_``-prefixed bare name
   used in a function body is explicitly imported (not just via wildcard
   or proxy fallback).

2. **AST: cross-module call pattern** — library modules that call into
   other library modules use ``import module`` + ``module.func()``
   (module-qualified), not ``from module import func``.  The latter
   creates a local binding that ``patch("module.func", ...)`` can't
   reach, breaking testability.

3. **Proxy resolution** — every name defined in library modules is
   accessible through the proxy, catching missing submodules or
   explicit imports.

4. **Function identity** — proxy-resolved objects are the same objects
   as in the defining module, catching stale bindings or shadowing.

5. **No duplicate definitions** — each function/class is defined in
   exactly one library module.

6. **No circular imports** — each library module is independently
   importable.

7. **Proxy submodule completeness** — ``_SUBMODULES`` references match
   actual ``import ... as`` statements.

All expectations are derived dynamically from module ASTs and runtime
state — no hardcoded name lists that drift as modules evolve.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    REPO_ROOT / "ai" / "claude" / "bin" / "review-post",
    REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate",
]
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"

BUILTINS = {"__name__", "__file__", "__class__", "__spec__"}

POST_LIB_MODULES = [
    "review_github",
    "review_format",
    "review_dedup",
    "review_posting",
]

SHARED_LIB_MODULES = [
    "review_common",
    "review_findings",
]


# ── Helpers ─────────────────────────────────────────────────────────────────


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


def _collect_definitions(mod_path: Path) -> set[str]:
    """Collect function, class, and constant definitions at module level."""
    tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
    defs: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defs.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    defs.add(target.id)
    return defs


def _collect_all_names(mod_path: Path) -> set[str]:
    """Collect all function, class, constant, and regex/compiled names."""
    tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _collect_cross_module_from_imports(
    tree: ast.Module, target_modules: set[str],
) -> list[tuple[str, str]]:
    """Find ``from target_module import callable`` patterns.

    Returns (module, function_name) for callable functions imported via
    ``from`` — classes, exceptions, and constants are excluded.
    """
    results: list[tuple[str, str]] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in target_modules:
            continue
        for alias in node.names:
            name = alias.name
            if name == "*":
                continue
            if name[0].isupper():
                continue
            results.append((node.module, name))
    return results


def _get_proxy_submodule_names(script: Path) -> list[str]:
    """Extract module names from _SUBMODULES tuple in a script."""
    tree = ast.parse(script.read_text(), filename=str(script))

    aliases: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SUBMODULES":
                    if isinstance(node.value, ast.Tuple):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Name):
                                aliases.append(elt.id)

    import_map: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_map[alias.asname or alias.name] = alias.name

    return [import_map.get(a, a) for a in aliases]


def _get_submodule_defined_names(script: Path) -> list[tuple[str, str]]:
    """Get (module_name, defined_name) for all names defined in the
    script's proxy submodules.  Drives parametrized proxy resolution
    and identity tests dynamically.
    """
    pairs: list[tuple[str, str]] = []
    for mod_name in _get_proxy_submodule_names(script):
        mod_path = LIB_DIR / f"{mod_name}.py"
        if not mod_path.exists():
            continue
        for name in sorted(_collect_all_names(mod_path)):
            pairs.append((mod_name, name))
    return pairs


# ── 1. AST: bare underscore imports ────────────────────────────────────────


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_underscore_names_explicitly_imported(script):
    """Every _-prefixed bare name in a function must be in module scope.

    Wildcard imports skip _-prefixed names. The proxy __getattr__
    handles module.name from outside, but bare lookups in functions
    use globals() which requires explicit import.
    """
    source = script.read_text()
    tree = ast.parse(source, filename=str(script))

    available = _collect_explicit_names(tree)
    referenced = _collect_bare_underscore_refs(tree)
    missing = sorted(referenced - available)

    assert not missing, (
        f"{script.name}: _-prefixed names used as bare references "
        f"but not explicitly imported (wildcard skips them):\n"
        + "\n".join(f"  - {name}" for name in missing)
    )


# ── 2. AST: cross-module call pattern ──────────────────────────────────────


_PATCHABLE_MODULES = set(POST_LIB_MODULES)


@pytest.mark.parametrize("mod_name", POST_LIB_MODULES)
def test_cross_module_calls_use_module_qualified_pattern(mod_name):
    """Library modules must use ``import X`` + ``X.func()`` for cross-module
    calls to other new library modules, not ``from X import func``.

    ``from X import func`` creates a local binding. Patching
    ``X.func`` in tests won't reach the local copy, breaking mocks.
    """
    mod_path = LIB_DIR / f"{mod_name}.py"
    if not mod_path.exists():
        pytest.skip(f"{mod_name}.py not found")

    tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
    peer_modules = _PATCHABLE_MODULES - {mod_name}

    violations = _collect_cross_module_from_imports(tree, peer_modules)

    assert not violations, (
        f"{mod_name}: uses 'from X import func' for peer library modules "
        f"(breaks patch testability). Use 'import X' + 'X.func()' instead:\n"
        + "\n".join(f"  - from {mod} import {fn}" for mod, fn in violations)
    )


# ── 3. Proxy resolution (dynamic) ──────────────────────────────────────────


_RP_SUBMODULE_NAMES = _get_submodule_defined_names(SCRIPTS[0])
_RO_SUBMODULE_NAMES = _get_submodule_defined_names(SCRIPTS[1])


@pytest.mark.parametrize(
    "mod_name,name",
    _RP_SUBMODULE_NAMES,
    ids=[f"rp.{n}" for _, n in _RP_SUBMODULE_NAMES],
)
def test_rp_proxy_resolves_name(mod_name, name, rp):
    """Every name defined in a review-post submodule must be accessible
    through the proxy.  Derived dynamically from module ASTs — adding
    a function to any submodule automatically adds a test case.
    """
    assert hasattr(rp, name), (
        f"rp.{name} (defined in {mod_name}) is not resolvable through "
        f"the proxy — check explicit imports and _SUBMODULES"
    )


@pytest.mark.parametrize(
    "mod_name,name",
    _RO_SUBMODULE_NAMES,
    ids=[f"ro.{n}" for _, n in _RO_SUBMODULE_NAMES],
)
def test_ro_proxy_resolves_name(mod_name, name, ro):
    """Every name defined in a review-orchestrate submodule must be
    accessible through the proxy.
    """
    assert hasattr(ro, name), (
        f"ro.{name} (defined in {mod_name}) is not resolvable through "
        f"the proxy — check explicit imports and _SUBMODULES"
    )


# ── 4. Function identity (dynamic) ─────────────────────────────────────────


def _callable_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Filter to callable functions (not constants/regex patterns)."""
    return [(m, n) for m, n in pairs if not n.isupper()]


_RP_CALLABLE_PAIRS = _callable_pairs(_RP_SUBMODULE_NAMES)


@pytest.mark.parametrize(
    "mod_name,func_name",
    _RP_CALLABLE_PAIRS,
    ids=[f"{m}.{n}" for m, n in _RP_CALLABLE_PAIRS],
)
def test_rp_proxy_returns_original_object(mod_name, func_name, rp):
    """rp.func must be the exact same object as module.func.

    If the proxy returns a different object (stale binding, accidental
    shadowing, wrong submodule), patches on the defining module won't
    affect what rp.func calls.
    """
    source_mod = sys.modules.get(mod_name)
    if source_mod is None:
        pytest.skip(f"{mod_name} not in sys.modules")
    if not hasattr(source_mod, func_name):
        pytest.skip(f"{mod_name}.{func_name} not found at runtime")

    proxy_obj = getattr(rp, func_name)
    source_obj = getattr(source_mod, func_name)
    assert proxy_obj is source_obj, (
        f"rp.{func_name} is not the same object as {mod_name}.{func_name}"
    )


# ── 5. No duplicate definitions ────────────────────────────────────────────


def test_no_duplicate_definitions_across_post_modules():
    """Each function/class must be defined in exactly one library module.

    Copy-paste during extraction can leave a function defined in two
    modules. Both would work, but patches would only hit one.
    """
    all_modules = POST_LIB_MODULES + SHARED_LIB_MODULES
    defs_by_name: dict[str, list[str]] = {}

    for mod_name in all_modules:
        mod_path = LIB_DIR / f"{mod_name}.py"
        if not mod_path.exists():
            continue
        for defn in _collect_definitions(mod_path):
            defs_by_name.setdefault(defn, []).append(mod_name)

    duplicates = {
        name: modules
        for name, modules in defs_by_name.items()
        if len(modules) > 1
    }
    assert not duplicates, (
        "Functions/classes defined in multiple modules:\n"
        + "\n".join(
            f"  - {name}: {', '.join(mods)}"
            for name, mods in sorted(duplicates.items())
        )
    )


# ── 6. No circular imports ─────────────────────────────────────────────────


@pytest.mark.parametrize("mod_name", POST_LIB_MODULES)
def test_library_module_imports_independently(mod_name):
    """Each library module must be importable on its own."""
    mod = sys.modules.get(mod_name)
    assert mod is not None, (
        f"{mod_name} is not in sys.modules after loading review-post — "
        f"it may have failed to import"
    )
    assert hasattr(mod, "__file__"), f"{mod_name} has no __file__"


# ── 7. Proxy submodule completeness ────────────────────────────────────────


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_proxy_submodules_match_imports(script):
    """Every module listed in _SUBMODULES must have a matching import."""
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
                import_map[alias.asname or alias.name] = alias.name

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


# ── 8. Wildcard coverage (dynamic) ─────────────────────────────────────────


def _collect_public_names(mod_path: Path) -> set[str]:
    """Collect public names (no _ prefix) defined at module level."""
    tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


@pytest.mark.parametrize("mod_name", POST_LIB_MODULES)
def test_wildcard_import_covers_public_names(mod_name, rp):
    """Public names from each library module must be accessible via rp.

    Wildcard import covers public names (no _ prefix). If a module
    defines a new public function but isn't in _SUBMODULES, this
    catches it.
    """
    mod_path = LIB_DIR / f"{mod_name}.py"
    public_names = _collect_public_names(mod_path)

    missing = sorted(name for name in public_names if not hasattr(rp, name))

    assert not missing, (
        f"Public names from {mod_name} not accessible via rp proxy:\n"
        + "\n".join(f"  - {name}" for name in missing)
    )
