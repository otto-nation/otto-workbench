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

    with patch("gh.run_reads.fetch_latest_runs", side_effect=[run_reads.RunDiscovery(rows=(_row(100),))] * 2), \
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

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100),))), \
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

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100),))), \
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

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100),))), \
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

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100),))), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(timeout=0)

    assert "timeout" in capsys.readouterr().err.lower()
    assert result.merged["status"] == "in_progress"
    assert result.counts.running == 1


def test_poll_raises_when_the_branch_has_no_runs():
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery()):
        with pytest.raises(ci_runs.RunUnavailable, match="feat/test"):
            _poll(trail=trail)
    trail.warn.assert_called_once()
    assert trail.warn.call_args[0][0] == "no_runs"


def test_poll_retries_then_gives_up_when_no_run_data_comes_back():
    """A read that returned nothing is retried before the wait is abandoned.

    Every other transient condition in this loop is retried; a single flaky
    `gh` call should not end a fifteen-minute wait. The retry is bounded, so
    a cause that is not transient costs a couple of intervals rather than the
    caller's whole budget.
    """
    trail = MagicMock()
    with patch("gh.run_reads.fetch_latest_runs",
               return_value=run_reads.RunDiscovery(rows=(_row(100),))), \
         patch("gh.run_reads.fetch_run_data", return_value=None) as view:
        with pytest.raises(ci_runs.RunUnavailable, match="Gave up polling"):
            _poll(trail=trail)
    assert view.call_count == 3, "two retries, then the give-up"
    trail.error.assert_called_once()
    assert trail.error.call_args[0][0] == "fetch_run_data"


def test_a_failed_run_listing_mid_wait_is_retried_not_reported_as_no_checks():
    """`gh run list` failing is not the commit having no checks, and the loop
    exists to outlast exactly this."""
    good = run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),))
    discoveries = iter((run_reads.RunDiscovery(failed=True), good))
    done = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", side_effect=lambda *a, **k: next(discoveries)), \
         patch("gh.run_reads.fetch_run_data", return_value=done), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(head_sha="abc123")

    assert result.merged["conclusion"] == "success"


def test_an_unread_poll_is_retried_before_the_wait_reports_it():
    """Everything has settled but one read failed — spend a pass re-reading it."""
    done = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
    ])
    rollups = iter((
        run_reads.CommitChecks(unreadable=True),
        run_reads.CommitChecks(answered=True),
    ))

    with patch("gh.run_reads.fetch_latest_runs",
               return_value=run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),))), \
         patch("gh.run_reads.fetch_run_data", return_value=done), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: next(rollups)) as rollup, \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(head_sha="abc123")

    assert rollup.call_count == 2
    assert result.merged.get("_unread", ()) == ()
    assert result.merged["conclusion"] == "success"


def test_a_persistently_unread_poll_still_finishes():
    """A token that can never read the rollup must not burn the whole timeout."""
    done = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs",
               return_value=run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),))), \
         patch("gh.run_reads.fetch_run_data", return_value=done), \
         patch("gh.run_reads.fetch_commit_checks",
               return_value=run_reads.CommitChecks(unreadable=True)) as rollup, \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(head_sha="abc123")

    assert rollup.call_count == 3, "two retries, then report what is known"
    assert result.merged["_unread"] != ()
    assert result.merged["conclusion"] == ""


def test_poll_re_resolves_run_ids_unless_one_is_pinned():
    """`--run` pins the id; without it a later push's run is picked up."""
    run_data = _run("completed", "success", [
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100),))) as fetch_ids, \
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

    discovered = run_reads.RunDiscovery(rows=(_row(100), _row(200)))
    with patch("gh.run_reads.fetch_latest_runs", return_value=discovered), \
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

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100), _row(200)))), \
         patch("gh.run_reads.fetch_run_data", side_effect=serve), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll()

    assert sorted(j["name"] for j in result.merged["jobs"]) == ["Lint", "Test"]
    assert result.counts.completed == 2


