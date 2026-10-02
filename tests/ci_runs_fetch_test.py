"""`fetch_merged`: reading a commit's runs and external checks into one merged verdict."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.run_reads  # noqa: E402
import pr.ci_runs  # noqa: E402


# ── fetch_merged ─────────────────────────────────────────────────────────


def _rows(payloads):
    """The `gh run list` discovery the payloads below would have come from."""
    return gh.run_reads.RunDiscovery(rows=tuple(gh.run_reads.RunRow(
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
               return_value=checks or gh.run_reads.CommitChecks()):
        return pr.ci_runs.fetch_merged("owner/repo", _rows(payloads))


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
    state = pr.ci_runs.parse_run("owner/repo", fetched.merged)
    assert state.failures == {}
    assert state.conclusion != "failure"


def test_all_runs_cancelled_and_jobless_still_merges():
    """With no better leader available, the original order stands."""
    fetched = _fetch_merged([
        {"databaseId": 300, "conclusion": "cancelled", "status": "completed", "jobs": []},
        {"databaseId": 200, "conclusion": "cancelled", "status": "completed", "jobs": []},
    ])
    assert fetched.merged["conclusion"] == "cancelled"


# ── checks that are not Actions jobs ─────────────────────────────────────


def _external(name, conclusion, status="completed", source="check_run", job_id=0):
    """An external check as `run_reads` would have handed it over.

    Put through the same normaliser the real rollup path uses rather than
    hand-rolling the dict: the rule that a finished check failed unless it
    concluded green is applied at that boundary, and a double that restates
    the shape instead of sharing it keeps passing after the code it stands
    for has stopped agreeing with it.
    """
    return gh.run_reads._as_failure_unless_green(
        {"name": name, "databaseId": job_id, "status": status, "conclusion": conclusion,
         "steps": [], "_check_source": source, "_details_url": "", "_summary": ""})


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
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "failure", job_id=77),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"
    assert [j["name"] for j in fetched.merged["jobs"]] == ["test", "CodeQL"]
    codeql = next(j for j in fetched.merged["jobs"] if j["name"] == "CodeQL")
    assert codeql["databaseId"] == 77


def test_a_status_context_failure_turns_a_green_commit_red():
    checks = gh.run_reads.CommitChecks(
        answered=True,
        external=(_external("scalr/plan", "failure", source="status_context"),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"


def test_an_unfinished_external_check_leaves_the_commit_undecided():
    """Reported complete, a running external check lets --wait stop early."""
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "", status="in_progress"),),
    )
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["status"] == "in_progress"
    assert fetched.merged["conclusion"] == ""


def test_an_external_failure_outranks_an_unfinished_one():
    """A failure stands whatever else is still going — merge_runs' own rule."""
    checks = gh.run_reads.CommitChecks(answered=True, external=(
        _external("CodeQL", "failure"),
        _external("scalr/plan", "", status="in_progress", source="status_context"),
    ))
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    assert fetched.merged["conclusion"] == "failure"


def test_external_checks_are_counted_among_the_jobs():
    """--wait reports N/M; an external check nobody counted makes the run look shorter."""
    checks = gh.run_reads.CommitChecks(answered=True, external=(
        _external("CodeQL", "failure"),
        _external("scalr/plan", "", status="in_progress", source="status_context"),
    ))
    fetched = _fetch_merged([dict(_PASSING_RUN)], checks=checks)
    counts = pr.ci_runs.count_job_states(fetched.merged)
    assert counts.total == 3
    assert counts.failed == 1
    assert counts.running == 1
    assert counts.finished is False


def test_a_commit_whose_only_checks_are_external_still_reports():
    """A repo can check a commit without running a workflow on it."""
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "failure", source="status_context"),),
    )
    with patch("gh.run_reads.fetch_run_data", return_value=None), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(), head_sha="abc")
    assert fetched is not None
    assert fetched.merged["conclusion"] == "failure"
    assert [j["name"] for j in fetched.merged["jobs"]] == ["scalr/plan"]


def test_nothing_at_all_is_still_nothing_to_report():
    with patch("gh.run_reads.fetch_commit_checks", return_value=gh.run_reads.CommitChecks()):
        assert pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(), head_sha="abc") is None


# ── runs the rollup spares us fetching ───────────────────────────────────


