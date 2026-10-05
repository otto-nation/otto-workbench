"""Scheduler rules about the tree: dirtiness, one fetcher per repo, stacks, CI waits."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.store  # noqa: E402
from batch.model import DecisionKind, RunStatus, Step, StepStatus  # noqa: E402

from batch_scheduler_support import ALL, NEED, NO, Harness, _quiet_outcomes, row  # noqa: F401

ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}
ONLY_REBASE = {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NO}


def _retry(h, decision):
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": decision.id, "action": "retry"})


def test_a_retried_step_over_a_dirty_tree_becomes_a_decision():
    h = Harness([row(1, ONLY_REVIEW)], codes={("review", "/wt/b1"): 1})
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    failed = h.run.open_decisions()[0]
    h.codes.clear()
    h.sched._dirty = lambda wt: True
    _retry(h, failed)
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    dirty = h.run.open_decisions()[0]
    assert dirty.kind is DecisionKind.DIRTY_WORKTREE
    assert dirty.payload["reason"] == "dirty" and "stash" in dirty.payload["stash"]
    assert [a[1] for a in h.spawned] == ["review"]


def test_a_paused_rebase_still_resumes_on_retry():
    h = Harness([row(1, ONLY_REBASE)], codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    conflict = h.run.open_decisions()[0]
    assert conflict.kind is DecisionKind.REBASE_CONFLICT
    h.codes.clear()
    h.sched._dirty = lambda wt: True
    h.sched._rebasing = lambda wt: True
    _retry(h, conflict)
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["rebase", "rebase"]


def test_only_a_rebase_may_start_while_one_is_paused():
    h = Harness([row(1, ONLY_REVIEW)], rebasing=lambda wt: True)
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    d = h.run.open_decisions()[0]
    assert d.kind is DecisionKind.DIRTY_WORKTREE and d.payload["reason"] == "rebase_in_progress"
    assert h.spawned == []


def test_two_rebases_in_one_repo_never_run_together():
    h = Harness([row(1, ONLY_REBASE), row(2, ONLY_REBASE)], pool=2)
    h.sched.run_until_blocked()
    assert h.max_live == 1


# passes-at-base: the control for the per-repo rule — two repos never contended
def test_rebases_in_different_repos_run_together():
    h = Harness([row(1, ONLY_REBASE), row(2, ONLY_REBASE, repo="o/s", repo_dir="/s")], pool=2)
    h.sched.run_until_blocked()
    assert h.max_live == 2


def test_a_stacked_pr_waits_for_its_base_and_resumes_when_it_finishes():
    base = row(1, ONLY_REVIEW)
    stacked = row(2, ONLY_REBASE, base_ref="b1")
    # The base's review moves HEAD, so it closes with a publish decision.
    h = Harness([base, stacked], pool=2, moves={("review", "/wt/b1")})
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    waiting = [d for d in h.run.open_decisions("o/r#2")]
    assert waiting[0].kind is DecisionKind.STEP_REVIEW
    assert waiting[0].payload["evidence"] == [{"kind": "stacked_on", "item": "o/r#1"}]
    assert not any(a[1] == "rebase" for a in h.spawned)
    publish = h.run.open_decisions("o/r#1")[0]
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": publish.id, "action": "discard"})
    h.sched.run_until_blocked()
    assert any(a[1] == "rebase" and a[-1] == "/wt/b2" for a in h.spawned)
