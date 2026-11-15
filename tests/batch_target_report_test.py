"""`batch.target_report`: one target's findings and commands, in the sessions[] shape.

Revert: in findings(), filter state.fix.fix.items on NEEDS_HUMAN instead of
calling pr.needs_person.owed_threads; the declined-thread test fails.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.target_report  # noqa: E402
from batch.session_report import NO_CHECKOUT  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr_target_support import CI_SHA, state_with  # noqa: E402

DECLINED = ItemOutcome(id="PRRT_1", outcome=FixOutcome.DECLINED, summary="s", reason="r",
                       file="a.py", line=2)


def test_every_finding_with_its_command():
    state = state_with(items=[DECLINED], jobs=["test", "lint"], closeout=True)
    doc = batch.target_report.report(state, "/wt")
    assert doc["schema_version"] == 1
    [entry] = doc["sessions"]
    thread = {"id": "PRRT_1", "outcome": "declined", "summary": "s", "reason": "r",
              "file": "a.py", "line": 2, "replyable": True}
    assert entry == {
        "pr": "o/r#7", "url": "https://github.com/o/r/pull/7", "branch": "feat/x",
        "base_ref": "", "after": [], "is_draft": False, "state": "needs_session",
        "severity": "act", "worktree": "/wt", "remote_sha": "", "local_sha": "",
        "findings": [
            {"reason": "threads_need_human", "step": "comments", "detail": "1 open: PRRT_1",
             "files": [], "data": {"threads": [thread]}, "log": None},
            {"reason": "ci_failing", "step": "ci", "detail": f"failing at {CI_SHA[:12]}",
             "files": [], "data": {"jobs": ["lint", "test"]}, "log": None},
            {"reason": "closeout_owed", "step": "comments",
             "detail": "closeout owed per pr state", "files": [], "data": {}, "log": None},
        ],
        "commands": [
            {"argv": ["pr", "comments", "--fix", "--repo-dir", "/wt"],
             "why": "address review comments", "action": "comments"},
            {"argv": ["pr", "ci", "--fix", "--repo-dir", "/wt"],
             "why": "fix failing CI (lint, test)", "action": "ci"},
            {"argv": ["pr", "comments", "--finish", "--post", "--repo-dir", "/wt"],
             "why": "closeout owed per pr state", "action": "closeout"},
        ],
        "notes": [batch.target_report.SETTLE_NOTE],
        "logs": [],
    }


def test_the_settle_note_names_the_settle_verdicts():
    assert batch.target_report.SETTLE_NOTE == (
        "settle one thread with pr comments --settle ID --as fixed|dismissed|already_addressed")


def test_no_worktree_means_no_commands_and_says_so():
    [entry] = batch.target_report.report(state_with(items=[DECLINED]), "")["sessions"]
    assert entry["commands"] == []
    assert entry["notes"] == [NO_CHECKOUT, batch.target_report.SETTLE_NOTE]


def test_nothing_to_show_is_an_empty_sessions_list():
    settled = ItemOutcome(id="PRRT_2", outcome=FixOutcome.FIXED)
    assert batch.target_report.report(state_with(items=[settled]), "/wt")["sessions"] == []
    assert batch.target_report.report(state_with(jobs=["t"], pr_number=None), "/wt")["sessions"] == []
    assert batch.target_report.report(None, "/wt") == {"schema_version": 1, "sessions": []}


def test_the_finding_keys_and_the_pr_key():
    assert batch.target_report.FINDING_KEYS == frozenset(
        {"threads_need_human:comments", "ci_failing:ci", "closeout_owed:comments"})
    assert batch.target_report.pr_key_of(state_with()) == "o/r#7"
    assert batch.target_report.pr_key_of(state_with(pr_number=None)) is None
    assert batch.target_report.pr_key_of(None) is None
