import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.gc  # noqa: E402
import batch.plan  # noqa: E402
import batch.store  # noqa: E402
import core.run_lock  # noqa: E402
import core.serde  # noqa: E402
import core.workbench_paths  # noqa: E402
import review.gc  # noqa: E402
from batch.model import (  # noqa: E402
    Decision,
    DecisionKind,
    Item,
    ItemStatus,
    Run,
    RunStatus,
    Step,
    StepRecord,
    StepStatus,
)
from batch_git_support import remote_and_clone  # noqa: E402
from conftest import git_out  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402

DAY = 86400
NOW = 2_000_000_000.0


def _item(status=ItemStatus.DONE, step_status=StepStatus.DONE, drafted=False):
    return Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                status=status, steps=[StepRecord(Step.REBASE, status=step_status,
                                                 drafted=drafted)])


def _save(run_id, status=RunStatus.DONE, item=None, decisions=(), age_days=10.0,
          namespace="refs/pr-batch/abcd", ref_dirs=("/r",)):
    run = Run(id=run_id, started_at="t", steps=[Step.REBASE], pool=1, auto_publish=[],
              status=status, items=[item or _item()], decisions=list(decisions),
              ref_namespace=namespace, ref_dirs=list(ref_dirs))
    core.serde.write_json(batch.store.run_dir(run.id) / batch.store.STATE_FILE,
                          core.serde.to_dict(run))
    stamp = NOW - age_days * DAY
    os.utime(batch.store.run_dir(run_id) / batch.store.STATE_FILE, (stamp, stamp))
    return run


@pytest.fixture
def dropped_refs():
    with mock.patch("batch.plan.drop_refs") as drop:
        yield drop


def _remaining():
    return batch.store.run_ids()


@pytest.mark.parametrize("status,removed", [
    (RunStatus.DONE, True),
    (RunStatus.CANCELLED, True),
    (RunStatus.RUNNING, False),
    (RunStatus.WAITING, False),
    (RunStatus.INTERRUPTED, False),
])
def test_only_finished_runs_are_removed(status, removed, dropped_refs):
    _save("r1", status=status, age_days=365)
    assert batch.gc.gc_runs(now=NOW) == int(removed)
    assert _remaining() == ([] if removed else ["r1"])


def test_a_clean_run_is_kept_until_the_stale_window(dropped_refs):
    _save("young", age_days=review.gc.GC_STALE_DAYS - 0.5)
    _save("old", age_days=review.gc.GC_STALE_DAYS + 0.5)
    assert batch.gc.gc_runs(now=NOW) == 1
    assert _remaining() == ["young"]


@pytest.mark.parametrize("item,decisions", [
    (_item(step_status=StepStatus.FAILED), []),
    (_item(status=ItemStatus.DROPPED), []),
    (_item(status=ItemStatus.READY_TO_PUBLISH, drafted=True), []),
    (_item(drafted=True), [Decision(id="d1", item="o/r#1", step="publish",
                                    kind=DecisionKind.PUBLISH, resolution="discard")]),
    (_item(), [Decision(id="d1", item="o/r#1", step="publish", kind=DecisionKind.FAILED)]),
], ids=["failed-step", "dropped", "drafted-unpublished", "drafted-discarded",
        "open-failed-decision"])
