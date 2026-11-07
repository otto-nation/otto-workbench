"""Scheduler: steps never publish; the batch publishes, under a lease it keeps honest."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.store  # noqa: E402
from batch.model import STEP_ORDER, DecisionKind, ItemStatus, RunStatus, Step  # noqa: E402
# `_close_open_sessions` and `_quiet_outcomes` are autouse fixtures, imported only so
# pytest registers them (the `noqa: F401` covers them); the other names are used directly.
from batch_scheduler_support import (  # noqa: F401
    ALL, NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)

ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}
REBASE_AND_REVIEW = {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NEED}


def test_no_step_pushes_or_posts_even_under_auto_publish():
    h = Harness([row(1)])
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
    assert not any(f.reason.value == "ready_to_push" for f in h.run.items[0].findings)


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


def test_a_rebase_that_moved_nothing_records_no_start_tip():
    # A no-op rebase reports the tip it found, which may already be a hand rebase:
    # recording it would make publish check the remote against the rebased tip.
    h = Harness([row(1, {Step.REBASE: NEED})],
                stdouts={("rebase", "/wt/b1"): '{"status": "completed", "pre_rebase_head": "p0"}'})
    h.sched.run_until_blocked()
    assert h.run.items[0].pre_rebase_head == ""


def test_a_local_branch_the_remote_lacks_needs_a_publish_with_nothing_drafted():
    h = Harness([row(1, {Step.REBASE: NEED})],
                unpublished=lambda wt, sha: (wt, sha) == ("/wt/b1", "h"))
    assert h.sched.run_until_blocked() is RunStatus.DONE
    item = h.run.items[0]
    assert item.status is ItemStatus.DONE
    assert any(f.reason.value == "ready_to_push" for f in item.findings)
    assert h.published == []


def batch_row_with_head(r, head):
    """The replanned row for *r*, with GitHub reporting *head*."""
    import dataclasses
    return dataclasses.replace(r, head_sha=head,
                               needs={Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NEED})


def test_the_scheduler_hands_classify_the_head_before_the_step(monkeypatch):
    seen = []
    real = batch.outcomes.classify
    monkeypatch.setattr(batch.outcomes, "classify",
                        lambda *a, **k: seen.append(k.get("head_before")) or real(*a, **k))
    h = Harness([row(1, ONLY_REVIEW)], heads={"/wt/b1": "before1"})
    h.sched.run_until_blocked()
    assert seen == ["before1"]


def test_each_step_records_the_commits_it_made_but_the_rebase_does_not():
    """Publish checks these are still on the branch; a rebase only replays existing ones."""
    h = Harness([row(1, REBASE_AND_REVIEW)],
                moves={("rebase", "/wt/b1"), ("review", "/wt/b1")})
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert item.step(Step.REBASE).commits == []
    assert item.step(Step.REVIEW).commits == ["review-2"]


def test_a_commit_listing_git_refused_records_the_attempts_tip():
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")},
                commits=lambda wt, base: None)
    h.sched.run_until_blocked()
    assert h.run.items[0].step(Step.REVIEW).commits == ["review-1"]
    assert "commits_unrecorded" in h.kinds()


def test_a_remote_reset_behind_the_lease_is_not_adopted():
    """Regression: the local branch contained the reset-to commit, so the lease moved
    back and the next publish pushed the removed commits again."""
    h = Harness([row(1, ONLY_REVIEW)], replan=lambda r: batch_row_with_head(r, "h2"),
                descends=lambda wt, old, new: False)
    h.sched.run_until_blocked()
    assert h.run.items[0].remote_sha == "h"


def test_a_step_with_no_starting_head_records_its_tip_without_a_failure_event():
    """No base to list from is not git refusing the listing."""
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")}, heads={"/wt/b1": ""})
    h.sched.run_until_blocked()
    assert h.run.items[0].step(Step.REVIEW).commits == ["review-1"]
    assert "commits_unrecorded" not in h.kinds()
