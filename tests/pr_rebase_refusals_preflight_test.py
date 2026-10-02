"""Tests for rebase.refusals: the landed and unrelated-history checks and refusals."""

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.client  # noqa: E402
import gh.landed  # noqa: E402
import git.client  # noqa: E402
import core.report  # noqa: E402
import rebase.types  # noqa: E402
import rebase.refusals  # noqa: E402
import pr.domains  # noqa: E402
import pr.state  # noqa: E402

from pr_rebase_support import (
    _TARGET,
    _OTHER_TARGET,
    _LANDED_BRANCH,
    _LANDED_PR,
    _completed,
    _landed_ctx,
)

_LANDED_URL = "https://x/pull/726"


def _gh_response(payload: str, returncode: int = 0):
    """Patch the transport so the gh call answers with *payload*.

    Stubbed under `gh.client` rather than at it, so the argv the client builds
    and the tier it picks are both still observable from the call.
    """
    return mock.patch(
        "core.proc.subprocess.run",
        return_value=_completed(["gh"], returncode=returncode, stdout=payload),
    )


def _run_tracker_check(merged=None, ctx=None, *, looked=True, remedy=""):
    """Run the tracker half of the preflight with gh's answer forced."""
    answer = gh.landed.TrackerAnswer(
        merged=merged, looked=looked, remedy=remedy,
    )
    with mock.patch.object(gh.landed, "merged_pr", return_value=answer):
        return rebase.refusals.tracker_landed_check("/fake", ctx or _landed_ctx())


def _run_git_check(*, ahead=3, empty_diff=False, upstream=False, ctx=None):
    """Run the git half of the preflight with each signal forced."""
    with mock.patch.object(git.client, "commits_ahead", return_value=ahead), \
         mock.patch.object(gh.landed, "diff_is_empty", return_value=empty_diff), \
         mock.patch.object(gh.landed, "all_commits_upstream", return_value=upstream):
        return rebase.refusals.git_landed_check(
            "/fake", ctx or _landed_ctx(), target_ref=_TARGET,
        )


def test_every_landed_signal_has_a_refusal_of_its_own():
    """`branch_landed` owns the names and this script re-exports them.

    A signal added to the lib and not mapped here would reach `_as_refusal` and
    build a report whose `signal` no `RefusalSignal` matches — the skill's table
    would document a value the script cannot emit under any name it knows.
    """
    refusals = {member.value for member in rebase.types.RefusalSignal}

    assert {signal.value for signal in gh.landed.LandedSignal} <= refusals


def test_tracker_check_reports_a_merged_pr():
    report = _run_tracker_check(
        gh.landed.MergedPR(number=_LANDED_PR, url=_LANDED_URL),
    )

    assert report.signal == rebase.types.RefusalSignal.PR_MERGED.value
    assert report.pr_number == _LANDED_PR
    assert report.detail == f"PR #{_LANDED_PR} is merged ({_LANDED_URL})"


def test_tracker_check_leaves_commits_ahead_unmeasured():
    """It runs before the checkout, so HEAD is another branch — null, not wrong."""
    report = _run_tracker_check(gh.landed.MergedPR(number=_LANDED_PR))

    assert report.commits_ahead is None


def test_tracker_check_omits_the_link_when_gh_reports_no_url():
    """The detail sentence is documented in SKILL.md — no empty parentheses."""
    report = _run_tracker_check(gh.landed.MergedPR(number=_LANDED_PR))

    assert report.detail == f"PR #{_LANDED_PR} is merged"


def test_tracker_check_never_reads_head():
    """The whole split rests on this: it must be safe before the checkout.

    Every HEAD-dependent signal is on the git side, so reaching for one here
    would reintroduce the ordering bug the split fixes.
    """
    answer = gh.landed.TrackerAnswer(
        merged=gh.landed.MergedPR(number=_LANDED_PR),
    )
    with mock.patch.object(gh.landed, "merged_pr", return_value=answer), \
         mock.patch.object(git.client, "commits_ahead") as ahead, \
         mock.patch.object(gh.landed, "diff_is_empty") as diff, \
         mock.patch.object(gh.landed, "all_commits_upstream") as cherry:
        rebase.refusals.tracker_landed_check("/fake", _landed_ctx())

    ahead.assert_not_called()
    diff.assert_not_called()
    cherry.assert_not_called()


