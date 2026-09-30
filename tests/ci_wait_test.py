"""Tests for `pr.ci_wait` — the poll loop behind `ci-check --wait`."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr import ci_annotations  # noqa: E402
from pr import ci_failures as ci  # noqa: E402
from gh import run_reads  # noqa: E402
from pr import ci_runs  # noqa: E402
from pr import ci_wait  # noqa: E402


def _row(run_id, **kw):
    """A `gh run list` row for a run whose payload the test supplies itself."""
    return run_reads.RunRow(run_id=run_id, **kw)


@pytest.fixture(autouse=True)
def _no_rollup():
    """No commit-check rollup unless a test asks for one.

    An unanswered rollup is what a commit GitHub reports no checks for, so
    every case below behaves as it did before the rollup existed — and none of
    them reaches the network to find that out.
    """
    with patch("gh.run_reads.fetch_commit_checks",
               return_value=run_reads.CommitChecks()):
        yield


def _no_log_fallback(kind):
    """A `log_fallback` result for a job whose logs yielded nothing."""
    return ci_annotations.LogFallback([], "", kind, structured=False)


def _run(status, conclusion, jobs):
    return {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": status, "conclusion": conclusion, "jobs": jobs,
    }


def _poll(**kwargs):
    """`poll_until_complete` with the arguments a test rarely varies filled in."""
    defaults = dict(
        run_id=None, timeout=120, interval=0, trail=MagicMock(),
    )
    defaults.update(kwargs)
    return ci_wait.poll_until_complete("owner/repo", "feat/test", **defaults)


def test_poll_emits_partial_on_new_failure(capsys):
    """When a job fails mid-run, that job is reported before the run finishes."""
    cycle1 = _run("in_progress", "failure", [
        {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])
    cycle2 = _run("completed", "failure", [
        {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])
    payloads = iter((cycle1, cycle2))

    with patch("gh.run_reads.fetch_latest_runs", side_effect=[[_row(100)], [_row(100)]]), \
         patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(payloads)), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback",
               return_value=_no_log_fallback(ci.FailureKind.BUILD)), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll()

    stdout = capsys.readouterr().out
    chunks = [c.strip() for c in stdout.split("---") if c.strip()]
    assert len(chunks) == 1
    assert json.loads(chunks[0])["type"] == "partial"
    assert result.run_ids == [100]
    assert result.merged["status"] == "completed"


def test_poll_reports_each_failed_job_once(capsys):
    """A job that failed on the first poll is not re-reported on the second."""
    failed = _run("in_progress", "failure", [
        {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])
    done = _run("completed", "failure", [
        {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])
    payloads = iter((failed, failed, done))

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]), \
         patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(payloads)), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback",
               return_value=_no_log_fallback(ci.FailureKind.BUILD)), \
         patch("pr.ci_wait.time.sleep"):
        _poll()

    chunks = [c for c in capsys.readouterr().out.split("---") if c.strip()]
    assert len(chunks) == 1


def test_poll_reads_new_failures_through_ci_runs_definition():
    """New-failure detection is `ci_runs.failed_jobs`'s definition, not its own.

    Stub `ci_runs.failed_jobs` to call a job "failed" that GitHub itself marked
    a success — standing in for a future change to its definition. `ci_wait`
    must report exactly that job. If it still hand-rolled its own check against
    `run_reads.FAILURE_CONCLUSIONS` instead of calling through, it would look at
    this job's real "success" conclusion and never report it at all.
    """
    weird_job = {"name": "Weird", "conclusion": "success", "databaseId": 20, "status": "completed"}
    run_data = _run("completed", "success", [weird_job])

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_runs.failed_jobs", return_value=[weird_job]), \
         patch("pr.ci_wait.emit_partial") as emit_partial, \
         patch("pr.ci_wait.time.sleep"):
        _poll()

    emit_partial.assert_called_once()
    assert emit_partial.call_args[0][2] == [weird_job]


def test_poll_emits_status_lines(capsys):
    """Status lines showing job counts appear on stderr."""
    run_data = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll()

    assert "2/2" in capsys.readouterr().err
    assert result.counts.completed == 2


def test_poll_returns_what_it_has_when_it_times_out(capsys):
    """A timeout still hands back the last poll — a partial run is worth reporting."""
    run_data = _run("in_progress", "", [
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(timeout=0)

    assert "timeout" in capsys.readouterr().err.lower()
    assert result.merged["status"] == "in_progress"
    assert result.counts.running == 1


def test_poll_raises_when_the_branch_has_no_runs():
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_runs", return_value=[]):
        with pytest.raises(ci_runs.RunUnavailable, match="feat/test"):
            _poll(trail=trail)
    trail.warn.assert_called_once()
    assert trail.warn.call_args[0][0] == "no_runs"


def test_poll_raises_when_no_run_data_comes_back():
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]), \
         patch("gh.run_reads.fetch_run_data", return_value=None):
        with pytest.raises(ci_runs.RunUnavailable, match="Failed to fetch"):
            _poll(trail=trail)
    trail.error.assert_called_once()
    assert trail.error.call_args[0][0] == "fetch_run_data"


def test_poll_re_resolves_run_ids_unless_one_is_pinned():
    """`--run` pins the id; without it a later push's run is picked up."""
    run_data = _run("completed", "success", [
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100)]) as fetch_ids, \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(run_id=555)

    fetch_ids.assert_not_called()
    assert result.run_ids == [555]


# ── what a poll does not ask twice ───────────────────────────────────────


def _two_run_polls():
    """Run 100 finishes in poll 1; run 200 is still going until poll 2."""
    finished = {"databaseId": 100, "number": 1, "headSha": "abc", "status": "completed",
                "conclusion": "success",
                "jobs": [{"name": "Lint", "conclusion": "success",
                          "databaseId": 10, "status": "completed"}]}
    running = {"databaseId": 200, "number": 2, "headSha": "abc", "status": "in_progress",
               "conclusion": None,
               "jobs": [{"name": "Test", "conclusion": None,
                         "databaseId": 20, "status": "in_progress"}]}
    done = {**running, "status": "completed", "conclusion": "success",
            "jobs": [{"name": "Test", "conclusion": "success",
                      "databaseId": 20, "status": "completed"}]}
    state = {200: iter((running, done))}
    return finished, state


def test_a_run_that_has_finished_is_not_fetched_again_next_poll():
    """Its payload cannot change, so re-reading it is a call per run per poll."""
    finished, state = _two_run_polls()

    def serve(repo, rid):
        return finished if rid == 100 else next(state[200])

    rows = [_row(100), _row(200)]
    with patch("gh.run_reads.fetch_latest_runs", return_value=rows), \
         patch("gh.run_reads.fetch_run_data", side_effect=serve) as view, \
         patch("pr.ci_wait.time.sleep"):
        result = _poll()

    asked = [c.args[1] for c in view.call_args_list]
    assert asked.count(100) == 1, "the finished run was re-read"
    assert asked.count(200) == 2, "the running run must be re-read"
    assert result.counts.total == 2


def test_the_held_payload_still_reaches_the_final_merge():
    """Skipping the re-read must not drop the run from what is reported."""
    finished, state = _two_run_polls()

    def serve(repo, rid):
        return finished if rid == 100 else next(state[200])

    with patch("gh.run_reads.fetch_latest_runs", return_value=[_row(100), _row(200)]), \
         patch("gh.run_reads.fetch_run_data", side_effect=serve), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll()

    assert sorted(j["name"] for j in result.merged["jobs"]) == ["Lint", "Test"]
    assert result.counts.completed == 2
