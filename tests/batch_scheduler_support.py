"""Plan-row fixtures shared by batch run construction tests."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402

NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
ALL = {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NEED,
       batch.model.Step.REVIEW: NEED}


def row(n, needs=ALL, *, repo="o/r", repo_dir="/r", **extra):
    return PlanRow(repo, repo_dir, n, f"t{n}", f"b{n}", "h", False, dict(needs), **extra)
