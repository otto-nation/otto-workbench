"""Tests for bin/local/select-pytest — which pytest files a change selects.

The map's failure mode is silent in both directions: a test that maps to
nothing is never selected and its subject goes untested, while a bootstrap ref
treated as a dependency selects everything and the gate is as slow as before.
Both are asserted here, alongside the parsing that decides which is which.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "select-pytest"

from conftest import load_script

sp = load_script("select_pytest", SCRIPT)


def _refs(source: str) -> set[str]:
    import ast
    return sp._path_refs(ast.parse(source))


def test_a_direct_repo_root_ref_is_a_dependency():
    assert "bin/local/validate-ceiling" in _refs(
        'S = REPO_ROOT / "bin" / "local" / "validate-ceiling"'
    )


def test_a_ref_built_through_an_alias_resolves_to_the_whole_path():
    """`BIN_DIR / "wiki"` means ai/bin/wiki, not wiki.

    A regex over the text sees only the last hop, which names a path that
    exists nowhere — so the test maps to nothing and is never selected.
    """
    assert "ai/bin/wiki" in _refs(
        'BIN_DIR = REPO_ROOT / "ai" / "bin"\n'
        'thing = BIN_DIR / "wiki"\n'
    )


def test_an_alias_defined_after_its_use_still_resolves():
    """Module order is runtime order, not definition order, so both passes run.

    Chained, deliberately: one alias used before its definition is resolved by
    a single sweep, because the sweep walks the whole tree before any ref is
    read. Only an alias built from *another* alias defined later needs the
    second pass, so a one-alias fixture leaves the loop untested.
    """
    assert "ai/bin/sub/thing" in _refs(
        'SUB = BIN_DIR / "sub"\n'
        'x = SUB / "thing"\n'
        'BIN_DIR = REPO_ROOT / "ai" / "bin"\n'
    )


def test_the_path_dunder_file_idiom_is_the_repo_root():
    """165 test files spell the root this way rather than importing a constant."""
    assert "ai/bin/ceiling-scan" in _refs(
        'BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "bin"\n'
        'x = BIN_DIR / "ceiling-scan"\n'
    )


def test_three_parents_is_not_the_repo_root():
    """That is the directory above the repo; resolving it would name files
    outside the tree as though they were in it.

    The segments are deliberately not `ai`/`bin`: those spell a BOOTSTRAP_REF,
    which is dropped by an unrelated filter, so the assertion held whether the
    parent count was checked or not.
    """
    assert _refs(
        'D = Path(__file__).resolve().parent.parent.parent / "docs"\n'
        'x = D / "thing.md"\n'
    ) == set()


def test_a_dunder_file_call_that_is_not_Path_is_not_the_repo_root():
    """Any helper taking __file__ would otherwise anchor a path at the root."""
    assert _refs(
        'D = somewhere(__file__).resolve().parent.parent / "docs"\n'
        'x = D / "thing.md"\n'
    ) == set()


def test_a_bare_bootstrap_root_is_not_a_dependency():
    """LIB_DIR = REPO_ROOT / "ai" / "lib" says where imports come from.

    Counting it as a ref would select all 96 files that declare it for any
    change under ai/lib — the full suite, wearing a disguise.
    """
    assert _refs('LIB_DIR = REPO_ROOT / "ai" / "lib"') == set()


def test_a_path_under_a_bootstrap_root_is_a_dependency():
    """The exemption is for the root itself, not for everything beneath it."""
    assert "ai/lib/core/proc.py" in _refs(
        'LIB_DIR = REPO_ROOT / "ai" / "lib"\n'
        'x = LIB_DIR / "core" / "proc.py"\n'
    )


def test_a_path_rooted_in_a_fixture_is_not_a_ref():
    """tmp_path / "a" names a scratch file, not a subject in the repo."""
    assert _refs('x = tmp_path / "a" / "b"') == set()


def test_a_non_literal_segment_stops_the_resolution():
    """A computed segment cannot be resolved, and half a path would be wrong."""
    assert _refs(
        'D = REPO_ROOT / "ai"\n'
        'x = D / name / "thing"\n'
    ) == set()


def test_a_dotted_import_resolves_to_the_longest_real_module():
    index = sp._module_index()
    assert sp._resolve("core.proc.run", index) == "ai/lib/core/proc.py"
    assert sp._resolve("core.proc", index) == "ai/lib/core/proc.py"


def test_an_unknown_import_resolves_to_nothing():
    assert sp._resolve("json.loads", sp._module_index()) is None


def test_a_dep_on_a_directory_needs_a_separator_to_match():
    """A dep on bin/local must not be matched by bin/locality.

    Directory-ness is read from the filesystem, so the dep has to be a real
    directory for the prefix arm to apply at all.
    """
    assert sp._selects("tests/t.py", {"bin/local"}, ["bin/locality/x"]) is False
    assert sp._selects("tests/t.py", {"bin/local"}, ["bin/local/thing.sh"]) is True


def test_a_test_selects_itself_when_it_is_the_changed_file():
    assert sp._selects("tests/widget_test.py", set(), ["tests/widget_test.py"]) is True


def test_a_test_in_a_subpackage_selects_itself():
    """tests/ holds a package, and a flat glob once omitted all 83 of its tests."""
    assert sp._selects("tests/test_nesting/test_python.py", set(),
                       ["tests/test_nesting/test_python.py"]) is True


def test_an_unrelated_change_selects_nothing():
    assert sp._selects("tests/t.py", {"ai/lib/core/proc.py"}, ["docs/readme.md"]) is False


def test_the_real_map_covers_every_test_file():
    """--validate is the guard against a rename silently unmapping a test."""
    result = subprocess.run([str(SCRIPT), "--validate"], capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_the_real_map_reaches_a_module_through_its_imports():
    """A test importing pr.fix depends on what pr.fix itself imports.

    Transitivity is what makes the map useful: most changes land in a module no
    test imports directly.
    """
    mapping = sp.dependency_map()
    assert "ai/lib/core/proc.py" in mapping["tests/fix_engine_test.py"]


def test_a_change_to_one_script_selects_its_own_test_and_the_sweepers():
    """The property the whole selector exists for.

    Only the test for that script, plus the ones whose subject is the whole
    directory — test_python_compile byte-compiles every file under bin/, so a
    change to any of them is genuinely its business.
    """
    mapping = sp.dependency_map()
    selected = [t for t, deps in mapping.items()
                if sp._selects(t, deps, ["bin/local/validate-ceiling"])]
    assert sorted(selected) == ["tests/test_python_compile.py",
                                "tests/validate_ceiling_test.py"]


def test_a_directory_prefix_is_not_kept_beside_the_file_it_leads_to():
    """ast.walk offers every inner node of a `/` chain as well as the whole.

    Keeping `bin/local` beside `bin/local/validate-ceiling` made one script's
    change select all 19 tests that name any script in that directory.
    """
    refs = _refs('S = REPO_ROOT / "bin" / "local" / "validate-ceiling"')
    assert refs == {"bin/local/validate-ceiling"}


def test_a_directory_ref_survives_when_nothing_extends_it():
    """A test whose subject really is the directory keeps it."""
    assert _refs('D = REPO_ROOT / "bin" / "local"') == {"bin/local"}


def test_conftest_selects_everything(monkeypatch, capsys):
    """Every test's fixtures live there, so nothing about it is narrow.

    Driven through main() with the diff stubbed, because the trigger set is the
    only thing that makes this work: a conftest change reaches no test through
    _selects, so asserting the outcome is the only way to tell the trigger
    fired. A previous version asserted the string's membership in the set two
    lines from its definition, which holds with the check deleted from main().
    """
    monkeypatch.setattr(sp, "_changed_files", lambda base: ["tests/conftest.py"])
    assert sp.main(["--base", "whatever"]) == 0
    printed = capsys.readouterr().out.split()
    assert printed == sp._all_tests()


def test_a_fixture_change_selects_everything(monkeypatch, capsys):
    """Golden files nothing maps to — both golden tests spell the directory
    with a single `.parent`, which is the tests dir and not the repo root."""
    monkeypatch.setattr(sp, "_changed_files",
                        lambda base: ["tests/fixtures/mcp_tools.json"])
    assert sp.main(["--base", "whatever"]) == 0
    assert capsys.readouterr().out.split() == sp._all_tests()


def test_validate_fails_on_an_unmapped_test():
    """The guard's teeth. Asserting only the green case lets `if False:` pass."""
    assert sp._validate({"tests/mystery_test.py": set()}) == 1


