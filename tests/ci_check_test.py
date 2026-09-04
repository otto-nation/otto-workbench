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

import fix_engine  # noqa: E402
import land  # noqa: E402
import publishing  # noqa: E402
from land import CommitStatus  # noqa: E402
from pr_fix import FixOutcome, ItemOutcome  # noqa: E402
from pr_state import PRIdentity, PRState  # noqa: E402
from proc import CmdResult  # noqa: E402


def _no_log_fallback(kind):
    """A `_log_fallback` result for a job whose logs yielded nothing."""
    return ci_check._LogFallback([], "", kind, structured=False)


# ── _fetch_latest_run_ids ─────────────────────────────────────────────────


def test_deduplicates_rerun_of_same_workflow():
    """A re-run of the same workflow should supersede the original."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_keeps_distinct_workflows():
    """Different workflows for the same commit should all be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200, 201]


def test_rerun_with_multiple_workflows():
    """Re-run of one workflow shouldn't affect other workflows."""
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [300, 201]


def test_filters_to_latest_sha():
    """Only runs for the latest SHA should be included."""
    runs = [
        {"databaseId": 300, "headSha": "def", "workflowName": "CI"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [300]


def test_empty_run_list():
    with patch("gh_client.json_out", return_value=[]):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_filters_skipped_runs():
    """Skipped workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Dependabot", "conclusion": "skipped"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_filters_cancelled_runs():
    """Cancelled workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Old CI", "conclusion": "cancelled"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == [200]


def test_all_skipped_returns_empty():
    """When all runs at the latest SHA are skipped, return empty list."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "A", "conclusion": "skipped"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "B", "conclusion": "cancelled"},
    ]
    with patch("gh_client.json_out", return_value=runs):
        result = ci_check._fetch_latest_run_ids("owner/repo", "main")
    assert result == []


def test_in_progress_runs_kept():
    """Runs still in progress (conclusion=None) should be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": None},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy", "conclusion": "skipped"},
    ]
    with patch("gh_client.json_out", return_value=runs):
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


def test_merge_runs_in_progress_clears_success():
    """An in-progress run should prevent the merged result from reporting success."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "status": "completed", "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_check._merge_runs(runs)
    assert result["status"] == "in_progress"
    assert result["conclusion"] == ""


def test_merge_runs_in_progress_preserves_failure():
    """A real failure should still surface even when another run is in-progress."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "status": "completed", "conclusion": "failure", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_check._merge_runs(runs)
    assert result["conclusion"] == "failure"
    assert result["status"] == "in_progress"


# ── CIFixAdapter ────────────────────────────────────────────────────────


def _ci_state():
    return PRState(identity=PRIdentity(
        repo="owner/repo", branch="feat/test", pr_number=42,
        head_sha="abc123", worktree_root="",
    ))


def _ci_adapter(tmp_path, failures, run_number=7, state=None):
    """The CI adapter as `_run_fix` builds it, against a real worktree path."""
    return ci_check.CIFixAdapter(
        failures, {"run_number": run_number},
        make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        state if state is not None else _ci_state(),
    )


def test_only_fixable_failures_are_handed_to_the_agent(tmp_path):
    """Infra and flaky failures are held back — no edit would clear either."""
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
    adapter = _ci_adapter(tmp_path, failures)

    assert [i.id for i in adapter.items()] == ["lint-1"]
    assert [f["id"] for f in adapter.skipped] == ["infra-1", "flaky-1"]


def test_each_item_carries_the_failure_the_agent_has_to_read(tmp_path):
    """Location, job and the failure text all reach the checklist entry."""
    failures = [
        {"id": "sc2086-bin-foo-42", "job": "shellcheck", "kind": "lint",
         "annotation": "SC2086: Double quote", "headline": "SC2086: Double quote",
         "file": "bin/foo.sh", "line": 42, "outcome": "new"},
        {"id": "pytest-test-auth-18", "job": "pytest", "kind": "test",
         "annotation": "AssertionError", "headline": "AssertionError",
         "file": "tests/auth.py", "line": 18, "outcome": "persisting"},
    ]
    lint, test = _ci_adapter(tmp_path, failures).items()

    assert (lint.file, lint.line, lint.label) == ("bin/foo.sh", 42, "shellcheck")
    assert "SC2086: Double quote" in lint.body
    assert (test.file, test.line, test.label) == ("tests/auth.py", 18, "pytest")
    assert "persisting" in test.body


