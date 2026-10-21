"""Tests for batch.publish's drafted-commit gate and its forward-only lease moves.

A publish must not mark an item done after a commit a step drafted has left the
branch, and a lease may only move to a remote that went forward from it. The
tree reads run against real repos: each case is a claim about what git reports
after a particular hand edit to the branch.
"""

import sys
from pathlib import Path

from batch_git_support import advance, remote_and_clone, remote_tip
from conftest import commit_all, git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.publish  # noqa: E402
import batch.resolve  # noqa: E402
import batch.requests  # noqa: E402
import batch.store  # noqa: E402
import git.client  # noqa: E402
from batch.model import Step  # noqa: E402
from batch.publish import Refusal, TreeState  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402

WT = ["--repo-dir", "/wt"]


def _item(worktree="/wt", remote="r0", **kw):
    steps = [batch.model.StepRecord(s) for s in batch.model.STEP_ORDER]
    return batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="feat",
                            head_sha=remote, worktree=worktree, steps=steps, **kw)


def _tree(ahead, behind, *, remote="r0", local="l1", **kw):
    return TreeState(local=local, remote=remote,
                     divergence=RefDivergence(ahead=ahead, behind=behind, comparable=True),
                     **kw)


def _head(repo) -> str:
    return git_out(repo, "rev-parse", "HEAD").strip()


def _draft(pair, name="fix", step=Step.CI):
    """Commit *name* in work as *step*'s drafted work; return the item and the sha."""
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"),
                 base_ref="main")
    (pair.work / f"{name}.txt").write_text(f"{name}\n")
    commit_all(pair.work, f"fix: {name}")
    sha = _head(pair.work)
    rec = item.step(step)
    rec.drafted = True
    rec.commits.append(sha)
    return item, sha


def _label(sha, subject):
    return f"{git.client.abbrev(sha)} {subject}"


# ── plan ────────────────────────────────────────────────────────────────────


def test_a_dropped_drafted_commit_refuses_even_with_nothing_to_push():
    """A hand push without the commit leaves local == origin: nothing to push, work gone."""
    got = batch.publish.plan(_item(), "pr", _tree(0, 0, dropped=("c1 fix: x",)))
    assert (got.refusal, got.commits, got.commands) == (
        Refusal.DRAFTED_DROPPED, ["c1 fix: x"], [])
    assert "1 commit(s) the batch drafted" in got.detail


def test_a_confirmed_dropped_commit_publishes():
    got = batch.publish.plan(_item(), "pr", _tree(1, 0, dropped=("c1 fix: x",)),
                             confirmed=["c1 fix: x"])
    assert got.commands == [["pr", "push", "--expect", "r0", *WT]]


def test_a_confirmation_does_not_cover_a_drafted_commit_dropped_since():
    tree = _tree(1, 0, dropped=("c1 fix: x", "c2 fix: y"))
    got = batch.publish.plan(_item(), "pr", tree, confirmed=["c1 fix: x"])
    assert (got.refusal, got.commits) == (Refusal.DRAFTED_DROPPED, ["c1 fix: x", "c2 fix: y"])


def test_drafted_commits_are_listed_once_in_step_order():
    item = _item()
    item.step(Step.REVIEW).commits = ["b", "a"]
    item.step(Step.CI).commits = ["a", "c"]
    assert batch.publish.drafted_commits(item) == ["a", "c", "b"]


# ── read_tree against real history ──────────────────────────────────────────


def test_a_drafted_commit_still_on_the_branch_is_carried(tmp_path):
    pair = remote_and_clone(tmp_path)
    item, _ = _draft(pair)
    assert batch.publish.read_tree(item).dropped == ()


def test_a_drafted_commit_reset_away_by_hand_is_refused(tmp_path):
    pair = remote_and_clone(tmp_path)
    item, sha = _draft(pair)
    git_in(pair.work, "reset", "-q", "--hard", "HEAD~1")
    tree = batch.publish.read_tree(item)
    assert tree.dropped == (_label(sha, "fix: fix"),)
    assert batch.publish.plan(item, "pr", tree).refusal is Refusal.DRAFTED_DROPPED


def test_a_reset_and_new_commit_still_misses_the_drafted_one(tmp_path):
    pair = remote_and_clone(tmp_path)
    item, sha = _draft(pair)
    git_in(pair.work, "reset", "-q", "--hard", "HEAD~1")
    (pair.work / "other.txt").write_text("other\n")
    commit_all(pair.work, "fix: other")
    assert batch.publish.read_tree(item).dropped == (_label(sha, "fix: fix"),)


def test_a_rebase_that_dropped_one_drafted_commit_names_only_that_one(tmp_path):
    """The replayed survivor has a new sha; it is carried as a patch-equivalent."""
    pair = remote_and_clone(tmp_path)
    item, first = _draft(pair, "one")
    (pair.work / "two.txt").write_text("two\n")
    commit_all(pair.work, "fix: two")
    item.step(Step.CI).commits.append(_head(pair.work))
    git_in(pair.work, "rebase", "-q", "--onto", "HEAD~2", "HEAD~1")
    assert batch.publish.read_tree(item).dropped == (_label(first, "fix: one"),)


def test_a_drafted_commit_rebased_onto_a_new_base_is_carried(tmp_path):
    pair = remote_and_clone(tmp_path)
    item, sha = _draft(pair)
    advance(pair.seed, "main", "upstream")
    git_in(pair.work, "fetch", "-q", "origin")
    git_in(pair.work, "rebase", "-q", "origin/main")
    assert _head(pair.work) != sha
    assert batch.publish.read_tree(item).dropped == ()


