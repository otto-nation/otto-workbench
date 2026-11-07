"""Session commands derived from v1 run state.

Revert targets are named in each test: delete or restore that line in
session_report / scheduler / model and this file must fail.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import DecisionKind, FindingReason, ItemStatus, RunStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.publish import Refusal, TreeState  # noqa: E402
from batch.scheduler import new_run  # noqa: E402
from batch_report_support import SHA, make_decision, make_item, make_run, no_debt  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_JOIN_CHARS = ("cd", "&&", ";")


def _ahead(item):
    return TreeState(local=SHA, remote=item.remote_sha,
                     divergence=RefDivergence(ahead=1, behind=0, comparable=True),
                     checked_out=item.branch)


def _argv(entries):
    return [c.argv for e in entries for c in e.commands]


def test_push_argv_has_full_sha_and_absolute_repo_dir(tmp_path):
    # Revert: pass git.client.abbrev(item.remote_sha) as --expect.
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat")
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=lambda i: _ahead(i))
    assert len(entries) == 1
    push = next(c for c in entries[0].commands if c.push)
    expect = push.argv[push.argv.index("--expect") + 1]
    assert SHA_RE.match(expect)
    repo_dir = push.argv[push.argv.index("--repo-dir") + 1]
    assert Path(repo_dir).is_absolute()
    assert all(tok not in c.argv for c in entries[0].commands for tok in _JOIN_CHARS)
    assert push.argv[0] == "pr"


def test_relative_repo_dir_yields_no_checkout():
    # Revert: os.path.abspath(repo_dir) in _checkout / _repo_args.
    item = make_item(1, worktree="/no/such/worktree", repo_dir="rel-repo", branch="feat")
    debt = CloseoutDebt(summary=True)
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: debt, tree=None)
    assert entries
    assert batch.session_report.NO_CHECKOUT in entries[0].notes
    assert entries[0].commands == []


def test_stack_order_emits_base_before_child():
    # Revert: reverse the ordered list in _order.
    base = make_item(1, status=ItemStatus.AWAITING_DECISION)
    child = make_item(2, status=ItemStatus.AWAITING_DECISION, stacked_on=base.key)
    run = make_run(
        [child, base],
        [make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"}, id="d1",
                       item=base.key),
         make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"}, id="d2",
                       item=child.key)],
        status=RunStatus.INTERRUPTED,
    )
    keys = [e.pr for e in batch.session_report.sessions(run, closeout=no_debt, tree=None)]
    assert keys == [base.key, child.key]
    child_entry = next(e for e in batch.session_report.sessions(run, closeout=no_debt, tree=None)
                       if e.pr == child.key)
    assert child_entry.after == [base.key]


def test_non_draft_note_only_when_pushing_a_public_pr(tmp_path):
    tree = lambda i: _ahead(i)
    public = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), is_draft=False)
    draft = make_item(2, worktree=str(tmp_path), repo_dir=str(tmp_path), is_draft=True)
    note = batch.session_report.COMMENT_BEFORE_PUSH
    public_notes = batch.session_report.sessions(
        make_run([public]), closeout=no_debt, tree=tree)[0].notes
    draft_notes = batch.session_report.sessions(
        make_run([draft]), closeout=no_debt, tree=tree)[0].notes
    assert note in public_notes
    assert note not in draft_notes


def test_publish_refusal_is_push_refused():
    # Revert: map every publish Refusal to failed_unknown.
    item = make_item(1)
    tree = TreeState(local=SHA, remote="b" * 40,
                     divergence=RefDivergence(ahead=1, behind=0, comparable=True),
                     checked_out=item.branch)
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=lambda i: tree)
    reasons = [f.reason for f in entries[0].findings]
    assert FindingReason.PUSH_REFUSED in reasons
    assert entries[0].findings[reasons.index(FindingReason.PUSH_REFUSED)].data["refusal"] == (
        Refusal.REMOTE_MOVED.value)


def test_closeout_command_never_track_all():
    # Revert: use CloseoutDebt.command (adds --track-all when an issue is owed).
    item = make_item(1, worktree="/no/such/wt", repo_dir="/abs/repo")
    debt = CloseoutDebt(summary=True, deferred_issue=True)
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: debt, tree=None)
    argv = entries[0].commands[0].argv
    assert "--track-all" not in argv
    assert argv[:4] == ("pr", "comments", "--finish", "--post")


def test_new_run_copies_is_draft():
    # Revert: drop is_draft=r.is_draft in scheduler.new_run.
    row = PlanRow("o/r", "/r", 1, "t", "b", SHA, True,
                  {Step.REBASE: StepNeed(True, "x")})
    run = new_run([row], steps=[Step.REBASE], selected=None, pool=1, auto_publish=[])
    assert run.items[0].is_draft is True


def test_waiting_and_running_runs_have_no_sessions():
    item = make_item(1, status=ItemStatus.AWAITING_DECISION)
    d = make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"})
    for status in (RunStatus.WAITING, RunStatus.RUNNING):
        run = make_run([item], [d], status=status)
        assert batch.session_report.sessions(run, closeout=no_debt, tree=None) == []


@pytest.mark.parametrize("kind,step,payload,reason", [
    (DecisionKind.REBASE_CONFLICT, "rebase", {"files": ["a.go"]},
     FindingReason.CONFLICT_HANDWRITTEN),
    (DecisionKind.FAILED, "ci", {"reason": "quota_exhausted"},
     FindingReason.BUDGET_EXHAUSTED),
    (DecisionKind.FAILED, "publish", {"reason": Refusal.REMOTE_MOVED.value},
     FindingReason.PUSH_REFUSED),
    (DecisionKind.COMMENT_ITEM, "comments", {"id": "T1"},
     FindingReason.THREADS_NEED_HUMAN),
    (DecisionKind.STEP_REVIEW, "review",
     {"evidence": [{"kind": "open_findings", "findings": [{"severity": "must"}]}]},
     FindingReason.FAILED_UNKNOWN),
    (DecisionKind.STEP_REVIEW, "rebase",
     {"evidence": [{"kind": "stale", "files": ["a.pb.go"]}]},
     FindingReason.STALE_GENERATED),
])
def test_findings_from_decisions_mapping(kind, step, payload, reason):
    item = make_item(1, status=ItemStatus.AWAITING_DECISION)
    run = make_run([item], [make_decision(kind, step, payload)], status=RunStatus.INTERRUPTED)
    found = batch.session_report.findings_from_decisions(run, item)
    assert [f.reason for f in found] == [reason]