def test_a_failure_with_no_location_still_becomes_an_item(tmp_path):
    """A build failure names no file, and the em dash stands in for one."""
    failures = [
        {"id": "build-1", "job": "gradle", "kind": "build",
         "annotation": "compilation failed", "headline": "compilation failed",
         "file": None, "line": None, "outcome": "new"},
    ]
    item, = _ci_adapter(tmp_path, failures).items()

    assert (item.file, item.line) == ("", 0)
    assert item.location() == "—"


def test_a_run_of_only_skipped_failures_hands_over_nothing(tmp_path):
    """`_run_fix` reads `fixable` to decide there is nothing to ask an agent."""
    failures = [
        {"id": "infra-1", "job": "docker", "kind": "infra",
         "annotation": "OOM", "headline": "OOM",
         "file": None, "line": None, "outcome": "new"},
    ]
    adapter = _ci_adapter(tmp_path, failures)

    assert adapter.fixable == []
    assert adapter.items() == []


def test_the_tracking_file_is_named_for_the_run(tmp_path):
    """One directory per pass holds both the checklist and the session log."""
    adapter = _ci_adapter(tmp_path, [], run_number=11)

    assert adapter.title == "CI Fix Tracking — Run #11"
    assert adapter.tracking_path == tmp_path / "ignore" / "ci-failures" / "fix-tracking.md"
    assert adapter.session_log == tmp_path / "ignore" / "ci-failures" / "fix-session.jsonl"


def test_the_commit_message_counts_what_the_agent_answered(tmp_path):
    """Fixed and not-fixed, so the log says what a pass achieved without a diff."""
    adapter = _ci_adapter(tmp_path, [])
    outcomes = [
        ItemOutcome(id="a", outcome=FixOutcome.FIXED),
        ItemOutcome(id="b", outcome=FixOutcome.DECLINED),
        ItemOutcome(id="c", outcome=FixOutcome.DEFERRED),
    ]

    spec = adapter.landing(outcomes)

    assert spec.message == "fix: address CI failures\n\n1 fixed, 2 skipped"
    assert spec.regen == "chore: regenerate after CI fixes"


def test_a_pass_that_fixed_nothing_says_only_what_it_did(tmp_path):
    """No counts line — "0 fixed, 3 skipped" is noise in a log."""
    adapter = _ci_adapter(tmp_path, [])
    outcomes = [ItemOutcome(id="a", outcome=FixOutcome.DECLINED)]

    assert adapter.landing(outcomes).message == "fix: address CI failures"


def test_held_back_failures_are_recorded_as_skipped(tmp_path):
    """A record holding only the agent's answers reads as if infra was never seen."""
    failures = [
        {"id": "lint-1", "job": "shellcheck", "kind": "lint",
         "annotation": "SC2086", "headline": "SC2086",
         "file": "bin/foo.sh", "line": 42, "outcome": "new"},
        {"id": "infra-1", "job": "docker", "kind": "infra",
         "annotation": "connection refused", "headline": "connection refused",
         "file": None, "line": None, "outcome": "new"},
    ]
    state = _ci_state()
    adapter = _ci_adapter(tmp_path, failures, state=state)
    run = fix_engine.FixRun(
        outcomes=[ItemOutcome(id="lint-1", outcome=FixOutcome.FIXED)],
        landed=land.LandResult(CommitStatus.PUSH_HELD, "deadbee"),
        head_before="cafe123",
    )

    with patch.object(ci_check.pr_state, "save_state") as saved:
        adapter.record(run)

    recorded = {i.id: i for i in state.ci.fix.items}
    assert recorded["lint-1"].outcome is FixOutcome.FIXED
    assert recorded["infra-1"].outcome is FixOutcome.SKIPPED
    assert "infra" in recorded["infra-1"].reason
    assert recorded["infra-1"].read_sha == "cafe123"
    assert state.ci.fix.commit_sha == "deadbee"
    assert saved.called


# ── _fetch_job_failure ──────────────────────────────────────────────────


