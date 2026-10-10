import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.outcomes  # noqa: E402
import batch.plan  # noqa: E402
import batch.report  # noqa: E402
import review.paths  # noqa: E402
from batch.model import (STEP_ORDER, Decision, DecisionKind, Item, ItemStatus, Run,  # noqa: E402
                         RunStatus, Step, StepRecord)
from pr.comments_fix import CloseoutDebt  # noqa: E402


def _item(n=1, status=ItemStatus.AWAITING_DECISION, **kw):
    return Item(key=f"o/r#{n}", repo="o/r", repo_dir="/r", pr=n, branch=f"b{n}", head_sha="s",
                worktree=f"/wt/b{n}", status=status,
                steps=[StepRecord(s) for s in STEP_ORDER], **kw)


def _run(items, decisions=(), status=RunStatus.WAITING):
    return Run(id="r1", started_at="t", steps=list(STEP_ORDER), pool=1, auto_publish=[],
               status=status, items=list(items), decisions=list(decisions))


def _d(kind, step, payload=None, id="d1", item="o/r#1"):
    return Decision(id=id, item=item, step=step, kind=kind, payload=payload or {})


def _only_next(run):
    [entry] = batch.report.build(run, active=False)["next"]
    return entry


@pytest.fixture(autouse=True)
def _no_debt(monkeypatch):
    monkeypatch.setattr(batch.plan, "closeout_debt", lambda d, b: CloseoutDebt())


def test_terminal_items_are_counted_but_not_listed():
    run = _run([_item(1), _item(2, status=ItemStatus.DONE)],
               [_d(DecisionKind.FAILED, "review", {"reason": "error"})])
    doc = batch.report.build(run, active=False)
    assert [i["pr"] for i in doc["items"]] == ["o/r#1"]
    assert doc["counts"] == {"items": {"awaiting_decision": 1, "done": 1}, "open_decisions": 1}
    assert doc["items"][0]["steps"] == {"rebase": "pending", "ci": "pending",
                                        "comments": "pending", "review": "pending"}


def test_each_open_decision_is_listed_with_copy_paste_commands():
    run = _run([_item(1)], [_d(DecisionKind.COMMENT_ITEM, "comments", {
        "id": "PRRT_1", "outcome": "needs_human", "summary": "rename the flag",
        "reason": "", "file": "a.py", "line": 3, "replyable": True})])
    entry = _only_next(run)
    assert entry["actions"] == ["reply", "settle-addressed", "settle-dismissed",
                                "settle-fixed", "track"]
    assert entry["commands"] == [
        "pr batch resolve r1 d1 --action reply --body-file <path>",
        "pr batch resolve r1 d1 --action settle-addressed",
        "pr batch resolve r1 d1 --action settle-dismissed --reason <text>",
        "pr batch resolve r1 d1 --action settle-fixed [--commit <sha>]",
        "pr batch resolve r1 d1 --action track",
    ]
    assert entry["prep"] == "thread PRRT_1 on a.py:3: rename the flag"
    assert (entry["pr"], entry["worktree"], entry["kind"]) == ("o/r#1", "/wt/b1", "comment_item")


def test_an_active_run_lists_nothing_and_says_to_wait():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review", {"reason": "error"})],
               status=RunStatus.RUNNING)
    doc = batch.report.build(run, active=True)
    assert doc["next"] == []
    assert doc["run"]["active"] is True and doc["run"]["exit_hint"] is None
    assert doc["run"]["hint"] == "a scheduler is driving this run; wait for its run_summary line"


@pytest.mark.parametrize("status", [RunStatus.DONE, RunStatus.CANCELLED])
def test_a_finished_run_offers_no_resolve_commands(status):
    run = _run([_item(1, status=ItemStatus.DROPPED)],
               [_d(DecisionKind.FAILED, "review", {"reason": "error"})], status=status)
    doc = batch.report.build(run, active=False)
    assert doc["next"] == [] and doc["run"]["exit_hint"] == 0


def _owed(d, b):
    return CloseoutDebt(summary=True) if b == "b1" else CloseoutDebt()


def test_a_finished_run_lists_the_pr_command_for_debt_still_owed():
    run = _run([_item(1, status=ItemStatus.DONE), _item(2, status=ItemStatus.DONE)],
               status=RunStatus.DONE)
    doc = batch.report.build(run, active=False, closeout=_owed)
    [entry] = doc["next"]
    assert entry["kind"] == "closeout_owed" and entry["pr"] == "o/r#1"
    assert entry["why"] == "closeout owed: summary"
    assert entry["commands"] == ["pr comments --finish --post --repo-dir /wt/b1"]
    assert (entry["decision"], entry["actions"]) == (None, [])
    assert doc["run"]["exit_hint"] == 0
    assert "pr still owes" in doc["run"]["hint"]
    running = _run([_item(1, status=ItemStatus.DONE), _item(2, status=ItemStatus.DONE)],
                   status=RunStatus.RUNNING)
    assert batch.report.build(running, active=True, closeout=_owed)["next"] == []


