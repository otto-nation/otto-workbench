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
from gh import run_reads  # noqa: E402
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
        {"_run_id": 2, "databaseId": 2, "conclusion": "failure",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "failure"


def test_merge_runs_jobless_action_required_does_not_poison_conclusion():
    """An approval-gated run has no jobs, so it can name no failure.

    GitHub concludes a run `action_required` while it waits for someone to
    approve it, and such a run carries no jobs at all. Overriding on that
    conclusion reported the commit `failure` over an empty failure list — a
    red verdict naming nothing, which no fix pass can clear.
    """
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]},
        {"_run_id": 2, "databaseId": 2, "conclusion": "action_required", "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "success"


def test_merge_runs_jobless_startup_failure_and_stale_do_not_poison_conclusion():
    """The rule is the missing evidence, not the particular word for it."""
    for conclusion in ("startup_failure", "stale"):
        runs = [
            {"_run_id": 1, "databaseId": 1, "conclusion": "success",
             "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]},
            {"_run_id": 2, "databaseId": 2, "conclusion": conclusion, "jobs": []},
        ]
        assert ci_runs.merge_runs(runs)["conclusion"] == "success", conclusion


def test_merge_runs_action_required_with_a_failed_job_still_overrides():
    """A *job* in that state has genuinely run, so its run keeps its claim.

    `FAILURE_CONCLUSIONS` is the job-level filter too, and there the word means
    something the run-level use does not: only the jobless run is declined.
    """
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "action_required",
         "jobs": [{"name": "deploy", "status": "completed", "conclusion": "action_required"}]},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "failure"


def test_merge_runs_a_failed_job_under_a_passing_run_still_overrides():
    """The jobs are the evidence, so they are what the override reads."""
    runs = [
        {"_run_id": 1, "databaseId": 1, "conclusion": "success", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "conclusion": "failure",
         "jobs": [{"name": "lint", "status": "completed", "conclusion": "timed_out"}]},
    ]
    assert ci_runs.merge_runs(runs)["conclusion"] == "failure"


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
        {"_run_id": 1, "databaseId": 1, "status": "completed", "conclusion": "failure",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["conclusion"] == "failure"
    assert result["status"] == "in_progress"


def test_merge_runs_in_progress_clears_a_jobless_failure_conclusion():
    """A run still going has reached no verdict, and the finished one named nothing.

    The conclusion was kept purely because the word was in `FAILURE_CONCLUSIONS`,
    so an approval-gated run beside a running one reported the commit red while
    its real CI had not finished.
    """
    runs = [
        {"_run_id": 1, "databaseId": 1, "status": "completed",
         "conclusion": "action_required", "jobs": []},
        {"_run_id": 2, "databaseId": 2, "status": "in_progress", "conclusion": None, "jobs": []},
    ]
    result = ci_runs.merge_runs(runs)
    assert result["status"] == "in_progress"
    assert result["conclusion"] == ""


# ── fetch_merged ─────────────────────────────────────────────────────────


def _rows(payloads):
    """The `gh run list` discovery the payloads below would have come from."""
    return run_reads.RunDiscovery(rows=tuple(run_reads.RunRow(
        run_id=p["databaseId"], number=p.get("number", 0),
        head_sha=p.get("headSha", "abc"), status=p.get("status", ""),
        conclusion=p.get("conclusion", ""),
    ) for p in payloads))


def _fetch_merged(payloads, checks=None):
    """Run `fetch_merged` over payloads GitHub would have served, in list order.

    The rollup answers nothing by default, so every run is fetched — which is
    what these cases were written against and what a commit GitHub has no
    rollup for still does.
    """
    by_id = {p["databaseId"]: p for p in payloads}
    with patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: by_id[rid]), \
         patch("gh.run_reads.fetch_commit_checks",
               return_value=checks or run_reads.CommitChecks()):
        return ci_runs.fetch_merged("owner/repo", _rows(payloads))


def test_a_jobless_cancelled_run_does_not_become_the_merged_conclusion():
    """`Release` is routinely cancelled with no jobs at a commit whose CI passed.

    Leading the merge with it would leave the commit reported as `cancelled`,
    which `CIDomain.readiness` turns into a spurious `CI failing` blocker.
    """
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed", "jobs": []},
        {"databaseId": 200, "conclusion": "success", "status": "completed",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]},
    ])
    assert fetched.merged["conclusion"] == "success"


def test_a_cancelled_run_that_has_jobs_may_lead_the_merge():
    """The jobs are the evidence — a run holding them keeps its claim to lead."""
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "cancelled"}]},
        {"databaseId": 200, "conclusion": "success", "status": "completed", "jobs": []},
    ])
    assert fetched.merged["conclusion"] == "cancelled"


