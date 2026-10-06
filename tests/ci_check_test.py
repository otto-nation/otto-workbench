"""Tests for `cli.ci_check` — the flow behind the `ci-check` command."""

import json
import sys
import contextlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from conftest import assert_no_worktree_exit, make_ctx, write_thrash_log

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import agent.retry  # noqa: E402
import git.land  # noqa: E402
import core.publishing  # noqa: E402
from git.land import CommitStatus  # noqa: E402
import pr.ci_annotations  # noqa: E402
import pr.ci_check  # noqa: E402
import pr.ci_failures  # noqa: E402
import gh.run_reads  # noqa: E402
import pr.ci_runs  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402
import core.run_lock
import pr.context
import rebase.ci_fix
import rebase.commands
import rebase.target
import rebase.types


def _row(run_id, **kw):
    """A `gh run list` row for a run whose payload the test supplies itself."""
    return gh.run_reads.RunRow(run_id=run_id, **kw)


@pytest.fixture(autouse=True)
def _no_rollup():
    """No commit-check rollup unless a test asks for one.

    An unanswered rollup is what a commit GitHub reports no checks for, so
    every case below behaves as it did before the rollup existed — and none of
    them reaches the network to find that out.
    """
    with patch("gh.run_reads.fetch_commit_checks",
               return_value=gh.run_reads.CommitChecks()):
        yield


def _no_log_fallback(kind):
    """A `log_fallback` result for a job whose logs yielded nothing."""
    return pr.ci_annotations.LogFallback([], "", kind, structured=False)


def _report(**kwargs) -> CIReport:
    """A finished report, as `_run_ci` hands it to the fix phase."""
    defaults = dict(
        repo="owner/repo", branch="feat/test", pr_number=42,
        run_id=100, run_ids=[100], run_number=7, head_sha="abc123",
        conclusion="failure", behind_main=0, failures={},
        progression={}, resolved_since_prior=[],
    )
    defaults.update(kwargs)
    return CIReport(**defaults)


# ── _rebase_if_behind ───────────────────────────────────────────────────


def _mock_ctx(worktree_root="/tmp/wt", branch="feat/auth"):
    ctx = MagicMock()
    ctx.worktree_root = Path(worktree_root)
    ctx.require_worktree.return_value = Path(worktree_root)
    ctx.branch = branch
    return ctx


def test_rebase_if_behind_skips_when_not_behind():
    trail = MagicMock()
    assert rebase.ci_fix.rebase_if_behind(trail, _report(), _mock_ctx()) is False
    trail.decision.assert_not_called()


@contextlib.contextmanager
def _rebase_returning(rc, *, posting):
    """Stand in for the rebase, with the gate in a known state.

    `publishing` is a process-wide flag, so a test that opens it has to shut it
    again or every later test in the session runs as if `--post` were given.
    """
    state = MagicMock()
    state.rebase.force_pushed = posting
    with patch.object(rebase.target, "resolve_target_ref",
                      return_value="origin/main"), \
         patch.object(rebase.commands, "cmd_start", return_value=rc) as start, \
         patch.object(core.publishing, "enabled", return_value=posting), \
         patch.object(rebase.types, "load_or_init", return_value=state):
        yield start


def test_rebase_if_behind_rebases_in_process_on_success():
    """The rebase is a call, not a spawn — so it answers this run's gate."""
    trail = MagicMock()
    with _rebase_returning(0, posting=True) as start:
        result = rebase.ci_fix.rebase_if_behind(trail, _report(behind_main=5),
                                            _mock_ctx())

    assert result is True
    trail.info.assert_called()
    assert start.call_args[0][0] == "/tmp/wt"
    assert start.call_args[0][2] is rebase.types.RunMode.FIX
    assert start.call_args.kwargs["target_ref"] == "origin/main"
    assert start.call_args.kwargs["trail"] is trail


def test_a_draft_run_rebases_but_does_not_move_the_remote():
    """The behaviour this decomposition changes.

    The old spawn force-pushed whatever this run was told, because the gate is
    a process-wide flag and a child process never saw it. In-process, the
    rebase's own push drafts, and the caller must not then report a moved HEAD
    to a pass that would fix CI against it.
    """
    trail = MagicMock()
    with _rebase_returning(0, posting=False) as start:
        result = rebase.ci_fix.rebase_if_behind(trail, _report(behind_main=5),
                                            _mock_ctx())

    start.assert_called_once()
    assert result is False
    assert "drafted" in trail.info.call_args[0][1]