def test_a_real_head_sha_reaches_the_rollup_short_circuit():
    """The `_no_rollup` fixture stands for an unanswered rollup, but only once
    `_commit_checks` is given a real sha to ask about. Every other test in this
    file leaves rows at the default empty `head_sha` and never passes one to
    `_poll`, so `_commit_checks`'s `if not sha: return CommitChecks()` fires
    before `fetch_commit_checks` is ever called — the fixture's mock goes
    unexercised there. This is the one case that reaches past that short
    circuit, so a future change to its condition fails a test here instead of
    nothing at all.
    """
    run_data = _run("completed", "success", [
        {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
    ])

    with patch("gh.run_reads.fetch_latest_runs", return_value=run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),))), \
         patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.fetch_commit_checks",
               return_value=run_reads.CommitChecks()) as fetch_checks, \
         patch("pr.ci_wait.time.sleep"):
        _poll(head_sha="abc123")

    fetch_checks.assert_called_once_with("owner/repo", "abc123")


def test_a_pinned_run_asks_its_rollup_at_its_own_commit():
    """`run_id` names a specific run; the rollup asked about it must be for
    that run's own commit, not whatever the branch head currently is.

    Regression test for the defect where `head_sha` (the branch's current
    head) was passed to `fetch_merged` unconditionally even when `run_id`
    pinned a run for a different, historical commit — letting an external
    check answered at the *current* head get merged into a report about the
    pinned run, silently attributing another commit's verdict to it.
    """
    run_data = _run("completed", "success", [])
    by_sha = {
        "currenthead": run_reads.CommitChecks(
            answered=True,
            external=({"name": "CodeQL", "databaseId": 0, "status": "completed",
                       "conclusion": "failure", "steps": [],
                       "_check_source": "check_run"},)),
        "abc123": run_reads.CommitChecks(answered=True),
    }

    with patch("gh.run_reads.fetch_run_data", return_value=run_data), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: by_sha[sha]) as fetch_checks, \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(run_id=555, head_sha="currenthead")

    # `_run` builds its payload at abc123 — the commit the pinned run ran on.
    # Never queried at "currenthead": that would be the regression this test
    # guards against — see the docstring above.
    assert [c.args[1] for c in fetch_checks.call_args_list] == ["abc123"]
    assert result.merged["conclusion"] == "success"


def test_a_rollup_with_a_check_still_running_is_asked_again():
    """The other half of the cache's rule, and the half that can go wrong.

    Holding an unsettled rollup would freeze an external check at whatever it
    said on the first poll — a CodeQL run still going would stay "running"
    for the life of the wait and could never be reported when it failed.
    Only a rollup with nothing left in flight may be reused.
    """
    in_progress = _run("in_progress", "", [
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])
    done = _run("completed", "success", [
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])
    run_payloads = iter((in_progress, done))
    running_check = {"name": "CodeQL", "databaseId": 99, "status": "in_progress",
                     "conclusion": "", "steps": [], "_check_source": "check_run"}
    failed_check = {**running_check, "status": "completed", "conclusion": "failure"}
    rollups = iter((
        run_reads.CommitChecks(answered=True, external=(running_check,)),
        run_reads.CommitChecks(answered=True, external=(failed_check,)),
    ))

    with patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(run_payloads)), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: next(rollups)) as fetch_checks, \
         patch("gh.run_reads.fetch_annotations", return_value=[]) as fetch_annotations, \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(run_id=555, head_sha="currenthead")

    # The failed check's own job id is a real id (unlike the running check
    # that preceded it), so the external-failure path actually reaches
    # `fetch_annotations` for it rather than short-circuiting to `[]`.
    fetch_annotations.assert_called_once_with("owner/repo", 99)

    assert [c.args[1] for c in fetch_checks.call_args_list] == ["abc123", "abc123"]
    assert result.merged["conclusion"] == "failure"