def test_fetch_job_failure_returns_correct_structure():
    """_fetch_job_failure returns dict with expected keys."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote", "path": "bin/foo.sh", "start_line": 42},
    ]
    job = {"name": "shellcheck", "conclusion": "failure", "databaseId": 10}
    run_data = {"databaseId": 100}
    with patch("ci_check._fetch_annotations", return_value=annotations):
        result = ci_check._fetch_job_failure("owner/repo", job, run_data)
    assert result is not None
    assert result["job_name"] == "shellcheck"
    assert result["kind"] == ci_check.ci.FailureKind.LINT
    assert len(result["items"]) == 1
    assert result["items"][0].file == "bin/foo.sh"
    assert result["failed_step"] is None


def test_fetch_job_failure_with_no_annotations():
    """_fetch_job_failure falls back when no annotations exist."""
    job = {"name": "Build", "conclusion": "failure", "databaseId": 10}
    run_data = {"databaseId": 100}
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._fetch_job_failure("owner/repo", job, run_data)
    assert result is not None
    assert result["job_name"] == "Build"
    assert "no-annotation" in result["items"][0].id


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
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
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
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    assert len(result.failures) == 1


def test_parse_run_includes_timed_out_jobs():
    """Timed-out jobs should be treated as failures."""
    run_data = _make_run_data([
        {"name": "Slow Test", "conclusion": "timed_out", "databaseId": 10},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.TEST)):
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
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 200


def test_parse_run_defaults_source_run_id_to_primary():
    """Without _source_run_id on the job, fall back to run's databaseId."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.items[0].source_run_id == 100


# ── _extract_failed_step ────────────────────────────────────────────────


def test_extract_failed_step_from_steps():
    job = {
        "name": "Generate & verify",
        "steps": [
            {"name": "Checkout", "conclusion": "success"},
            {"name": "Setup Node", "conclusion": "success"},
            {"name": "Generate & check drift", "conclusion": "failure"},
            {"name": "Post Checkout", "conclusion": "skipped"},
        ],
    }
    assert ci_check._extract_failed_step(job) == "Generate & check drift"


def test_extract_failed_step_no_steps():
    job = {"name": "Build"}
    assert ci_check._extract_failed_step(job) is None


def test_extract_failed_step_all_success():
    job = {
        "name": "Lint",
        "steps": [
            {"name": "Checkout", "conclusion": "success"},
            {"name": "Run lint", "conclusion": "success"},
        ],
    }
    assert ci_check._extract_failed_step(job) is None


def test_extract_failed_step_timed_out():
    job = {
        "name": "Slow tests",
        "steps": [
            {"name": "Run tests", "conclusion": "timed_out"},
        ],
    }
    assert ci_check._extract_failed_step(job) == "Run tests"


def test_parse_run_includes_failed_step():
    """_parse_run should extract failed_step from job steps data."""
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
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.failed_step == "Generate & check drift"


def test_parse_run_failed_step_none_without_steps():
    """Jobs without steps data should have failed_step=None."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    with patch("ci_check._fetch_annotations", return_value=[]):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert group.failed_step is None


# ── _annotations_uninformative ─────────────────────────────────────────


def test_uninformative_no_paths():
    """Annotations without file paths are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_with_path():
    """Annotations with file paths are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote", "path": "bin/foo.sh", "start_line": 42},
    ]
    assert ci_check._annotations_uninformative(annotations) is False


def test_uninformative_ignores_notices():
    """Notice-level annotations are ignored when checking informativeness."""
    annotations = [
        {"annotation_level": "notice", "message": "some notice", "path": "README.md"},
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_mixed_informative_and_not():
    """If any non-notice annotation has a path, annotations are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
        {"annotation_level": "failure", "message": "error TS2304: Cannot find name 'foo'", "path": "src/app.ts", "start_line": 10},
    ]
    assert ci_check._annotations_uninformative(annotations) is False


