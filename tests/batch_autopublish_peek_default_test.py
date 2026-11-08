"""`--auto-publish ff` reads the tree with peek_tree by default.

Revert: Scheduler._tree defaulting to read_tree.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.publish  # noqa: E402
import batch.scheduler  # noqa: E402
from batch.model import Step  # noqa: E402
from batch_scheduler_support import (  # noqa: F401,E402
    NEED, Harness, _close_open_sessions, _quiet_outcomes, row,
)
from config.workbench_config import BatchConfig  # noqa: E402


def test_scheduler_defaults_tree_to_peek_tree():
    # Revert: tree or batch.publish.read_tree.
    h = Harness([row(1, {Step.CI: NEED})], with_ai=[Step.CI])
    sched = batch.scheduler.Scheduler(
        h.session, pr_bin="pr", cfg=BatchConfig(pool_max=4),
        host=lambda: None, spawn=lambda *a, **k: None,
        sleep=lambda s: None, emit=h._record,
    )
    assert sched._tree is batch.publish.peek_tree
