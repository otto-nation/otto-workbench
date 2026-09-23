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
    """Module order is runtime order, not definition order, so both passes run."""
    assert "ai/bin/wiki" in _refs(
        'def go():\n'
        '    return BIN_DIR / "wiki"\n'
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
    outside the tree as though they were in it."""
    assert _refs(
        'D = Path(__file__).resolve().parent.parent.parent / "ai"\n'
        'x = D / "bin"\n'
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
    """A dep on bin/local/pr must not be matched by bin/local/pr-rebase."""
    assert sp._selects("t.py", {"bin/local/pr"}, ["bin/local/pr-rebase"]) is False
    assert sp._selects("t.py", {"bin/local/pr"}, ["bin/local/pr/thing.sh"]) is True


def test_a_test_selects_itself_when_it_is_the_changed_file():
    assert sp._selects("widget_test.py", set(), ["tests/widget_test.py"]) is True


def test_an_unrelated_change_selects_nothing():
    assert sp._selects("t.py", {"ai/lib/core/proc.py"}, ["docs/readme.md"]) is False


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
    assert "ai/lib/core/proc.py" in mapping["fix_engine_test.py"]


def test_a_change_to_one_script_selects_its_own_test_and_the_sweepers():
    """The property the whole selector exists for.

    Only the test for that script, plus the ones whose subject is the whole
    directory — test_python_compile byte-compiles every file under bin/, so a
    change to any of them is genuinely its business.
    """
    mapping = sp.dependency_map()
    selected = [t for t, deps in mapping.items()
                if sp._selects(t, deps, ["bin/local/validate-ceiling"])]
    assert sorted(selected) == ["test_python_compile.py", "validate_ceiling_test.py"]


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


def test_conftest_selects_everything():
    """Every test's fixtures live there, so nothing about it is narrow."""
    result = subprocess.run(
        [str(SCRIPT), "--base", "HEAD"], capture_output=True, text=True, timeout=120,
        env=None,
    )
    assert result.returncode == 0
    assert "tests/conftest.py" in sp.FULL_SUITE_TRIGGERS


def test_all_lists_every_test_file_but_not_conftest():
    result = subprocess.run([str(SCRIPT), "--all"], capture_output=True,
                            text=True, timeout=120)
    lines = result.stdout.split()
    assert "tests/conftest.py" not in lines
    assert len(lines) > 100


def test_every_no_deps_entry_names_a_file_that_exists():
    """A stale entry is a test nobody notices has stopped being covered."""
    for name in sp.NO_DEPS_TESTS:
        assert (REPO_ROOT / "tests" / name).is_file(), name