def test_tracker_check_passes_when_github_has_no_answer():
    assert _run_tracker_check(None) is None


def test_tracker_check_refuses_a_snapshotless_read_the_breaker_declined():
    """The path `pr ci --fix` takes, which used to force-push through this.

    No snapshot is passed, so the refusal has to come from `by_tracker`'s own
    `looked`. Before this, a declined read was indistinguishable from "no
    merged PR" and the rebase replayed onto a branch that had already landed.
    """
    report = _run_tracker_check(None, looked=False, remedy="refills at 16:00")

    assert report is not None
    assert report.status == pr.domains.RebaseStatus.TRACKER_UNREAD.value
    assert report.signal == rebase.types.RefusalSignal.TRACKER_REFUSED.value
    assert "refills at 16:00" in report.detail


def test_git_check_never_asks_the_tracker():
    """The order is this script's, not the lib's ladder.

    `branch_landed.check` would fall through to gh here, and the round trip has
    already been spent before the checkout — where it is the only probe that can
    still answer for a branch `fetch --prune` just dropped.
    """
    with mock.patch.object(gh.landed, "merged_pr") as merged_pr:
        _run_git_check()

    merged_pr.assert_not_called()


def test_git_check_catches_a_squash_merge_by_empty_diff():
    """The squash-merge case: the commits are unreachable, the tree matches."""
    report = _run_git_check(empty_diff=True)

    assert report.signal == rebase.types.RefusalSignal.EMPTY_DIFF.value
    assert report.pr_number is None
    assert report.commits_ahead == 3


def test_git_check_catches_a_rebase_merge_by_patch_id():
    report = _run_git_check(upstream=True)

    assert report.signal == rebase.types.RefusalSignal.COMMITS_UPSTREAM.value
    assert report.commits_ahead == 3


def test_git_check_passes_an_unlanded_branch():
    assert _run_git_check() is None


def test_git_check_ignores_a_branch_with_no_commits_of_its_own():
    """Regression: both git signals read as landed for a freshly cut branch.

    An empty diff and an empty `git cherry` are vacuously true there, so
    without the guard every new worktree would be refused before its first
    rebase.
    """
    assert _run_git_check(ahead=0, empty_diff=True, upstream=True) is None


def _run_unrelated_check(*, merge_base_rc, rev_parse_rc=0):
    """Run the unrelated-history check with git's merge-base answer stubbed."""
    def fake_run(cmd, **kwargs):
        rc = 0
        if cmd[:2] == ["git", "merge-base"]:
            rc = merge_base_rc
        elif cmd[:2] == ["git", "rev-parse"]:
            rc = rev_parse_rc
        return subprocess.CompletedProcess(
            args=cmd, returncode=rc, stdout="", stderr="",
        )

    with mock.patch("subprocess.run", side_effect=fake_run):
        return rebase.refusals.unrelated_history_check(
            "/fake", _landed_ctx(), target_ref=_TARGET,
        )


def test_unrelated_check_refuses_a_branch_with_no_merge_base():
    """A branch cut from a different root would replay its whole history."""
    report = _run_unrelated_check(merge_base_rc=1)

    assert report.signal == rebase.types.RefusalSignal.NO_MERGE_BASE.value
    assert report.status == pr.domains.RebaseStatus.UNRELATED_HISTORY.value
    assert _TARGET in report.detail
    # No merge base means no meaningful "ahead of" count to report.
    assert report.commits_ahead is None


def test_unrelated_check_passes_a_connected_branch():
    assert _run_unrelated_check(merge_base_rc=0) is None


def test_unrelated_check_passes_a_ref_that_does_not_resolve():
    """A typo'd `--onto` fails merge-base too, and is not unrelated history.

    Refusing it would send the operator after a root they do not have; git's own
    error for the missing ref is the honest report.
    """
    assert _run_unrelated_check(merge_base_rc=1, rev_parse_rc=1) is None


