"""Scheduler: steps never publish; the batch publishes, under a lease it keeps honest."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
from batch.model import STEP_ORDER, DecisionKind, ItemStatus, RunStatus, Step  # noqa: E402
from batch.publish import GIT_PUSH  # noqa: E402

from batch_scheduler_support import ALL, NEED, NO, Harness, _quiet_outcomes, row  # noqa: F401

ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}
REBASE_AND_REVIEW = {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NEED}


def test_no_step_pushes_or_posts_even_under_auto_publish():
    h = Harness([row(1)], auto_publish=STEP_ORDER)
    h.sched.run_until_blocked()
    flat = [arg for argv in h.spawned for arg in argv]
    assert "--push" not in flat and "--post" not in flat and "--finish" not in flat
    assert "--no-push" in h.spawned[0]


def test_a_step_that_moved_head_is_drafted_and_one_that_did_not_is_not():
    h = Harness([row(1, REBASE_AND_REVIEW)], moves={("review", "/wt/b1")})
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert item.step(Step.REVIEW).drafted is True
    assert item.step(Step.REBASE).drafted is False


def test_a_run_that_made_no_commits_needs_no_publish():
    h = Harness([row(1, ONLY_REVIEW)])
    assert h.sched.run_until_blocked() is RunStatus.DONE
    assert not any(d.kind is DecisionKind.PUBLISH for d in h.run.decisions)


def test_auto_publish_publishes_when_every_drafted_step_is_listed():
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW])
    assert h.sched.run_until_blocked() is RunStatus.DONE
    assert h.published == [[GIT_PUSH, "/wt/b1"]]
    assert h.run.items[0].status is ItemStatus.DONE


def test_an_auto_published_item_finishes_once():
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")},
                auto_publish=[Step.REVIEW])
    h.sched.run_until_blocked()
    assert [f["status"] for k, f in h.events if k == "item_finished"] == ["done"]


def test_auto_publish_waits_when_a_drafted_step_is_not_listed():
    h = Harness([row(1, REBASE_AND_REVIEW)],
                moves={("rebase", "/wt/b1"), ("review", "/wt/b1")}, auto_publish=[Step.REVIEW])
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    assert h.published == []
    assert [d.kind for d in h.run.open_decisions()] == [DecisionKind.PUBLISH]


def test_the_lease_follows_github_while_nothing_is_drafted():
    h = Harness([row(1, ONLY_REVIEW)], replan=lambda r: batch_row_with_head(r, "h2"))
    h.sched.run_until_blocked()
    assert h.run.items[0].remote_sha == "h2"


def test_the_lease_is_frozen_once_a_step_has_drafted():
    h = Harness([row(1, REBASE_AND_REVIEW)], moves={("rebase", "/wt/b1")},
                replan=lambda r: batch_row_with_head(r, "h2" if r.local_head != "h0" else "h"))
    h.sched.run_until_blocked()
    assert h.run.items[0].remote_sha == "h"


def test_a_head_the_local_branch_lacks_is_not_adopted():
    h = Harness([row(1, ONLY_REVIEW)], replan=lambda r: batch_row_with_head(r, "h2"),
                contains=lambda wt, sha: False)
    h.sched.run_until_blocked()
    assert h.run.items[0].remote_sha == "h"


def test_a_rebase_records_the_tip_it_started_from():
    h = Harness([row(1, {Step.REBASE: NEED})], moves={("rebase", "/wt/b1")},
                stdouts={("rebase", "/wt/b1"): '{"status": "completed", "pre_rebase_head": "p0"}'})
    h.sched.run_until_blocked()
    assert h.run.items[0].pre_rebase_head == "p0"


def batch_row_with_head(r, head):
    """The replanned row for *r*, with GitHub reporting *head*."""
    import dataclasses
    return dataclasses.replace(r, head_sha=head,
                               needs={Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NEED})
