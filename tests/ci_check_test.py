"""Tests for ci-check script functions."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

_ci_check_path = str(BIN_DIR / "ci-check")
_loader = importlib.machinery.SourceFileLoader("ci_check", _ci_check_path)
_spec = importlib.util.spec_from_loader("ci_check", _loader, origin=_ci_check_path)
ci_check = importlib.util.module_from_spec(_spec)
ci_check.__file__ = _ci_check_path
_spec.loader.exec_module(ci_check)
sys.modules.setdefault("ci_check", ci_check)


# ── _fetch_latest_run_ids ─────────────────────────────────────────────────


def _mock_gh_run_list(runs):
    """Return a mock subprocess result with the given run list as JSON."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps(runs)
    return mock


def test_deduplicates_rerun_of_same_workflow():
    """A re-run of the same workflow should supersede the original."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_keeps_distinct_workflows():
    """Different workflows for the same commit should all be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200, 201]


def test_rerun_with_multiple_workflows():
    """Re-run of one workflow shouldn't affect other workflows."""
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [300, 201]


def test_filters_to_latest_sha():
    """Only runs for the latest SHA should be included."""
    runs = [
        {"databaseId": 300, "headSha": "def", "workflowName": "CI"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [300]


def test_empty_run_list():
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list([])):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_filters_skipped_runs():
    """Skipped workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Dependabot", "conclusion": "skipped"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_filters_cancelled_runs():
    """Cancelled workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Old CI", "conclusion": "cancelled"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_all_skipped_returns_empty():
    """When all runs at the latest SHA are skipped, return empty list."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "A", "conclusion": "skipped"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "B", "conclusion": "cancelled"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_in_progress_runs_kept():
    """Runs still in progress (conclusion=None) should be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": None},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy", "conclusion": "skipped"},
    ]
    with patch("ci_check.subprocess.run", return_value=_mock_gh_run_list(runs)):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


# ── _merge_runs ──────────────────────────────────────────────────────────


def test_merge_runs_skipped_does_not_poison_conclusion():
    """A skipped workflow should not override the overall conclusion to failure."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "skipped", "jobs": []},
    ]
    result = ci_check._merge_runs(runs)
    assert result["conclusion"] == "success"


def test_merge_runs_cancelled_does_not_poison_conclusion():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "cancelled", "jobs": []},
    ]
    result = ci_check._merge_runs(runs)
    assert result["conclusion"] == "success"


def test_merge_runs_real_failure_overrides():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "failure", "jobs": []},
    ]
    result = ci_check._merge_runs(runs)
    assert result["conclusion"] == "failure"


def test_merge_runs_empty_list():
    assert ci_check._merge_runs([]) is None


def test_merge_runs_collects_all_jobs():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": [{"name": "build"}]},
        {"_run_id": 2, "databaseId": 2, "conclusion": "success", "jobs": [{"name": "lint"}]},
    ]
    result = ci_check._merge_runs(runs)
    assert len(result["jobs"]) == 2
    assert result["jobs"][0]["name"] == "build"
    assert result["jobs"][1]["name"] == "lint"
