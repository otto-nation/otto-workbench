"""A failed step's tail includes lines that arrived as the child exited."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
from batch.model import DecisionKind, Step  # noqa: E402

from batch_scheduler_support import (  # noqa: F401
    Harness, NEED, _close_open_sessions, _quiet_outcomes, row,
)


def test_failure_tail_keeps_lines_read_after_last_drain():
    h = Harness([row(1, {Step.REBASE: NEED})], codes={("rebase", "/wt/b1"): 1})
    inner = h.sched._spawn
    pending = []

    def spawn(argv, **kw):
        proc = inner(argv, **kw)
        real_poll = proc.poll

        def poll():
            code = real_poll()
            if code is not None:
                pending.append("✗ commit failed: pre-commit hook refused")
            return code

        proc.poll = poll
        proc.drain_lines = lambda: [pending.pop(0)] if pending else []
        return proc

    h.sched._spawn = spawn
    h.sched.run_until_blocked()
    failed = next(d for d in h.run.decisions if d.kind is DecisionKind.FAILED)
    assert failed.payload["reason"] == "commit_failed"
    assert failed.payload["log_tail"] == ["✗ commit failed: pre-commit hook refused"]
    assert failed.payload["detail"] == "commit failed: pre-commit hook refused"
