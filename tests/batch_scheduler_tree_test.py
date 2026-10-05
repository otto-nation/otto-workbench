"""Scheduler rules about the tree: dirtiness, one fetcher per repo, stacks, CI waits."""

import dataclasses
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


RED = '---\n{"type": "final", "failures": {"build": {"job": "build"}}}\n'
GREEN = '---\n{"type": "final", "failures": {}}\n'
REVIEW_AND_CI = {Step.REBASE: NO, Step.CI: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}


def _ci_spawns(h):
    return [a for a in h.spawned if a[1] == "ci"]


def test_a_running_rollup_makes_the_ci_step_wait():
    pending = row(1, {Step.CI: NEED}, ci_state="PENDING")
    h = Harness([pending], replan=lambda r: pending)
    h.sched.run_until_blocked()
    assert "--wait" in _ci_spawns(h)[0]


def test_a_waiting_ci_step_does_not_hold_the_repos_fetch_slot():
    """A CI step polling GitHub for minutes must not hold back a rebase in its repo."""
    rows = {1: row(1, {Step.CI: NEED}, ci_state="PENDING"), 2: row(2, ONLY_REBASE)}
    h = Harness(list(rows.values()), pool=2, replan=lambda r: rows[r.pr])
    h.sched.run_until_blocked()
    assert h.max_live == 2
    assert "--wait" in _ci_spawns(h)[0]


def test_watch_ci_rechecks_once_and_reopens_on_red():
    h = Harness([row(1, REVIEW_AND_CI)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW], watch_ci=True, stdouts={("ci", "/wt/b1"): RED})
    real = h.sched._spawn

    def spawn(argv, **kw):
        if argv[1] == "ci" and "--fix" in argv:
            h.heads["/wt/b1"] = "ci-fixed"
        return real(argv, **kw)

    h.sched._spawn = spawn
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    watch, fix = _ci_spawns(h)
    assert "--fix" not in watch and "--wait" in watch
    assert "--fix" in fix
    publish = h.run.open_decisions()[0]
    assert publish.kind is DecisionKind.PUBLISH
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": publish.id, "action": "publish"})
    assert h.sched.run_until_blocked() is RunStatus.DONE
    assert len(_ci_spawns(h)) == 2
    assert h.run.items[0].ci_watched is True
    # The fix was pushed after the one re-check, so its CI was never read.
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished[-1]["ci_not_rechecked"] == ["o/r#1"]


def test_a_green_recheck_finishes_the_item():
    """The replan says CI is not needed — a just-pushed head reports no checks yet — so
    only the watch flag gets the re-check past `_confirm`."""
    h = Harness([row(1, REVIEW_AND_CI)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW], watch_ci=True, stdouts={("ci", "/wt/b1"): GREEN},
                replan=lambda r: dataclasses.replace(r, needs=dict(REVIEW_AND_CI)))
    assert h.sched.run_until_blocked() is RunStatus.DONE
    assert len(_ci_spawns(h)) == 1
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished[-1]["ci_not_rechecked"] == []


def test_a_watch_run_with_no_checks_yet_is_reported_not_rechecked():
    """`pr ci --wait` exits 1 with "No checks found" seconds after a push: no report, no
    failure decision, and the summary says CI was not re-checked."""
    h = Harness([row(1, REVIEW_AND_CI)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW], watch_ci=True,
                codes={("ci", "/wt/b1"): 1}, stdouts={("ci", "/wt/b1"): ""})
    assert h.sched.run_until_blocked() is RunStatus.DONE
    assert len(_ci_spawns(h)) == 1
    assert not [d for d in h.run.decisions if d.kind is DecisionKind.FAILED]
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished[-1]["ci_not_rechecked"] == ["o/r#1"]


def test_without_watch_ci_the_summary_names_what_was_not_rechecked():
    h = Harness([row(1, REVIEW_AND_CI)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW])
    h.sched.run_until_blocked()
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished[-1]["ci_not_rechecked"] == ["o/r#1"]
    assert _ci_spawns(h) == []
