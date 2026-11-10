"""Tests for batch.publish: what a publish pushes, read from the tree, under which lease."""

import sys
from pathlib import Path

import pytest

from batch_git_support import advance, remote_and_clone, remote_tip
from conftest import commit_all, git_in, git_out, init_repo, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
PR_BIN = str(REPO_ROOT / "ai" / "bin" / "pr")
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.publish  # noqa: E402
import git.rewrites  # noqa: E402
import batch.requests  # noqa: E402
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


def test_no_verify_appends_to_push_commands_not_comments():
    item = _item(track=["T1"])
    item.step(batch.model.Step.COMMENTS).drafted = True
    got = batch.publish.plan(item, "pr", _tree(1, 0), no_verify=True)
    assert got.commands == [["pr", "push", "--expect", "r0", "--no-verify", *WT],
                            ["pr", "comments", "--finish", "--post", "--track", "T1",
                             "--no-push-verify", *WT]]


def test_no_verify_is_not_passed_to_comments_when_unset():
    item = _item(track=["T1"])
    item.step(batch.model.Step.COMMENTS).drafted = True
    got = batch.publish.plan(item, "pr", _tree(1, 0), no_verify=False)
    assert got.commands == [["pr", "push", "--expect", "r0", *WT],
                            ["pr", "comments", "--finish", "--post", "--track", "T1", *WT]]
    assert "--no-push-verify" not in got.commands[1]


def test_no_verify_appends_to_a_diverged_rebase_push():
    got = batch.publish.plan(_item(), "pr", _tree(2, 3), no_verify=True)
    assert got.commands == [["pr", "rebase", "--push-only", "--expect", "r0", "--no-verify", *WT]]


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
                                  pool=1)
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


def test_a_rebase_from_a_tip_an_earlier_rebase_rewrote_is_incorporated(tmp_path):
    # An earlier run rebased the branch without pushing: its tip holds the remote's
    # commits as patch-equivalents, not ancestors, and this run's rebase starts there.
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    advance(pair.seed, "main", "upstream")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "rebase", "-q", "origin/main")
    start = git_out(pair.work, "rev-parse", "HEAD").strip()
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main", pre_rebase_head=start)
    tree = batch.publish.read_tree(item)
    assert tree.incorporated is True
    assert batch.publish.plan(item, "pr", tree).commands == [
        ["pr", "rebase", "--push-only", "--expect", planned, "--repo-dir", str(pair.work)]]


def test_a_rewritten_start_tip_missing_a_remote_commit_is_not_incorporated(tmp_path):
    pair = remote_and_clone(tmp_path)
    advance(pair.seed, "main", "upstream")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "rebase", "-q", "origin/main")
    start = git_out(pair.work, "rev-parse", "HEAD").strip()
    planned = advance(pair.seed, "feat", "elsewhere")
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main", pre_rebase_head=start)
    assert batch.publish.read_tree(item).incorporated is False


def _fork_point_rebased(tmp_path):
    """A stacked branch whose two-commit parent was squash-merged, rebased by hand with a fork point.

    Returns the pair, the planned remote head, and the parent's commits as
    `git cherry -v` names them.
    """
    pair = remote_and_clone(tmp_path)
    parent1 = remote_tip(pair.origin, "feat")
    parent2 = advance(pair.seed, "feat", "parent2")
    planned = advance(pair.seed, "feat", "child")
    git_in(pair.seed, "checkout", "-q", "main")
    (pair.seed / "feat.txt").write_text("feat\n")
    (pair.seed / "parent2").write_text("parent2\n")
    commit_all(pair.seed, "feat: parent (squashed)")
    git_in(pair.seed, "push", "-q", "origin", "main")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "reset", "-q", "--hard", "origin/feat")
    git_in(pair.work, "rebase", "-q", "--onto", "origin/main", parent2, "feat")
    return pair, planned, [f"{parent1} feat", f"{parent2} chore: parent2"]


