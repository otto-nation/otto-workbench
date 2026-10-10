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
    assert h.published == [["pr", "push", "--expect", "h", "--repo-dir", "/wt/b1"]]
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


def test_a_retry_that_commits_nothing_keeps_what_the_failed_attempt_drafted():
    h = Harness([row(1, ONLY_REVIEW)], codes={("review", "/wt/b1"): 1},
                moves={("review", "/wt/b1")})
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    d = h.run.open_decisions()[0]
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.codes.clear()
    h.moves.clear()
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    assert [x[1] for x in h.spawned] == ["review", "review"]
    assert [d.kind for d in h.run.open_decisions()] == [DecisionKind.PUBLISH]


def test_a_retry_that_commits_nothing_still_sees_the_first_attempts_red_trailer(monkeypatch):
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")})
    # The first attempt's commit, `review-1`, carries `Fix-Checks: red`; it is in
    # the range only when the read starts from before the first attempt.
    monkeypatch.setattr(batch.outcomes, "fix_checks", lambda wt, hb: (
        [{"commit": "review-1", "status": "red"}]
        if hb == "h0" and h.heads.get(wt) == "review-1" else []))
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    d = h.run.open_decisions()[0]
    assert d.kind is DecisionKind.STEP_REVIEW
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.moves.clear()
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    assert [x[1] for x in h.spawned] == ["review", "review"]
    again = h.run.open_decisions()
    assert [x.kind for x in again] == [DecisionKind.STEP_REVIEW]
    assert again[0].payload["evidence"][0]["kind"] == "checks_unverified"


def test_a_retry_keeps_the_draft_in_state_written_before_start_head_existed():
    h = Harness([row(1, ONLY_REVIEW)], codes={("review", "/wt/b1"): 1},
                moves={("review", "/wt/b1")})
    h.sched.run_until_blocked()
    h.run.items[0].step(Step.REVIEW).start_head = ""
    d = h.run.open_decisions()[0]
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.codes.clear()
    h.moves.clear()
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    assert [d.kind for d in h.run.open_decisions()] == [DecisionKind.PUBLISH]


def test_a_retry_after_an_interrupt_keeps_what_the_interrupted_attempt_committed():
    h = Harness([row(1, ONLY_REVIEW)], moves={("review", "/wt/b1")})
    h.sched._sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    d = h.run.open_decisions()[0]
    assert d.kind is DecisionKind.INTERRUPTED
    h.run = batch.store.load(h.run.id)
    h.resume()
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.moves.clear()
    assert h.sched.run_until_blocked() is RunStatus.WAITING
    assert [x[1] for x in h.spawned] == ["review", "review"]
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


def test_the_scheduler_hands_classify_the_head_before_the_step(monkeypatch):
    seen = []
    real = batch.outcomes.classify
    monkeypatch.setattr(batch.outcomes, "classify",
                        lambda *a, **k: seen.append(k.get("head_before")) or real(*a, **k))
    h = Harness([row(1, ONLY_REVIEW)], heads={"/wt/b1": "before1"})
    h.sched.run_until_blocked()
    assert seen == ["before1"]
