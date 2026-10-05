"""Tests for the `Fix-Checks:` trailer a fix pass's commit carries."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import fix.suite  # noqa: E402
import pr.fix  # noqa: E402
from config.workbench_config import FixConfig, WorkbenchConfig  # noqa: E402
from fix.suite import SuiteResult, SuiteStatus  # noqa: E402

from fix_engine_support import StubAdapter, _answer, _reads, head, landed  # noqa: F401
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


@pytest.mark.parametrize("status", [s for s in SuiteStatus if s is not SuiteStatus.NOT_ATTEMPTED])
def test_every_reportable_status_names_itself(status):
    assert fix.suite.checks_trailer(SuiteResult(status=status)) == f"Fix-Checks: {status.value}"


def test_a_pass_that_checked_nothing_carries_no_trailer():
    assert fix.suite.with_trailer("fix: x", SuiteResult()) == "fix: x"


def test_git_reads_the_trailer_back():
    message = fix.suite.with_trailer("fix: x\n\n1 fixed, 0 skipped",
                                     SuiteResult(status=SuiteStatus.RED))
    parsed = subprocess.run(["git", "interpret-trailers", "--parse"], input=message,
                            capture_output=True, text=True, check=True).stdout
    assert parsed.strip() == "Fix-Checks: red"


def test_the_unverified_values_are_suite_statuses():
    assert pr.fix.UNVERIFIED_CHECKS == {
        SuiteStatus.RED.value, SuiteStatus.TIMED_OUT.value, SuiteStatus.ERROR.value}


def test_the_engine_commits_the_trailer(tmp_path, landed, head, snapshots):
    script = tmp_path / "checks"
    script.write_text("#!/usr/bin/env bash\nexit 1\n")
    script.chmod(0o755)
    adapter = StubAdapter(tmp_path)
    adapter.config = WorkbenchConfig(fix=FixConfig(verify_command=str(script), verify_timeout=30))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _answer(adapter)):
        fix.engine.run(adapter)
    assert landed.call_args.kwargs["message"].endswith("\n\nFix-Checks: red")