def test_a_truncated_rollup_is_not_cached_even_when_every_check_is_completed():
    """A truncated rollup must not be cached just because every check it did
    read is `completed` — the page it never reached could hold a check that
    never got read at all, completed or not.

    Regression test for the gap where `_checks_settled` cached on `answered`
    and the external jobs it had without checking `truncated`, so a rollup cut
    short by a transient page failure got treated as settled and a check on an
    unread page was never surfaced for the rest of the wait.
    """
    in_progress = _run("in_progress", "", [
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])
    done = _run("completed", "success", [
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])
    run_payloads = iter((in_progress, done))
    completed_check = {"name": "CodeQL", "databaseId": 0, "status": "completed",
                       "conclusion": "success", "steps": [], "_check_source": "check_run"}
    failed_check = {**completed_check, "conclusion": "failure"}
    rollups = iter((
        run_reads.CommitChecks(answered=True, truncated=True, external=(completed_check,)),
        run_reads.CommitChecks(answered=True, truncated=False, external=(failed_check,)),
    ))

    with patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(run_payloads)), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: next(rollups)) as fetch_checks, \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(run_id=555, head_sha="currenthead")

    # The truncated first rollup must be re-asked on the second poll rather
    # than served from cache — if it were cached, the second poll would reuse
    # the "success" verdict and never see the check an unread page flipped.
    assert [c.args[1] for c in fetch_checks.call_args_list] == ["abc123", "abc123"]
    assert result.merged["conclusion"] == "failure"


def test_the_rollup_is_re_read_every_poll():
    """A settled rollup is not safe to carry forward, unlike a run attempt.

    A run attempt is immutable once concluded and a re-run is a new attempt,
    so the payload cache can key on that and be right. A commit's rollup has
    no such identity: a status context can be re-posted at the same commit
    and a check run re-requested, with nothing in the answer to say it
    changed. Caching it was tried and bought one call per poll at the price
    of a verdict that could go stale mid-wait, which is the trade this whole
    branch exists to refuse.
    """
    in_progress = _run("in_progress", "", [
        {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
    ])
    done = _run("completed", "success", [
        {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
    ])
    payloads = iter((in_progress, done))
    settled = run_reads.CommitChecks(answered=True)

    with patch("gh.run_reads.fetch_latest_runs",
               return_value=run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),))), \
         patch("gh.run_reads.fetch_run_data", side_effect=lambda repo, rid: next(payloads)), \
         patch("gh.run_reads.fetch_commit_checks", return_value=settled) as rollup, \
         patch("pr.ci_wait.time.sleep"):
        _poll(head_sha="abc123")

    assert [c.args[1] for c in rollup.call_args_list] == ["abc123", "abc123"]


def test_a_re_run_is_not_served_from_the_previous_attempts_payload():
    """Re-running a workflow reuses the run id and increments the attempt.

    Keyed on the id alone, a run that passed, was re-run, and failed would be
    answered out of the cache as the pass it used to be for the rest of the
    wait — a green verdict over a red run, from the optimisation meant to be
    free.
    """
    passed = {"databaseId": 100, "number": 1, "headSha": "abc123", "status": "completed",
              "conclusion": "success",
              "jobs": [{"name": "Lint", "conclusion": "success",
                        "databaseId": 10, "status": "completed"}]}
    failed = {**passed, "conclusion": "failure",
              "jobs": [{"name": "Lint", "conclusion": "failure",
                        "databaseId": 10, "status": "completed"}]}
    served = iter((passed, failed))
    # Poll 1 sees attempt 1 alongside a run still going; poll 2 sees the
    # re-run as attempt 2, and everything finished.
    discoveries = iter((
        run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123"),
                                     _row(200, head_sha="abc123"))),
        run_reads.RunDiscovery(rows=(_row(100, head_sha="abc123", attempt=2),)),
    ))
    running = {"databaseId": 200, "number": 2, "headSha": "abc123",
               "status": "in_progress", "conclusion": None,
               "jobs": [{"name": "Test", "conclusion": None,
                         "databaseId": 20, "status": "in_progress"}]}

    def serve(repo, rid):
        return next(served) if rid == 100 else running

    with patch("gh.run_reads.fetch_latest_runs", side_effect=lambda *a, **k: next(discoveries)), \
         patch("gh.run_reads.fetch_run_data", side_effect=serve) as view, \
         patch("gh.run_reads.fetch_annotations", return_value=[]), \
         patch("pr.ci_annotations.log_fallback",
               return_value=_no_log_fallback(ci.FailureKind.LINT)), \
         patch("pr.ci_wait.time.sleep"):
        result = _poll(head_sha="abc123")

    assert [c.args[1] for c in view.call_args_list].count(100) == 2
    assert result.merged["conclusion"] == "failure"
