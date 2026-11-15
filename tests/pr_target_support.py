"""Saved `pr` state for one target, for the target report, session and note tests."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.ci_failures  # noqa: E402
import pr.state  # noqa: E402

CI_SHA = "c0ffee1234567890c0ffee1234567890c0ffee12"


def state_with(*, items=(), jobs=(), closeout=False, pr_number=7, branch="feat/x"):
    """A PRState for o/r#*pr_number* with *items* outcomes, failing *jobs*, and closeout owed or not."""
    state = pr.state.PRState(identity=pr.state.PRIdentity("o/r", branch, pr_number, "h", ""))
    state.fix.fix.items = list(items)
    if jobs:
        groups = {j: pr.ci_failures.FailureGroup(j, pr.ci_failures.FailureKind.TEST, ()) for j in jobs}
        state.ci.runs = {5: pr.ci_failures.RunState(5, 1, CI_SHA, "completed", "failure", "", groups)}
        state.ci.latest_run_id = 5
    state.fix.summary_deferred = closeout
    return state