def test_uninformative_generic_path_dot_github():
    """Annotations with path='.github' and generic message are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_generic_exit_code_message():
    """Annotations with a source path but generic 'exit code' message are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": "src/main.go", "start_line": 1},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_exited_with_code():
    """'exited with code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Step exited with code 1", "path": "src/main.go", "start_line": 1},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_returned_non_zero():
    """'returned a non-zero code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Command returned a non-zero code: 2", "path": "Makefile", "start_line": 10},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_check_failure_on_line():
    """'check failure on line' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Check failure on line 42", "path": ".github/workflows/ci.yml", "start_line": 42},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_failed_with_exit_code():
    """'failed with exit code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Job failed with exit code 1", "path": ".github/workflows/ci.yml", "start_line": 1},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_informative_real_error_with_source_path():
    """Annotations with a real source path and specific error are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote to prevent globbing", "path": "bin/foo.sh", "start_line": 42},
    ]
    assert ci_check._annotations_uninformative(annotations) is False


def test_informative_mixed_generic_and_specific():
    """If any annotation has a real path and specific message, annotations are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
        {"annotation_level": "failure", "message": "error TS2304: Cannot find name 'foo'", "path": "src/app.ts", "start_line": 10},
    ]
    assert ci_check._annotations_uninformative(annotations) is False


# ── _parse_run log enrichment for BUILD failures ──────────────────────


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
    with patch("ci_check._fetch_annotations", return_value=uninformative_annotations):
        fallback = ci_check._LogFallback(log_annotations, log_context, ci_check.ci.FailureKind.BUILD, structured=False)
        with patch("ci_check._log_fallback", return_value=fallback) as mock_fallback:
            result = ci_check._parse_run("owner/repo", run_data)
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
    with patch("ci_check._fetch_annotations", return_value=uninformative_annotations):
        with patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)):
            result = ci_check._parse_run("owner/repo", run_data)
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
    with patch("ci_check._fetch_annotations", return_value=uninformative_annotations):
        fallback = ci_check._LogFallback(log_annotations, log_context, ci_check.ci.FailureKind.TEST, structured=False)
        with patch("ci_check._log_fallback", return_value=fallback) as mock_fallback:
            result = ci_check._parse_run("owner/repo", run_data)
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
    with patch("ci_check._fetch_annotations", return_value=uninformative_annotations):
        with patch("ci_check._log_fallback") as mock_fallback:
            ci_check._parse_run("owner/repo", run_data)
    mock_fallback.assert_not_called()


# ── _commits_behind_main ────────────────────────────────────────────────


def test_commits_behind_main_returns_count():
    with patch("gh_client.api", return_value=CmdResult(0, "15\n")):
        result = ci_check._commits_behind_main("owner/repo", "feat/auth")
    assert result == 15


def test_commits_behind_main_returns_zero_on_error():
    with patch("gh_client.api", return_value=CmdResult(1)):
        result = ci_check._commits_behind_main("owner/repo", "feat/auth")
    assert result == 0


def test_commits_behind_main_returns_zero_on_non_numeric():
    with patch("gh_client.api", return_value=CmdResult(0, "null\n")):
        result = ci_check._commits_behind_main("owner/repo", "feat/auth")
    assert result == 0


# ── _rebase_if_behind ───────────────────────────────────────────────────


def _mock_ctx(worktree_root="/tmp/wt", branch="feat/auth"):
    ctx = MagicMock()
    ctx.worktree_root = Path(worktree_root)
    ctx.require_worktree.return_value = Path(worktree_root)
    ctx.branch = branch
    return ctx


def test_rebase_if_behind_skips_when_not_behind():
    trail = MagicMock()
    report = {"behind_main": 0}
    assert ci_check._rebase_if_behind(trail, report, _mock_ctx()) is False
    trail.decision.assert_not_called()


def test_rebase_if_behind_skips_when_field_missing():
    trail = MagicMock()
    report = {}
    assert ci_check._rebase_if_behind(trail, report, _mock_ctx()) is False


def test_rebase_if_behind_runs_rebase_on_success():
    trail = MagicMock()
    report = {"behind_main": 5}
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
    report = {"behind_main": 10}
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
                            MagicMock(), {"behind_main": 3}, ctx)


# ── _parse_test_artifact ─────────────────────────────────────────────