def test_a_run_that_kept_work_gets_the_failed_window(item, decisions, dropped_refs):
    _save("kept", item=item, decisions=decisions, age_days=review.gc.GC_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 0
    _save("kept", item=item, decisions=decisions,
          age_days=review.gc.GC_FAILED_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 1


def test_published_drafts_and_settled_failures_count_as_clean(dropped_refs):
    decisions = [
        Decision(id="d1", item="o/r#1", step="rebase", kind=DecisionKind.FAILED,
                 resolution="retry"),
        Decision(id="d2", item="o/r#1", step="publish", kind=DecisionKind.PUBLISH,
                 resolution="publish"),
    ]
    _save("clean", item=_item(drafted=True), decisions=decisions,
          age_days=review.gc.GC_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 1


def test_a_held_lock_keeps_the_run(dropped_refs):
    _save("held", age_days=365)
    record = {"pid": os.getppid(), "command": "pr batch run", "started": "t0",
              "released": None, "role": "driver"}
    lock = batch.store.run_dir("held") / core.run_lock.LOCK_FILE
    with held_elsewhere(lock, record):
        assert batch.gc.gc_runs(now=NOW) == 0
    assert _remaining() == ["held"]
    dropped_refs.assert_not_called()


def test_a_run_that_changes_before_the_lock_is_kept(dropped_refs, monkeypatch):
    _save("racy", age_days=365)
    real = core.run_lock.hold_resource

    def revive(lock, **kwargs):
        _save("racy", status=RunStatus.RUNNING, age_days=365)
        return real(lock, **kwargs)

    monkeypatch.setattr(core.run_lock, "hold_resource", revive)
    assert batch.gc.gc_runs(now=NOW) == 0
    assert _remaining() == ["racy"]
    dropped_refs.assert_not_called()


def test_refs_are_dropped_before_the_run_is_removed(dropped_refs):
    seen = []

    def record(dirs, ns):
        tomb = batch.store.batch_root() / "r1.reaping" / batch.store.STATE_FILE
        seen.append((dirs, ns, tomb.is_file()))
        return True

    dropped_refs.side_effect = record
    _save("r1", age_days=365)
    batch.gc.gc_runs(now=NOW)
    assert seen == [(["/r"], "refs/pr-batch/abcd", True)]
    assert _remaining() == []


def test_a_run_whose_refs_would_not_delete_is_kept(dropped_refs):
    dropped_refs.return_value = False
    _save("r1", age_days=365)
    assert batch.gc.gc_runs(now=NOW) == 0
    assert _remaining() == ["r1"]


def test_a_run_with_no_refs_to_drop_is_removed(tmp_path):
    """Nothing to drop is not a failure: a run with no namespace, or whose
    checkout is gone, is still collected. Uses the real `drop_refs`."""
    _save("no-ns", age_days=365, namespace="")
    _save("gone", age_days=365, ref_dirs=[str(tmp_path / "gone")])
    assert batch.gc.gc_runs(now=NOW) == 2
    assert _remaining() == []


def test_an_unreadable_state_file_is_kept(dropped_refs):
    _save("bad", age_days=365)
    (batch.store.run_dir("bad") / batch.store.STATE_FILE).write_text("{not json")
    assert batch.gc.gc_runs(now=NOW) == 0
    assert _remaining() == ["bad"]


def test_a_run_that_cannot_be_removed_is_reported_not_counted(dropped_refs, monkeypatch):
    _save("r1", age_days=365)

    def boom(*_a, **_k):
        raise OSError("busy")

    monkeypatch.setattr(batch.store.shutil, "rmtree", boom)
    assert batch.gc.gc_runs(now=NOW) == 0
    assert _remaining() == ["r1"]


def test_a_leftover_tombstone_is_warned_not_removed(dropped_refs, capsys):
    leftover = batch.store.batch_root() / "old.reaping"
    leftover.mkdir(parents=True)
    (leftover / batch.store.STATE_FILE).write_text("{}")
    assert batch.gc.gc_runs(now=NOW) == 0
    err = capsys.readouterr().err
    assert leftover.is_dir()
    assert "old.reaping" in err
    assert "leaving in place" in err


def test_no_batch_directory_is_nothing_to_clean():
    assert batch.gc.gc_runs(now=time.time()) == 0


def _register(repo_dir):
    path = core.workbench_paths.projects_registry()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{repo_dir}\n")


def _plant(tmp_path, ns, branches=("feat", "main")):
    pair = remote_and_clone(tmp_path)
    assert batch.plan.fetch_namespace(str(pair.work), ns, list(branches))
    return pair


def test_gc_drops_an_orphan_namespace_with_no_claim_or_run(tmp_path):
    ns = batch.plan.new_namespace()
    pair = _plant(tmp_path, ns)
    _register(pair.work)
    assert git_out(pair.work, "for-each-ref", ns) != ""
    assert batch.gc.gc_runs(now=NOW) == 1
    assert git_out(pair.work, "for-each-ref", ns) == ""


def test_gc_keeps_a_namespace_a_run_still_references(tmp_path):
    ns = batch.plan.new_namespace()
    pair = _plant(tmp_path, ns)
    _register(pair.work)
    _save("live", status=RunStatus.INTERRUPTED, age_days=365,
          namespace=ns, ref_dirs=(str(pair.work),))
    assert batch.gc.gc_runs(now=NOW) == 0
    assert git_out(pair.work, "for-each-ref", ns) != ""
    assert _remaining() == ["live"]


def test_gc_keeps_a_namespace_with_a_live_claim(tmp_path):
    ns = batch.plan.new_namespace()
    pair = _plant(tmp_path, ns)
    _register(pair.work)
    batch.plan.write_claim(ns, [str(pair.work)], pid=os.getpid())
    assert batch.gc.gc_runs(now=NOW) == 0
    assert git_out(pair.work, "for-each-ref", ns) != ""
    assert batch.plan.read_claim(ns) is not None


def test_gc_keeps_a_namespace_whose_plan_claims_it_during_the_scan(tmp_path):
    ns = batch.plan.new_namespace()
    pair = _plant(tmp_path, ns)
    _register(pair.work)
    scan = batch.gc._namespace_repos

    def scan_then_claim():
        found = scan()
        # A plan that starts mid-sweep: its claim lands after the repo scan.
        batch.plan.write_claim(ns, [str(pair.work)], pid=os.getpid())
        return found

    with mock.patch.object(batch.gc, "_namespace_repos", scan_then_claim):
        assert batch.gc.gc_namespaces() == 0
    assert git_out(pair.work, "for-each-ref", ns) != ""
