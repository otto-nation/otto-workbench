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


def test_dropped_is_computed_once_per_branch_tip(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    item, _ = _draft(pair)
    calls = []
    real = batch.publish._compute_dropped
    monkeypatch.setattr(batch.publish, "_compute_dropped",
                        lambda *a: calls.append(1) or real(*a))
    batch.publish.read_tree(item)
    batch.publish.read_tree(item)
    assert len(calls) == 1


def test_an_unverifiable_answer_is_asked_again_rather_than_kept(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
    item.step(Step.REVIEW).commits = ["e" * 40]
    calls = []
    real = batch.publish._compute_dropped
    monkeypatch.setattr(batch.publish, "_compute_dropped",
                        lambda *a: calls.append(1) or real(*a))
    batch.publish.read_tree(item)
    batch.publish.read_tree(item)
    assert len(calls) == 2


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


def _remote_moved_run(item):
    run = batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
                          auto_publish=[], items=[item])
    run.decisions = [batch.model.Decision(
        id="d1", item=item.key, step="publish", kind=batch.model.DecisionKind.FAILED,
        payload={"reason": Refusal.REMOTE_MOVED.value})]
    item.status = batch.model.ItemStatus.AWAITING_DECISION
    return run


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, log_path=None):
        self.calls.append(argv)
        return 0