def test_parse_test_artifact_jsonl(tmp_path):
    """Artifact with Go test JSONL should extract failure output."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    jsonl = artifact_dir / "test-results.json"
    lines = [
        '{"Action":"output","Package":"github.com/foo/tests","Output":"=== RUN   TestFoo\\n"}',
        '{"Action":"output","Package":"github.com/foo/tests","Output":"    foo_test.go:42: expected 1, got 2\\n"}',
        '{"Action":"output","Package":"github.com/foo/tests","Output":"--- FAIL: TestFoo (0.01s)\\n"}',
        '{"Action":"fail","Package":"github.com/foo/tests","Elapsed":0.01}',
    ]
    jsonl.write_text("\n".join(lines))

    result = ci_check._parse_test_artifact(str(artifact_dir))
    assert "--- FAIL: TestFoo" in result
    assert "expected 1, got 2" in result


def test_parse_test_artifact_returns_empty_on_no_failures(tmp_path):
    """Artifact with all passing tests returns empty."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    jsonl = artifact_dir / "test-results.json"
    lines = [
        '{"Action":"output","Package":"github.com/foo/tests","Output":"=== RUN   TestFoo\\n"}',
        '{"Action":"pass","Package":"github.com/foo/tests","Elapsed":0.01}',
    ]
    jsonl.write_text("\n".join(lines))

    result = ci_check._parse_test_artifact(str(artifact_dir))
    assert result == ""


# ── _annotations_to_items headline from context ─────────────────────


