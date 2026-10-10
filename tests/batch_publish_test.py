"""Tests for batch.publish: what a publish pushes, read from the tree, under which lease."""

import sys
from pathlib import Path

import pytest

from batch_git_support import advance, remote_and_clone, remote_tip
from conftest import commit_all, git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
PR_BIN = str(REPO_ROOT / "ai" / "bin" / "pr")
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.publish  # noqa: E402
import batch.resolve  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.publish import Refusal, TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def _item(worktree="/wt", remote="r0", **kw):
    steps = [batch.model.StepRecord(s) for s in batch.model.STEP_ORDER]
    return batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="feat",
                            head_sha=remote, worktree=worktree, steps=steps, **kw)


def _tree(ahead, behind, *, remote="r0", comparable=True, **kw):
    return TreeState(local="l1", remote=remote,
                     divergence=RefDivergence(ahead=ahead, behind=behind, comparable=comparable),
                     **kw)


WT = ["--repo-dir", "/wt"]


@pytest.mark.parametrize("tree,commands,refusal", [
    (_tree(0, 0), [], None),
    (_tree(2, 0), [["pr", "push", "--expect", "r0", *WT]], None),
    (_tree(2, 3), [["pr", "rebase", "--push-only", "--expect", "r0", *WT]], None),
    (_tree(2, 0, remote="other"), [], Refusal.REMOTE_MOVED),
    (_tree(0, 0, comparable=False), [], Refusal.NOT_COMPARABLE),
    (_tree(2, 0, fetched=False), [], Refusal.FETCH_FAILED),
    (_tree(2, 3, incorporated=False), [], Refusal.NOT_INCORPORATED),
    (_tree(2, 3, unincorporated=("c1 subj",)), [], Refusal.NOT_INCORPORATED_REMOTE),
])
def test_the_publish_table(tree, commands, refusal):
    got = batch.publish.plan(_item(), "pr", tree)
    assert (got.commands, got.refusal) == (commands, refusal)
    assert got.pushes is bool(commands)


def test_an_unincorporated_refusal_names_the_remote_commits():
    got = batch.publish.plan(_item(), "pr", _tree(2, 3, unincorporated=("c1 subj", "c2 two")))
    assert got.commits == ["c1 subj", "c2 two"]
    assert "2 remote commit(s)" in got.detail


def test_a_confirmed_publish_force_pushes_past_unincorporated_commits():
    got = batch.publish.plan(_item(), "pr", _tree(2, 3, unincorporated=("c1 subj",)),
                             confirmed=["c1 subj"])
    assert got.commands == [["pr", "rebase", "--push-only", "--expect", "r0", *WT]]


def test_a_confirmation_covers_only_the_commits_the_operator_saw():
    tree = _tree(2, 3, unincorporated=("c1 subj", "c2 new"))
    got = batch.publish.plan(_item(), "pr", tree, confirmed=["c1 subj"])
    assert (got.refusal, got.commits) == (Refusal.NOT_INCORPORATED_REMOTE, ["c1 subj", "c2 new"])


def test_comment_replies_follow_a_drafted_comments_step_and_tracking():
    item = _item(track=["T1"])
    item.step(batch.model.Step.COMMENTS).drafted = True
    got = batch.publish.plan(item, "pr", _tree(1, 0))
    assert got.commands == [["pr", "push", "--expect", "r0", *WT],
                            ["pr", "comments", "--finish", "--post", "--track", "T1", *WT]]


def test_the_plan_base_ref_reaches_the_item():
    need = {batch.model.Step.REVIEW: StepNeed(True, "x")}
    row = PlanRow("o/r", "/r", 1, "t", "feat", "h", False, need, base_ref="main")
    run = batch.scheduler.new_run([row], steps=list(batch.model.STEP_ORDER), selected=None,
                                  pool=1, auto_publish=[])
    assert run.items[0].base_ref == "main"


def test_a_local_branch_ahead_reads_as_a_fast_forward(tmp_path):
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    assert batch.publish.plan(item, "pr", batch.publish.read_tree(item)).commands == [
        ["pr", "push", "--expect", item.remote_sha, "--repo-dir", str(pair.work)]]


def test_a_hand_rebased_branch_publishes_with_expect(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main")
    advance(pair.seed, "main", "upstream")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "rebase", "-q", "origin/main")
    tree = batch.publish.read_tree(item)
    assert tree.unincorporated == ()
    assert batch.publish.plan(item, "pr", tree).commands == [
        ["pr", "rebase", "--push-only", "--expect", planned, "--repo-dir", str(pair.work)]]


