"""Held comment-fix edits that never committed must not be published as fixed.

Incident: `pr comments --fix` in batch held publishing for threads awaiting
discussion, then `git commit` failed the pre-commit hook. The edits stayed
staged. After `pr batch resolve … --action settle-*`, resume produced a
`publish` decision. `batch.publish` saw HEAD == origin (nothing to push) and
ran `pr comments --finish --post`, which posted FIXED replies for work that
never reached the remote.

`git.land` does attempt the commit while the gate is held — the hold is the
push, not the commit. The failure here is the closeout: `post_pending_fix_replies`
treats an empty `commit_sha` as "the operator landed it by hand" and drains
the FIXED queue even when HEAD has not moved.
"""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _fix, _make_state, _no_published_summary,
)
from batch_git_support import remote_and_clone, remote_tip  # noqa: E402
from conftest import git_in  # noqa: E402
import batch.model  # noqa: E402
import batch.publish  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.thread_models import ReportThread  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402
import git.client  # noqa: E402
import git.push  # noqa: E402
import pr.attribution  # noqa: E402
import review.closeout  # noqa: E402

PR_BIN = str(REPO_ROOT / "ai" / "bin" / "pr")


SNAPSHOT = "abc1234"


def _item(worktree="/wt", remote="r0", **kw):
    steps = [batch.model.StepRecord(s) for s in batch.model.STEP_ORDER]
    return batch.model.Item(
        key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="feat",
        head_sha=remote, worktree=worktree, steps=steps, **kw,
    )


def _tree(ahead, behind, *, remote="r0", comparable=True, **kw):
    return TreeState(
        local="l1", remote=remote,
        divergence=RefDivergence(ahead=ahead, behind=behind, comparable=comparable),
        **kw,
    )


def _failed_queue(*, replies_pending=True):
    """What `--fix` persisted after a hold and a refused commit."""
    fix = _fix(
        items=[
            ItemOutcome(
                id="t1", summary="fix it", file="x.py", line=1,
                outcome=FixOutcome.FIXED,
            ),
        ],
        commit_sha="",
        commit_status="commit_failed",
        head_sha=SNAPSHOT,
        replies_pending=replies_pending,
    )
    threads_by_id = {
        "t1": ReportThread(id="t1", is_resolved=False, comments=[{"databaseId": 100}]),
    }
    return fix, threads_by_id


class TestFinishMustNotClaimUncommittedFixes:
    """`--finish --post` is not a licence to invent a landing."""

    def test_does_not_post_fixed_replies_when_head_never_moved(self, publishing_on):
        """The hook rejected the commit and nobody landed the work afterwards.

        The regression: FIXED replies would have gone out
        for staged, uncommitted edits. The former
        `test_falls_back_to_the_linkless_shape_when_no_commit_can_be_named`
        encoded that drain as intended; this is the claim it missed.
        """
        fix, threads_by_id = _failed_queue()
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value=SNAPSHOT), \
             patch.object(git.push, "holds", return_value=False), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply") as mock_reply, \
             patch("pr.comments.resolve_thread") as mock_resolve:
            review.closeout.post_pending_fix_replies(
                state, "owner/repo", 1, threads_by_id,
            )
        mock_reply.assert_not_called()
        mock_resolve.assert_not_called()
        assert fix.replies_pending is True
        assert fix.fix.commit_status == "commit_failed"

    def test_a_held_push_with_no_sha_is_not_something_finish_can_send(
        self, publishing_on,
    ):
        """`push_held_commit` no-ops without a SHA; replies must wait with it.

        Same empty-sha shape as commit_failed: the comments docstring says the
        commit sits locally with status `push_held`, but a refused commit never
        produced one.
        """
        def boom(*a, **kw):
            raise AssertionError(f"pushed with no commit to push: {a}")

        state = _make_state(_fix(commit_sha="", commit_status="commit_failed"))
        with patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "commit_failed"
        assert state.fix.fix.commit_sha == ""


def _finish_post_commands(plan):
    """Commands that would drain the FIXED queue via `--finish --post`."""
    return [c for c in plan.commands if "--finish" in c and "--post" in c]


class TestPublishDoesNotFinishUncommittedFixes:
    """The batch caller that would fire those replies.

    `publish.plan` looks at SHAs and `CloseoutDebt`. A `commit_failed` record
    with an empty sha, or a worktree holding staged uncommitted edits, must
    not plan `pr comments --finish --post` — that posts FIXED replies for
    work that never landed. Refuse or omit the finish-post step.
    """

    def test_commit_failed_with_empty_sha_does_not_plan_finish_post(self):
        """Owed replies after a refused commit must not be published as fixed."""
        fix, _ = _failed_queue()
        assert fix.fix.commit_sha == ""
        assert str(fix.fix.commit_status) == "commit_failed"
        got = batch.publish.plan(
            _item(), "pr", _tree(0, 0),
            closeout=CloseoutDebt(replies=True, unlanded=True),
        )
        assert got.pushes is False
        assert _finish_post_commands(got) == []
        assert not got.ok or got.commands == []

    def test_dirty_tree_does_not_plan_finish_post(self, tmp_path):
        """Staged uncommitted fixes must not be published as landed."""
        pair = remote_and_clone(tmp_path)
        (pair.work / "fix.txt").write_text("staged\n")
        git_in(pair.work, "add", "fix.txt")
        item = _item(worktree=str(pair.work), remote=remote_tip(pair.origin, "feat"))
        got = batch.publish.plan(
            item, PR_BIN, batch.publish.read_tree(item),
            closeout=CloseoutDebt(replies=True),
        )
        assert got.pushes is False
        assert _finish_post_commands(got) == []
        assert not got.ok or got.commands == []
