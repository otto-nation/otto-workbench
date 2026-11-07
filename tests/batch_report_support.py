"""Shared constructors for batch session-report tests."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch.model import (STEP_ORDER, Decision, DecisionKind, Item, ItemStatus, Run,  # noqa: E402
                         RunStatus, StepRecord)
from pr.comments_fix import CloseoutDebt  # noqa: E402

SHA = "a" * 40


def make_item(n=1, **kw):
    fields = dict(
        key=f"o/r#{n}", repo="o/r", repo_dir="/r", pr=n, branch=f"b{n}",
        head_sha=SHA, remote_sha=SHA, worktree=f"/wt/b{n}",
        status=ItemStatus.DONE, steps=[StepRecord(s) for s in STEP_ORDER],
    )
    fields.update(kw)
    return Item(**fields)


def make_run(items, decisions=(), status=RunStatus.DONE, run_id="r1"):
    return Run(id=run_id, started_at="t", steps=list(STEP_ORDER), pool=1, auto_publish=[],
               status=status, items=list(items), decisions=list(decisions))


def make_decision(kind, step, payload=None, id="d1", item="o/r#1"):
    return Decision(id=id, item=item, step=step, kind=kind, payload=payload or {})


def no_debt(repo_dir, branch):
    return CloseoutDebt()
