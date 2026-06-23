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
