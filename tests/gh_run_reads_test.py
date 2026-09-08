"""What `gh.run_reads` asks GitHub about a workflow run, and what it does with the answer."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult  # noqa: E402
from gh import run_reads  # noqa: E402


# ── fetch_latest_run_ids ──────────────────────────────────────────────────


def test_deduplicates_rerun_of_same_workflow():
    """A re-run of the same workflow should supersede the original."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_keeps_distinct_workflows():
    """Different workflows for the same commit should all be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [200, 201]


def test_rerun_with_multiple_workflows():
    """Re-run of one workflow shouldn't affect other workflows."""
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [300, 201]


def test_filters_to_latest_sha():
    """Only runs for the latest SHA should be included."""
    runs = [
        {"databaseId": 300, "headSha": "def", "workflowName": "CI"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [300]


def test_empty_run_list():
    with patch("gh.client.json_out", return_value=[]):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_filters_skipped_runs():
    """Skipped workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Dependabot", "conclusion": "skipped"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_filters_cancelled_runs():
    """Cancelled workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Old CI", "conclusion": "cancelled"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_all_skipped_returns_empty():
    """When all runs at the latest SHA are skipped, return empty list."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "A", "conclusion": "skipped"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "B", "conclusion": "cancelled"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_in_progress_runs_kept():
    """Runs still in progress (conclusion=None) should be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": None},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy", "conclusion": "skipped"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        result = run_reads.fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


# ── fetch_job_logs ────────────────────────────────────────────────────────


def test_job_logs_allow_escape_sequences():
    """gh refuses a coloured log outright, which reads here as a job with no logs."""
    with patch("gh.client.api", return_value=CmdResult(0, "logs")) as mock_api:
        run_reads.fetch_job_logs("owner/repo", 10)
    assert mock_api.call_args.kwargs["allow_escape_sequences"] is True


# ── download_artifact ─────────────────────────────────────────────────────


def test_download_artifact_yields_the_directory_gh_wrote_into():
    with patch("gh.client.ok", return_value=True) as mock_ok:
        with run_reads.download_artifact("owner/repo", 100, "test-results-go") as artifact_dir:
            assert artifact_dir is not None
            assert Path(artifact_dir).is_dir()
    assert "test-results-go" in mock_ok.call_args.args


def test_download_artifact_yields_none_when_there_is_no_such_artifact():
    with patch("gh.client.ok", return_value=False):
        with run_reads.download_artifact("owner/repo", 100, "test-results-go") as artifact_dir:
            assert artifact_dir is None


def test_download_artifact_removes_the_directory_on_the_way_out():
    """The caller reads inside the block — a path that outlived it would be a leak."""
    with patch("gh.client.ok", return_value=True):
        with run_reads.download_artifact("owner/repo", 100, "test-results-go") as artifact_dir:
            held = artifact_dir
    assert not Path(held).exists()


# ── commits_behind_main ───────────────────────────────────────────────────


def test_commits_behind_main_returns_count():
    with patch("gh.client.api", return_value=CmdResult(0, "15\n")):
        result = run_reads.commits_behind_main("owner/repo", "feat/auth")
    assert result == 15


def test_commits_behind_main_returns_zero_on_error():
    with patch("gh.client.api", return_value=CmdResult(1)):
        result = run_reads.commits_behind_main("owner/repo", "feat/auth")
    assert result == 0


def test_commits_behind_main_returns_zero_on_non_numeric():
    with patch("gh.client.api", return_value=CmdResult(0, "null\n")):
        result = run_reads.commits_behind_main("owner/repo", "feat/auth")
    assert result == 0
