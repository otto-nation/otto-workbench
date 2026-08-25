"""Tests for bin/local/validate-ceiling."""

from pathlib import Path

from conftest import load_script
from conftest import write_marker_file as _write

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-ceiling"

vc = load_script("validate_ceiling", SCRIPT)


def test_a_marker_with_a_trigger_passes(tmp_path):
    _write(tmp_path, "a.py", "# ceiling: one retry, upgrade if flakes get common")
    assert vc.check_directory(tmp_path) == []


def test_a_marker_with_a_trigger_below_it_passes(tmp_path):
    _write(
        tmp_path, "a.py",
        "# ceiling: one retry only.",
        "# Upgrade to a backoff if flakes get common.",
    )
    assert vc.check_directory(tmp_path) == []


def test_a_permanent_marker_passes(tmp_path):
    _write(tmp_path, "a.py", "# ceiling-permanent: the prefix is truncated, and stays so.")
    assert vc.check_directory(tmp_path) == []


def test_a_marker_naming_only_a_tradeoff_fails(tmp_path):
    _write(tmp_path, "a.py", "# ceiling: one retry only.")
    violations = vc.check_directory(tmp_path)
    assert [(v["file"], v["line"]) for v in violations] == [("a.py", 1)]
