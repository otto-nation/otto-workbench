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


def test_merge_runs_tags_source_run_id():
    """Each job should carry _source_run_id from its originating run."""
    runs = [
        {"_run_id": 100, "databaseId": 100, "conclusion": "success", "jobs": [{"name": "lint"}]},
        {"_run_id": 200, "databaseId": 200, "conclusion": "failure", "jobs": [{"name": "build"}]},
    ]
    result = ci_check._merge_runs(runs)
    assert result["jobs"][0]["_source_run_id"] == 100
    assert result["jobs"][1]["_source_run_id"] == 200


# ── _build_ci_tracking_file ─────────────────────────────────────────────


def test_tracking_file_includes_fixable_failures(tmp_path):
    """Lint and test failures should appear in the tracking file."""
    tracking = tmp_path / "fix-tracking.md"
    failures = [
        {"id": "sc2086-bin-foo-42", "job": "shellcheck", "kind": "lint",
         "annotation": "SC2086: Double quote", "headline": "SC2086: Double quote",
         "file": "bin/foo.sh", "line": 42, "outcome": "new"},
        {"id": "pytest-test-auth-18", "job": "pytest", "kind": "test",
         "annotation": "AssertionError", "headline": "AssertionError",
         "file": "tests/auth.py", "line": 18, "outcome": "persisting"},
    ]
    count = ci_check._build_ci_tracking_file(tracking, failures, 7)

    assert count == 2
    content = tracking.read_text()
    assert "Run #7" in content
    assert "bin/foo.sh:42" in content
    assert "tests/auth.py:18" in content
    assert "SC2086: Double quote" in content
    assert content.count("- [ ] Apply fix") == 2
    assert "persisting" in content


def test_tracking_file_excludes_infra_and_flaky(tmp_path):
    """Infra and flaky failures should not appear in the tracking file."""
    tracking = tmp_path / "fix-tracking.md"
    failures = [
        {"id": "lint-1", "job": "shellcheck", "kind": "lint",
         "annotation": "SC2086", "headline": "SC2086",
         "file": "bin/foo.sh", "line": 42, "outcome": "new"},
        {"id": "infra-1", "job": "docker", "kind": "infra",
         "annotation": "connection refused", "headline": "connection refused",
         "file": None, "line": None, "outcome": "new"},
        {"id": "flaky-1", "job": "pytest", "kind": "flaky",
         "annotation": "timeout", "headline": "timeout",
         "file": "tests/slow.py", "line": 1, "outcome": "new"},
    ]
    count = ci_check._build_ci_tracking_file(tracking, failures, 8)

    assert count == 1
    content = tracking.read_text()
    assert "shellcheck" in content
    assert "docker" not in content
    assert "timeout" not in content


def test_tracking_file_returns_zero_when_all_skipped(tmp_path):
    """When all failures are infra/flaky, no file is written and count is 0."""
    tracking = tmp_path / "fix-tracking.md"
    failures = [
        {"id": "infra-1", "job": "docker", "kind": "infra",
         "annotation": "OOM", "headline": "OOM",
         "file": None, "line": None, "outcome": "new"},
    ]
    count = ci_check._build_ci_tracking_file(tracking, failures, 9)

    assert count == 0
    assert not tracking.exists()


def test_tracking_file_handles_missing_file_and_line(tmp_path):
    """Failures without file/line should show '—' as the reference."""
    tracking = tmp_path / "fix-tracking.md"
    failures = [
        {"id": "build-1", "job": "gradle", "kind": "build",
         "annotation": "compilation failed", "headline": "compilation failed",
         "file": None, "line": None, "outcome": "new"},
    ]
    count = ci_check._build_ci_tracking_file(tracking, failures, 10)

    assert count == 1
    content = tracking.read_text()
    assert "— — gradle" in content


# ── _parse_run ─────────────────────────────────────────────────────────


def _make_run_data(jobs):
    """Build minimal run data with given jobs."""
    return {
        "databaseId": 100,
        "number": 1,
        "headSha": "abc123",
        "status": "completed",
        "conclusion": "failure",
        "jobs": jobs,
    }


def test_parse_run_skips_null_conclusion_jobs():
    """Jobs with null conclusion (in-progress) should not be treated as failures."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
        {"name": "Build", "conclusion": None, "databaseId": 11},
        {"name": "Test", "conclusion": None, "databaseId": 12},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    assert len(result.failures) == 1
    assert "lint" in result.failures


def test_parse_run_skips_success_and_neutral_jobs():
    """Successful and neutral jobs should not appear as failures."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
        {"name": "Build", "conclusion": "success", "databaseId": 11},
        {"name": "Deploy", "conclusion": "neutral", "databaseId": 12},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    assert len(result.failures) == 1


def test_parse_run_includes_timed_out_jobs():
    """Timed-out jobs should be treated as failures."""
    run_data = _make_run_data([
        {"name": "Slow Test", "conclusion": "timed_out", "databaseId": 10},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.TEST)):
            result = ci_check._parse_run("owner/repo", run_data)
    assert len(result.failures) == 1


def test_parse_run_propagates_source_run_id():
    """Failure items should carry source_run_id from merged jobs."""
    run_data = {
        "databaseId": 100,
        "number": 1,
        "headSha": "abc123",
        "status": "completed",
        "conclusion": "failure",
        "jobs": [
            {"name": "Build", "conclusion": "failure", "databaseId": 10, "_source_run_id": 200},
        ],
    }
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 200


def test_parse_run_defaults_source_run_id_to_primary():
    """Without _source_run_id on the job, fall back to run's databaseId."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 100


# ── _count_checked / _count_unchecked ────────────────────────────────────


def test_count_checked_and_unchecked(tmp_path):
    tracking = tmp_path / "tracking.md"
    tracking.write_text("- [x] Done\n- [ ] Todo\n- [x] Also done\n- [ ] Also todo\n")
    assert ci_check._count_checked(tracking) == 2
    assert ci_check._count_unchecked(tracking) == 2


def test_count_on_missing_file(tmp_path):
    missing = tmp_path / "nope.md"
    assert ci_check._count_checked(missing) == 0
    assert ci_check._count_unchecked(missing) == 0
