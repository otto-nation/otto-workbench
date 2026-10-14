"""Tests for bin/local/validate-run-locks."""

from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-run-locks"

vrl = load_script("validate_run_locks", SCRIPT)

HANDLER = "ai/lib/cli/pr_rebase.py"


def _reasons(source, relpath=HANDLER):
    return [v.reason for v in vrl.check_source(source, relpath)]


@pytest.mark.parametrize("name", sorted(vrl.TAKING))
def test_a_handler_taking_a_lock_itself_is_refused(name):
    source = f"import core.run_lock\n\ndef main():\n    core.run_lock.{name}(t, 'x', 't')\n"
    reasons = _reasons(source)
    assert len(reasons) == 1
    assert f"core.run_lock.{name}" in reasons[0]


def test_a_lock_imported_by_name_is_refused():
    assert _reasons("from core.run_lock import claim_for_process\n")


def test_the_scope_vocabulary_imported_by_name_passes():
    assert _reasons("from core.run_lock import CHECKOUT, TARGET, LockBusy\n") == []


def test_asking_about_a_lock_is_not_taking_one():
    source = ("import core.run_lock\ncore.run_lock.is_held(t)\n"
              "core.run_lock.report_busy(e)\n")
    assert _reasons(source) == []


def test_the_declared_acquisition_function_may_take_locks():
    source = "import core.run_lock\ncore.run_lock.hold(s, command='x', started='t')\n"
    assert _reasons(source, "ai/lib/cli/locks.py") == []


def test_the_allowlist_admits_only_the_functions_it_names():
    source = ("import core.run_lock\n"
              "core.run_lock.lock_checkout(t, w, command='x', started='t')\n"
              "core.run_lock.acquire(t, 'x', 't')\n")
    reasons = _reasons(source, "ai/lib/review/run.py")
    assert len(reasons) == 1
    assert "core.run_lock.acquire" in reasons[0]


def test_the_repo_passes():
    assert vrl.main(["--quiet"]) == 0
