import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from otto_queue.model import EntryState  # noqa: E402
from batch.model import Item, Job, Step, StepRecord, StepStatus  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.ready import Snapshot, ready, submit_order  # noqa: E402
from batch.scheduler import new_run  # noqa: E402


def _item(*, stacked_on="", jobs=None, steps=None):
    recs = steps or [StepRecord(Step.REBASE, StepStatus.PENDING),
                     StepRecord(Step.CI, StepStatus.PENDING)]
    return Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b",
                head_sha="a", stacked_on=stacked_on, steps=recs, jobs=jobs or [])


CLEAN = Snapshot(dirty=False, rebasing=False, branch_ok=True, queue_states={})
WANTED = frozenset({Step.REBASE, Step.CI})


def test_rebase_ready_when_clean_and_wanted():
    assert ready(_item(), Step.REBASE, wanted=WANTED, snap=CLEAN, parent=None)


def test_dirty_or_rebasing_or_wrong_branch_is_not_ready():
    item = _item()
    assert not ready(item, Step.REBASE, wanted=WANTED,
                     snap=Snapshot(True, False, True, {}), parent=None)
    assert not ready(item, Step.REBASE, wanted=WANTED,
                     snap=Snapshot(False, True, True, {}), parent=None)
    assert not ready(item, Step.REBASE, wanted=WANTED,
                     snap=Snapshot(False, False, False, {}), parent=None)


def test_live_job_blocks_resubmit():
    jobs = [Job(Step.REBASE, 9, submitted_at="t")]
    snap = Snapshot(False, False, True, {9: EntryState.QUEUED})
    assert not ready(_item(jobs=jobs), Step.REBASE, wanted=WANTED, snap=snap, parent=None)


def test_ci_waits_for_wanted_rebase_success():
    item = _item()
    assert not ready(item, Step.CI, wanted=WANTED, snap=CLEAN, parent=None)
    jobs = [Job(Step.REBASE, 1, submitted_at="t")]
    snap = Snapshot(False, False, True, {1: EntryState.DONE})
    assert ready(_item(jobs=jobs), Step.CI, wanted=WANTED, snap=snap, parent=None)


def test_ci_does_not_wait_when_rebase_not_wanted():
    item = _item(steps=[StepRecord(Step.CI, StepStatus.PENDING)])
    assert ready(item, Step.CI, wanted=frozenset({Step.CI}), snap=CLEAN, parent=None)


def test_stacked_child_waits_for_parent_rebase_job_success():
    child = _item(stacked_on="o/r#2")
    parent = Item(key="o/r#2", repo="o/r", repo_dir="/r", pr=2, branch="p",
                  head_sha="b", steps=[StepRecord(Step.REBASE)],
                  jobs=[Job(Step.REBASE, 7, submitted_at="t")])
    waiting = Snapshot(False, False, True, {7: EntryState.RUNNING})
    assert not ready(child, Step.REBASE, wanted=WANTED, snap=waiting, parent=parent)
    done = Snapshot(False, False, True, {7: EntryState.DONE})
    assert ready(child, Step.REBASE, wanted=WANTED, snap=done, parent=parent)


def test_parent_not_in_run_does_not_block():
    child = _item(stacked_on="o/r#2")
    assert ready(child, Step.REBASE, wanted=WANTED, snap=CLEAN, parent=None)


def test_new_run_sets_stacked_on_and_child_is_ready_without_parent():
    # Parent not passed because it is not in this evaluation. When the parent
    # is in the run, pass it (see test_stacked_child_waits_for_parent_rebase_job_success).
    need = {Step.REBASE: StepNeed(True, "x")}
    rows = [
        PlanRow("o/r", "/r", 1, "t", "b1", "h1", False, dict(need)),
        PlanRow("o/r", "/r", 2, "t", "b2", "h2", False, dict(need), base_ref="b1"),
    ]
    run = new_run(rows, steps=[Step.REBASE], selected=None, pool=1)
    parent, child = run.items
    assert parent.stacked_on == ""
    assert child.stacked_on == parent.key
    assert ready(child, Step.REBASE, wanted=frozenset({Step.REBASE}),
                 snap=CLEAN, parent=None)


def test_child_rebase_ready_when_parent_skipped_rebase():
    parent = Item(key="o/r#2", repo="o/r", repo_dir="/r", pr=2, branch="p",
                  head_sha="b", steps=[StepRecord(Step.REBASE, StepStatus.SKIPPED)])
    child = _item(stacked_on="o/r#2")
    assert ready(child, Step.REBASE, wanted=WANTED, snap=CLEAN, parent=parent)


def test_failed_parent_rebase_blocks_child():
    parent = Item(key="o/r#2", repo="o/r", repo_dir="/r", pr=2, branch="p",
                  head_sha="b", steps=[StepRecord(Step.REBASE)],
                  jobs=[Job(Step.REBASE, 7, submitted_at="t")])
    snap = Snapshot(False, False, True, {7: EntryState.FAILED})
    child = _item(stacked_on="o/r#2")
    assert not ready(child, Step.REBASE, wanted=WANTED, snap=snap, parent=parent)


def test_succeeded_step_with_need_gone_is_not_ready():
    jobs = [Job(Step.REBASE, 1, submitted_at="t")]
    snap = Snapshot(False, False, True, {1: EntryState.DONE})
    assert not ready(_item(jobs=jobs), Step.REBASE, wanted=frozenset({Step.CI}),
                     snap=snap, parent=None)


def test_submit_order_fewest_conflicts_then_generated_then_unstacked():
    a = _item(); a.key, a.pr = "o/r#10", 10  # 5 handwritten
    b = _item(); b.key, b.pr = "o/r#11", 11  # 1 handwritten
    c = _item(); c.key, c.pr = "o/r#12", 12  # 2 generated-only
    d = _item(stacked_on="o/r#10"); d.key, d.pr = "o/r#13", 13  # 1 handwritten stacked
    files = {
        "o/r#10": ("a.py", "b.py", "c.py", "d.py", "e.py"),
        "o/r#11": ("z.py",),
        "o/r#12": ("x.gen", "y.gen"),
        "o/r#13": ("s.py",),
    }
    gen = lambda _item, p: p.endswith(".gen")
    ordered = submit_order(
        [(a, Step.REBASE), (b, Step.REBASE), (c, Step.REBASE), (d, Step.REBASE),
         (b, Step.CI)],
        conflict_files=files, generated=gen)
    assert [i.key for i, s in ordered if s is Step.REBASE] == [
        "o/r#12", "o/r#11", "o/r#10", "o/r#13"]
    assert ordered[-1] == (b, Step.CI)
