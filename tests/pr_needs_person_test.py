"""`pr.needs_person`: what on one PR needs a person, read from saved state.

Revert: make owed_threads read (NEEDS_HUMAN, DEFERRED) instead of
pr.settlement.UNSETTLED_OUTCOMES; the declined test fails.
"""

import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.ci_failures  # noqa: E402
import pr.comments_fix  # noqa: E402
import pr.needs_person  # noqa: E402
import pr.state  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402


def _state(items=(), runs=None, latest=None):
    state = pr.state.PRState(identity=pr.state.PRIdentity("o/r", "feat/x", 7, "h", ""))
    state.fix.fix.items = list(items)
    state.ci.runs = dict(runs or {})
    state.ci.latest_run_id = latest
    return state


def _run(run_id, sha, jobs):
    groups = {j: pr.ci_failures.FailureGroup(j, pr.ci_failures.FailureKind.TEST, ()) for j in jobs}
    return pr.ci_failures.RunState(run_id, 1, sha, "completed", "failure", "", groups)


def test_the_reason_and_step_strings_and_their_pairing():
    assert (pr.needs_person.THREADS_NEED_HUMAN, pr.needs_person.CI_FAILING,
            pr.needs_person.CLOSEOUT_OWED) == ("threads_need_human", "ci_failing", "closeout_owed")
    assert pr.needs_person.FINDING_STEPS == {
        "threads_need_human": "comments", "ci_failing": "ci", "closeout_owed": "comments"}
    assert pr.needs_person.NEXT_REASONS == frozenset(pr.needs_person.FINDING_STEPS)


def test_pr_key_and_repo_args():
    assert pr.needs_person.pr_key("o/r", 7) == "o/r#7"
    assert pr.needs_person.repo_args("/wt") == ("--repo-dir", "/wt")
    assert pr.needs_person.repo_args("/r", "feat/x") == ("--repo-dir", "/r", "--branch", "feat/x")


def test_next_command_for_each_reason():
    repo = ("--repo-dir", "/wt")
    ci = pr.needs_person.next_command("ci_failing", repo, ["lint", "test"])
    assert ci == pr.needs_person.NextCommand(
        ("pr", "ci", "--fix", "--repo-dir", "/wt"), "fix failing CI (lint, test)", "ci")
    assert pr.needs_person.next_command("ci_failing", repo).why == "fix failing CI"
    assert pr.needs_person.next_command("threads_need_human", repo) == pr.needs_person.NextCommand(
        ("pr", "comments", "--fix", "--repo-dir", "/wt"), "address review comments", "comments")
    closeout = pr.needs_person.next_command("closeout_owed", repo)
    assert closeout.argv == (*shlex.split(pr.comments_fix.CLOSEOUT_COMMAND), "--repo-dir", "/wt")
    assert (closeout.why, closeout.action) == ("closeout owed per pr state", "closeout")
    with pytest.raises(KeyError):
        pr.needs_person.next_command("review_findings", repo)


def test_owed_threads_are_the_unsettled_outcomes_declined_included():
    items = [ItemOutcome(id="PRRT_1", outcome=FixOutcome.NEEDS_HUMAN, summary="s", file="a.py", line=2),
             ItemOutcome(id="PRRT_2", outcome=FixOutcome.DECLINED, reason="wrong premise"),
             ItemOutcome(id="ic-5-0", outcome=FixOutcome.DEFERRED)]
    settled = [ItemOutcome(id=f"PRRT_{o.value}", outcome=o) for o in (
        FixOutcome.FIXED, FixOutcome.DISMISSED, FixOutcome.ALREADY_ADDRESSED,
        FixOutcome.SKIPPED, FixOutcome.SETTLED_ELSEWHERE)]
    owed = pr.needs_person.owed_threads(_state(items + settled))
    assert owed == [
        {"id": "PRRT_1", "outcome": "needs_human", "summary": "s", "reason": "",
         "file": "a.py", "line": 2, "replyable": True},
        {"id": "PRRT_2", "outcome": "declined", "summary": "", "reason": "wrong premise",
         "file": items[1].file, "line": items[1].line, "replyable": True},
        {"id": "ic-5-0", "outcome": "deferred", "summary": "", "reason": "",
         "file": items[2].file, "line": items[2].line, "replyable": False},
    ]


def test_failing_ci_reads_the_latest_stored_run():
    assert pr.needs_person.failing_ci(_state().ci) is None
    green = _state(runs={4: _run(4, "a" * 40, [])}, latest=4)
    assert pr.needs_person.failing_ci(green.ci) is None
    red = _state(runs={4: _run(4, "a" * 40, ["old"]), 5: _run(5, "b" * 40, ["test", "lint"])},
                 latest=5)
    assert pr.needs_person.failing_ci(red.ci) == pr.needs_person.FailingCI(("lint", "test"), "b" * 40)
    assert red.ci.latest_run() is red.ci.runs[5]
    assert red.ci.verdict_sha() == "b" * 40
