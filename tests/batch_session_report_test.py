"""Session commands derived from v1 run state.

Revert targets are named in each test: delete or restore that line in
session_report / scheduler / model and this file must fail.
"""

import re
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import (DecisionKind, Finding, FindingReason, ItemStatus,  # noqa: E402
                         RunStatus, Step)
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
    assert push.action == "push"
    assert push.to_dict()["action"] == "push"


def test_relative_repo_dir_yields_no_checkout():
    # Revert: os.path.abspath(repo_dir) in _checkout / _repo_args.
    item = make_item(1, worktree="/no/such/worktree", repo_dir="rel-repo", branch="feat")
    debt = CloseoutDebt(summary=True)
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: debt, tree=None)
    argv = [c.argv for e in entries for c in e.commands]
    assert not any("rel-repo" in a for a in argv)
    assert entries == []


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


def test_sessions_order_holds_when_items_are_built_in_parallel():
    # Revert: skip _order (workers finish child before base).
    low = make_item(3, status=ItemStatus.AWAITING_DECISION)
    base = make_item(1, status=ItemStatus.AWAITING_DECISION)
    child = make_item(2, status=ItemStatus.AWAITING_DECISION, stacked_on=base.key)
    run = make_run(
        [low, child, base],
        [make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"}, id="d1",
                       item=base.key),
         make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"}, id="d2",
                       item=child.key),
         make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"}, id="d3",
                       item=low.key)],
        status=RunStatus.INTERRUPTED,
    )
    child_done = threading.Event()

    def tree(item):
        # base finishes only after child, forcing out-of-order worker completion.
        if item.key == base.key:
            assert child_done.wait(timeout=10)
        elif item.key == child.key:
            child_done.set()
        return None

    keys = [e.pr for e in batch.session_report.sessions(run, closeout=no_debt, tree=tree)]
    assert keys == [base.key, child.key, low.key]


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


def test_publish_refusal_is_push_refused(tmp_path):
    # Revert: map every publish Refusal to failed_unknown.
    wt = str(tmp_path)
    item = make_item(1, worktree=wt, repo_dir=wt)
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


def test_closeout_command_never_track_all(tmp_path):
    # Revert: use CloseoutDebt.command (adds --track-all when an issue is owed).
    wt = str(tmp_path)
    item = make_item(1, worktree=wt, repo_dir=wt)
    debt = CloseoutDebt(summary=True, deferred_issue=True)
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: debt, tree=None)
    argv = entries[0].commands[0].argv
    assert "--track-all" not in argv
    assert argv[:4] == ("pr", "comments", "--finish", "--post")
    assert entries[0].commands[0].action == "closeout"


def test_new_run_copies_is_draft():
    # Revert: drop is_draft=r.is_draft in scheduler.new_run.
    row = PlanRow("o/r", "/r", 1, "t", "b", SHA, True,
                  {Step.REBASE: StepNeed(True, "x")})
    run = new_run([row], steps=[Step.REBASE], selected=None, pool=1)
    assert run.items[0].is_draft is True


def test_session_commands_carry_stable_action_ids(tmp_path):
    wt = str(tmp_path)
    cases = [
        (FindingReason.CONFLICT_HANDWRITTEN, "rebase", {}),
        (FindingReason.CONFLICT_GENERATED, "rebase_generated", {}),
        (FindingReason.CI_FAILING, "ci", {}),
        (FindingReason.REVIEW_FINDINGS, "review", {}),
        (FindingReason.THREADS_NEED_HUMAN, "comments", {}),
        (FindingReason.WRONG_BRANCH, "switch_branch", {}),
    ]
    for reason, action, extra in cases:
        item = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.NEEDS_SESSION,
                         **extra)
        item.findings = [Finding(reason, "x")]
        entries = batch.session_report.sessions(
            make_run([item]), closeout=no_debt, tree=None)
        actions = [c.action for e in entries for c in e.commands if not c.files_issue]
        assert actions == [action]
        assert entries[0].commands[0].to_dict()["action"] == action


