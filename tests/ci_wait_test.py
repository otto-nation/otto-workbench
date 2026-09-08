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
from pr import ci_runs  # noqa: E402
from pr import ci_wait  # noqa: E402


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

    with patch("gh.run_reads.fetch_latest_run_ids", side_effect=[[100], [100]]), \
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

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
         patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(payloads)), \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback",
               return_value=_no_log_fallback(ci.FailureKind.BUILD)), \
         patch("pr.ci_wait.time.sleep"):
        _poll()

    chunks = [c for c in capsys.readouterr().out.split("---") if c.strip()]
    assert len(chunks) == 1


def test_poll_emits_status_lines(capsys):
    """Status lines showing job counts appear on stderr."""
    run_data = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
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

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(timeout=0)

    assert "timeout" in capsys.readouterr().err.lower()
    assert result.merged["status"] == "in_progress"
    assert result.counts.running == 1


def test_poll_raises_when_the_branch_has_no_runs():
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[]):
        with pytest.raises(ci_runs.RunUnavailable, match="feat/test"):
            _poll(trail=trail)
    trail.warn.assert_called_once()
    assert trail.warn.call_args[0][0] == "no_runs"


def test_poll_raises_when_no_run_data_comes_back():
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]), \
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

    with patch("gh.run_reads.fetch_latest_run_ids", return_value=[100]) as fetch_ids, \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(run_id=555)

    fetch_ids.assert_not_called()
    assert result.run_ids == [555]