def test_refuse_over_budget_aborts_before_refusing(capsys):
    """The refusal restores the branch — it does not leave a rebase in progress."""
    ctx = _landed_ctx()
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None):
        rc = rebase.refusals.refuse_over_budget("/fake", ctx, rebase.refusals.BudgetBreach(
            signal=rebase.types.RefusalSignal.CONFLICTS_OVER_BUDGET,
            detail="conflicts in 35 files, over the 20-file budget",
        ), target_ref=_TARGET)

    assert rc == 4
    assert ["git", "rebase", "--abort"] in commands
    payload = json.loads(capsys.readouterr().out)
    assert payload["signal"] == rebase.types.RefusalSignal.CONFLICTS_OVER_BUDGET.value
    assert payload["status"] == pr.domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value
    assert payload["override"] == "--force"
    assert "35" in payload["detail"]


def test_refuse_over_budget_names_the_count_that_ran_out(capsys):
    """Two budgets, one status — `signal` is what tells them apart."""
    with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None):
        rebase.refusals.refuse_over_budget("/fake", _landed_ctx(), rebase.refusals.BudgetBreach(
            signal=rebase.types.RefusalSignal.RESOLUTIONS_OVER_BUDGET,
            detail="63 conflict resolutions across 9 file(s)",
        ), target_ref=_TARGET)

    payload = json.loads(capsys.readouterr().out)
    assert payload["signal"] == "resolutions_over_budget"
    assert payload["status"] == pr.domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value


def test_refuse_renders_the_hint_for_every_refusal_status():
    """Every status a refusal can carry has an explanation to print.

    `_refuse` indexes the hint table by status, so a status added without a row
    raises rather than printing nothing — this pins that they stay in step.
    """
    statuses = {
        pr.domains.RebaseStatus.ALREADY_LANDED.value,
        pr.domains.RebaseStatus.UNRELATED_HISTORY.value,
        pr.domains.RebaseStatus.PARTIALLY_LANDED.value,
        pr.domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value,
        pr.domains.RebaseStatus.TRACKER_UNREAD.value,
    }
    assert set(rebase.refusals.REFUSAL_HINTS) == statuses


def test_refuse_landed_emits_the_exit_4_payload(capsys):
    ctx = _landed_ctx()
    report = rebase.types.RefusalReport(
        branch=_LANDED_BRANCH, signal="pr_merged",
        detail=f"PR #{_LANDED_PR} is merged", commits_ahead=18, pr_number=_LANDED_PR,
    )

    with mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None):
        rc = rebase.refusals.refuse(ctx, report, target_ref=_TARGET)

    captured = capsys.readouterr()
    assert rc == 4
    assert json.loads(captured.out) == {
        "branch": _LANDED_BRANCH, "signal": "pr_merged",
        "detail": f"PR #{_LANDED_PR} is merged", "commits_ahead": 18,
        "pr_number": _LANDED_PR, "status": "already_landed",
        "override": "--force", "remedy": "",
    }
    assert f"Refusing to rebase {_LANDED_BRANCH}" in captured.err
    assert "--force" in captured.err


def test_refuse_landed_keeps_every_documented_key_when_unmeasured(capsys):
    """SKILL.md documents the key set — the tracker path nulls, never drops."""
    report = rebase.types.RefusalReport(
        branch=_LANDED_BRANCH, signal="pr_merged",
        detail=f"PR #{_LANDED_PR} is merged", pr_number=_LANDED_PR,
    )

    with mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None):
        rebase.refusals.refuse(_landed_ctx(), report, target_ref=_TARGET)

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "branch", "signal", "detail", "commits_ahead",
        "pr_number", "status", "override", "remedy",
    }
    assert payload["commits_ahead"] is None
    # Empty rather than absent: a caller reads "there is no narrower way to do
    # this" from the value, not from the key being missing.
    assert payload["remedy"] == ""


def test_refuse_landed_records_the_status_for_the_dashboard():
    ctx = _landed_ctx()
    report = rebase.types.RefusalReport(
        branch=_LANDED_BRANCH, signal="empty_diff", detail="no diff", commits_ahead=2,
    )

    with mock.patch.object(core.report, "emit_json"):
        rebase.refusals.refuse(ctx, report, target_ref=_OTHER_TARGET)

    state = pr.state.load_state(ctx.target_dir)
    assert state.rebase.status == pr.domains.RebaseStatus.ALREADY_LANDED.value
    assert state.rebase.target_base == _OTHER_TARGET