def test_rebase_if_behind_continues_on_failure():
    trail = MagicMock()
    with _rebase_returning(1, posting=True):
        result = rebase.ci_fix.rebase_if_behind(trail, _report(behind_main=10),
                                            _mock_ctx())
    assert result is False
    trail.warn.assert_called()


def test_a_refused_rebase_is_reported_apart_from_a_failed_one():
    """A preflight refusal is a decision, not a breakage.

    This path passes no snapshot, so `tracker_landed_check` reads the tracker
    itself — and that read can be declined by the budget breaker, which this
    run has already been spending GraphQL against. Telling the operator the
    rebase "failed" would send them looking for a broken rebase instead of a
    spent quota.
    """
    trail = MagicMock()
    with _rebase_returning(rebase.types.REFUSAL_EXIT, posting=True):
        result = rebase.ci_fix.rebase_if_behind(trail, _report(behind_main=5),
                                            _mock_ctx())

    assert result is False
    assert trail.warn.call_args[0][0] == "rebase_refused"


def test_a_paused_rebase_is_reported_apart_from_a_failed_one():
    """Exit 3 is a pause, not a breakage — the fix pass refuses it next.

    Lumping it into the generic failure branch tells the operator fixes will
    continue on the current base, immediately before the guard added at the
    top of `_run_fix` refuses to touch a paused rebase at all.
    """
    trail = MagicMock()
    with _rebase_returning(rebase.types.CONFLICTS_EXIT, posting=True):
        result = rebase.ci_fix.rebase_if_behind(trail, _report(behind_main=5),
                                            _mock_ctx())

    assert result is False
    assert trail.warn.call_args[0][0] == "rebase_paused"


def test_a_refused_rebase_does_not_report_a_moved_head():
    """The fix pass that follows runs on the un-rebased base.

    Returning True here would tell the caller CI is about to re-run on a new
    HEAD, when nothing was replayed and nothing was pushed.
    """
    trail = MagicMock()
    with _rebase_returning(rebase.types.REFUSAL_EXIT, posting=True):
        assert rebase.ci_fix.rebase_if_behind(
            trail, _report(behind_main=5), _mock_ctx(),
        ) is False

    assert not any(
        call[0][0] == "rebase_done" for call in trail.info.call_args_list
    )


def test_rebase_if_behind_without_a_worktree_exits_with_guidance(capsys):
    """A rebase needs somewhere to run — "--repo-dir None" is not it."""
    ctx = make_ctx(branch="feat/auth", worktree_root=None, head_sha="abc1234")
    assert_no_worktree_exit(capsys, "feat/auth", rebase.ci_fix.rebase_if_behind,
                            MagicMock(), _report(behind_main=3), ctx)


# ── _run_ci_wait ─────────────────────────────────────────────────────────
#
# The polling itself is `pr.ci_wait`'s, and `ci_wait_test.py` covers it. What is
# left here is the glue: turning a finished poll into the final report, and
# turning a poll that had nothing to watch into an exit.


def _wait_args(run=None):
    args = MagicMock()
    args.wait_timeout = 120
    args.wait_interval = 0
    args.run = run
    args.head_sha = ""
    return args


def test_run_ci_wait_emits_the_final_report(capsys):
    """The poll's last merged payload becomes the run's report on stdout."""
    run_data = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "completed", "conclusion": "failure",
        "jobs": [
            {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
        ],
    }

    with patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery(rows=(_row(100),))), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback",
               return_value=_no_log_fallback(pr.ci_failures.FailureKind.BUILD)), \
         patch("gh.run_reads.commits_behind_main", return_value=0), \
         patch("pr.ci_wait.time.sleep"):
        report = pr.ci_check.run_ci_wait(MagicMock(), _wait_args(), make_ctx())

    chunks = [c.strip() for c in capsys.readouterr().out.split("---") if c.strip()]
    final = json.loads(chunks[-1])
    assert final["type"] == "final"
    assert report.conclusion == "failure"


