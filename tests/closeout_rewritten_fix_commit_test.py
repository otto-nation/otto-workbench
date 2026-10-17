"""Regression: a conflict-rewritten fix commit must not close out as pushed.

`--finish --post` follows a held fix across a rebase, pushes what is still
held, then drains deferred replies. When the rebase resolved a conflict, the
replay is not patch-equivalent, and the repo's own `core.hooksPath` kept git
from recording the rewrite. The recorded SHA is then an orphan: still in the
object database, on no branch, not on the remote. Current closeout treats a
successful push of HEAD as "Pushed held fixes (X)", marks the record PUSHED,
and then skips replies with "Push still pending" because X itself never
arrived. A later `--finish` is silent about the orphan once the status is
PUSHED. These tests pin the contract that should have held.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _fix, _no_published_summary  # noqa: E402
from conftest import git_in, make_ctx, run_checked
from pr_history_rewrite_test import (
    _feature_off_origin,
    _fix_commit,
    _held_fix_branch,
    _short_sha,
)
from git.land import CommitStatus
import core.log
import git.push
import git.replay
import pr.history_rewrite
import pr.state
import pr.summary_publish
import pr.thread_replies
from pr.fix import FixOutcome, ItemOutcome
from pr.state import PRIdentity, PRState
from pr.thread_models import PRReport
import review.closeout
import review.deferred_issue


def _conflict_orphan(tmp_path):
    """Held fix X, then a conflicted rebase whose replay is a different patch.

    No rewrite log: `core.hooksPath` is `/dev/null`, matching a repo that
    bypasses the workbench `post-rewrite` hook. The remote carries the
    replayed HEAD; X remains resolvable and is not an ancestor of HEAD.
    """
    work = _feature_off_origin(tmp_path)
    git_in(work, "config", "core.hooksPath", "/dev/null")
    held = _fix_commit(work, "base.txt", content="feature fix\n")

    git_in(work, "checkout", "-q", "main")
    (work / "base.txt").write_text("upstream\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "upstream work")
    git_in(work, "push", "-q", "origin", "main")

    git_in(work, "checkout", "-q", "feature")
    conflicted = run_checked(
        ["git", "-C", str(work), "rebase", "origin/main"], check=False,
    )
    assert conflicted.returncode != 0, conflicted.stderr
    (work / "base.txt").write_text("rewritten fix\n")
    git_in(work, "add", "base.txt")
    run_checked([
        "git", "-C", str(work), "-c", "core.editor=true",
        "rebase", "--continue",
    ])
    git_in(work, "push", "-q", "-u", "origin", "feature")

    replay = _short_sha(work)
    assert held != replay
    assert git.replay.rewritten_away(work, held)
    assert not git.replay.ReplayFinder(work).find(held).found
    assert git.push.holds(work, held) is False
    return SimpleNamespace(path=work, held=held, replay=replay)


def _fixed_item(sha):
    return ItemOutcome(
        id="t1", outcome=FixOutcome.FIXED, commit_sha=sha, read_sha=sha,
    )


def _save_closeout(repo, *, status, replies_pending=True):
    """Persist a held-fix snapshot the way `--finish` reloads it from disk."""
    target = repo.path / "target"
    pr.state.save_state(target, PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feature", pr_number=42,
            head_sha=repo.held, worktree_root=str(repo.path),
        ),
        fix=_fix(
            commit_sha=repo.held, commit_status=status, head_sha=repo.held,
            replies_pending=replies_pending, items=[_fixed_item(repo.held)],
        ),
    ))
    return make_ctx(
        branch="feature", worktree_root=repo.path,
        head_sha=repo.replay, target_dir=target, pr_number=42,
    )


def _finish(ctx):
    """`--finish --post` with the GitHub side of closeout stubbed out."""
    with patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
            patch.object(pr.summary_publish, "render_deferred_summary"), \
            patch.object(pr.thread_replies, "reply_to_fixed", return_value=1) as reply:
        review.closeout.finish_deferred_work(ctx, PRReport())
    return reply


class TestConflictRewrittenFixCommit:
    """The recorded SHA did not survive the rebase; closeout must not pretend."""

    def test_push_held_commit_does_not_mark_an_orphan_pushed(
        self, tmp_path, publishing_on,
    ):
        """X is not on HEAD or the remote; a push of HEAD is not a push of X."""
        repo = _conflict_orphan(tmp_path)
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="feature", pr_number=42,
                head_sha=repo.held, worktree_root=str(repo.path),
            ),
            fix=_fix(
                commit_sha=repo.held, commit_status=CommitStatus.PUSH_HELD,
                head_sha=repo.held, replies_pending=True,
                items=[_fixed_item(repo.held)],
            ),
        )
        logged = []
        with patch.object(core.log, "info", side_effect=logged.append):
            pr.history_rewrite.follow_history_rewrite(state, repo.path)
            review.closeout.push_held_commit(state, repo.path)

        assert git.push.holds(repo.path, repo.held) is False
        assert state.fix.fix.commit_status != CommitStatus.PUSHED
        assert not any("Pushed held fixes" in m for m in logged)

    def test_finish_says_the_fix_commit_is_gone_not_that_a_push_is_pending(
        self, tmp_path, publishing_on,
    ):
        """Nothing is pending: the recorded commit is not on the branch."""
        repo = _conflict_orphan(tmp_path)
        ctx = _save_closeout(repo, status=CommitStatus.PUSH_HELD)
        logged, warned = [], []
        with patch.object(core.log, "info", side_effect=logged.append), \
                patch.object(core.log, "warn", side_effect=warned.append):
            _finish(ctx)

        assert any(
            repo.held in w and "is no longer on this branch" in w
            for w in warned
        )
        assert not any("Push still pending" in m for m in logged)

    def test_a_second_finish_still_warns_when_the_orphan_is_marked_pushed(
        self, tmp_path, publishing_on,
    ):
        """The stuck state — PUSHED, replies still pending, SHA orphaned —
        must not go silent on the next `--finish`."""
        repo = _conflict_orphan(tmp_path)
        ctx = _save_closeout(repo, status=CommitStatus.PUSHED)
        logged, warned = [], []
        with patch.object(core.log, "info", side_effect=logged.append), \
                patch.object(core.log, "warn", side_effect=warned.append):
            _finish(ctx)

        assert any(
            repo.held in w and "is no longer on this branch" in w
            for w in warned
        )
        assert not any("Push still pending" in m for m in logged)

    def test_a_patch_equivalent_rebase_without_a_rewrite_log_drains_replies(
        self, tmp_path, publishing_on,
    ):
        """Control: the replay is the same change, already on the remote."""
        repo = _held_fix_branch(tmp_path)
        ctx = _save_closeout(repo, status=CommitStatus.PUSH_HELD)
        logged = []
        with patch.object(core.log, "info", side_effect=logged.append):
            reply = _finish(ctx)

        saved = pr.state.load_state(repo.path / "target")
        assert not any("Push still pending" in m for m in logged)
        assert reply.call_count == 1
        assert reply.call_args[0][4].sha == repo.replay
        assert saved.fix.replies_pending is False
        assert saved.fix.fix.commit_status == CommitStatus.PUSHED
        assert saved.fix.fix.commit_sha == repo.replay