def test_stale_generated_emits_tip_note_and_never_reset_hard(tmp_path):
    wt = str(tmp_path)
    for one_sided, needle in (
        (False, "regenerat"),
        (True, "one-sided"),
    ):
        item = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.NEEDS_SESSION,
                         pre_rebase_head=SHA)
        item.findings = [Finding(FindingReason.STALE_GENERATED, "rebase",
                                 files=["a.pb.go"], data={"one_sided": one_sided})]
        entries = batch.session_report.sessions(
            make_run([item]), closeout=no_debt, tree=None)
        assert entries
        joined_notes = " ".join(entries[0].notes)
        joined_argv = " ".join(tok for c in entries[0].commands for tok in c.argv)
        actions = [c.action for c in entries[0].commands]
        assert needle in joined_notes
        assert "a.pb.go" in joined_notes
        assert "pushing" in joined_notes
        if one_sided:
            assert "both sides" in joined_notes
            assert "missing hunks" in joined_notes
        else:
            assert "rebased tip" in joined_notes
        assert "reset" not in joined_argv
        assert "--hard" not in joined_argv
        assert "undo_rebase" not in actions


def test_not_incorporated_remote_carries_commits_and_expect_push_command(tmp_path):
    wt = str(tmp_path)
    item = make_item(1, worktree=wt, repo_dir=wt, branch="feat")
    tree = TreeState(
        local=SHA, remote=item.remote_sha,
        divergence=RefDivergence(ahead=1, behind=2, comparable=True),
        unincorporated=("c1 subj one", "c2 subj two"),
        checked_out=item.branch,
    )
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=lambda i: tree)
    refused = next(f for f in entries[0].findings if f.reason is FindingReason.PUSH_REFUSED)
    assert refused.data["refusal"] == "not_incorporated_remote"
    assert refused.data["commits"] == ["c1 subj one", "c2 subj two"]
    expected = ("pr", "rebase", "--push-only", "--expect", item.remote_sha,
                "--repo-dir", wt)
    push = next(c for c in entries[0].commands if c.action == "push")
    assert push.argv == expected
    assert push.push is True
    run.no_verify = True
    with_nv = batch.session_report.sessions(
        run, closeout=no_debt, tree=lambda i: tree)
    nv_push = next(c for c in with_nv[0].commands if c.action == "push")
    assert "--no-verify" in nv_push.argv
    assert nv_push.argv == ("pr", "rebase", "--push-only", "--expect", item.remote_sha,
                            "--no-verify", "--repo-dir", wt)


def test_running_run_sessions_only_terminal_items(tmp_path):
    wt = str(tmp_path)
    settled = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.NEEDS_SESSION)
    inflight = make_item(2, worktree=wt, repo_dir=wt, status=ItemStatus.RUNNING)
    running = make_run(
        [settled, inflight],
        [
            make_decision(DecisionKind.REBASE_CONFLICT, "rebase", {"files": ["a.go"]},
                          id="d1", item=settled.key),
            make_decision(DecisionKind.REBASE_CONFLICT, "rebase", {"files": ["b.go"]},
                          id="d2", item=inflight.key),
        ],
        status=RunStatus.RUNNING,
    )
    running.schema_version = 2
    entries = batch.session_report.sessions(running, closeout=no_debt, tree=None)
    assert [e.pr for e in entries] == [settled.key]
    assert [c.action for c in entries[0].commands] == ["rebase"]
    waiting_item = make_item(1, status=ItemStatus.AWAITING_DECISION)
    waiting = make_run(
        [waiting_item],
        [make_decision(DecisionKind.FAILED, "rebase", {"reason": "error"})],
        status=RunStatus.WAITING,
    )
    assert batch.session_report.sessions(waiting, closeout=no_debt, tree=None)


def test_schema3_running_run_includes_nonterminal_items(tmp_path):
    wt = str(tmp_path)
    inflight = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.QUEUED)
    run = make_run([inflight], status=RunStatus.RUNNING)
    assert run.schema_version >= 3
    debt = CloseoutDebt(summary=True)
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: debt, tree=None)
    assert [e.pr for e in entries] == [inflight.key]


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
     {"evidence": [{"kind": "open_findings", "findings": [{"severity": "must-fix"}]}]},
     FindingReason.REVIEW_FINDINGS),
    (DecisionKind.STEP_REVIEW, "rebase",
     {"evidence": [{"kind": "stale", "files": ["a.pb.go"]}]},
     FindingReason.STALE_GENERATED),
])
def test_findings_from_decisions_mapping(kind, step, payload, reason):
    item = make_item(1, status=ItemStatus.AWAITING_DECISION)
    run = make_run([item], [make_decision(kind, step, payload)], status=RunStatus.INTERRUPTED)
    found = batch.session_report.findings_from_decisions(run, item)
    assert [f.reason for f in found] == [reason]