def test_run_ci_wait_leaves_nothing_to_poll_to_the_entry_point():
    """`RunUnavailable` travels to `main`, which owns the exit code."""
    with patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()):
        with pytest.raises(pr.ci_runs.RunUnavailable, match="No checks found"):
            pr.ci_check.run_ci_wait(MagicMock(), _wait_args(), make_ctx())


def test_run_ci_leaves_nothing_to_report_on_to_the_entry_point():
    """The single-shot path raises the same thing rather than exiting itself."""
    args = _wait_args()
    with patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()):
        with pytest.raises(pr.ci_runs.RunUnavailable, match="No checks found"):
            pr.ci_check.run_ci(MagicMock(), args, make_ctx())


def test_run_ci_asks_a_pinned_runs_rollup_at_that_runs_own_commit():
    """`--run <id>` pins a specific run; the rollup must be for that run's
    commit, not whatever the branch currently sits at.

    Regression test for the defect where `ctx.head_sha` (the branch's current
    head) was passed to `fetch_merged` even when `--run` pinned a historical
    run for a different commit — letting an external check answered at the
    *current* head get merged into a report about the pinned run, silently
    attributing another commit's verdict to it.

    Not answered by skipping the rollup for a pinned run: that leaves the
    defect this whole change exists to fix alive on the `--run` path, where a
    red external check would report green. The run names its own commit once
    fetched, so the question waits rather than going unasked.
    """
    run_data = {
        "databaseId": 555, "number": 3, "headSha": "runsha",
        "status": "completed", "conclusion": "success", "jobs": [],
    }
    by_sha = {
        "currenthead": gh.run_reads.CommitChecks(
            answered=True,
            external=({"name": "CodeQL", "databaseId": 0, "status": "completed",
                       "conclusion": "failure", "steps": [],
                       "_check_source": "check_run"},)),
        "runsha": gh.run_reads.CommitChecks(answered=True),
    }
    args = _wait_args(run=555)
    ctx = make_ctx(head_sha="currenthead")

    with patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: by_sha[sha]) as fetch_checks, \
         patch("gh.run_reads.commits_behind_main", return_value=0):
        report = pr.ci_check.run_ci(MagicMock(), args, ctx)

    assert [c.args[1] for c in fetch_checks.call_args_list] == ["runsha"]
    assert report.conclusion == "success"


def test_a_pinned_runs_own_external_failure_is_still_reported():
    """The point of asking at all — skipping the rollup here would report green."""
    run_data = {
        "databaseId": 555, "number": 3, "headSha": "runsha",
        "status": "completed", "conclusion": "success", "jobs": [],
    }
    checks = gh.run_reads.CommitChecks(
        answered=True,
        external=({"name": "CodeQL", "databaseId": 0, "status": "completed",
                   "conclusion": "failure", "steps": [],
                   "_check_source": "check_run", "_summary": "1 alert"},))
    args = _wait_args(run=555)

    with patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("gh.run_reads.commits_behind_main", return_value=0):
        report = pr.ci_check.run_ci(MagicMock(), args, make_ctx(head_sha="currenthead"))

    assert report.conclusion == "failure"


def test_main_reports_a_missing_run_and_exits_one(capsys):
    """The library raises; the entry point is what a shell sees a status from."""
    with patch.object(sys, "argv", ["ci-check"]), \
         patch.object(pr.context, "resolve", return_value=make_ctx()), \
         patch.object(core.run_lock, "claim_for_process"), \
         patch.object(cli.ci_check.Trail, "start", return_value=MagicMock()), \
         patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()):
        assert cli.ci_check.main([]) == 1

    assert "No checks found" in capsys.readouterr().err


def test_main_takes_no_checkout_lock_without_fix():
    """The dashboard path reads GitHub and writes nothing to the worktree.

    Locking it anyway can refuse, or be refused by, a concurrent `pr rebase`
    that legitimately holds it.
    """
    with patch.object(sys, "argv", ["ci-check"]), \
         patch.object(pr.context, "resolve", return_value=make_ctx()), \
         patch.object(core.run_lock, "claim_for_process") as claim, \
         patch.object(cli.ci_check.Trail, "start", return_value=MagicMock()), \
         patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()):
        cli.ci_check.main([])

    assert claim.call_args.kwargs["worktree"] is None


