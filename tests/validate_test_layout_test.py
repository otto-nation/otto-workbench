"""Tests for bin/local/validate-test-layout."""

import os
from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-test-layout"

vtl = load_script("validate_test_layout", SCRIPT)


def _write(root: Path, name: str, body: str = "x = 1\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _lines(n: int) -> str:
    return "x = 1\n" * n


def _run(root: Path, *extra: str) -> int:
    return vtl.main(["--max-lines", "10", "--quiet", *extra, str(root)])


# ── Naming ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "thing_test.py", "conftest.py", "__init__.py", "fakes_support.py",
])
def test_a_suite_or_declared_support_module_is_allowed(name):
    assert vtl.name_allowed(name)


@pytest.mark.parametrize("name", [
    "test_thing.py", "helpers.py", "thing_tests.py", "test.py",
])
def test_anything_else_is_refused(name):
    """`test_thing.py` above all: pytest still collects it, which is how the
    retired convention survived for as long as it did."""
    assert not vtl.name_allowed(name)


def test_main_walks_the_python_tree_only_once(tmp_path, monkeypatch):
    """main() reuses the `discover(root)` it already computed for the naming
    check instead of paying for a second `.py` walk via `discover_all`."""
    _write(tmp_path, "tests/a_test.py")
    real_walk = os.walk
    calls = []

    def counting_walk(top, *args, **kwargs):
        calls.append(top)
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(vtl.os, "walk", counting_walk)
    assert _run(tmp_path) == 0
    assert len(calls) == 2


def test_a_prefix_named_suite_fails_the_gate(tmp_path):
    _write(tmp_path, "tests/ok_test.py")
    _write(tmp_path, "tests/test_thing.py")
    assert vtl.misnamed(tmp_path) == ["tests/test_thing.py"]
    assert _run(tmp_path) == 1


def test_a_misnamed_module_in_a_subpackage_is_found(tmp_path):
    """tests/test_nesting/ is a package of suites; a flat listing exempts it."""
    _write(tmp_path, "tests/sub/test_deep.py")
    assert vtl.misnamed(tmp_path) == ["tests/sub/test_deep.py"]


def test_non_suite_files_are_ignored(tmp_path):
    _write(tmp_path, "tests/fixtures/data.json", "{}")
    _write(tmp_path, "tests/helpers.bash", _lines(900))
    assert vtl.discover(tmp_path) == []
    assert vtl.discover(tmp_path, vtl.BATS_SUFFIX) == []


def test_a_bats_suite_is_not_held_to_the_python_naming_rule(tmp_path):
    _write(tmp_path, "tests/thing.bats", _lines(3))
    assert vtl.misnamed(tmp_path) == []


def test_pycache_is_skipped(tmp_path):
    _write(tmp_path, "tests/__pycache__/test_x.py", _lines(900))
    assert vtl.discover(tmp_path) == []


def test_files_outside_tests_are_not_scanned(tmp_path):
    _write(tmp_path, "ai/lib/test_thing.py", _lines(900))
    assert vtl.discover(tmp_path) == []


def test_a_symlinked_module_is_skipped(tmp_path):
    """A dangling link would raise OSError in over_cap; a live one could
    double-count a file reachable by two paths. validate-file-size's `_in_dir`
    excludes symlinks for the same reason."""
    target = _write(tmp_path, "elsewhere/real_test.py", _lines(900))
    link = tmp_path / "tests" / "linked_test.py"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    assert vtl.discover(tmp_path) == []


def test_a_dangling_symlink_does_not_raise(tmp_path):
    link = tmp_path / "tests" / "dangling_test.py"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "does_not_exist.py")
    assert vtl.discover(tmp_path) == []
    assert _run(tmp_path) == 0


# ── Size ─────────────────────────────────────────────────────────────────────

def test_a_suite_at_the_cap_passes(tmp_path):
    _write(tmp_path, "tests/a_test.py", _lines(10))
    assert vtl.over_cap(tmp_path, 10) == []
    assert _run(tmp_path) == 0


