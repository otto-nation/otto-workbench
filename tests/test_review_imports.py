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

3. **Proxy resolution** — every name that tests access through the
   ``rp`` / ``ro`` proxy actually resolves at runtime, catching
   missing submodules or deleted functions.

4. **Function identity** — ``rp.func`` resolves to the same object as
   the function in its defining module, catching stale bindings or
   accidental shadowing.

5. **No duplicate definitions** — each function/class is defined in
   exactly one library module, catching copy-paste leftovers.

6. **No circular imports** — each library module is independently
   importable.

7. **Proxy submodule completeness** — ``_SUBMODULES`` references match
   actual ``import ... as`` statements.
"""

from __future__ import annotations

import ast
import importlib
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

ORCHESTRATE_LIB_MODULES = [
    "review_agent",
    "review_pipeline",
    "review_preflight",
    "review_prompt",
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


def _collect_definitions(tree: ast.Module) -> set[str]:
    """Collect function, class, and constant definitions at module level."""
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


def _collect_cross_module_from_imports(
    tree: ast.Module, target_modules: set[str],
) -> list[tuple[str, str]]:
    """Find ``from target_module import callable`` patterns.

    Returns list of (module, function_name) for callable functions
    imported via ``from`` (not classes/exceptions/constants).
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


@pytest.mark.parametrize(
    "mod_name",
    POST_LIB_MODULES,
    ids=lambda m: m,
)
def test_cross_module_calls_use_module_qualified_pattern(mod_name):
    """Library modules must use ``import X`` + ``X.func()`` for cross-module
    calls to other new library modules, not ``from X import func``.

    ``from X import func`` creates a local binding. Patching
    ``X.func`` in tests won't reach the local copy, breaking mocks.
    Module-qualified calls (``X.func()``) look up ``func`` on ``X``
    at call time, so a single ``patch("X.func", ...)`` works.
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


# ── 3. Proxy resolution ────────────────────────────────────────────────────


_RP_EXPECTED_NAMES = [
    # review_findings
    "Finding", "parse_findings", "parse_diff_hunks",
    "_extract_path", "_extract_body_text", "_parse_finding_line",
    "_finalize_finding", "_close_previous",
    "_is_section_boundary", "_match_severity_header",
    "FINDING_ID_RE", "FIRST_FILE_RE", "STRIKETHROUGH_RE",
    # review_common
    "SEVERITY_LABELS", "SEVERITY_MUST", "SEVERITY_SHOULD",
    "SEVERITY_NIT", "SEVERITY_IDIOMS",
    # review_github
    "_gh_api", "_fetch_pr_metadata", "_get_diff",
    "_check_existing_pending", "_fetch_json_list",
    "_is_rate_limited", "_is_line_resolution_error",
    "_handle_api_attempt", "_post_with_retries",
    "_count_new_commits", "LineResolutionError",
    "MAX_RETRIES", "RATE_LIMIT_WAIT", "RATE_LIMIT_BACKOFF",
    "RATE_LIMIT_MAX_WAIT", "NON_RATE_LIMIT_DELAY", "GH_API_TIMEOUT",
    # review_format
    "classify_findings", "renumber_for_posting",
    "format_inline_comment", "format_body_text",
    "resolve_permalinks", "_resolve_path", "_hunk_end",
    "_format_path_ref", "_format_finding_line", "_build_permalink",
    "_PERMALINK_REF_RE",
    "CLASS_INLINE", "CLASS_FILE_LEVEL", "CLASS_SKIPPED",
    # review_dedup
    "dedup_against_posted", "_word_set", "_jaccard",
    "_extract_body_findings", "_fetch_bot_comments",
    "DEDUP_THRESHOLD",
    # review_posting
    "_chunk_comments", "_post_chunked_review", "post_review",
    "_submit_review", "write_post_tracking",
    "_post_as_comment", "_reclassify_and_retry",
    "_post_and_track", "_print_dry_run", "_format_submit_command",
    "_format_comment_body",
    "DEFAULT_CHUNK_SIZE", "HEAD_SHA_RE",
]


@pytest.mark.parametrize("name", _RP_EXPECTED_NAMES)
def test_rp_proxy_resolves_name(name, rp):
    """Every name that tests or callers access through the review-post
    proxy must resolve without AttributeError.
    """
    assert hasattr(rp, name), (
        f"rp.{name} is not resolvable — missing from explicit imports, "
        f"wildcard imports, and proxy __getattr__ fallback"
    )


_RO_EXPECTED_NAMES = [
    # review_common
    "_info", "_warn", "_err", "_derive_path", "_run",
    "SEVERITY_LABELS", "MODE_PR", "MODE_SELF",
    "PIPELINE_MULTI", "PIPELINE_SINGLE",
    "FILENAME_SESSION", "FILENAME_META",
    # review_findings
    "Finding", "parse_findings", "renumber_findings",
    "merge_reviews", "verify_findings",
    "strip_evidence_blocks", "strip_stable_ids",
    "annotate_prior_with_stable_ids",
    "_count_findings", "_has_findings",
    "_extract_section", "_clean_section_text",
    "_validate_group_output",
    "renumber_section", "_renumber_prefix",
    "_finding_dedup_key", "_dedup_findings",
    "_clean_triage", "_dedup_triage",
    "_remove_dropped_findings", "_is_next_finding_or_section",
    "FINDING_SECTIONS", "FINDING_PREFIX_MUST",
    "FINDING_PREFIX_SHOULD", "FINDING_PREFIX_NIT",
    "FINDING_PREFIX_IDIOMS",
    # review_preflight
    "PRMetadata", "PRContext", "ReviewJob",
    "group_files", "classify_tier", "collect_preflight_data",
    "Group", "GROUP_TIER1", "GROUP_TIER3",
    "_parse_numstat", "_scope_diff", "_truncate_diff",
    "_read_file_safe", "_split_large_dir",
    "_merge_smallest_groups",
    "fetch_pr_metadata", "fetch_branch_metadata",
    "format_preflight_data",
    # review_pipeline
    "_fetch_metadata",
    "run_single_agent", "run_multi_phase",
    "_build_meta_header", "_build_mechanical_fallback",
    "_write_clean_review", "_mechanical_verdict",
    "_check_serial_abort", "_diagnose_result_type",
    "_diagnose_missing_output", "_is_model_error",
    "_try_recover_output", "_extract_heredoc",
    "_extract_denied_content",
    "_parse_session_cost", "_check_budget",
    "_resolve_model",
    "DEFAULT_MAX_PARALLEL", "DEFAULT_MAX_COST",
    "MULTI_PHASE_LINE_THRESHOLD", "MULTI_PHASE_FILE_THRESHOLD",
    "CONSECUTIVE_FAIL_THRESHOLD",
]


@pytest.mark.parametrize("name", _RO_EXPECTED_NAMES)
def test_ro_proxy_resolves_name(name, ro):
    """Every name that tests access through the review-orchestrate
    proxy must resolve without AttributeError.
    """
    assert hasattr(ro, name), (
        f"ro.{name} is not resolvable — missing from explicit imports, "
        f"wildcard imports, and proxy __getattr__ fallback"
    )


# ── 4. Function identity ───────────────────────────────────────────────────


_RP_IDENTITY_CHECKS = [
    ("review_github", [
        "_gh_api", "_fetch_pr_metadata", "_get_diff",
        "_check_existing_pending", "_post_with_retries",
        "_handle_api_attempt", "_count_new_commits",
    ]),
    ("review_format", [
        "classify_findings", "renumber_for_posting",
        "format_inline_comment", "format_body_text",
        "resolve_permalinks",
    ]),
    ("review_dedup", [
        "dedup_against_posted", "_word_set", "_jaccard",
        "_fetch_bot_comments",
    ]),
    ("review_posting", [
        "_chunk_comments", "_post_chunked_review", "post_review",
        "_submit_review", "write_post_tracking",
        "_reclassify_and_retry", "_post_and_track",
    ]),
]


def _identity_params():
    for mod_name, funcs in _RP_IDENTITY_CHECKS:
        for func_name in funcs:
            yield pytest.param(mod_name, func_name, id=f"{mod_name}.{func_name}")


@pytest.mark.parametrize("mod_name,func_name", _identity_params())
def test_rp_proxy_returns_original_object(mod_name, func_name, rp):
    """rp.func must be the exact same object as module.func.

    If the proxy returns a different object (stale binding, accidental
    shadowing, wrong submodule), patches on the defining module won't
    affect what rp.func calls.
    """
    source_mod = sys.modules[mod_name]
    proxy_obj = getattr(rp, func_name)
    source_obj = getattr(source_mod, func_name)
    assert proxy_obj is source_obj, (
        f"rp.{func_name} is not the same object as {mod_name}.{func_name} — "
        f"proxy returned {proxy_obj!r}, source has {source_obj!r}"
    )


# ── 5. No duplicate definitions ────────────────────────────────────────────


def test_no_duplicate_definitions_across_post_modules():
    """Each function/class must be defined in exactly one post library module.

    Copy-paste during extraction can leave a function defined in two
    modules. Both would work, but patches would only hit one.
    """
    all_modules = POST_LIB_MODULES + SHARED_LIB_MODULES
    defs_by_name: dict[str, list[str]] = {}

    for mod_name in all_modules:
        mod_path = LIB_DIR / f"{mod_name}.py"
        if not mod_path.exists():
            continue
        tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
        for defn in _collect_definitions(tree):
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
    """Each library module must be importable on its own.

    Catches circular import chains between new modules.
    """
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


# ── 8. Wildcard + explicit imports cover all module exports ────────────────


def _collect_module_public_names(mod_path: Path) -> set[str]:
    """Collect all public names defined at module level."""
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
    defines a new public function but review-post doesn't import it
    (e.g. missing wildcard or the module isn't in _SUBMODULES), this
    catches it.
    """
    mod_path = LIB_DIR / f"{mod_name}.py"
    public_names = _collect_module_public_names(mod_path)

    missing = []
    for name in sorted(public_names):
        if not hasattr(rp, name):
            missing.append(name)

    assert not missing, (
        f"Public names from {mod_name} not accessible via rp proxy:\n"
        + "\n".join(f"  - {name}" for name in missing)
    )