def test_an_owed_tracking_issue_quotes_track_all_and_an_unadmitted_item_names_its_branch():
    item = _item(1, status=ItemStatus.DROPPED)
    item.worktree = ""
    run = _run([item], status=RunStatus.CANCELLED)
    doc = batch.report.build(run, active=False,
                             closeout=lambda d, b: CloseoutDebt(deferred_issue=True))
    assert doc["next"][0]["commands"] == [
        "pr comments --finish --post --track-all --repo-dir /r --branch b1"]


def test_a_closed_pr_is_not_listed_as_owing_anything():
    run = _run([_item(1, status=ItemStatus.SKIPPED_CLOSED)], status=RunStatus.DONE)
    assert batch.report.build(run, active=False, closeout=_owed)["next"] == []


def test_a_waiting_run_does_not_list_owed_commands_beside_its_decisions():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review", {"reason": "error"})])
    kinds = [e["kind"] for e in batch.report.build(run, active=False, closeout=_owed)["next"]]
    assert kinds == ["failed"]


def test_a_waiting_run_hints_exit_10_and_resume():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review", {"reason": "error"})])
    view = batch.report.build(run, active=False)["run"]
    assert view["exit_hint"] == 10 and "pr batch resume r1" in view["hint"]


def test_a_failed_decision_says_its_classified_reason():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "comments", {
        "reason": "ai_prompt_failed", "detail": "ai prompt failed", "exit_code": 1,
        "log_tail": ["x"] * 40})])
    entry = _only_next(run)
    assert entry["why"] == batch.outcomes.why("ai_prompt_failed")
    assert entry["payload"] == {"reason": "ai_prompt_failed", "detail": "ai prompt failed",
                                "exit_code": 1}


def test_a_rebase_conflict_names_the_files_to_resolve():
    run = _run([_item(1)], [_d(DecisionKind.REBASE_CONFLICT, "rebase", {
        "status": "conflicts", "files": ["a.py", "b.py"], "remaining_commits": 2})])
    entry = _only_next(run)
    assert entry["prep"] == "resolve a.py, b.py in /wt/b1, then retry"
    assert entry["payload"] == {"files": ["a.py", "b.py"], "remaining_commits": 2}


def test_a_review_decision_points_at_the_review_and_counts_findings():
    findings = [{"severity": "must-fix", "title": "x", "declined": False},
                {"severity": "should-fix", "title": "y", "declined": False},
                {"severity": "should-fix", "title": "z", "declined": True}]
    run = _run([_item(1)], [_d(DecisionKind.STEP_REVIEW, "review", {
        "evidence": [{"kind": "open_findings", "findings": findings}]})])
    entry = _only_next(run)
    path = review.paths.self_review_file_path("o/r", "b1")
    assert entry["prep"] == f"read {path}; finding counts 1/1/0 (must/should/nit), 1 declined"
    assert entry["payload"] == {"evidence": ["open_findings"],
                                "findings": {"must": 1, "should": 1, "nit": 0, "declined": 1}}


def test_a_remote_commit_refusal_names_the_commits_to_check():
    run = _run([_item(1, remote_sha="abc123")], [_d(DecisionKind.FAILED, "publish", {
        "reason": "not_incorporated_remote", "detail": "1 remote commit(s) …",
        "commits": ["c1 fix: elsewhere"], "exit_code": 1, "log_tail": []})])
    entry = _only_next(run)
    assert entry["prep"] == "check c1 fix: elsewhere against abc123"
    assert "force-publish" in entry["actions"]


def test_a_failed_review_points_at_its_log_and_session_log(reviews_dir):
    session = review.paths.self_review_dir("o/r", "b1") / review.paths.FILENAME_SESSION
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")
    item = _item(1)
    item.step(Step.REVIEW).log_path = "/logs/o__r-1-review-0.log"
    run = _run([item], [_d(DecisionKind.FAILED, "review", {
        "reason": "review_orchestration_failed", "exit_code": 1, "log_tail": ["x"]})])
    entry = _only_next(run)
    assert entry["log"] == "/logs/o__r-1-review-0.log"
    assert entry["session_log"] == str(session)
    assert "log_tail" not in entry["payload"]