def test_a_failure_still_wins_over_a_jobless_cancelled_run():
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed", "jobs": []},
        {"databaseId": 200, "conclusion": "failure", "status": "completed",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
    ])
    assert fetched.merged["conclusion"] == "failure"


def test_every_fetched_run_is_still_reported_when_one_is_reordered():
    """Reordering picks the leader; it must not drop a payload or its jobs."""
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed", "jobs": []},
        {"databaseId": 200, "conclusion": "success", "status": "completed",
         "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]},
    ])
    assert [p["_run_id"] for p in fetched.payloads] == [300, 200]
    assert len(fetched.merged["jobs"]) == 1


def test_an_approval_gated_run_is_not_reported_as_a_failure_naming_nothing():
    """The whole path: a jobless `action_required` run through to the RunState.

    This is the reported shape — a run held awaiting approval is the only run
    at its commit on this repo, so nothing else can carry the conclusion. The
    merge used to write `failure` over it, and `parse_run` then found no failed
    job to name, leaving `conclusion: failure` above `failures: {}`.
    """
    fetched = _fetch_merged([
        {"databaseId": 400, "conclusion": "action_required", "status": "completed",
         "number": 7, "headSha": "abc123", "jobs": []},
    ])
    state = ci_runs.parse_run("owner/repo", fetched.merged)
    assert state.failures == {}
    assert state.conclusion != "failure"


def test_all_runs_cancelled_and_jobless_still_merges():
    """With no better leader available, the original order stands."""
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed", "jobs": []},
        {"databaseId": 200, "conclusion": "cancelled", "status": "completed", "jobs": []},
    ])
    assert fetched.merged["conclusion"] == "cancelled"


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


def test_parse_run_does_not_double_count_a_job_reported_by_two_runs():
    """A cancelled run and the real run of one workflow both reach merge_runs.

    Their job lists are concatenated, so the same job name arrives twice and its
    failures land in one group — the item count must still be the real one.
    """
    run_data = _make_run_data([
        {"name": "Test", "conclusion": "failure", "databaseId": 10, "_source_run_id": 300},
        {"name": "Test", "conclusion": "failure", "databaseId": 11, "_source_run_id": 200},
    ])
    annotations = [
        {"annotation_level": "failure", "message": "boom", "path": "a.py", "start_line": 3, "title": "E1"},
    ]
    with patch("gh.run_reads.fetch_annotations", return_value=annotations):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.LINT)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert len(group.items) == 1


def test_parse_run_keeps_distinct_failures_sharing_one_id():
    """Two annotations on one file and line share an id but are two failures."""
    run_data = _make_run_data([
        {"name": "Lint", "conclusion": "failure", "databaseId": 10},
    ])
    annotations = [
        {"annotation_level": "failure", "message": "unused import", "path": "a.py", "start_line": 3, "title": "E1"},
        {"annotation_level": "failure", "message": "line too long", "path": "a.py", "start_line": 3, "title": "E1"},
    ]
    with patch("gh.run_reads.fetch_annotations", return_value=annotations):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.LINT)):
            result = ci_runs.parse_run("owner/repo", run_data)
    group = list(result.failures.values())[0]
    assert len(group.items) == 2


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


# ── checks that are not Actions jobs ─────────────────────────────────────


def _external(name, conclusion, status="completed", source="check_run", job_id=0):
    return {"name": name, "databaseId": job_id, "status": status, "conclusion": conclusion,
            "steps": [], "_check_source": source, "_details_url": "", "_summary": ""}


def _green_run(run_id, *names):
    return {run_id: tuple(
        {"name": n, "databaseId": i, "status": "completed", "conclusion": "success",
         "steps": []}
        for i, n in enumerate(names, start=1)
    )}


_PASSING_RUN = {"databaseId": 200, "conclusion": "success", "status": "completed",
                "number": 5, "headSha": "abc",
                "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]}


def test_an_app_check_failure_turns_a_green_commit_red():
    """The defect: every Actions run passed, so the commit reported `All checks passed`."""
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "failure", job_id=77),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"
    assert [j["name"] for j in fetched.merged["jobs"]] == ["test", "CodeQL"]
    codeql = next(j for j in fetched.merged["jobs"] if j["name"] == "CodeQL")
    assert codeql["databaseId"] == 77