def test_main_takes_the_checkout_lock_with_fix():
    """--fix rebases and commits in this checkout, so the tree needs locking too."""
    ctx = make_ctx()
    with patch.object(sys, "argv", ["ci-check", "--fix"]), \
         patch.object(pr.context, "resolve", return_value=ctx), \
         patch.object(core.run_lock, "claim_for_process") as claim, \
         patch.object(cli.ci_check.Trail, "start", return_value=MagicMock()), \
         patch("gh.run_reads.fetch_latest_runs", return_value=gh.run_reads.RunDiscovery()):
        cli.ci_check.main(["--fix"])

    assert claim.call_args.kwargs["worktree"] == ctx.worktree_root


# ── the fix pass, end to end ──────────────────────────────────────────────


_ONE_FAILURE = {
    "build": pr.ci_failures.FailureGroup(
        job="build", kind=pr.ci_failures.FailureKind.BUILD,
        items=(pr.ci_failures.FailureItem(
            id="build-1", annotation="compilation failed", file="src/main.go",
            line=3, diagnosis=None, fix_sha=None, outcome=None,
            headline="compilation failed",
        ),),
    ),
}


def _drive_fix(tmp_path, *, tick, landed=None, exit_code=0):
    """Run `_run_fix` over one build failure, with the agent and the landing stubbed.

    The engine writes the checklist immediately before each invocation, so an
    agent that answers something has to answer it from inside the call — `tick`
    says whether it does.

    The verify gate is off: these tests count and read the fix agent's own
    calls, and the gate is a second agent on the same backend whose behaviour
    `fix_ci_gate_test` covers.

    Returns (the exit code, the invoke mock, the Trail mock).
    """
    artifacts = tmp_path / "ci-failures"
    artifacts.mkdir(parents=True)
    write_thrash_log(artifacts / "fix-session.jsonl")
    tracking = artifacts / "fix-tracking.md"

    def invoke(*args, **kwargs):
        if tick:
            tracking.write_text(
                tracking.read_text().replace("- [ ] fixed", "- [x] fixed", 1),
            )
        return exit_code

    trail = MagicMock()
    report = _report(failures=_ONE_FAILURE, run_number=1)
    with patch("rebase.ci_fix.rebase_if_behind", return_value=False), \
         patch("git.land.land",
               return_value=landed or git.land.LandResult(CommitStatus.NO_CHANGES)), \
         patch("git.client.head_sha", return_value="cafe123"), \
         patch("fix.scope.changed_files", return_value=set()), \
         patch("agent.backend.invoke_fix",
               side_effect=invoke) as inv:
        rc = rebase.ci_fix.run_fix(
            trail, report,
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
            verify=False,
        )
    return rc, inv, trail


def test_a_paused_rebase_stops_the_fix_pass(tmp_path):
    """A mid-rebase index is not something to turn an AI fixer loose on.

    `pr rebase` used to abort on its way out of every failure, so "the rebase
    failed, carry on with fixes" left a clean tree. It no longer does: a run
    that cannot resolve one file now stops with the replay and everything it
    already resolved intact. Carrying on from there would have the fix pass
    edit files still carrying conflict markers and commit them onto a detached
    HEAD.
    """
    trail = MagicMock()
    report = _report(failures=_ONE_FAILURE, run_number=1)
    with patch("rebase.ci_fix.rebase_if_behind", return_value=False), \
         patch("rebase.inspect.rebase_in_progress",
               return_value=True), \
         patch("fix.engine.run") as run:
        rc = rebase.ci_fix.run_fix(
            trail, report,
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )

    assert rc == 1
    run.assert_not_called()
    assert trail.error.call_args[0][0] == "rebase_paused"


def test_ci_fix_pass_that_checks_nothing_off_is_retried_with_the_hint(tmp_path):
    """The hint is CI's own, not whichever one the diagnosis happens to name.

    `agent.retry.hint_for` is written for a phase producing a document out of
    nothing, and a fix pass is handed a checklist that already exists.
    """
    _, inv, _ = _drive_fix(tmp_path, tick=False)
    prompts = [c.args[0].prompt for c in inv.call_args_list]

    assert len(prompts) == 2
    assert prompts[1] == agent.retry.CI_FIX_RETRY_HINT + prompts[0]


def test_ci_fix_pass_with_a_checked_box_is_not_retried(tmp_path):
    """One ticked box is work, and the thrash guard stays out of a working pass."""
    _, inv, _ = _drive_fix(tmp_path, tick=True)
    assert inv.call_count == 1