def test_a_suite_one_over_the_cap_fails(tmp_path):
    _write(tmp_path, "tests/a_test.py", _lines(11))
    assert vtl.over_cap(tmp_path, 10) == [("tests/a_test.py", 11)]
    assert _run(tmp_path) == 1


def test_a_support_module_is_capped_too(tmp_path):
    """A helper module is still a module someone reads."""
    _write(tmp_path, "tests/fakes_support.py", _lines(11))
    assert _run(tmp_path) == 1


def test_a_bats_suite_at_the_cap_passes(tmp_path):
    _write(tmp_path, "tests/a.bats", _lines(10))
    assert vtl.over_cap(tmp_path, 10) == []
    assert _run(tmp_path) == 0


def test_a_bats_suite_one_over_the_cap_fails(tmp_path):
    _write(tmp_path, "tests/a.bats", _lines(11))
    assert vtl.over_cap(tmp_path, 10) == [("tests/a.bats", 11)]
    assert _run(tmp_path) == 1


def test_a_bats_test_header_counts_as_one_line(tmp_path):
    """`@test "it's" {` must not open a quote the counter carries forward."""
    body = '@test "it\'s fine" {\n  run true\n}\n' * 3 + "# comment\n\n"
    _write(tmp_path, "tests/a.bats", body)
    assert vtl.over_cap(tmp_path, 8) == [("tests/a.bats", 9)]


def test_a_known_bats_suite_over_the_cap_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setitem(vtl.KNOWN_OVER, "tests/a.bats", "#853")
    _write(tmp_path, "tests/a.bats", _lines(11))
    assert _run(tmp_path) == 0


def test_a_new_bats_suite_fails_even_beside_a_known_one(tmp_path, monkeypatch):
    """An exemption must not carry cover for anything but itself."""
    monkeypatch.setitem(vtl.KNOWN_OVER, "tests/known.bats", "#853")
    _write(tmp_path, "tests/known.bats", _lines(11))
    _write(tmp_path, "tests/new.bats", _lines(11))
    assert _run(tmp_path) == 1


def test_prose_does_not_count_against_the_cap(tmp_path):
    body = '"""Doc.\n' + "prose\n" * 50 + '"""\n' + "# why\n" * 20 + _lines(5)
    _write(tmp_path, "tests/a_test.py", body)
    assert vtl.over_cap(tmp_path, 10) == []


def test_violations_are_reported_worst_first(tmp_path):
    _write(tmp_path, "tests/small_test.py", _lines(12))
    _write(tmp_path, "tests/big_test.py", _lines(30))
    assert [p for p, _ in vtl.over_cap(tmp_path, 10)] == [
        "tests/big_test.py", "tests/small_test.py"]


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a 0o000 file, so the mode proves nothing",
)
def test_an_unreadable_file_names_itself_instead_of_raising(tmp_path):
    locked = _write(tmp_path, "tests/locked_test.py", _lines(3))
    locked.chmod(0o000)
    try:
        with pytest.raises(SystemExit) as caught:
            _run(tmp_path)
    finally:
        locked.chmod(0o644)
    assert "tests/locked_test.py" in str(caught.value)


# ── This repo ────────────────────────────────────────────────────────────────

def test_every_module_in_this_repo_is_named_by_the_rule():
    assert vtl.misnamed(REPO_ROOT) == []


def test_the_exemptions_are_exactly_what_is_over_the_cap():
    """Pins the list so it can only shrink.

    An entry added to quiet a newly oversized suite fails here, and so does one
    left behind after its suite was split under the cap.
    """
    over = {p for p, _ in vtl.over_cap(REPO_ROOT, vtl.MAX_CODE_LINES)}
    assert over == set(vtl.KNOWN_OVER)


def test_every_exemption_names_the_issue_that_owns_it():
    assert all(owner.startswith("#") for owner in vtl.KNOWN_OVER.values())