def test_a_run_the_rollup_proved_green_is_not_fetched():
    """Its job payload holds only the steps of jobs that did not fail."""
    checks = gh.run_reads.CommitChecks(
        answered=True, actions=_green_run(200, "test", "lint"),
    )
    rows = [gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc",
                             status="completed", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data") as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    view.assert_not_called()
    assert [j["name"] for j in fetched.merged["jobs"]] == ["test", "lint"]
    assert fetched.merged["number"] == 5
    assert fetched.merged["conclusion"] == "success"
    assert pr.ci_runs.count_job_states(fetched.merged).total == 2


def test_a_run_the_rollup_did_not_account_for_is_still_fetched():
    """A cancelled run is absent from the rollup while its failed jobs live on."""
    cancelled = {"databaseId": 300, "conclusion": "cancelled", "status": "completed",
                 "jobs": [{"name": "build", "status": "completed", "conclusion": "failure"}]}
    checks = gh.run_reads.CommitChecks(answered=True, actions=_green_run(200, "test"))
    rows = [gh.run_reads.RunRow(run_id=300, head_sha="abc", conclusion="cancelled"),
            gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data", return_value=cancelled) as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    assert [c.args[1] for c in view.call_args_list] == [300]
    # What skipping it would have cost: the failed job itself. The merged
    # conclusion stays `cancelled` either way — `cancelled` is not a failure
    # conclusion, which is why the job list is the thing worth asserting on.
    assert [j["name"] for j in pr.ci_runs.failed_jobs(fetched.merged)] == ["build"]


def test_an_unanswered_rollup_fetches_every_run():
    """No rollup is no evidence — an approval-gated commit has none at all."""
    checks = gh.run_reads.CommitChecks(answered=False)
    rows = [gh.run_reads.RunRow(run_id=200, head_sha="abc", conclusion="success"),
            gh.run_reads.RunRow(run_id=300, head_sha="abc", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data",
               side_effect=lambda repo, rid: {"databaseId": rid, "conclusion": "success",
                                              "status": "completed", "jobs": []}) as view, \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(rows=tuple(rows)), head_sha="abc")
    assert sorted(c.args[1] for c in view.call_args_list) == [200, 300]


def test_an_unpushed_head_retries_at_the_commit_the_runs_ran_on():
    """A local HEAD the API never saw answers nothing, which is not `no checks`."""
    external = (_external("CodeQL", "failure"),)
    answers = {"local": gh.run_reads.CommitChecks(answered=False),
               "pushed": gh.run_reads.CommitChecks(answered=True, external=external)}
    rows = [gh.run_reads.RunRow(run_id=200, number=5, head_sha="pushed", conclusion="success")]
    with patch("gh.run_reads.fetch_run_data", return_value=dict(_PASSING_RUN)), \
         patch("gh.run_reads.fetch_commit_checks",
               side_effect=lambda repo, sha: answers[sha]) as rollup:
        fetched = pr.ci_runs.fetch_merged("owner/repo", gh.run_reads.RunDiscovery(rows=tuple(rows)), head_sha="local")
    assert [c.args[1] for c in rollup.call_args_list] == ["local", "pushed"]
    assert fetched.merged["conclusion"] == "failure"


# ── knowledge that is incomplete must not read as a pass ──────────────────


def _discovery(*rows, failed=False):
    return gh.run_reads.RunDiscovery(rows=rows, failed=failed)


def _green_payload(run_id=200):
    return {"databaseId": run_id, "number": 5, "headSha": "abc", "status": "completed",
            "conclusion": "success",
            "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]}


def _merged(discovery, checks, served=_green_payload):
    fetch = served if callable(served) else (lambda repo, rid: served)
    with patch("gh.run_reads.fetch_run_data",
               side_effect=lambda repo, rid: fetch(rid) if callable(served) else served), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        return pr.ci_runs.fetch_merged("owner/repo", discovery, head_sha="abc")


def test_an_unreadable_rollup_withholds_the_pass():
    """Not knowing what the external checks said is not the same as them passing."""
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), gh.run_reads.CommitChecks(unreadable=True))
    assert fetched.merged["conclusion"] == ""
    assert fetched.merged["_unread"] == ("the commit's check rollup could not be read",)


def test_a_commit_with_genuinely_no_rollup_still_passes():
    """GitHub reporting no checks is a fact about the commit, not a failed read.

    An approval-gated run has no rollup at all, and treating that as unread
    would report every held workflow as unknown.
    """
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), gh.run_reads.CommitChecks(answered=False))
    assert fetched.merged["conclusion"] == "success"
    assert "_unread" not in fetched.merged


def test_a_truncated_rollup_withholds_the_pass():
    """The checks past the page could be the failing ones."""
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    fetched = _merged(_discovery(row), gh.run_reads.CommitChecks(answered=True, truncated=True))
    assert fetched.merged["conclusion"] == ""
    assert "more checks than were listed" in fetched.merged["_unread"][0]


def test_a_run_nobody_could_read_withholds_the_pass():
    """The run was dropped from the payloads; its verdict must not be assumed."""
    good = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    bad = gh.run_reads.RunRow(run_id=300, head_sha="abc", conclusion="failure")
    fetched = _merged(
        _discovery(good, bad), gh.run_reads.CommitChecks(answered=True),
        served=lambda rid: _green_payload() if rid == 200 else None,
    )
    assert fetched.merged["conclusion"] == ""
    assert fetched.merged["_unread"] == ("run 300 could not be read",)


