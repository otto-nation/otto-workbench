"""Tests for the plan's private-namespace refs and the rebase need read from them."""

import sys
from pathlib import Path

import pytest
from batch_git_support import advance, remote_and_clone
from conftest import commit_all, git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.model import STEP_ORDER, RunStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
# `_close_open_sessions` is an autouse fixture; imported only so pytest registers it.
from batch_scheduler_support import _close_open_sessions, attach_scheduler  # noqa: F401,E402
from config.workbench_config import BatchConfig  # noqa: E402


def _node(branch="feat", base="main", merge_state="BLOCKED"):
    return {"number": 7, "title": "t", "isDraft": False, "headRefName": branch,
            "headRefOid": "remote1", "mergeStateStatus": merge_state, "baseRefName": base,
            "repository": {"nameWithOwner": "o/a"}, "reviewThreads": {"nodes": []}}


def _quiet(monkeypatch):
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))


def _fetch(work, ns, branches=("feat", "main")):
    return batch.plan.fetch_namespace(str(work), ns, list(branches))


def test_the_plan_fetch_leaves_remote_tracking_refs_untouched(tmp_path):
    pair = remote_and_clone(tmp_path)
    before = git_out(pair.work, "for-each-ref", "refs/remotes/origin")
    advance(pair.seed, "main", "upstream")
    ns = batch.plan.new_namespace()
    assert _fetch(pair.work, ns)
    assert git_out(pair.work, "for-each-ref", "refs/remotes/origin") == before
    assert git_out(pair.work, "for-each-ref", "--format=%(refname)", ns).split() == [
        f"{ns}/feat", f"{ns}/main"]


def test_a_blocked_pr_that_is_behind_still_needs_a_rebase(tmp_path, monkeypatch):
    _quiet(monkeypatch)
    pair = remote_and_clone(tmp_path)
    advance(pair.seed, "main", "upstream")
    ns = batch.plan.new_namespace()
    _fetch(pair.work, ns)
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
    monkeypatch.setattr(batch.plan, "fetch_namespace", lambda d, ns, branches=(): False)
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
    _fetch(pair.work, ns, ("feat", "feat2", "main"))
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
    _fetch(pair.work, ns)
    assert batch.plan.drop_refs([str(pair.work)], ns) is True
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
    return attach_scheduler(
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
    # The DRIVER session is still open; finish() on teardown would drop again.
    monkeypatch.setattr(batch.plan, "drop_refs", lambda *a, **k: None)


def test_a_held_packed_refs_lock_fails_the_drop_once_and_keeps_the_refs(
        tmp_path, capsys):
    pair = remote_and_clone(tmp_path)
    ns = batch.plan.new_namespace()
    _fetch(pair.work, ns)
    lock = Path(git_out(pair.work, "rev-parse", "--git-path",
                        "packed-refs.lock").strip())
    if not lock.is_absolute():
        lock = pair.work / lock
    lock.write_text("")
    before = git_out(pair.work, "for-each-ref", "--format=%(refname)", ns)
    assert batch.plan.drop_refs([str(pair.work)], ns) is False
    err = capsys.readouterr().err
    assert err.count("could not delete") == 1
    assert str(lock) in err
    assert "confirm no git process holds it before removing the lock" in err
    assert git_out(pair.work, "for-each-ref", "--format=%(refname)", ns) == before


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

    def fetch_then_raise(d, ns, branches=()):
        real(d, ns, branches)
        raise RuntimeError("boom")

    monkeypatch.setattr(batch.plan, "fetch_namespace", fetch_then_raise)
    with pytest.raises(RuntimeError):
        batch.plan.build_plan([str(pair.work)])
    assert git_out(pair.work, "for-each-ref", batch.plan.NAMESPACE_ROOT) == ""


def test_a_fork_branch_named_like_a_base_does_not_stack_that_pr():
    need = {Step.REBASE: StepNeed(True, "x")}
    rows = [PlanRow("o/r", "/r", 1, "t", "main", "h1", False, dict(need), is_fork=True),
            PlanRow("o/r", "/r", 2, "t", "b2", "h2", False, dict(need), base_ref="main")]
    run = batch.scheduler.new_run(rows, steps=list(STEP_ORDER), selected=None, pool=1,
                                  auto_publish=[])
    assert [i.stacked_on for i in run.items] == ["", ""]


def test_a_repo_whose_refs_would_not_delete_does_not_skip_the_rest(monkeypatch):
    seen = []

    def drop(repo_dir, namespace):
        seen.append(repo_dir)
        return repo_dir != "/a"

    monkeypatch.setattr(batch.plan, "_drop_repo_refs", drop)
    assert batch.plan.drop_refs(["/b", "/a"], "refs/pr-batch/abcd") is False
    assert seen == ["/a", "/b"]


def test_build_plan_fetches_only_the_branches_rows_need(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    git_in(pair.seed, "checkout", "-q", "-b", "other")
    (pair.seed / "other.txt").write_text("other\n")
    commit_all(pair.seed, "other")
    git_in(pair.seed, "push", "-q", "origin", "other")
    _plan_against(monkeypatch)
    plan = batch.plan.build_plan([str(pair.work)])
    refs = git_out(pair.work, "for-each-ref", "--format=%(refname)", plan.ref_namespace)
    assert f"{plan.ref_namespace}/feat" in refs
    assert f"{plan.ref_namespace}/main" in refs
    assert f"{plan.ref_namespace}/other" not in refs
    batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)


def test_a_vanished_branch_does_not_lose_the_others(tmp_path):
    pair = remote_and_clone(tmp_path)
    git_in(pair.seed, "push", "-q", "origin", "--delete", "feat")
    ns = batch.plan.new_namespace()
    assert _fetch(pair.work, ns, ("feat", "main")) is True
    refs = git_out(pair.work, "for-each-ref", "--format=%(refname)", ns).split()
    assert f"{ns}/main" in refs
    assert f"{ns}/feat" not in refs


def test_claim_is_written_before_fetch_and_removed_after_drop(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    _plan_against(monkeypatch)
    seen = []
    real = batch.plan.fetch_namespace

    def fetch(d, ns, branches=()):
        seen.append(batch.plan.read_claim(ns) is not None)
        return real(d, ns, branches)

    monkeypatch.setattr(batch.plan, "fetch_namespace", fetch)
    plan = batch.plan.build_plan([str(pair.work)])
    assert seen == [True]
    assert batch.plan.read_claim(plan.ref_namespace) is not None
    assert batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace) is True
    assert batch.plan.read_claim(plan.ref_namespace) is None


def test_pid_exists_treats_unexpected_oserror_as_live(monkeypatch):
    def boom(pid, sig):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(batch.plan.os, "kill", boom)
    assert batch.plan._pid_exists(123) is True
