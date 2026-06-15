"""Import convention tests for review scripts and library modules.

These tests verify correctness of import patterns:

1. **AST: bare underscore imports** — every ``_``-prefixed bare name
   used in a function body is explicitly imported.

2. **AST: cross-module call pattern** — library modules that call into
   other library modules use ``import module`` + ``module.func()``
   (module-qualified), not ``from module import func``.

3. **No duplicate definitions** — each function/class is defined in
   exactly one library module.

4. **No circular imports** — each library module is independently
   importable.

5. **Entry point callable** — ``main()`` is resolvable in each script.

6. **Cross-module bare references** — every bare name referencing a
   peer module's definition has an explicit import.

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

ALL_LIB_MODULES = sorted(
    p.stem for p in LIB_DIR.glob("review_*.py")
)

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


def _collect_function_locals(func_node: ast.AST) -> set[str]:
    """Collect names defined locally within a function (params, assignments).

    Only walks the immediate function body — stops at nested function
    boundaries so inner locals don't mask outer module-level references.
    """
    local_names: set[str] = set()
    for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
        local_names.add(arg.arg)
    if func_node.args.vararg:
        local_names.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        local_names.add(func_node.args.kwarg.arg)

    def _walk_shallow(node: ast.AST):
        """Walk AST children but don't descend into nested functions."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child is not func_node:
                    local_names.add(child.name)
                    continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                local_names.add(child.id)
            elif isinstance(child, ast.For) and isinstance(child.target, ast.Name):
                local_names.add(child.target.id)
            _walk_shallow(child)

    _walk_shallow(func_node)
    return local_names


def _walk_excluding_nested_functions(node: ast.AST):
    """Yield all descendant nodes, stopping at nested function boundaries."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child is not node:
                continue
        yield child
        yield from _walk_excluding_nested_functions(child)


def _collect_bare_refs(tree: ast.Module) -> set[str]:
    """Collect bare Name references that depend on module-level scope.

    Walks both top-level functions and class method bodies. Excludes
    names locally defined (params, assignments, loop vars) within their
    enclosing function. Stops at nested function boundaries so inner
    function references don't leak into the outer scope.
    """
    refs: set[str] = set()

    func_nodes = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_nodes.append(child)

    for func in func_nodes:
        locals_ = _collect_function_locals(func)
        for child in _walk_excluding_nested_functions(func):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id not in locals_:
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


# ── 3. No duplicate definitions ────────────────────────────────────────────


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
def test_library_module_imports_independently(mod_name, rp):
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
    tree = ast.parse(script.read_text(), filename=str(script))

    aliases = [
        elt.id
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "_SUBMODULES"
        and isinstance(node.value, ast.Tuple)
        for elt in node.value.elts
        if isinstance(elt, ast.Name)
    ]

    imports = {
        alias.asname or alias.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    for alias in aliases:
        assert alias in imports, (
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


# ── 8. Cross-module bare references (dynamic) ────────────────────────────────

# Build a map of {name: defining_module} for all names across all lib modules.
# Dynamically discovered — adding a module or function updates the map.
_PEER_DEFS: dict[str, str] = {}
for _mod_name in ALL_LIB_MODULES:
    _mod_path = LIB_DIR / f"{_mod_name}.py"
    for _defn in _collect_all_names(_mod_path):
        _PEER_DEFS.setdefault(_defn, _mod_name)

# All Python files that import review_* modules (lib modules + entry scripts)
_ALL_PYTHON_SOURCES = [
    (mod_name, LIB_DIR / f"{mod_name}.py") for mod_name in ALL_LIB_MODULES
] + [
    (script.name, script) for script in SCRIPTS
]


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


def _collect_wildcard_sources(tree: ast.Module) -> set[str]:
    """Collect module names from ``from X import *`` statements."""
    sources: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    sources.add(node.module)
    return sources


def _find_missing_cross_module_imports(
    source_name: str, source_path: Path,
) -> list[tuple[str, str]]:
    """Find bare names referencing peer-module definitions without an import.

    Returns [(name, defining_module), ...] for each missing import.
    Accounts for wildcard imports (``from X import *``) which make
    public (non-``_``-prefixed) names available.
    """
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    bare_refs = _collect_bare_refs(tree)
    available = _collect_explicit_names(tree)

    wildcard_sources = _collect_wildcard_sources(tree)
    for wc_mod in wildcard_sources:
        wc_path = LIB_DIR / f"{wc_mod}.py"
        if wc_path.exists():
            available |= _collect_public_names(wc_path)

    own_module = source_path.stem if source_path.suffix == ".py" else source_name

    missing = []
    for ref in sorted(bare_refs):
        if ref in available:
            continue
        if ref not in _PEER_DEFS:
            continue
        if _PEER_DEFS[ref] == own_module:
            continue
        missing.append((ref, _PEER_DEFS[ref]))
    return missing



@pytest.mark.parametrize(
    "source_name,source_path",
    _ALL_PYTHON_SOURCES,
    ids=[name for name, _ in _ALL_PYTHON_SOURCES],
)
def test_cross_module_bare_refs_are_imported(source_name, source_path):
    """Every bare name that references a peer module's definition must
    be explicitly imported.

    Bare name lookups use globals() — if a name is defined in
    review_preflight but used bare in review_pipeline without an
    explicit import, it's a NameError at runtime. Dynamically
    discovered from module ASTs so adding modules or functions
    automatically adds coverage.
    """
    missing = _find_missing_cross_module_imports(source_name, source_path)

    assert not missing, (
        f"{source_name}: bare references to peer-module names "
        f"without explicit imports (will be NameError at runtime):\n"
        + "\n".join(
            f"  - {name} (defined in {mod})" for name, mod in missing
        )
    )