def test_decision_detail_carries_the_full_payload():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review",
                               {"reason": "error", "exit_code": 1, "log_tail": ["a", "b"]})])
    detail = batch.report.decision_detail(run, "d1")
    assert detail["payload"]["log_tail"] == ["a", "b"]
    assert (detail["decision"], detail["resolution"], detail["step"]) == ("d1", "", "review")


def test_next_view_drops_only_the_items():
    doc = batch.report.build(_run([_item(1)]), active=False)
    assert set(batch.report.next_view(doc)) == {"schema_version", "run", "counts", "next"}


def test_decision_event_carries_only_what_an_agent_acts_on():
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review",
                               {"reason": "ai_prompt_failed", "log_tail": ["x"]})])
    assert batch.report.decision_event(run, run.decisions[0]) == {
        "item": "o/r#1", "decision": "d1", "decision_kind": "failed",
        "why": batch.outcomes.why("ai_prompt_failed"),
        "commands": ["pr batch resolve r1 d1 --action drop-pr",
                     "pr batch resolve r1 d1 --action retry",
                     "pr batch resolve r1 d1 --action skip-step"]}


def test_a_stacked_review_says_which_item_to_wait_for():
    run = _run([_item(1), _item(2)], [_d(DecisionKind.STEP_REVIEW, "review", {
        "evidence": [{"kind": "stacked_on", "item": "o/r#2"}]})])
    entry = _only_next(run)
    assert entry["prep"] == "wait for o/r#2 to finish; `pr batch resume` retries this step"
    assert entry["payload"]["stacked_on"] == ["o/r#2"]


def test_a_run_still_marked_running_exits_failed():
    assert batch.report.exit_code(RunStatus.RUNNING) == 1


_WHY_CASES = [
    (DecisionKind.FAILED, "comments", {"reason": "ai_prompt_failed"},
     "an AI prompt the step depends on failed"),
    (DecisionKind.REBASE_CONFLICT, "rebase", {"files": ["a.py"]},
     "the rebase stopped on conflicts in 1 file(s)"),
    (DecisionKind.REBASE_REFUSED, "rebase", {"detail": "diverged"},
     "pr rebase refused: diverged"),
    (DecisionKind.OPEN_FINDINGS, "review", {}, "the review step needs a person: open_findings"),
    (DecisionKind.STEP_REVIEW, "ci", {"evidence": [{"kind": "ci_unfixed"}]},
     "the ci step needs a person: ci_unfixed"),
    (DecisionKind.DIRTY_WORKTREE, "ci", {"reason": "rebase_in_progress"},
     "a rebase is paused in the worktree"),
    (DecisionKind.COMMENT_ITEM, "comments", {"outcome": "needs_human"},
     "a review comment needs a person (needs_human)"),
    (DecisionKind.INTERRUPTED, "review", {},
     "the review step was interrupted before it finished"),
    (DecisionKind.PUBLISH, "publish", {"drafted": ["review"]},
     "drafted work is ready to publish: review"),
]


def test_every_decision_kind_has_a_why_case():
    assert {case[0] for case in _WHY_CASES} == set(DecisionKind)


@pytest.mark.parametrize(("kind", "step", "payload", "sentence"), _WHY_CASES,
                         ids=[case[0].value for case in _WHY_CASES])
def test_why_says_exactly_why_each_kind_waits(kind, step, payload, sentence):
    assert batch.report.why(_d(kind, step, payload)) == sentence


def test_a_review_decision_without_a_session_file_has_no_session_log(reviews_dir):
    run = _run([_item(1)], [_d(DecisionKind.FAILED, "review", {"reason": "error"})])
    assert _only_next(run)["session_log"] is None


def test_a_closeout_publish_says_what_pr_still_owes():
    run = _run([_item(1, status=ItemStatus.READY_TO_PUBLISH)], [_d(
        DecisionKind.PUBLISH, "publish", {"drafted": [], "track": [], "closeout": "summary"})])
    entry = _only_next(run)
    assert entry["why"] == "pr comments owes this PR a closeout: summary"
    assert entry["payload"] == {"drafted": [], "track": [], "closeout": "summary"}


def test_a_drafted_publish_that_also_owes_a_closeout_names_both():
    run = _run([_item(1, status=ItemStatus.READY_TO_PUBLISH)], [_d(
        DecisionKind.PUBLISH, "publish",
        {"drafted": ["rebase"], "track": [], "closeout": "3 replies"})])
    assert _only_next(run)["why"] == ("drafted work is ready to publish: rebase; "
                                      "pr comments owes this PR a closeout: 3 replies")