def test_the_fix_prompt_names_the_failure_and_the_tracking_file(tmp_path):
    """What the engine substitutes has to survive the template CI actually ships."""
    _, inv, _ = _drive_fix(tmp_path, tick=True)
    text = inv.call_args.args[0].prompt
    assert "src/main.go:3" in text
    assert "compilation failed" in text
    assert str(tmp_path / "ci-failures" / "fix-tracking.md") in text


def test_a_refused_commit_fails_the_fix_run(tmp_path):
    """The fixes are loose in the worktree; exiting zero reports work nobody has."""
    refused = git.land.LandResult(CommitStatus.COMMIT_FAILED, error="hook rejected it")
    rc, _, _ = _drive_fix(tmp_path, tick=True, landed=refused)
    assert rc == 1


def test_a_held_push_still_passes_the_fix_run(tmp_path):
    """Drafting the push is the default, not a failure — the commit is real."""
    held = git.land.LandResult(CommitStatus.PUSH_HELD, sha="abc1234",
                           resume="git -C '/fake' push")
    rc, _, trail = _drive_fix(tmp_path, tick=True, landed=held)
    assert rc == 0
    assert trail.info.call_args.kwargs["data"]["resume"] == "git -C '/fake' push"


def test_a_backend_that_exits_non_zero_fails_the_fix_run(tmp_path):
    """An agent that ticked boxes and still crashed did not finish cleanly."""
    rc, _, _ = _drive_fix(tmp_path, tick=True, exit_code=2)
    assert rc == 1


def test_the_fix_pass_gives_the_land_owner_its_trail(tmp_path):
    """`land` reports a refused commit to the trail — with none, nothing records it."""
    trail = MagicMock()
    artifacts = tmp_path / "ci-failures"
    artifacts.mkdir(parents=True)
    with patch("rebase.ci_fix.rebase_if_behind", return_value=False), \
         patch("git.land.land",
               return_value=git.land.LandResult(CommitStatus.NO_CHANGES)) as mock_land, \
         patch("git.client.head_sha", return_value="cafe123"), \
         patch("fix.scope.changed_files", return_value=set()), \
         patch("agent.backend.invoke_fix", return_value=0):
        rebase.ci_fix.run_fix(
            trail, _report(failures=_ONE_FAILURE, run_number=1),
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    assert mock_land.call_args.kwargs["trail"] is trail


def test_the_fix_pass_commits_gated_and_asks_for_the_recovery(tmp_path):
    """A run without `--post` commits and drafts the push; regeneration is retried."""
    with patch("rebase.ci_fix.rebase_if_behind", return_value=False), \
         patch("git.land.land",
               return_value=git.land.LandResult(CommitStatus.NO_CHANGES)) as mock_land, \
         patch("git.client.head_sha", return_value="cafe123"), \
         patch("fix.scope.changed_files", return_value=set()), \
         patch("agent.backend.invoke_fix", return_value=0):
        rebase.ci_fix.run_fix(
            MagicMock(), _report(failures=_ONE_FAILURE, run_number=1),
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    kwargs = mock_land.call_args.kwargs
    assert kwargs["gated"] is True
    assert kwargs["regen"] == "chore: regenerate after CI fixes"
    assert kwargs["message"] == "fix: address CI failures"


# ── --post opens the gate ─────────────────────────────────────────────────


def _gate_at_first_work(argv):
    """Whether the run could publish by the time it started doing anything.

    Target resolution is the first thing after the parse, so a run that opted in
    must already be able to publish there — a gate opened later is a gate some
    code path can push ahead of.
    """
    seen = {}

    def stop(*args, **kwargs):
        seen["enabled"] = core.publishing.enabled()
        raise SystemExit(0)

    with patch.object(sys, "argv", ["ci-check", *argv]), \
         patch.object(pr.context, "resolve", side_effect=stop), \
         pytest.raises(SystemExit):
        cli.ci_check.main()
    return seen["enabled"]


def test_a_fix_run_without_post_cannot_publish():
    assert _gate_at_first_work(["--fix"]) is False


def test_post_opens_the_gate_before_anything_runs():
    assert _gate_at_first_work(["--fix", "--post"]) is True