def test_a_hand_push_without_the_drafted_commit_is_refused(tmp_path):
    """Remote == local after the hand push, and the retry adopts it as the lease."""
    pair = remote_and_clone(tmp_path)
    item, sha = _draft(pair)
    git_in(pair.work, "reset", "-q", "--hard", "HEAD~1")
    (pair.work / "other.txt").write_text("other\n")
    commit_all(pair.work, "fix: other")
    git_in(pair.work, "push", "-q", "origin", "feat")
    item.remote_sha = remote_tip(pair.origin, "feat")
    tree = batch.publish.read_tree(item)
    got = batch.publish.plan(item, "pr", tree)
    assert (got.refusal, got.commits) == (Refusal.DRAFTED_DROPPED, [_label(sha, "fix: fix")])


def test_a_commit_git_cannot_resolve_is_refused_as_unverifiable(tmp_path):
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    item.step(Step.REVIEW).commits = ["f" * 40]
    assert batch.publish.read_tree(item).dropped == ("fffffff (unverifiable)",)


# ── forward-only lease moves ────────────────────────────────────────────────


def test_a_remote_that_fast_forwarded_to_a_commit_local_holds_is_a_forward_move(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    item = _item(worktree=str(pair.work), remote=planned)
    moved = advance(pair.seed, "feat", "colleague")
    git_in(pair.work, "pull", "-q", "--ff-only", "origin", "feat")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    tree = batch.publish.read_tree(item)
    assert tree.remote == moved
    assert batch.publish.remote_fast_forwarded(item, tree)


def test_a_remote_reset_backwards_is_not_a_forward_move(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = advance(pair.seed, "feat", "later")
    git_in(pair.work, "pull", "-q", "--ff-only", "origin", "feat")
    item = _item(worktree=str(pair.work), remote=planned)
    git_in(pair.seed, "push", "-q", "-f", "origin", "feat~1:refs/heads/feat")
    tree = batch.publish.read_tree(item)
    assert tree.remote != planned
    assert not batch.publish.remote_fast_forwarded(item, tree)


def test_a_remote_commit_local_lacks_is_not_a_forward_move(tmp_path):
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    advance(pair.seed, "feat", "colleague")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    assert not batch.publish.remote_fast_forwarded(item, batch.publish.read_tree(item))


def _remote_moved_run(item):
    run = batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
                          auto_publish=[], items=[item])
    run.decisions = [batch.model.Decision(
        id="d1", item=item.key, step="publish", kind=batch.model.DecisionKind.FAILED,
        payload={"reason": Refusal.REMOTE_MOVED.value})]
    item.status = batch.model.ItemStatus.AWAITING_DECISION
    return run


def test_a_remote_moved_retry_adopts_a_fast_forward_local_already_holds(tmp_path):
    """Regression: local held the remote plus a fix, so it was neither equal to the
    remote nor patch-equivalent to it, and every retry refused remote_moved."""
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    run = _remote_moved_run(item)
    moved = advance(pair.seed, "feat", "colleague")
    git_in(pair.work, "pull", "-q", "--ff-only", "origin", "feat")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    head = _head(pair.work)
    batch.resolve.apply(run, batch.requests.Decide("d1", "retry"), pr_bin="pr")
    assert item.remote_sha == moved
    assert _head(pair.work) == head
    assert batch.publish.plan(item, "pr", batch.publish.read_tree(item)).commands == [
        ["pr", "push", "--expect", moved, "--repo-dir", str(pair.work)]]


def test_a_remote_moved_retry_keeps_the_lease_after_a_reset_backwards(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = advance(pair.seed, "feat", "later")
    git_in(pair.work, "pull", "-q", "--ff-only", "origin", "feat")
    item = _item(worktree=str(pair.work), remote=planned)
    run = _remote_moved_run(item)
    git_in(pair.seed, "push", "-q", "-f", "origin", "feat~1:refs/heads/feat")
    (pair.work / "fix.txt").write_text("fix\n")
    commit_all(pair.work, "fix: x")
    batch.resolve.apply(run, batch.requests.Decide("d1", "retry"), pr_bin="pr")
    assert item.remote_sha == planned


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, log_path=None):
        self.calls.append(argv)
        return 0


def test_confirming_one_refusal_does_not_unconfirm_the_other(tmp_path, monkeypatch):
    """A branch can lose drafted work and remote work at once; each force-publish
    adds to what the operator confirmed rather than replacing it."""
    monkeypatch.setattr(batch.store, "attempt_log_path",
                        lambda *a: tmp_path / "publish.log")
    item = _item(remote="s")
    item.step(Step.CI).commits = ["c1"]
    run = batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
                          auto_publish=[], items=[item], watch_ci=True)
    run.decisions = [batch.model.Decision(id="d1", item=item.key, step="publish",
                                          kind=batch.model.DecisionKind.PUBLISH)]

    def tree(it):
        return TreeState(local="new", remote=it.remote_sha,
                         divergence=RefDivergence(ahead=1, behind=1, comparable=True),
                         unincorporated=("r1 theirs",), dropped=("c1 ours",))

    rec = _Recorder()

    def answer(action):
        decision = run.open_decisions()[-1]
        batch.resolve.apply(run, batch.requests.Decide(decision.id, action), pr_bin="pr",
                            runner=rec, tree=tree,
                            closeout=lambda d, b: batch.resolve.CloseoutDebt())
        return run.open_decisions()

    first = answer("publish")[-1]
    assert first.payload["reason"] == Refusal.DRAFTED_DROPPED.value
    second = answer("force-publish")[-1]
    assert second.payload["reason"] == Refusal.NOT_INCORPORATED_REMOTE.value
    assert answer("force-publish") == []
    assert rec.calls == [["pr", "rebase", "--push-only", "--expect", "s", *WT]]
    assert item.forced == []
    assert item.step(Step.CI).commits == []