def test_a_squashed_replay_recorded_by_git_is_incorporated(tmp_path):
    pair = remote_and_clone(tmp_path)
    a = remote_tip(pair.origin, "feat")
    b = advance(pair.seed, "feat", "two")
    c = advance(pair.seed, "feat", "three")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "reset", "-q", "--hard", "origin/feat")
    git_in(pair.seed, "checkout", "-q", "main")
    (pair.seed / "upstream.txt").write_text("upstream\n")
    commit_all(pair.seed, "chore: upstream")
    git_in(pair.seed, "push", "-q", "origin", "main")
    git_in(pair.work, "fetch", "-q", "origin")
    tree = git_out(pair.work, "rev-parse", "HEAD^{tree}").strip()
    squash = git_out(
        pair.work, "commit-tree", tree, "-p", "origin/main", "-m", "feat: squashed",
    ).strip()
    git_in(pair.work, "reset", "-q", "--hard", squash)
    common = git.rewrites.common_dir(pair.work)
    git.rewrites.record(common, [
        git.rewrites.Rewrite(a, squash),
        git.rewrites.Rewrite(b, squash),
        git.rewrites.Rewrite(c, squash),
    ])
    item = _item(worktree=str(pair.work), remote=c, base_ref="main")
    state = batch.publish.read_tree(item)
    got = batch.publish.plan(item, "pr", state)
    assert state.unincorporated == ()
    assert got.commands == [
        ["pr", "rebase", "--push-only", "--expect", c, "--repo-dir", str(pair.work)]]


def test_a_one_to_one_rewrite_is_still_refused(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = advance(pair.seed, "feat", "elsewhere")
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    local = git_out(pair.work, "rev-parse", "HEAD").strip()
    git.rewrites.record(git.rewrites.common_dir(pair.work), [
        git.rewrites.Rewrite(planned, local),
    ])
    tree = batch.publish.read_tree(item)
    got = batch.publish.plan(item, "pr", tree)
    assert got.refusal is Refusal.NOT_INCORPORATED_REMOTE
    assert f"{planned} chore: elsewhere" in got.commits


@pytest.mark.parametrize("start", ["planned", "none"])
def test_a_fork_point_rebase_by_hand_is_not_incorporated_remote(tmp_path, start):
    # "planned": a forced re-run kept the refused rebase's start tip. "none": the
    # batch's own rebase was a no-op, so no start tip was recorded.
    pair, planned, skipped = _fork_point_rebased(tmp_path)
    item = _item(worktree=str(pair.work), remote=planned, base_ref="main",
                 pre_rebase_head=planned if start == "planned" else "")
    tree = batch.publish.read_tree(item)
    refused = batch.publish.plan(item, "pr", tree)
    assert (refused.refusal, refused.commits) == (Refusal.NOT_INCORPORATED_REMOTE, skipped)


def _publish_run(pair):
    run = batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
                          auto_publish=[], items=[_item(worktree=str(pair.work),
                                                        remote=remote_tip(pair.origin, "feat"))])
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    run.decisions.append(batch.model.Decision(id="d1", item="o/r#1", step="publish",
                                              kind=batch.model.DecisionKind.PUBLISH))
    return run


def _remote_moved_run(pair):
    run = _publish_run(pair)
    run.decisions = [batch.model.Decision(
        id="d1", item="o/r#1", step="publish", kind=batch.model.DecisionKind.FAILED,
        payload={"reason": Refusal.REMOTE_MOVED.value})]
    run.items[0].status = batch.model.ItemStatus.AWAITING_DECISION
    return run


def _head(work):
    return git_out(work, "rev-parse", "HEAD").strip()


def _advance_main(pair):
    git_in(pair.seed, "checkout", "-q", "main")
    (pair.seed / "upstream.txt").write_text("upstream\n")
    commit_all(pair.seed, "chore: upstream")
    git_in(pair.seed, "push", "-q", "origin", "main")


def _rebase_feat(repo):
    git_in(repo, "fetch", "-q", "origin")
    git_in(repo, "checkout", "-q", "feat")
    git_in(repo, "rebase", "-q", "origin/main")


def _equivalent_rebase(pair):
    """Rebase feat onto a new main on origin and, independently, in work."""
    _advance_main(pair)
    _rebase_feat(pair.seed)
    git_in(pair.seed, "push", "-q", "--force", "origin", "feat")
    _rebase_feat(pair.work)


def _rebase_feat_resolving(repo, text):
    git_in(repo, "fetch", "-q", "origin")
    git_in(repo, "checkout", "-q", "feat")
    run_checked(["git", "-C", str(repo), "rebase", "-q", "origin/main"], check=False)
    (repo / "feat.txt").write_text(text)
    git_in(repo, "add", "feat.txt")
    git_in(repo, "-c", "core.editor=true", "rebase", "--continue")


def _assert_adopted(item, pair, planned):
    remote = remote_tip(pair.origin, "feat")
    assert remote != planned
    assert item.remote_sha == remote
    assert item.published_sha == ""
    assert _head(pair.work) == remote
    assert item.status is batch.model.ItemStatus.QUEUED
    assert batch.publish.plan(item, "pr", batch.publish.read_tree(item)).ok


def _assert_refused(item, pair, planned, head):
    assert item.remote_sha == planned
    assert item.published_sha == ""
    assert _head(pair.work) == head
    assert item.status is batch.model.ItemStatus.QUEUED


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
