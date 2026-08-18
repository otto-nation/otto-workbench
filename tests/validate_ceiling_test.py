"""Tests for bin/local/validate-ceiling."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-ceiling"

_loader = importlib.machinery.SourceFileLoader("validate_ceiling", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_ceiling", _loader)
vc = importlib.util.module_from_spec(_spec)
sys.modules["validate_ceiling"] = vc
_spec.loader.exec_module(vc)


def _write(tmp_path: Path, name: str, *lines: str) -> None:
    """Write a fixture file one line per argument.

    Never a triple-quoted block: a marker at the start of a line in this file
    is a marker in the repo, and this validator is what would fail on it.
    """
    (tmp_path / name).write_text("\n".join(lines) + "\n")


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


def test_the_repo_itself_is_clean():
    """The gate lands green — no allowlist, so it cannot ratchet."""
    assert vc.check_directory(REPO_ROOT) == []
