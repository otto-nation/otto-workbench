"""Tests for ci-check script functions."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from conftest import (
    CI_CHECK, assert_no_worktree_exit, load_script, make_ctx, write_thrash_log,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

ci_check = load_script("ci_check", CI_CHECK)

from agent import retry as agent_retry  # noqa: E402
from git import land  # noqa: E402
from core import publishing  # noqa: E402
from git.land import CommitStatus  # noqa: E402
from pr import ci_annotations  # noqa: E402
from pr import ci_failures as ci  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402


def _no_log_fallback(kind):
    """A `log_fallback` result for a job whose logs yielded nothing."""
    return ci_annotations.LogFallback([], "", kind, structured=False)


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
    assert ci_check._rebase_if_behind(trail, _report(), _mock_ctx()) is False
    trail.decision.assert_not_called()


def test_rebase_if_behind_runs_rebase_on_success():
    trail = MagicMock()
    report = _report(behind_main=5)
    mock_run = MagicMock()
    mock_run.returncode = 0
    with patch("ci_check.subprocess.run", return_value=mock_run) as mock_subrun:
        result = ci_check._rebase_if_behind(trail, report, _mock_ctx())
    assert result is True
    trail.info.assert_called()
    called_cmd = mock_subrun.call_args[0][0]
    assert "--fix" in called_cmd
    assert "--repo-dir" in called_cmd
    assert "--branch" in called_cmd


def test_rebase_if_behind_continues_on_failure():
    trail = MagicMock()
    report = _report(behind_main=10)
    mock_run = MagicMock()
    mock_run.returncode = 1
    mock_run.stderr = "conflict\n"
    with patch("ci_check.subprocess.run", return_value=mock_run):
        result = ci_check._rebase_if_behind(trail, report, _mock_ctx())
    assert result is False
    trail.warn.assert_called()


def test_rebase_if_behind_without_a_worktree_exits_with_guidance(capsys):
    """A rebase needs somewhere to run — "--repo-dir None" is not it."""
    ctx = make_ctx(branch="feat/auth", worktree_root=None, head_sha="abc1234")
    assert_no_worktree_exit(capsys, "feat/auth", ci_check._rebase_if_behind,
                            MagicMock(), _report(behind_main=3), ctx)


# ── _run_ci_wait ─────────────────────────────────────────────────────────


def test_run_ci_wait_emits_partial_on_new_failure(capsys):
    """When a job fails during polling, a partial JSON report is emitted."""
    # First poll: one job running, one failed
    run_data_cycle1 = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "in_progress", "conclusion": "failure",
        "jobs": [
            {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
        ],
    }
    # Second poll: all complete
    run_data_cycle2 = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "completed", "conclusion": "failure",
        "jobs": [
            {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
        ],
    }
    call_count = [0]
    def mock_fetch_data(repo, run_id):
        cycle = call_count[0]
        call_count[0] += 1
        if cycle == 0:
            return run_data_cycle1
        return run_data_cycle2

    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 120
    mock_args.wait_interval = 0  # no sleep in tests
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("gh.run_reads.fetch_latest_run_ids", side_effect=[[100], [100]]), \
         patch("gh.run_reads.fetch_run_data", side_effect=mock_fetch_data), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)), \
         patch("gh.run_reads.commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stdout = capsys.readouterr().out
    assert "---" in stdout
    chunks = [c.strip() for c in stdout.split("---") if c.strip()]
    assert len(chunks) >= 2
    partial = json.loads(chunks[0])
    assert partial["type"] == "partial"
    final = json.loads(chunks[-1])
    assert final["type"] == "final"


def test_run_ci_wait_emits_status_lines(capsys):
    """Status lines showing job counts appear on stderr."""
    run_data = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "completed", "conclusion": "success",
        "jobs": [
            {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
        ],
    }
    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 120
    mock_args.wait_interval = 0
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stderr = capsys.readouterr().err
    assert "2/2" in stderr


def test_run_ci_wait_times_out(capsys):
    """When timeout is reached, a final report is emitted with whatever we have."""
    run_data = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "in_progress", "conclusion": "",
        "jobs": [
            {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
        ],
    }
    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 0  # immediate timeout
    mock_args.wait_interval = 0
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stderr = capsys.readouterr().err
    assert "timeout" in stderr.lower()


# ── the fix pass, end to end ──────────────────────────────────────────────


_ONE_FAILURE = {
    "build": ci.FailureGroup(
        job="build", kind=ci.FailureKind.BUILD,
        items=(ci.FailureItem(
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

    Returns (the exit code, the invoke mock, the Trail mock).
    """
    artifacts = tmp_path / "ignore" / "ci-failures"
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
    with patch("ci_check._rebase_if_behind", return_value=False), \
         patch("ci_check.fix_engine.land.land",
               return_value=landed or land.LandResult(CommitStatus.NO_CHANGES)), \
         patch("ci_check.fix_engine.git_client.head_sha", return_value="cafe123"), \
         patch("ci_check.fix_engine.agent_invoke.ai_backend.invoke_fix",
               side_effect=invoke) as inv:
        rc = ci_check._run_fix(
            trail, report,
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    return rc, inv, trail


def test_ci_fix_pass_that_checks_nothing_off_is_retried_with_the_hint(tmp_path):
    """The hint is CI's own, not whichever one the diagnosis happens to name.

    `agent_retry.hint_for` is written for a phase producing a document out of
    nothing, and a fix pass is handed a checklist that already exists.
    """
    _, inv, _ = _drive_fix(tmp_path, tick=False)
    prompts = [c.args[0].prompt for c in inv.call_args_list]

    assert len(prompts) == 2
    assert prompts[1] == agent_retry.CI_FIX_RETRY_HINT + prompts[0]


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
    assert str(tmp_path / "ignore" / "ci-failures" / "fix-tracking.md") in text


def test_a_refused_commit_fails_the_fix_run(tmp_path):
    """The fixes are loose in the worktree; exiting zero reports work nobody has."""
    refused = land.LandResult(CommitStatus.COMMIT_FAILED, error="hook rejected it")
    rc, _, _ = _drive_fix(tmp_path, tick=True, landed=refused)
    assert rc == 1


def test_a_held_push_still_passes_the_fix_run(tmp_path):
    """Drafting the push is the default, not a failure — the commit is real."""
    held = land.LandResult(CommitStatus.PUSH_HELD, sha="abc1234",
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
    artifacts = tmp_path / "ignore" / "ci-failures"
    artifacts.mkdir(parents=True)
    with patch("ci_check._rebase_if_behind", return_value=False), \
         patch("ci_check.fix_engine.land.land",
               return_value=land.LandResult(CommitStatus.NO_CHANGES)) as mock_land, \
         patch("ci_check.fix_engine.git_client.head_sha", return_value="cafe123"), \
         patch("ci_check.fix_engine.agent_invoke.ai_backend.invoke_fix", return_value=0):
        ci_check._run_fix(
            trail, _report(failures=_ONE_FAILURE, run_number=1),
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    assert mock_land.call_args.kwargs["trail"] is trail


def test_the_fix_pass_commits_gated_and_asks_for_the_recovery(tmp_path):
    """A run without `--post` commits and drafts the push; regeneration is retried."""
    with patch("ci_check._rebase_if_behind", return_value=False), \
         patch("ci_check.fix_engine.land.land",
               return_value=land.LandResult(CommitStatus.NO_CHANGES)) as mock_land, \
         patch("ci_check.fix_engine.git_client.head_sha", return_value="cafe123"), \
         patch("ci_check.fix_engine.agent_invoke.ai_backend.invoke_fix", return_value=0):
        ci_check._run_fix(
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
        seen["enabled"] = publishing.enabled()
        raise SystemExit(0)

    with patch.object(sys, "argv", ["ci-check", *argv]), \
         patch.object(ci_check.pr_context, "resolve", side_effect=stop), \
         pytest.raises(SystemExit):
        ci_check.main()
    return seen["enabled"]


def test_a_fix_run_without_post_cannot_publish():
    assert _gate_at_first_work(["--fix"]) is False


def test_post_opens_the_gate_before_anything_runs():
    assert _gate_at_first_work(["--fix", "--post"]) is True
