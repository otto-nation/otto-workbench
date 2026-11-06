"""Tests for batch.publish.peek_tree: a tree read that does not move origin's refs."""

import sys
from pathlib import Path

from batch_git_support import advance, remote_and_clone, remote_tip
from conftest import git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.publish  # noqa: E402
from batch.publish import Refusal  # noqa: E402


def _item(worktree, remote):
    steps = [batch.model.StepRecord(s) for s in batch.model.STEP_ORDER]
    return batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="feat",
                            head_sha=remote, worktree=worktree, steps=steps)


def _origin_refs(repo):
    return git_out(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                   "refs/remotes/origin")


def test_peek_tree_leaves_origin_refs_when_the_remote_moved(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    origin_feat = git_out(pair.work, "rev-parse", "refs/remotes/origin/feat").strip()
    before = _origin_refs(pair.work)
    item = _item(str(pair.work), planned)
    moved = advance(pair.seed, "feat", "colleague")
    tree = batch.publish.peek_tree(item)
    after_feat = git_out(pair.work, "rev-parse", "refs/remotes/origin/feat").strip()
    assert after_feat == origin_feat
    assert _origin_refs(pair.work) == before
    assert after_feat != moved
    assert tree.remote == moved
    assert batch.publish.plan(item, "pr", tree).refusal is Refusal.REMOTE_MOVED