def test_validate_fails_on_a_no_deps_entry_that_became_mappable():
    """A stale entry runs every time, which is selection silently not applying."""
    entry = next(iter(sp.NO_DEPS_TESTS))
    assert sp._validate({entry: {"ai/lib/core/proc.py"}}) == 1


def test_validate_passes_a_declared_unmappable_test():
    entry = next(iter(sp.NO_DEPS_TESTS))
    assert sp._validate({entry: set(), "tests/x_test.py": {"lib/x.py"}}) == 0


def test_all_lists_every_collectable_test_file():
    """Compared against the filesystem, not a threshold.

    `> 100` had 87 files of slack on this repo: `_all_tests()` could drop half
    the suite and still pass. The count is what the selector's own callers
    depend on — run-tests passes this list to pytest verbatim.
    """
    result = subprocess.run([str(SCRIPT), "--all"], capture_output=True,
                            text=True, timeout=120)
    lines = result.stdout.split()
    expected = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "tests").rglob("*.py")
        if "__pycache__" not in p.parts
        and p.name not in ("conftest.py", "__init__.py")
    )
    assert lines == expected


def test_all_reaches_tests_in_a_subpackage():
    """A flat glob omitted tests/test_nesting/ — 83 tests — from every run,
    including the full-suite fallback, which prints this same list."""
    result = subprocess.run([str(SCRIPT), "--all"], capture_output=True,
                            text=True, timeout=120)
    assert "tests/test_nesting/test_python.py" in result.stdout.split()


def test_every_no_deps_entry_names_a_file_that_exists():
    """A stale entry is a test nobody notices has stopped being covered."""
    for name in sp.NO_DEPS_TESTS:
        assert (REPO_ROOT / name).is_file(), name
