"""Tests for the plan's private-namespace refs and the rebase need read from them."""

import sys
from pathlib import Path

import pytest
from batch_git_support import advance, remote_and_clone
from conftest import git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import core.proc  # noqa: E402
import git.client  # noqa: E402
from batch.model import STEP_ORDER, RunStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402


def _node(branch="feat", base="main", merge_state="BLOCKED"):
    return {"number": 7, "title": "t", "isDraft": False, "headRefName": branch,
            "headRefOid": "remote1", "mergeStateStatus": merge_state, "baseRefName": base,
            "repository": {"nameWithOwner": "o/a"}, "reviewThreads": {"nodes": []}}


def _quiet(monkeypatch):
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
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
    assert _scheduler(_empty_run(["/r"])).run_until_blocked() is RunStatus.DONE
    assert dropped == [(["/r"], "refs/pr-batch/abcd")]


def _scheduler(run):
    return batch.scheduler.Scheduler(
        run, pr_bin="pr", cfg=BatchConfig(pool_max=1),
        host=lambda: batch.admission.HostSample(None, None, None),
        spawn=lambda *a, **k: None, estimates=batch.admission.Estimates({}),
        emit=lambda *a, **k: None, sleep=lambda s: None)


def _empty_run(ref_dirs):
    return batch.scheduler.new_run([], steps=list(STEP_ORDER), selected=None, pool=1,
                                   auto_publish=[], ref_namespace="refs/pr-batch/abcd",
                                   ref_dirs=ref_dirs)


def test_a_run_whose_checkout_is_gone_settles_and_is_saved_done(tmp_path):
    run = _empty_run([str(tmp_path / "removed-checkout")])
    assert _scheduler(run).run_until_blocked() is RunStatus.DONE
    assert batch.store.load(run.id).status is RunStatus.DONE


def test_a_run_is_saved_done_before_its_refs_are_dropped(monkeypatch):
    def fail(dirs, ns):
        raise OSError("cleanup failed")

    monkeypatch.setattr(batch.plan, "drop_refs", fail)
    run = _empty_run(["/r"])
    with pytest.raises(OSError):
        _scheduler(run).run_until_blocked()
    assert batch.store.load(run.id).status is RunStatus.DONE


def test_a_ref_that_will_not_delete_is_warned_about(tmp_path, monkeypatch, capsys):
    pair = remote_and_clone(tmp_path)
    ns = batch.plan.new_namespace()
    batch.plan.fetch_namespace(str(pair.work), ns)
    real = git.client.run

    def run(*args, **kw):
        if args[:1] == ("update-ref",):
            return core.proc.CmdResult(returncode=1, stderr="ref is locked\n")
        return real(*args, **kw)

    monkeypatch.setattr(git.client, "run", run)
    batch.plan.drop_refs([str(pair.work)], ns)
    err = capsys.readouterr().err
    assert f"could not delete {ns}/feat in {pair.work}: ref is locked" in err


def _plan_against(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")
    monkeypatch.setattr(batch.plan, "_local_heads", lambda d: {})
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {
        "viewer": {"login": "me"}, "search": {"nodes": [_node()]}})


@pytest.mark.parametrize("exc", [RuntimeError, KeyboardInterrupt])
def test_a_plan_that_fails_after_fetching_leaves_no_refs(tmp_path, monkeypatch, exc):
    pair = remote_and_clone(tmp_path)
    _plan_against(monkeypatch)

    def explode(*a, **k):
        raise exc("boom")

    monkeypatch.setattr(batch.plan, "rows_from_search", explode)
    with pytest.raises(exc):
        batch.plan.build_plan([str(pair.work)])
    assert git_out(pair.work, "for-each-ref", batch.plan.NAMESPACE_ROOT) == ""


def test_a_fetch_that_fails_part_way_leaves_no_refs_on_a_failed_plan(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    _plan_against(monkeypatch)
    real = batch.plan.fetch_namespace
    # The refs land, then the fetch reports failure — as one that dies mid-way can.
    monkeypatch.setattr(batch.plan, "fetch_namespace",
                        lambda d, ns: real(d, ns) and False)

    def fail(q, v):
        raise batch.plan.PlanError("GitHub query failed")

    monkeypatch.setattr(batch.plan, "_graphql", fail)
    with pytest.raises(batch.plan.PlanError):
        batch.plan.build_plan([str(pair.work)])
    assert git_out(pair.work, "for-each-ref", batch.plan.NAMESPACE_ROOT) == ""


def test_a_fork_branch_named_like_a_base_does_not_stack_that_pr():
    need = {Step.REBASE: StepNeed(True, "x")}
    rows = [PlanRow("o/r", "/r", 1, "t", "main", "h1", False, dict(need), is_fork=True),
            PlanRow("o/r", "/r", 2, "t", "b2", "h2", False, dict(need), base_ref="main")]
    run = batch.scheduler.new_run(rows, steps=list(STEP_ORDER), selected=None, pool=1,
                                  auto_publish=[])
    assert [i.stacked_on for i in run.items] == ["", ""]
