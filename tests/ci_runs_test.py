"""Folding a commit's workflow runs into the one `RunState` everything downstream reads."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr import ci_annotations  # noqa: E402
from pr import ci_failures as ci  # noqa: E402
from pr import ci_runs  # noqa: E402


def _no_log_fallback(kind):
    """A `log_fallback` result for a job whose logs yielded nothing."""
    return ci_annotations.LogFallback([], "", kind, structured=False)


# ── merge_runs ───────────────────────────────────────────────────────────


def test_merge_runs_skipped_does_not_poison_conclusion():
    """A skipped workflow should not override the overall conclusion to failure."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "skipped", "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "success"


def test_merge_runs_cancelled_does_not_poison_conclusion():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "cancelled", "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "success"


def test_merge_runs_real_failure_overrides():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "failure", "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "failure"


def test_merge_runs_empty_list():
    assert ci_runs.merge_runs([]) is None


def test_merge_runs_collects_all_jobs():
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": [{"name": "build"}]},
        {"_run_id": 2, "databaseId": 2, "conclusion": "success", "jobs": [{"name": "lint"}]},
    ]
    result = ci_runs.merge_runs(runs)
    assert len(result["jobs"]) == 2
    assert result["jobs"][0]["name"] == "build"
    assert result["jobs"][1]["name"] == "lint"