def test_annotations_to_items_headline_from_context():
    """When annotation text has no headline, derive it from context."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
    ]
    context = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:42: expected 1, got 2"
    items = ci_check._annotations_to_items(annotations, "Test: svc-payment", source_run_id=100, context=context)
    assert len(items) == 1
    assert "FAIL: TestFoo" in items[0].headline


# ── _fetch_job_failure artifact fallback ─────────────────────────────


_UNINFORMATIVE_ANNOTATIONS = [
    {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
]

_ARTIFACT_CONTEXT = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:42: expected 1, got 2"


@patch("ci_check._fetch_test_artifact", return_value=_ARTIFACT_CONTEXT)
@patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.TEST))
@patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS)
def test_fetch_job_failure_uses_artifact_fallback(_mock_ann, _mock_log, _mock_art):
    """When annotations are uninformative and logs are empty, artifact fallback triggers."""
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10,
           "_source_run_id": 100}
    result = ci_check._fetch_job_failure("owner/repo", job, {"databaseId": 100})
    assert result is not None
    assert result["items"][0].context == _ARTIFACT_CONTEXT
    assert "FAIL: TestFoo" in result["items"][0].headline


@patch("ci_check._fetch_test_artifact")
@patch("ci_check._log_fallback")
@patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS)
def test_fetch_job_failure_skips_artifact_when_logs_succeed(_mock_ann, mock_log, mock_artifact):
    """When log fallback produces context, artifact download is not attempted."""
    log_context = "--- FAIL: TestBar (0.02s)\n    bar_test.go:10: wrong result"
    log_annotations = [{"message": log_context, "path": "", "start_line": 0, "title": ""}]
    mock_log.return_value = ci_check._LogFallback(
        log_annotations, log_context, ci_check.ci.FailureKind.TEST, structured=False,
    )
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10}
    ci_check._fetch_job_failure("owner/repo", job, {"databaseId": 100})
    mock_artifact.assert_not_called()


@patch("ci_check._fetch_test_artifact")
@patch("ci_check._log_fallback")
@patch("ci_check._fetch_annotations")
def test_fetch_job_failure_no_artifact_for_lint(mock_ann, mock_log, mock_artifact):
    """LINT failures do not trigger artifact download even when uninformative."""
    mock_ann.return_value = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": "", "start_line": 0},
    ]
    job = {"name": "shellcheck", "conclusion": "failure", "databaseId": 10}
    ci_check._fetch_job_failure("owner/repo", job, {"databaseId": 100})
    mock_log.assert_not_called()
    mock_artifact.assert_not_called()


# ── Test failures ──────────────────────────────────────────────────────────

# Trimmed from run #2893: the failure near the top, and at the bottom the
# warning traces and runner exit message the old extraction anchored on.
_BATS_LOG = "\n".join([
    "2026-08-28T18:34:33.5541835Z not ok 1648 bats_skip survives a setup in 71ms",
    "2026-08-28T18:34:33.5542700Z # (in test file tests/ui_facade.bats, line 164)",
    "2026-08-28T18:34:33.5543479Z #   `[[ \"$output\" == *\"# skip\"* ]]' failed",
    "2026-08-28T18:34:33.5544239Z ok 1651 output.sh accepts current bash in 27ms",
    "2026-08-28T18:35:03.5727631Z        in test file tests/install_targeted.bats, line 150)",
    "2026-08-28T18:35:03.7583872Z ##[error]Process completed with exit code 1.",
])

_BATS_JOB = {"name": "Tests (bats)", "conclusion": "failure", "databaseId": 10,
             "_source_run_id": 100}


def _bats_items():
    """`_fetch_job_failure` for a bats job whose only annotation is generic."""
    with patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("ci_check._fetch_job_logs", return_value=_BATS_LOG):
        return ci_check._fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100})["items"]


def test_bats_failure_reports_the_failing_assertion():
    item = _bats_items()[0]
    assert (item.file, item.line) == ("tests/ui_facade.bats", 164)


def test_bats_failure_does_not_inherit_the_generic_annotation_location():
    """The generic annotation anchors on `.github` — a log location beats it."""
    item = _bats_items()[0]
    assert item.file != _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert item.line != _UNINFORMATIVE_ANNOTATIONS[0]["start_line"]


def test_bats_failure_headline_names_the_failing_test():
    assert "bats_skip survives a setup" in _bats_items()[0].headline


def test_bats_failure_id_is_stable_across_runs():
    assert _bats_items()[0].id == _bats_items()[0].id == "err-tests/ui_facade.bats-164"


def test_every_bats_failure_gets_its_own_item():
    log = "\n".join([
        "2026-08-28T18:34:33.5541835Z not ok 2 failing early in 3ms",
        "2026-08-28T18:34:33.5542700Z # (in test file tests/a.bats, line 10)",
        "2026-08-28T18:34:33.5544239Z ok 3 passing later in 1ms",
        "2026-08-28T18:34:33.5545000Z not ok 4 failing late in 1ms",
        "2026-08-28T18:34:33.5546000Z # (in test file tests/b.bats, line 18)",
    ])
    with patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("ci_check._fetch_job_logs", return_value=log):
        result = ci_check._fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100})
    assert [(i.file, i.line) for i in result["items"]] == [
        ("tests/a.bats", 10), ("tests/b.bats", 18),
    ]


def test_an_unlocated_tap_failure_keys_on_its_test_name():
    """`hash()` is randomised per process, so a message hash never matches twice."""
    log = "2026-08-28T18:34:33.5541835Z not ok 1 setup_file failed in 2ms"
    with patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("ci_check._fetch_job_logs", return_value=log):
        result = ci_check._fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100})
    assert result["items"][0].id == "Tests (bats)-setup-file-failed"


def test_job_logs_allow_escape_sequences():
    """gh refuses a coloured log outright, which reads here as a job with no logs."""
    with patch("gh_client.api", return_value=CmdResult(0, "logs")) as mock_api:
        ci_check._fetch_job_logs("owner/repo", 10)
    assert mock_api.call_args.kwargs["allow_escape_sequences"] is True


# Trimmed from run 32793239084: one failure raised inside a helper, so pytest
# prints the caller's frame at 954 above the `_ _ _ _` rule and the raise site
# at 937 below it, then the summary and the same generic runner message.
_PYTEST_LOG = "\n".join([
    "2026-08-25T00:21:37.7671403Z _____ TestRetry.test_retries_on_zero_progress _____",
    "2026-08-25T00:21:37.7679183Z >       job = self._make_job(tmp_path)",
    "2026-08-25T00:21:37.7679478Z tests/test_review_fix_pass.py:954:",
    "2026-08-25T00:21:37.7679799Z _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _",
    "2026-08-25T00:21:37.7705587Z E       NameError: name '_git' is not defined",
    "2026-08-25T00:21:37.7706200Z tests/test_review_fix_pass.py:937: NameError",
    "2026-08-25T00:21:37.7819142Z =========================== short test summary info ============================",
    "2026-08-25T00:21:37.7822162Z FAILED tests/test_review_fix_pass.py::TestRetry::test_retries_on_zero_progress - NameError",
    "2026-08-25T00:21:37.8604173Z ##[error]Process completed with exit code 1.",
])

_PYTEST_JOB = {"name": "Tests (pytest)", "conclusion": "failure", "databaseId": 11,
               "_source_run_id": 100}


def _pytest_items(log=_PYTEST_LOG):
    """`_fetch_job_failure` for a pytest job whose only annotation is generic."""
    with patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("ci_check._fetch_job_logs", return_value=log):
        return ci_check._fetch_job_failure("owner/repo", _PYTEST_JOB, {"databaseId": 100})["items"]


def test_pytest_failure_reports_the_frame_that_raised():
    item = _pytest_items()[0]
    assert (item.file, item.line) == ("tests/test_review_fix_pass.py", 937)


def test_pytest_failure_does_not_inherit_the_generic_annotation_location():
    item = _pytest_items()[0]
    assert item.file != _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert item.line != _UNINFORMATIVE_ANNOTATIONS[0]["start_line"]


def test_pytest_failure_id_is_stable_across_runs():
    assert _pytest_items()[0].id == _pytest_items()[0].id == "err-tests/test_review_fix_pass.py-937"


def test_pytest_failure_headline_names_the_failing_test():
    assert _pytest_items()[0].headline.startswith(
        "FAILED tests/test_review_fix_pass.py::TestRetry::test_retries_on_zero_progress")


def test_an_unlocated_pytest_failure_keys_on_its_test_name():
    log = "\n".join([
        "2026-08-25T00:21:37.7819142Z ====================== short test summary info =======================",
        "2026-08-25T00:21:37.7822162Z FAILED tests/a_test.py::TestRetry::test_collected - RuntimeError",
    ])
    assert _pytest_items(log)[0].id == "Tests (pytest)-testretry-test-collected"


def test_a_non_tap_log_still_keeps_the_original_annotations():
    """Only a location the log reported displaces GitHub's own annotation."""
    log = "2026-06-22T17:22:01Z --- FAIL: TestFoo (0.01s)\n2026-06-22T17:22:01Z FAIL"
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10}
    with patch("ci_check._fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("ci_check._fetch_job_logs", return_value=log):
        result = ci_check._fetch_job_failure("owner/repo", job, {"databaseId": 100})
    assert result["items"][0].file == _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert "FAIL: TestFoo" in result["items"][0].context


# ── _count_job_states ──────────────────────────────────────────────────────


def test_count_job_states_all_completed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "completed", "conclusion": "failure"},
        {"name": "build", "status": "completed", "conclusion": "neutral"},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 3
    assert failed == 1
    assert running == 0
    assert queued == 0