def test_a_stale_local_branch_missing_a_remote_commit_is_refused(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = advance(pair.seed, "feat", "elsewhere")
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    tree = batch.publish.read_tree(item)
    assert tree.unincorporated == (f"{planned} chore: elsewhere",)
    got = batch.publish.plan(item, "pr", tree)
    assert (got.refusal, got.commits) == (Refusal.NOT_INCORPORATED_REMOTE,
                                          [f"{planned} chore: elsewhere"])


def test_a_push_by_somebody_else_refuses_the_publish(tmp_path):
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    advance(pair.seed, "feat", "colleague")
    assert batch.publish.plan(item, "pr", batch.publish.read_tree(item)).refusal is \
        Refusal.REMOTE_MOVED


def test_a_rebase_that_did_not_start_from_the_remote_is_not_incorporated(tmp_path):
    pair = remote_and_clone(tmp_path)
    base = git_out(pair.work, "rev-parse", "HEAD~1").strip()
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"),
                 pre_rebase_head=base)
    assert batch.publish.read_tree(item).incorporated is False


def _publish_run(pair):
    run = batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
                          auto_publish=[], items=[_item(worktree=str(pair.work),
                                                        remote=remote_tip(pair.origin, "feat"))])
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    run.decisions.append(batch.model.Decision(id="d1", item="o/r#1", step="publish",
                                              kind=batch.model.DecisionKind.PUBLISH))
    return run


def test_publishing_a_fast_forward_moves_the_remote_and_the_lease(tmp_path):
    pair = remote_and_clone(tmp_path)
    run = _publish_run(pair)
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    local = git_out(pair.work, "rev-parse", "HEAD").strip()
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"),
                        pr_bin=PR_BIN)
    assert remote_tip(pair.origin, "feat") == local
    assert run.items[0].remote_sha == local == run.items[0].published_sha
    assert run.items[0].status is batch.model.ItemStatus.DONE


def test_a_publish_that_commits_before_pushing_leases_on_what_it_pushed(tmp_path):
    pair = remote_and_clone(tmp_path)
    run = _publish_run(pair)
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")

    def regenerating_push(argv, log_path=None):
        (pair.work / "regen.txt").write_text("regen\n")
        commit_all(pair.work, "chore: regenerate")
        git_in(pair.work, "push", "-q", "origin", "feat")
        return 0

    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=regenerating_push)
    assert run.items[0].remote_sha == remote_tip(pair.origin, "feat")
    assert run.items[0].published_sha == run.items[0].remote_sha


def test_a_fast_forward_publish_logs_what_the_push_said(tmp_path):
    pair = remote_and_clone(tmp_path)
    run = _publish_run(pair)
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"),
                        pr_bin=PR_BIN)
    logs = sorted(batch.store.logs_dir("r1").glob("o__r-1-publish-*.log"))
    assert [p.name for p in logs] == ["o__r-1-publish-0.log"]
    assert "Pushed" in logs[0].read_text()


def test_a_fast_forward_a_hook_refuses_records_pre_push_rejected(tmp_path, live_git_hooks):
    pair = remote_and_clone(tmp_path)
    run = _publish_run(pair)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    git_in(pair.work, "config", "core.hooksPath", str(hooks))
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"),
                        pr_bin=PR_BIN)
    failed = run.open_decisions()[-1]
    assert failed.payload["reason"] == "pre_push_rejected"
    assert failed.payload["detail"].startswith("push refused (hook)")
    assert "lint failed" in Path(failed.payload["log"]).read_text()


def test_owed_closeout_alone_publishes_only_the_replies():
    got = batch.publish.plan(_item(), "pr", _tree(0, 0),
                             closeout=CloseoutDebt(summary=True))
    assert got.commands == [["pr", "comments", "--finish", "--post", *WT]]
    assert got.pushes is False and got.ok


def test_owed_closeout_follows_a_push_once():
    item = _item()
    item.step(batch.model.Step.COMMENTS).drafted = True
    got = batch.publish.plan(item, "pr", _tree(1, 0), closeout=CloseoutDebt(replies=True))
    assert got.commands == [
        ["pr", "push", "--expect", "r0", *WT],
        ["pr", "comments", "--finish", "--post", *WT],
    ]


def test_an_owed_tracking_issue_closes_out_with_track_all_in_place_of_ids():
    got = batch.publish.plan(_item(track=["T1"]), "pr", _tree(0, 0),
                             closeout=CloseoutDebt(deferred_issue=True))
    assert got.commands == [["pr", "comments", "--finish", "--post", "--track-all", *WT]]


def test_owed_closeout_is_still_refused_when_somebody_pushed():
    got = batch.publish.plan(_item(), "pr", _tree(0, 0, remote="other"),
                             closeout=CloseoutDebt(summary=True))
    assert (got.commands, got.refusal) == ([], Refusal.REMOTE_MOVED)


def test_no_debt_and_nothing_drafted_plans_nothing():
    assert batch.publish.plan(_item(), "pr", _tree(0, 0), closeout=CloseoutDebt()).commands == []