def test_merge_runs_tags_source_run_id():
    """Each job should carry _source_run_id from its originating run."""
    runs = [
        {"_run_id": 100, "databaseId": 100, "conclusion": "success", "jobs": [{"name": "lint"}]},
        {"_run_id": 200, "databaseId": 200, "conclusion": "failure", "jobs": [{"name": "build"}]},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["jobs"][0]["_source_run_id"] == 100
    assert result["jobs"][1]["_source_run_id"] == 200


def test_merge_runs_in_progress_clears_success():
    """An in-progress run should prevent the merged result from reporting success."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "status": "completed", "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["status"] == "in_progress"
    assert result["conclusion"] == ""


def test_merge_runs_in_progress_preserves_failure():
    """A real failure should still surface even when another run is in-progress."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "status": "completed", "conclusion": "failure", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "failure"
    assert result["status"] == "in_progress"


# ── parse_run ────────────────────────────────────────────────────────────


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
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    assert len(result.failures) == 1
    assert "lint" in result.failures


def test_parse_run_skips_success_and_neutral_jobs():
    """Successful and neutral jobs should not appear as failures."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
        {"name": "Build", "conclusion": "success", "databaseId": 11},
        {"name": "Deploy", "conclusion": "neutral", "databaseId": 12},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    assert len(result.failures) == 1


def test_parse_run_includes_timed_out_jobs():
    """Timed-out jobs should be treated as failures."""
    run_data = _make_run_data([
        {"name": "Slow Test", "conclusion": "timed_out", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.TEST)):
            result = ci_runs.parse_run("owner/repo", run_data)
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
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 200


def test_parse_run_defaults_source_run_id_to_primary():
    """Without _source_run_id on the job, fall back to run's databaseId."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 100


def test_parse_run_includes_failed_step():
    """parse_run should extract failed_step from job steps data."""
    run_data = _make_run_data([
        {
            "name": "Generate & verify",
            "conclusion": "failure",
            "databaseId": 10,
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Generate & check drift", "conclusion": "failure"},
            ],
        },
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.failed_step == "Generate & check drift"


def test_parse_run_failed_step_none_without_steps():
    """Jobs without steps data should have failed_step=None."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.failed_step is None


# ── parse_run log enrichment for BUILD failures ──────────────────────────


def test_parse_run_enriches_uninformative_build_annotations():
    """BUILD failures with uninformative annotations should be enriched via log fallback."""
    uninformative_annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": "", "start_line": 0},
    ]
    log_context = "Run 'mise run generate' locally and commit\ndev-ci/configs/lib-imports.json: 7 lines to delete"
    log_annotations = [{"message": log_context, "path": "", "start_line": 0, "title": ""}]

    run_data = _make_run_data([
        {"name": "Generate & verify", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=uninformative_annotations):
        fallback = ci_annotations.LogFallback(log_annotations, log_context, ci.FailureKind.BUILD, structured=False)
        with patch("pr.ci_annotations.log_fallback", return_value=fallback) as mock_fallback:
            result = ci_runs.parse_run("owner/repo", run_data)
    mock_fallback.assert_called_once()
    group = list(result.failures.values())[0]
    assert "exit code 1" in group.items[0].annotation
    assert "mise run generate" in group.items[0].context


def test_parse_run_keeps_uninformative_annotations_when_log_fallback_empty():
    """If log fallback returns nothing, keep the original annotations with no context."""
    uninformative_annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": "", "start_line": 0},
    ]
    run_data = _make_run_data([
        {"name": "Generate & verify", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=uninformative_annotations):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert "exit code 1" in group.items[0].annotation
    assert group.items[0].context is None


def test_parse_run_enriches_uninformative_test_annotations():
    """TEST failures with uninformative annotations get log context."""
    uninformative_annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": "", "start_line": 0},
    ]
    log_context = "--- FAIL: TestInvoiceCreate (0.05s)\n    invoice_test.go:42: expected 200, got 500"
    log_annotations = [{"message": log_context, "path": "", "start_line": 0, "title": ""}]

    run_data = _make_run_data([
        {"name": "pytest unit", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=uninformative_annotations):
        fallback = ci_annotations.LogFallback(log_annotations, log_context, ci.FailureKind.TEST, structured=False)
        with patch("pr.ci_annotations.log_fallback", return_value=fallback) as mock_fallback:
            result = ci_runs.parse_run("owner/repo", run_data)
    mock_fallback.assert_called_once()
    group = list(result.failures.values())[0]
    assert "exit code 1" in group.items[0].annotation
    assert "FAIL: TestInvoiceCreate" in group.items[0].context


def test_parse_run_does_not_enrich_lint_with_uninformative_annotations():
    """LINT failures should not trigger log enrichment even with uninformative annotations."""
    uninformative_annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": "", "start_line": 0},
    ]
    run_data = _make_run_data([
        {"name": "shellcheck", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=uninformative_annotations):
        with patch("pr.ci_annotations.log_fallback") as mock_fallback:
            ci_runs.parse_run("owner/repo", run_data)
    mock_fallback.assert_not_called()


# ── count_job_states ─────────────────────────────────────────────────────


def test_count_job_states_all_completed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "completed", "conclusion": "failure"},
        {"name": "build", "status": "completed", "conclusion": "neutral"},
    ]}
    counts = ci_runs.count_job_states(merged)
    assert counts.completed == 3
    assert counts.failed == 1
    assert counts.running == 0
    assert counts.queued == 0
    assert counts.finished is True


def test_count_job_states_mixed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": None},
        {"name": "build", "status": "queued", "conclusion": None},
        {"name": "deploy", "status": "waiting", "conclusion": None},
    ]}
    counts = ci_runs.count_job_states(merged)
    assert counts.completed == 1
    assert counts.failed == 0
    assert counts.running == 1
    assert counts.queued == 2


def test_a_queued_job_is_counted_in_the_total_the_status_line_reports():
    """`x/y jobs complete` has to name every job, or the run looks shorter than it is."""
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": None},
        {"name": "build", "status": "queued", "conclusion": None},
    ]}
    counts = ci_runs.count_job_states(merged)
    assert counts.total == 3
    assert counts.finished is False


def test_count_job_states_empty():
    merged = {"jobs": []}
    counts = ci_runs.count_job_states(merged)
    assert counts.completed == 0
    assert counts.failed == 0
    assert counts.running == 0
    assert counts.queued == 0


def test_count_job_states_timed_out_is_failed():
    merged = {"jobs": [
        {"name": "slow", "status": "completed", "conclusion": "timed_out"},
    ]}
    counts = ci_runs.count_job_states(merged)
    assert counts.completed == 1
    assert counts.failed == 1


def test_count_job_states_pending_is_queued():
    merged = {"jobs": [
        {"name": "deploy", "status": "pending", "conclusion": None},
    ]}
    assert ci_runs.count_job_states(merged).queued == 1
