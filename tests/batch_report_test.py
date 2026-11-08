"""The compact report after the decision surface is gone."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.report  # noqa: E402
from batch.model import (STEP_ORDER, Item, ItemStatus, Run,  # noqa: E402
                         RunStatus, StepRecord)
from batch.store import FailedRequest  # noqa: E402


def _item(n=1, status=ItemStatus.NEEDS_SESSION, **kw):
    return Item(key=f"o/r#{n}", repo="o/r", repo_dir="/r", pr=n, branch=f"b{n}", head_sha="s",
                worktree=f"/wt/b{n}", status=status,
                steps=[StepRecord(s) for s in STEP_ORDER], **kw)


def _run(items, status=RunStatus.DONE):
    return Run(id="r1", started_at="t", steps=list(STEP_ORDER), pool=1, auto_publish=[],
               status=status, items=list(items))


def test_terminal_items_are_counted_but_not_listed():
    run = _run([_item(1, status=ItemStatus.QUEUED), _item(2, status=ItemStatus.DONE)],
               status=RunStatus.RUNNING)
    doc = batch.report.build(run, active=False)
    assert [i["pr"] for i in doc["items"]] == ["o/r#1"]
    assert doc["counts"] == {"items": {"queued": 1, "done": 1}}
    assert "open_decisions" not in doc["counts"]
    assert "next" not in doc
    assert doc["items"][0]["steps"] == {"rebase": "pending", "ci": "pending",
                                        "comments": "pending", "review": "pending"}


def test_an_active_run_says_to_wait():
    run = _run([_item(1, status=ItemStatus.RUNNING)], status=RunStatus.RUNNING)
    doc = batch.report.build(run, active=True)
    assert "next" not in doc
    assert doc["run"]["active"] is True and doc["run"]["exit_hint"] is None
    assert doc["run"]["hint"] == "a scheduler is driving this run; wait for its run_summary line"


_QUIET_GITHUB = {"checked": True, "owed": [], "untracked": [], "moved": []}


def test_a_finished_run_whose_prs_github_shows_owing_work_says_so():
    run = _run([_item(1, status=ItemStatus.DONE)], status=RunStatus.DONE)
    github = {**_QUIET_GITHUB, "owed": [{"pr": "o/r#1"}, {"pr": "o/r#2"}],
              "untracked": [{"pr": "o/r#2"}]}
    hint = batch.report.build(run, active=False, github=github)["run"]["hint"]
    assert hint == ("the run is done; GitHub shows 2 open PRs with work owed; "
                    "1 open PR this run never saw — see `github`, and start a new "
                    "`pr batch run`")


def test_a_finished_run_github_shows_nothing_for_has_nothing_left():
    run = _run([_item(1, status=ItemStatus.DONE)], status=RunStatus.DONE)
    hint = batch.report.build(run, active=False, github=_QUIET_GITHUB)["run"]["hint"]
    assert hint == "the run is done; nothing is left to answer"


def test_an_unfinished_run_points_at_resume_and_the_report():
    run = _run([_item(1, status=ItemStatus.QUEUED)], status=RunStatus.INTERRUPTED)
    hint = batch.report.build(run, active=False)["run"]["hint"]
    assert "pr batch resume r1" in hint
    assert "pr batch report" in hint


def test_the_run_section_reports_what_the_inbox_holds_and_what_it_rejected():
    doc = batch.report.build(_run([_item(1)]), active=False, pending_requests=2,
                             failed_requests=[FailedRequest("a.json", "no decision d9")])
    assert doc["run"]["pending_requests"] == 2
    assert doc["run"]["failed_requests"] == [{"name": "a.json", "error": "no decision d9"}]


def test_a_report_built_without_inbox_counts_says_nothing_is_waiting():
    run_view = batch.report.build(_run([_item(1)]), active=False)["run"]
    assert (run_view["pending_requests"], run_view["failed_requests"]) == (0, [])


def test_a_run_still_marked_running_exits_failed():
    assert batch.report.exit_code(RunStatus.RUNNING) == 1


def test_a_waiting_v1_run_exits_failed_not_10():
    assert batch.report.exit_code(RunStatus.WAITING) == 1


def test_schema_version_is_2():
    doc = batch.report.build(_run([_item(1, status=ItemStatus.DONE)]), active=False)
    assert doc["schema_version"] == 2
