"""With --auto-publish, an item with an open decision never reaches the remote.

Real git on both sides: a bare origin, the batch's clone, and the real publish
path (fetch, compare, push), so "unchanged" is read off the remote itself.
"""

import sys
from pathlib import Path

from batch_git_support import remote_and_clone, remote_tip
from conftest import commit_all, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.model import DecisionKind, ItemStatus, RunStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402


class _Exited:
    pid = 1

    def __init__(self, code=0):
        self.code = code

    def poll(self):
        return self.code

    def drain_lines(self):
        return []

    def stdout(self):
        return "{}"

    def kill(self):
        pass


def _drive(pair, monkeypatch, findings, *, code=0):
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: findings)
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: [])
    head = remote_tip(pair.origin, "feat")
    planned = PlanRow("o/r", str(pair.work), 1, "t", "feat", head, False,
                      {Step.REVIEW: StepNeed(True, "x")})

    def spawn(argv, *, log_path, trail_root):
        (pair.work / "fix.txt").write_text("fix\n")
        commit_all(pair.work, "fix: review finding")
        return _Exited(code)

    run = batch.scheduler.new_run([planned], steps=[Step.REVIEW], selected=None, pool=1,
                                  auto_publish=[Step.REVIEW])
    sched = batch.scheduler.Scheduler(
        run, pr_bin=str(REPO_ROOT / "ai" / "bin" / "pr"), cfg=BatchConfig(pool_max=1),
        host=lambda: batch.admission.HostSample(None, None, None), spawn=spawn,
        replan=lambda r: planned,
        worktrees=lambda d, b: WorktreeResult(str(pair.work), False, ""),
        rss=lambda pid: 0, estimates=batch.admission.Estimates({}),
        emit=lambda *a, **k: None, sleep=lambda s: None)
    return run, sched.run_until_blocked(), head, sched


# passes-at-base: negative case; the old scheduler never pushed past an open decision either
def test_an_open_decision_keeps_auto_publish_off_the_remote(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    run, status, head, _ = _drive(pair, monkeypatch,
                               [{"severity": "must-fix", "title": "x", "declined": False}])
    assert status is RunStatus.WAITING
    assert run.open_decisions(run.items[0].key)
    assert remote_tip(pair.origin, "feat") == head


def test_a_clean_item_is_auto_published(tmp_path, monkeypatch):
    """The control: the same run with nothing open does reach the remote."""
    pair = remote_and_clone(tmp_path)
    run, status, head, _ = _drive(pair, monkeypatch, [])
    assert status is RunStatus.DONE
    assert run.items[0].status is ItemStatus.DONE
    assert remote_tip(pair.origin, "feat") == git_out(pair.work, "rev-parse", "HEAD").strip()
    assert remote_tip(pair.origin, "feat") != head


def test_a_skipped_step_that_committed_is_left_for_the_operator_to_publish(tmp_path, monkeypatch):
    """Skipping a failed step is not vouching for what it committed: no auto-publish."""
    pair = remote_and_clone(tmp_path)
    run, status, head, sched = _drive(pair, monkeypatch, [], code=1)
    assert status is RunStatus.WAITING
    failed = run.open_decisions(run.items[0].key)[0]
    assert failed.kind is DecisionKind.FAILED
    batch.store.save(run)
    batch.store.write_request(run.id, {"decision": failed.id, "action": "skip-step"})
    assert sched.run_until_blocked() is RunStatus.WAITING
    assert [d.kind for d in run.open_decisions(run.items[0].key)] == [DecisionKind.PUBLISH]
    assert remote_tip(pair.origin, "feat") == head