def test_a_failed_run_listing_withholds_the_pass():
    """An empty run list from a failed call is an absence of facts, not a green commit."""
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("CodeQL", "success"),))
    with patch("gh.run_reads.fetch_run_data", return_value=None), \
         patch("gh.run_reads.fetch_commit_checks", return_value=checks):
        fetched = pr.ci_runs.fetch_merged(
            "owner/repo", gh.run_reads.RunDiscovery(failed=True), head_sha="abc")
    assert fetched.merged["conclusion"] == ""
    assert "workflow run list could not be read" in fetched.merged["_unread"][0]


def test_a_real_failure_outranks_an_incomplete_read():
    """Unread withholds a pass; it must not erase a failure already evidenced."""
    good = gh.run_reads.RunRow(run_id=200, head_sha="abc", conclusion="failure")
    bad = gh.run_reads.RunRow(run_id=300, head_sha="abc", conclusion="failure")
    failing = {"databaseId": 200, "number": 5, "headSha": "abc", "status": "completed",
               "conclusion": "failure",
               "jobs": [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    fetched = _merged(
        _discovery(good, bad), gh.run_reads.CommitChecks(answered=True),
        served=lambda rid: failing if rid == 200 else None,
    )
    assert fetched.merged["conclusion"] == "failure"
    assert fetched.merged["_unread"] == ("run 300 could not be read",)


def test_a_cancelled_external_check_is_not_a_pass():
    """`cancelled` was absent from FAILURE_CONCLUSIONS, so it read as green."""
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "cancelled",
                                           source="status_context"),))
    fetched = _merged(_discovery(row), checks)
    assert fetched.merged["conclusion"] == "failure"


def test_an_unrecognised_external_conclusion_is_not_a_pass():
    """Whitelisted: a word GitHub adds to the enum later must not arrive as green."""
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("Trivy", "something_new"),))
    fetched = _merged(_discovery(row), checks)
    assert fetched.merged["conclusion"] == "failure"


def test_a_run_with_no_jobs_yet_is_not_finished():
    """A freshly queued run has an empty job list, and "none running" is vacuous.

    Read as finished, the first poll of every wait returned at once with no
    failures — a green that only meant the jobs did not exist yet.
    """
    assert pr.ci_runs.count_job_states({"jobs": []}).finished is False
    assert pr.ci_runs.count_job_states(
        {"jobs": [{"name": "a", "status": "completed", "conclusion": "success"}]},
    ).finished is True


def test_a_cancelled_external_check_survives_an_unrelated_unread_reason():
    """One vocabulary: the verdict and the job list must agree about `cancelled`.

    `_apply_external` called it a failure while `failed_jobs` — which asks
    the blacklist `cancelled` is missing from — saw no failed job. Any unread
    reason then put `_mark_unread` through `_claims_failure`, which re-derived
    the verdict from that empty list and cleared the failure again.
    """
    good = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    bad = gh.run_reads.RunRow(run_id=300, head_sha="abc", conclusion="success")
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "cancelled",
                                           source="status_context"),))
    fetched = _merged(
        _discovery(good, bad), checks,
        served=lambda rid: _green_payload() if rid == 200 else None,
    )
    assert fetched.merged["conclusion"] == "failure"
    assert fetched.merged["_unread"] == ("run 300 could not be read",)


def test_a_cancelled_external_check_becomes_a_named_failure():
    """A verdict naming nothing is not actionable — the check must reach the report."""
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "cancelled",
                                           source="status_context"),))
    fetched = _merged(_discovery(row), checks)
    assert [j["name"] for j in pr.ci_runs.failed_jobs(fetched.merged)] == ["scalr/plan"]


def test_a_cancelled_external_check_reaches_every_reader_of_a_conclusion():
    """One definition means the job counts agree with the verdict and the list.

    `count_job_states` asks `FAILURE_CONCLUSIONS` like everything else, so a
    check normalised at the boundary is counted without that counter having
    to learn the rule. Asserted because it is the reader furthest from the
    normalisation and the one no earlier test covered.
    """
    row = gh.run_reads.RunRow(run_id=200, number=5, head_sha="abc", conclusion="success")
    checks = gh.run_reads.CommitChecks(
        answered=True, external=(_external("scalr/plan", "cancelled",
                                           source="status_context"),))
    fetched = _merged(_discovery(row), checks)
    counts = pr.ci_runs.count_job_states(fetched.merged)
    assert counts.failed == 1
    assert fetched.merged["conclusion"] == "failure"
    assert [j["name"] for j in pr.ci_runs.failed_jobs(fetched.merged)] == ["scalr/plan"]
