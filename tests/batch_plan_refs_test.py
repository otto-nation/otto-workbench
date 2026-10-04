"""Tests for the plan's private-namespace refs and the rebase need read from them."""

import sys
from pathlib import Path

from batch_git_support import advance, remote_and_clone
from conftest import git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
from batch.model import STEP_ORDER, RunStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402


def _node(branch="feat", base="main", merge_state="BLOCKED"):
    return {"number": 7, "title": "t", "isDraft": False, "headRefName": branch,
            "headRefOid": "remote1", "mergeStateStatus": merge_state, "baseRefName": base,
            "repository": {"nameWithOwner": "o/a"}, "reviewThreads": {"nodes": []}}


def _quiet(monkeypatch):
    monkeypatch.setattr(batch.plan, "settled_ids", lambda repo_dir, branch: set())
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))


def test_the_plan_fetch_leaves_remote_tracking_refs_untouched(tmp_path):
    pair = remote_and_clone(tmp_path)
    before = git_out(pair.work, "for-each-ref", "refs/remotes/origin")
    advance(pair.seed, "main", "upstream")
    ns = batch.plan.new_namespace()
    assert batch.plan.fetch_namespace(str(pair.work), ns)
    assert git_out(pair.work, "for-each-ref", "refs/remotes/origin") == before
    assert git_out(pair.work, "for-each-ref", "--format=%(refname)", ns).split() == [
        f"{ns}/feat", f"{ns}/main"]


def test_a_blocked_pr_that_is_behind_still_needs_a_rebase(tmp_path, monkeypatch):
    _quiet(monkeypatch)
    pair = remote_and_clone(tmp_path)
    advance(pair.seed, "main", "upstream")
    ns = batch.plan.new_namespace()
    batch.plan.fetch_namespace(str(pair.work), ns)
    row = batch.plan._row(_node(), str(pair.work), "o/a", "", ns)
    assert row.needs[Step.REBASE] == StepNeed(True, "1 behind main")


def test_unknown_merge_state_without_refs_falls_back_to_needed(monkeypatch):
    _quiet(monkeypatch)
    row = batch.plan._row(_node(merge_state="UNKNOWN"), "/repos/a", "o/a")
    assert row.needs[Step.REBASE].needed is True
    assert "the rebase step decides" in row.needs[Step.REBASE].reason


def test_a_repo_whose_fetch_fails_plans_from_merge_state(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")
    monkeypatch.setattr(batch.plan, "_local_heads", lambda d: {})
    monkeypatch.setattr(batch.plan, "fetch_namespace", lambda d, ns: False)
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {
        "viewer": {"login": "me"},
        "search": {"nodes": [_node(merge_state="BEHIND")]}})
    plan = batch.plan.build_plan(["/repos/a"])
    assert plan.ref_dirs == []
    [row] = plan.rows
    assert row.ref_namespace == ""
    assert row.needs[Step.REBASE] == StepNeed(True, "behind its base")


def test_a_stacked_pr_is_counted_against_its_base_branch(tmp_path, monkeypatch):
    _quiet(monkeypatch)
    pair = remote_and_clone(tmp_path)
    git_in(pair.seed, "checkout", "-q", "-b", "feat2")
    git_in(pair.seed, "push", "-q", "origin", "feat2")
    advance(pair.seed, "feat", "on-the-base")
    ns = batch.plan.new_namespace()
    batch.plan.fetch_namespace(str(pair.work), ns)
    row = batch.plan._row(_node(branch="feat2", base="feat"), str(pair.work), "o/a", "", ns)
    assert row.needs[Step.REBASE] == StepNeed(True, "1 behind feat")


def test_replan_reads_need_from_the_rows_namespace(monkeypatch):
    seen = {}
    _quiet(monkeypatch)
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {
        "repository": {"pullRequest": dict(_node(), state="OPEN")}})
    monkeypatch.setattr(batch.plan, "tree_rebase_need",
                        lambda d, b, base, ns, ms: seen.setdefault("ns", ns) and StepNeed(False, "x"))
    batch.plan.replan_row(PlanRow("o/a", "/r", 7, "t", "feat", "h", False, {},
                                  ref_namespace="refs/pr-batch/abcd"))
    assert seen["ns"] == "refs/pr-batch/abcd"


def test_drop_refs_removes_the_namespace_and_nothing_else(tmp_path):
    pair = remote_and_clone(tmp_path)
    ns = batch.plan.new_namespace()
    batch.plan.fetch_namespace(str(pair.work), ns)
    batch.plan.drop_refs([str(pair.work)], ns)
    assert git_out(pair.work, "for-each-ref", ns) == ""
    assert git_out(pair.work, "for-each-ref", "refs/remotes/origin") != ""


def test_new_run_marks_a_pr_stacked_on_another_in_the_run():
    need = {Step.REBASE: StepNeed(True, "x")}
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h1", False, dict(need)),
            PlanRow("o/r", "/r", 2, "t", "b2", "h2", False, dict(need), base_ref="b1")]
    run = batch.scheduler.new_run(rows, steps=list(STEP_ORDER), selected=None, pool=1,
                                  auto_publish=[])
    assert [i.stacked_on for i in run.items] == ["", "o/r#1"]


def test_a_finished_run_drops_its_refs(monkeypatch):
    dropped = []
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: dropped.append((dirs, ns)))
    run = batch.scheduler.new_run([], steps=list(STEP_ORDER), selected=None, pool=1,
                                  auto_publish=[], ref_namespace="refs/pr-batch/abcd",
                                  ref_dirs=["/r"])
    sched = batch.scheduler.Scheduler(
        run, pr_bin="pr", cfg=BatchConfig(pool_max=1),
        host=lambda: batch.admission.HostSample(None, None, None),
        spawn=lambda *a, **k: None, estimates=batch.admission.Estimates({}),
        emit=lambda *a, **k: None, sleep=lambda s: None)
    assert sched.run_until_blocked() is RunStatus.DONE
    assert dropped == [(["/r"], "refs/pr-batch/abcd")]
