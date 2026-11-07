"""Session report emits the plan's push, not its comments closeout.

Revert: loop `planned.plan.commands` into session commands again.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import STEP_ORDER, Step, StepRecord  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch_report_support import SHA, make_item, make_run, no_debt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def test_drafted_tracked_item_emits_only_the_push(tmp_path):
    # Revert: emit every argv in planned.plan.commands.
    steps = [StepRecord(s, drafted=(s is Step.COMMENTS)) for s in STEP_ORDER]
    item = make_item(
        1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat",
        track=["T1"], steps=steps,
    )
    run = make_run([item])
    tree = TreeState(
        local=SHA, remote=item.remote_sha,
        divergence=RefDivergence(ahead=1, behind=0, comparable=True),
        checked_out=item.branch,
    )
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=lambda i: tree)
    assert len(entries) == 1
    commands = entries[0].commands
    assert sum(1 for c in commands if c.push) == 1
    assert all(c.argv[:2] != ("pr", "comments") for c in commands)
    push = next(c for c in commands if c.push)
    assert push.argv[0] == "pr" and "push" in push.argv
