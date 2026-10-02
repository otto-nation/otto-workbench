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


def test_a_prefix_named_suite_fails_the_gate(tmp_path):
    _write(tmp_path, "tests/ok_test.py")
    _write(tmp_path, "tests/test_thing.py")
    assert vtl.misnamed(tmp_path) == ["tests/test_thing.py"]
    assert _run(tmp_path) == 1


def test_a_misnamed_module_in_a_subpackage_is_found(tmp_path):
    """tests/test_nesting/ is a package of suites; a flat listing exempts it."""
    _write(tmp_path, "tests/sub/test_deep.py")
    assert vtl.misnamed(tmp_path) == ["tests/sub/test_deep.py"]


def test_non_python_files_are_ignored(tmp_path):
    _write(tmp_path, "tests/thing.bats", _lines(900))
    _write(tmp_path, "tests/fixtures/data.json", "{}")
    assert vtl.discover(tmp_path) == []


def test_pycache_is_skipped(tmp_path):
    _write(tmp_path, "tests/__pycache__/test_x.py", _lines(900))
    assert vtl.discover(tmp_path) == []


def test_files_outside_tests_are_not_scanned(tmp_path):
    _write(tmp_path, "ai/lib/test_thing.py", _lines(900))
    assert vtl.discover(tmp_path) == []


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


def test_prose_does_not_count_against_the_cap(tmp_path):
    body = '"""Doc.\n' + "prose\n" * 50 + '"""\n' + "# why\n" * 20 + _lines(5)
    _write(tmp_path, "tests/a_test.py", body)
    assert vtl.over_cap(tmp_path, 10) == []


def test_violations_are_reported_worst_first(tmp_path):
    _write(tmp_path, "tests/small_test.py", _lines(12))
    _write(tmp_path, "tests/big_test.py", _lines(30))
    assert [p for p, _ in vtl.over_cap(tmp_path, 10)] == [
        "tests/big_test.py", "tests/small_test.py"]


def test_a_known_suite_over_the_cap_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setitem(vtl.KNOWN_OVER, "tests/a_test.py", "#910")
    _write(tmp_path, "tests/a_test.py", _lines(11))
    assert _run(tmp_path) == 0


def test_a_new_suite_fails_even_beside_a_known_one(tmp_path, monkeypatch):
    """An exemption must not carry cover for anything but itself."""
    monkeypatch.setitem(vtl.KNOWN_OVER, "tests/known_test.py", "#910")
    _write(tmp_path, "tests/known_test.py", _lines(11))
    _write(tmp_path, "tests/new_test.py", _lines(11))
    assert _run(tmp_path) == 1


def test_an_exemption_does_not_cover_a_bad_name(tmp_path, monkeypatch):
    """KNOWN_OVER is a size exemption; it says nothing about naming."""
    monkeypatch.setitem(vtl.KNOWN_OVER, "tests/test_big.py", "#910")
    _write(tmp_path, "tests/test_big.py", _lines(11))
    assert _run(tmp_path) == 1


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
