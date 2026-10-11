"""Tests for pr.ci_need — whether a PR's check rollup needs CI attention.

The table is the one `batch.plan.ci_need` answered before it moved here, row
for row. The last test pins the one rollup on which batch and `ci-check`
answer differently on purpose.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.run_reads  # noqa: E402
import pr.ci_check  # noqa: E402
import pr.ci_need  # noqa: E402
import pr.ci_runs  # noqa: E402


@pytest.mark.parametrize("state,needed,reason", [
    ("FAILURE", True, "checks failure"), ("ERROR", True, "checks error"),
    ("PENDING", True, "checks running"), ("EXPECTED", True, "checks running"),
    ("SUCCESS", False, "checks green"), ("", False, "no checks reported"),
])
def test_need_follows_the_rollup(state, needed, reason):
    assert pr.ci_need.need(state) == pr.ci_need.CINeed(needed, reason)


def test_running_states_are_the_ones_a_ci_step_waits_on():
    assert pr.ci_need.RUNNING == frozenset({"PENDING", "EXPECTED"})


def test_an_empty_rollup_needs_nothing_while_ci_check_finds_no_checks():
    """No rollup is "nothing to do" to batch: a workflow waiting on approval reports
    none, and a CI step spawned for it could only fail. `ci-check` on the same commit
    raises `RunUnavailable`, which its entry point turns into a non-zero exit, and the
    batch watch path reads that exit as "not re-checked". Both answers are deliberate.
    This pins them side by side so a change to either one is seen."""
    assert pr.ci_need.need("") == pr.ci_need.CINeed(False, "no checks reported")
    args = MagicMock(head_sha="", run=None)
    with patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()), \
         patch("gh.run_reads.fetch_commit_checks", return_value=gh.run_reads.CommitChecks()):
        with pytest.raises(pr.ci_runs.RunUnavailable, match="No checks found"):
            pr.ci_check.run_ci(MagicMock(), args, make_ctx())
