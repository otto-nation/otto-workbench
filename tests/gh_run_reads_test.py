"""What `gh.run_reads` asks GitHub about a workflow run, and what it does with the answer."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult  # noqa: E402
from gh import run_reads  # noqa: E402


# ── fetch_latest_runs ──────────────────────────────────────────────────


def test_deduplicates_rerun_of_same_workflow():
    """A re-run of the same workflow should supersede the original."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200]


def test_keeps_distinct_workflows():
    """Different workflows for the same commit should all be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200, 201]


def test_rerun_with_multiple_workflows():
    """Re-run of one workflow shouldn't affect other workflows."""
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy"},
        {"databaseId": 100, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [300, 201]


def test_filters_to_latest_sha():
    """Only runs for the latest SHA should be included."""
    runs = [
        {"databaseId": 300, "headSha": "def", "workflowName": "CI"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [300]


def test_empty_run_list():
    with patch("gh.client.json_out", return_value=[]):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == []


def test_filters_skipped_runs():
    """Skipped workflows should be excluded from results."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Dependabot", "conclusion": "skipped"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200]


def test_keeps_cancelled_runs_so_their_failed_jobs_are_seen():
    """Cancelling a run does not un-fail the jobs that had already failed in it."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Old CI", "conclusion": "cancelled"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200, 201]


def test_cancelled_run_does_not_shadow_a_later_real_run():
    """A cancelled run is selected but claims no workflow name.

    Were it to claim one, the older real run of that workflow — the one holding
    the failures worth reporting — would be deduplicated away behind it.
    """
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI", "conclusion": "cancelled"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [300, 200]


def test_a_real_run_does_not_shadow_a_cancelled_run_of_its_workflow():
    """The shape of a re-run after a cancellation: the newer row is the real one.

    The cancelled row is older and still holds the jobs that had failed before
    it was cancelled, so it is selected even though its workflow name was
    already claimed. Testing the name check before the cancelled branch drops
    it, which is the whole defect running the other way round.
    """
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "cancelled"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [300, 200]


def test_skipped_run_does_not_shadow_a_later_real_run():
    """A skipped row is dropped outright, so the next row of that workflow is taken."""
    runs = [
        {"databaseId": 300, "headSha": "abc", "workflowName": "CI", "conclusion": "skipped"},
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": "failure"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200]


def test_all_skipped_returns_empty():
    """When all runs at the latest SHA are skipped, return empty list."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "A", "conclusion": "skipped"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "B", "conclusion": "skipped"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == []


def test_cancelled_run_is_selected_when_it_is_all_there_is():
    """A commit whose only run was cancelled still has a run to report on."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "A", "conclusion": "skipped"},
        {"databaseId": 201, "headSha": "abc", "workflowName": "B", "conclusion": "cancelled"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [201]


def test_in_progress_runs_kept():
    """Runs still in progress (conclusion=None) should be included."""
    runs = [
        {"databaseId": 200, "headSha": "abc", "workflowName": "CI", "conclusion": None},
        {"databaseId": 201, "headSha": "abc", "workflowName": "Deploy", "conclusion": "skipped"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert [r.run_id for r in found.rows] == [200]


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


def test_a_row_carries_the_number_the_dashboard_names_the_run_by():
    """Reduced to an id, a run skipped by the rollup renders as `CI Run #0`."""
    runs = [{"databaseId": 200, "headSha": "abc", "workflowName": "CI",
             "conclusion": "success", "status": "completed", "number": 42}]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert found.rows[0].number == 42
    assert found.rows[0].conclusion == "success"
    assert found.rows[0].as_payload(())["number"] == 42


# ── fetch_commit_checks ───────────────────────────────────────────────────


def _rollup(nodes, more=False, cursor="c1"):
    """A `gh api graphql` result carrying `nodes` as one page of the rollup."""
    payload = {"data": {"repository": {"object": {"statusCheckRollup": {
        "contexts": {
            "pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "nodes": nodes,
        },
    }}}}}
    return CmdResult(0, json.dumps(payload))


def _check_run(name, conclusion="SUCCESS", *, app="github-actions", run_id=900,
               status="COMPLETED", db_id=1):
    return {
        "__typename": "CheckRun", "name": name, "status": status,
        "conclusion": conclusion, "databaseId": db_id, "detailsUrl": "",
        "title": None,
        "checkSuite": {"app": {"slug": app},
                       "workflowRun": {"databaseId": run_id} if run_id else None},
    }


def test_an_app_posted_check_is_reported_where_an_actions_job_is_not():
    """The defect: a CodeQL failure was invisible because discovery was Actions-only."""
    result = _rollup([
        _check_run("Lint", run_id=900, db_id=1),
        _check_run("CodeQL", "FAILURE", app="github-advanced-security", run_id=None, db_id=77),
    ])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert [c["name"] for c in checks.external] == ["CodeQL"]
    assert checks.external[0]["conclusion"] == "failure"
    assert checks.external[0]["_check_source"] == "check_run"
    assert checks.external[0]["databaseId"] == 77


def test_a_status_context_in_error_is_a_failure_not_a_pass():
    """`error` is absent from FAILURE_CONCLUSIONS, so an unmapped one reads as passing."""
    result = _rollup([
        {"__typename": "StatusContext", "context": "scalr/plan", "state": "ERROR",
         "description": "plan errored", "targetUrl": "https://scalr/1"},
    ])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.external[0]["conclusion"] == "failure"
    assert checks.external[0]["status"] == "completed"
    assert checks.external[0]["name"] == "scalr/plan"
    assert checks.external[0]["_check_source"] == "status_context"


def test_a_pending_status_context_is_still_running_not_concluded():
    """Reported complete, a pending external check lets --wait return early."""
    result = _rollup([
        {"__typename": "StatusContext", "context": "scalr/plan", "state": "PENDING",
         "description": "", "targetUrl": ""},
    ])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.external[0]["status"] == "in_progress"
    assert checks.external[0]["conclusion"] == ""


def test_a_commit_with_no_rollup_is_unanswered_rather_than_green():
    """An approval-gated commit has no rollup at all — absence is not a pass."""
    with patch("gh.client.graphql", return_value=CmdResult(0, json.dumps({"data": None}))):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.answered is False
    assert checks.external == ()
    assert checks.green_run_ids() == frozenset()


def test_only_the_runs_the_rollup_accounted_for_can_be_green():
    """A cancelled run is dropped from the rollup; skipping its view would lose its failures.

    The set is pinned exactly rather than probed for an absent id: a `not in`
    against an id the fixture never creates passes whatever the code does.
    """
    result = _rollup([_check_run("Lint", run_id=900), _check_run("Deploy", run_id=902)])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.green_run_ids() == frozenset({900, 902})


def test_a_failed_job_keeps_its_run_out_of_the_green_set():
    """continue-on-error: the run concludes success while a job under it failed."""
    result = _rollup([
        _check_run("Lint", run_id=900, db_id=1),
        _check_run("Flaky", "FAILURE", run_id=900, db_id=2),
    ])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.green_run_ids() == frozenset()
    assert len(checks.actions[900]) == 2


def test_rollup_actions_rows_are_shaped_like_a_fetched_job():
    """They stand in for a job payload that was never fetched, in the same shape."""
    result = _rollup([_check_run("Lint", run_id=900, db_id=5)])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    row = checks.actions[900][0]
    assert row["databaseId"] == 5
    assert row["status"] == "completed"


def test_a_second_page_is_followed_rather_than_cut_off():
    """A failing check at position 101 that nobody listed is a commit reporting green."""
    page1 = _rollup([_check_run("Lint", run_id=900)], more=True)
    page2 = _rollup([_check_run("CodeQL", "FAILURE", app="scanner",
                                run_id=None, db_id=77)])
    with patch("gh.client.graphql", side_effect=[page1, page2]) as gql:
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert [c["name"] for c in checks.external] == ["CodeQL"]
    assert checks.truncated is False
    assert gql.call_args_list[1].kwargs["variables"]["after"] == "c1"


def test_one_page_is_one_call():
    """The common commit must not pay for the pathological one."""
    with patch("gh.client.graphql",
               return_value=_rollup([_check_run("Lint", run_id=900)])) as gql:
        run_reads.fetch_commit_checks("owner/repo", "abc")
    assert gql.call_count == 1


def test_giving_up_on_a_long_rollup_trusts_no_run_to_be_green():
    """Past where we stopped reading, a run's checks are unseen."""
    endless = _rollup([_check_run("Lint", run_id=900)], more=True)
    with patch("gh.client.graphql", return_value=endless) as gql:
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert gql.call_count == run_reads._ROLLUP_MAX_PAGES
    assert checks.truncated is True
    assert checks.green_run_ids() == frozenset()


def test_a_page_that_fails_keeps_what_was_already_read():
    """A later page failing is not the same as there being no rollup at all."""
    page1 = _rollup([_check_run("CodeQL", "FAILURE", app="scanner",
                                run_id=None, db_id=77)], more=True)
    with patch("gh.client.graphql", side_effect=[page1, CmdResult(1)]):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.answered is True
    assert checks.truncated is True
    assert [c["name"] for c in checks.external] == ["CodeQL"]


def test_head_sha_argument_beats_the_newest_row():
    """runs[0] is the newest row, which is not necessarily the commit being asked about."""
    runs = [
        {"databaseId": 300, "headSha": "newer", "workflowName": "CI"},
        {"databaseId": 200, "headSha": "asked", "workflowName": "CI"},
    ]
    with patch("gh.client.json_out", return_value=runs):
        found = run_reads.fetch_latest_runs("owner/repo", "main", head_sha="asked")
    assert [r.run_id for r in found.rows] == [200]


# ── commits_behind_main: counted locally ──────────────────────────────────


def test_a_worktree_is_counted_locally_rather_than_over_the_api():
    """The API call per invocation buys a number the remote-tracking refs hold."""
    with patch("git.topology.default_branch", return_value="main"), \
         patch("git.client.ok", return_value=True), \
         patch("git.client.commits_ahead", return_value=7) as ahead, \
         patch("gh.client.api") as api:
        result = run_reads.commits_behind_main("owner/repo", "feat/auth", cwd="/wt")
    assert result == 7
    api.assert_not_called()
    assert ahead.call_args.kwargs["target_ref"] == "origin/feat/auth"
    assert ahead.call_args.kwargs["rev"] == "origin/main"


def test_the_resolved_trunk_is_counted_against_not_the_literal_main():
    """A master-default repo was asked for a comparison that 404s, read back as zero."""
    with patch("git.topology.default_branch", return_value="master"), \
         patch("git.client.ok", return_value=True), \
         patch("git.client.commits_ahead", return_value=3) as ahead:
        result = run_reads.commits_behind_main("owner/repo", "feat/auth", cwd="/wt")
    assert result == 3
    assert ahead.call_args.kwargs["rev"] == "origin/master"


def test_an_unreadable_ref_falls_back_to_the_api_rather_than_reporting_current():
    """Zero from git means "current with the trunk" — the one wrong answer acted on."""
    with patch("git.topology.default_branch", return_value="main"), \
         patch("git.client.ok", return_value=False), \
         patch("git.client.commits_ahead", return_value=0), \
         patch("gh.client.api", return_value=CmdResult(0, "4\n")) as api:
        result = run_reads.commits_behind_main("owner/repo", "feat/auth", cwd="/wt")
    assert result == 4
    api.assert_called_once()


# passes-at-base: back-compat — a bare repo must still get a real answer, not the silent zero that reads as current with the trunk
def test_without_a_worktree_the_api_is_still_asked():
    """A bare-repo dashboard has no git to count with."""
    with patch("gh.client.api", return_value=CmdResult(0, "9\n")) as api:
        result = run_reads.commits_behind_main("owner/repo", "feat/auth")
    assert result == 9
    api.assert_called_once()


def test_the_trunk_is_never_behind_itself():
    """Comparing the default branch to itself is a call whose answer is always zero."""
    with patch("git.topology.default_branch", return_value="master"), \
         patch("gh.client.api") as api, patch("git.client.commits_ahead") as ahead:
        assert run_reads.commits_behind_main("owner/repo", "master", cwd="/wt") == 0
    api.assert_not_called()
    ahead.assert_not_called()


def test_without_a_worktree_both_spellings_of_the_trunk_are_skipped():
    """The trunk cannot be resolved there, so neither name spends a call to find out."""
    with patch("gh.client.api") as api:
        assert run_reads.commits_behind_main("owner/repo", "main") == 0
        assert run_reads.commits_behind_main("owner/repo", "master") == 0
    api.assert_not_called()


def test_a_commit_github_reports_no_checks_for_is_not_unreadable():
    """An approval-gated run has no rollup; that is a fact, not a failed read."""
    empty = CmdResult(0, json.dumps({"data": {"repository": {"object": None}}}))
    with patch("gh.client.graphql", return_value=empty):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.answered is False
    assert checks.unreadable is False


def test_a_failed_rollup_call_is_unreadable():
    with patch("gh.client.graphql", return_value=CmdResult(1)):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.unreadable is True


def test_graphql_errors_are_unreadable_rather_than_empty():
    """A token without the scope to read checks answers 200 with an errors block.

    Read as "no checks", a permissions problem would hide every external
    check in the repo behind a green report that names nothing.
    """
    denied = CmdResult(0, json.dumps(
        {"data": None, "errors": [{"message": "Resource not accessible"}]}))
    with patch("gh.client.graphql", return_value=denied):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.unreadable is True
    assert checks.answered is False


def test_malformed_json_is_unreadable():
    with patch("gh.client.graphql", return_value=CmdResult(0, "not json")):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.unreadable is True


def test_a_failed_run_listing_is_not_an_empty_one():
    """`gh run list` answers with nothing both when it fails and when there is
    nothing, and those mean opposite things."""
    with patch("gh.client.json_out", return_value=None):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert found.failed is True and found.rows == ()
    with patch("gh.client.json_out", return_value=[]):
        found = run_reads.fetch_latest_runs("owner/repo", "main")
    assert found.failed is False and found.rows == ()


def test_a_cancelled_external_check_speaks_the_failure_vocabulary():
    """`cancelled` is absent from FAILURE_CONCLUSIONS, so it is normalised here."""
    result = _rollup([_check_run("scalr/plan", "CANCELLED", app="scalr",
                                 run_id=None, db_id=7)])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.external[0]["conclusion"] == "failure"
    # What GitHub actually said survives for the reader.
    assert "cancelled" in checks.external[0]["_summary"]


def test_a_green_external_check_is_left_alone():
    # passes-at-base: back-compat — the pass path must survive the normalisation
    result = _rollup([_check_run("CodeQL", "SUCCESS", app="scanner",
                                 run_id=None, db_id=7)])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.external[0]["conclusion"] == "success"


def test_an_unfinished_external_check_is_not_called_a_failure():
    """Still running is not a verdict; only a finished check is judged."""
    result = _rollup([_check_run("CodeQL", None, app="scanner", run_id=None,
                                 db_id=7, status="IN_PROGRESS")])
    with patch("gh.client.graphql", return_value=result):
        checks = run_reads.fetch_commit_checks("owner/repo", "abc")
    assert checks.external[0]["conclusion"] == ""
    assert checks.external[0]["status"] == "in_progress"