def test_a_status_context_failure_turns_a_green_commit_red():
    checks = run_reads.CommitChecks(
        answered=True,
        external=(_external("scalr/plan", "failure", source="status_context"),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"


def test_an_unfinished_external_check_leaves_the_commit_undecided():
    """Reported complete, a running external check lets --wait stop early."""
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "", status="in_progress"),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["status"] == "in_progress"
    assert fetched.merged["conclusion"] == ""


def test_an_external_failure_outranks_an_unfinished_one():
    """A failure stands whatever else is still going — merge_runs' own rule."""
    checks = run_reads.CommitChecks(answered=True, external=(
        _external("CodeQL", "failure"),
        _external("scalr/plan", "", status="in_progress", source="status_context"),
    ))
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"


def test_external_checks_are_counted_among_the_jobs():
    """--wait reports N/M; an external check nobody counted makes the run look shorter."""
    checks = run_reads.CommitChecks(answered=True, external=(
        _external("CodeQL", "failure"),
        _external("scalr/plan", "", status="in_progress", source="status_context"),
    ))
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    counts = ci_runs.count_job_states(fetched.merged)
    assert counts.total == 3
    assert counts.failed == 1
    assert counts.running == 1
    assert counts.finished is False


def test_a_commit_whose_only_checks_are_external_still_reports():
    """A repo can check a commit without running a workflow on it."""
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "failure", source="status_context"),),
    )
    with patch("gh.run_reads.fetch_run_data", return_value=None), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(), head_sha="abc")
    assert fetched is not None
    assert fetched.merged["conclusion"] == "failure"
    assert [j["name"] for j in fetched.merged["jobs"]] == ["scalr/plan"]


def test_nothing_at_all_is_still_nothing_to_report():
    with patch("gh.run_reads.fetch_commit_checks", return_value=run_reads.CommitChecks()):
        assert ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(), head_sha="abc") is None


# ── runs the rollup spares us fetching ───────────────────────────────────


def test_a_run_the_rollup_proved_green_is_not_fetched():
    """Its job payload holds only the steps of jobs that did not fail."""
    checks = run_reads.CommitChecks(
        answered=True, actions=_green_run(200, "test", "lint"),
    )
    rows = [run_reads.RunRow(run_id=200, number=5, head_sha="abc",
                             status="completed", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data") as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    view.assert_not_called()
    assert [j["name"] for j in fetched.merged["jobs"]] == ["test", "lint"]
    assert fetched.merged["number"] == 5
    assert fetched.merged["conclusion"] == "success"
    assert ci_runs.count_job_states(fetched.merged).total == 2


def test_a_run_the_rollup_did_not_account_for_is_still_fetched():
    """A cancelled run is absent from the rollup while its failed jobs live on."""
    cancelled = {"databaseId": 300, "conclusion": "cancelled", "status": "completed",
                 "jobs": [{"name": "build", "status": "completed", "conclusion": "failure"}]}
    checks = run_reads.CommitChecks(answered=True, actions=_green_run(200, "test"))
    rows = [run_reads.RunRow(run_id=300, head_sha="abc", conclusion="cancelled"),
            run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data", return_value=cancelled) as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    assert [c.args[1] for c in view.call_args_list] == [300]
    # What skipping it would have cost: the failed job itself. The merged
    # conclusion stays `cancelled` either way — `cancelled` is not a failure
    # conclusion, which is why the job list is the thing worth asserting on.
    assert [j["name"] for j in ci_runs.failed_jobs(fetched.merged)] == ["build"]


def test_an_unanswered_rollup_fetches_every_run():
    """No rollup is no evidence — an approval-gated commit has none at all."""
    checks = run_reads.CommitChecks(answered=False)
    rows = [run_reads.RunRow(run_id=200, head_sha="abc", conclusion="success"),
            run_reads.RunRow(run_id=300, head_sha="abc", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data",
               side_effect=lambda repo, rid: {"databaseId": rid, "conclusion": "success",
                                              "status": "completed", "jobs": []}) as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    assert sorted(c.args[1] for c in view.call_args_list) == [200, 300]


def test_an_unpushed_head_retries_at_the_commit_the_runs_ran_on():
    """A local HEAD the API never saw answers nothing, which is not `no checks`."""
    external = (_external("CodeQL", "failure"),)
    answers = {"local": run_reads.CommitChecks(answered=False),
               "pushed": run_reads.CommitChecks(answered=True, external=external)}
    rows = [run_reads.RunRow(run_id=200, number=5, head_sha="pushed", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data", return_value=dict(_PASSING_RUN)), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: answers[sha]) as rollup:
        fetched = ci_runs.fetch_merged("owner/repo", run_reads.RunDiscovery(rows=tuple(rows)), head_sha="local")
    assert [c.args[1] for c in rollup.call_args_list] == ["local", "pushed"]
    assert fetched.merged["conclusion"] == "failure"


# ── knowledge that is incomplete must not read as a pass ──────────────────


def _discovery(*rows, failed=False):
    return run_reads.RunDiscovery(rows=rows, failed=failed)


def _green_payload(run_id=200):
    return {"databaseId": run_id, "number": 5, "headSha": "abc", "status": "completed",
            "conclusion": "success",
            "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]}


def _merged(discovery, checks, served=_green_payload):
    fetch = served if callable(served) else (lambda repo, rid: served)
    with patch("gh.run_reads.fetch_run_data",
               side_effect=lambda repo, rid: fetch(rid) if callable(served) else served), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        return ci_runs.fetch_merged("owner/repo", discovery, head_sha="abc")


def test_an_unreadable_rollup_withholds_the_pass():
    """Not knowing what the external checks said is not the same as them passing."""
    row = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), run_reads.CommitChecks(unreadable=True))
    assert fetched.merged["conclusion"] == ""
    assert fetched.merged["_unread"] == ("the commit's check rollup could not be read",)


def test_a_commit_with_genuinely_no_rollup_still_passes():
    """GitHub reporting no checks is a fact about the commit, not a failed read.

    An approval-gated run has no rollup at all, and treating that as unread
    would report every held workflow as unknown.
    """
    row = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), run_reads.CommitChecks(answered=False))
    assert fetched.merged["conclusion"] == "success"
    assert "_unread" not in fetched.merged


def test_a_truncated_rollup_withholds_the_pass():
    """The checks past the page could be the failing ones."""
    row = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), run_reads.CommitChecks(answered=True, truncated=True))
    assert fetched.merged["conclusion"] == ""
    assert "more checks than were listed" in fetched.merged["_unread"][0]


def test_a_run_nobody_could_read_withholds_the_pass():
    """The run was dropped from the payloads; its verdict must not be assumed."""
    good = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    bad = run_reads.RunRow(run_id=300, head_sha="abc", conclusion="failure")
    fetched = _merged(
        _discovery(good, bad), run_reads.CommitChecks(answered=True),
        served=lambda rid: _green_payload() if rid == 200 else None,
    )
    assert fetched.merged["conclusion"] == ""
    assert fetched.merged["_unread"] == ("run 300 could not be read",)


def test_a_failed_run_listing_withholds_the_pass():
    """An empty run list from a failed call is an absence of facts, not a green commit."""
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "success"),))
    with patch("gh.run_reads.fetch_run_data", return_value=None), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = ci_runs.fetch_merged(
            "owner/repo", run_reads.RunDiscovery(failed=True), head_sha="abc")
    assert fetched.merged["conclusion"] == ""
    assert "workflow run list could not be read" in fetched.merged["_unread"][0]


