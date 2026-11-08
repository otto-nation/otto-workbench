"""The report a run ends with includes the lease push argv.

Revert: `write_report(..., offline=True)` passing `tree=None`.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.store  # noqa: E402
import cli.pr_batch  # noqa: E402
from batch.model import RunStatus, Step  # noqa: E402
from batch_git_support import remote_and_clone, remote_tip  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402
from batch_session_support import seed_raw  # noqa: E402
from conftest import commit_all  # noqa: E402


def test_conclude_offline_report_contains_the_push_argv(tmp_path):
    # Revert: write_report(..., offline=True) passing tree=None.
    pair = remote_and_clone(tmp_path)
    (pair.work / "extra.txt").write_text("x\n")
    commit_all(pair.work, "extra")
    remote = remote_tip(pair.origin, "feat")
    item = make_item(1, worktree=str(pair.work), repo_dir=str(pair.work),
                     branch="feat", remote_sha=remote, head_sha=remote)
    item.step(Step.REVIEW).drafted = True
    run = make_run([item])
    seed_raw(run)
    assert cli.pr_batch._conclude(run.id, RunStatus.DONE, cli.pr_batch._Reporter()) == 0
    md = (batch.store.run_dir(run.id) / "report.md").read_text()
    assert "pr push --expect" in md
    assert remote in md