def test_count_job_states_mixed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": None},
        {"name": "build", "status": "queued", "conclusion": None},
        {"name": "deploy", "status": "waiting", "conclusion": None},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 1
    assert failed == 0
    assert running == 1
    assert queued == 2


def test_count_job_states_empty():
    merged = {"jobs": []}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 0
    assert failed == 0
    assert running == 0
    assert queued == 0


def test_count_job_states_timed_out_is_failed():
    merged = {"jobs": [
        {"name": "slow", "status": "completed", "conclusion": "timed_out"},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 1
    assert failed == 1


def test_count_job_states_pending_is_queued():
    merged = {"jobs": [
        {"name": "deploy", "status": "pending", "conclusion": None},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert queued == 1


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

    with patch("ci_check._fetch_latest_run_ids", side_effect=[[100], [100]]), \
         patch("ci_check._fetch_run_data", side_effect=mock_fetch_data), \
         patch("ci_check._fetch_annotations", return_value=[]), \
         patch("ci_check._log_fallback", return_value=_no_log_fallback(ci_check.ci.FailureKind.BUILD)), \
         patch("ci_check._commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        result = ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

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

    with patch("ci_check._fetch_latest_run_ids", return_value=[100]), \
         patch("ci_check._fetch_run_data", return_value=run_data), \
         patch("ci_check._commits_behind_main", return_value=0), \
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

    with patch("ci_check._fetch_latest_run_ids", return_value=[100]), \
         patch("ci_check._fetch_run_data", return_value=run_data), \
         patch("ci_check._commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        result = ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stderr = capsys.readouterr().err
    assert "timeout" in stderr.lower()


# ── the fix pass, end to end ──────────────────────────────────────────────


_ONE_FAILURE = {
    "id": "build-1", "job": "build", "kind": "build",
    "annotation": "compilation failed", "headline": "compilation failed",
    "file": "src/main.go", "line": 3, "outcome": "new",
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
    report = {"failures": [_ONE_FAILURE], "run_number": 1}
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
    assert prompts[1] == ci_check.agent_retry.CI_FIX_RETRY_HINT + prompts[0]


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
            trail, {"failures": [_ONE_FAILURE], "run_number": 1},
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
            MagicMock(), {"failures": [_ONE_FAILURE], "run_number": 1},
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