def test_a_real_failure_outranks_an_incomplete_read():
    """Unread withholds a pass; it must not erase a failure already evidenced."""
    good = run_reads.RunRow(run_id=200, head_sha="abc", conclusion="failure")
    bad = run_reads.RunRow(run_id=300, head_sha="abc", conclusion="failure")
    failing = {"databaseId": 200, "number": 5, "headSha": "abc", "status": "completed",
               "conclusion": "failure",
               "jobs": [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    fetched = _merged(
        _discovery(good, bad), run_reads.CommitChecks(answered=True),
        served=lambda rid: failing if rid == 200 else None,
    )
    assert fetched.merged["conclusion"] == "failure"
    assert fetched.merged["_unread"] == ("run 300 could not be read",)


def test_a_cancelled_external_check_is_not_a_pass():
    """`cancelled` was absent from FAILURE_CONCLUSIONS, so it read as green."""
    row = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "cancelled",
                                           source="status_context"),))
    fetched = _merged(_discovery(row), checks)
    assert fetched.merged["conclusion"] == "failure"


def test_an_unrecognised_external_conclusion_is_not_a_pass():
    """Whitelisted: a word GitHub adds to the enum later must not arrive as green."""
    row = run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = run_reads.CommitChecks(
        answered=True, external=(_external("Trivy", "something_new"),))
    fetched = _merged(_discovery(row), checks)
    assert fetched.merged["conclusion"] == "failure"


def test_a_run_with_no_jobs_yet_is_not_finished():
    """A freshly queued run has an empty job list, and "none running" is vacuous.

    Read as finished, the first poll of every wait returned at once with no
    failures — a green that only meant the jobs did not exist yet.
    """
    assert ci_runs.count_job_states({"jobs": []}).finished is False
    assert ci_runs.count_job_states(
        {"jobs": [{"name": "a", "status": "completed", "conclusion": "success"}]},
    ).finished is True
