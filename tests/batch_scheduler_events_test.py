"""`step_finished` names the log of the attempt it finished.

Revert: drop `log_path=rec.log_path` from the step_finished emit at the end of Scheduler._finish.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch.model import Step  # noqa: E402

from batch_scheduler_support import (  # noqa: F401
    Harness, NEED, _close_open_sessions, _quiet_outcomes, row,
)


def test_step_finished_names_the_attempts_log():
    h = Harness([row(1, {Step.REBASE: NEED})])
    h.sched.run_until_blocked()
    started = [f for k, f in h.events if k == "step_started"]
    finished = [f for k, f in h.events if k == "step_finished"]
    assert len(started) == len(finished) == 1
    assert finished[0]["log_path"] == started[0]["log_path"]
    assert finished[0]["log_path"].endswith(".log")
