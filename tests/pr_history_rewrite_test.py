"""pr.history_rewrite: following a held fix across a rebase."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _fix, _make_state, _no_published_summary, content  # noqa: E402
from conftest import git_in, make_ctx, run_checked
import pr.state
import core.log
import git.client
import git.push
from git.land import CommitStatus
import pr.thread_replies
import pr.history_rewrite
import pr.summary_publish
import pr.settlement
from pr.fix import FixOutcome, ItemOutcome
from pr.state import PRIdentity, PRState
from pr.thread_models import PRReport
import review.closeout
import review.deferred_issue


def _short_sha(path, rev="HEAD") -> str:
    """The short SHA of *rev* in the repo at *path*."""
    return run_checked(["git", "-C", str(path), "rev-parse", "--short", rev]).stdout.strip()


# What every fix pass commits under (`_commit_fixes`), so two rounds on one
# branch are indistinguishable by subject — which is why identity is content.
_FIX_SUBJECT = "fix: address review comments"


# One author date for every fix commit a helper here writes. Two rounds
# genuinely do land in the same second — a retry, fast CI — and a test that
# waited for the clock to tick would only be reproducing the easy case.
_FIX_DATE = "2026-01-01T00:00:00+00:00"


def _feature_off_origin(tmp_path) -> Path:
    """A clone with one pushed commit on `main` and `feature` checked out.

    Real git throughout, because the bug these build is precisely the
    disagreement between two ways of asking about a rewritten commit: the
    recorded SHA still resolves as an object, and no branch anywhere contains
    it. Nothing short of an actual rebase produces that pair.
    """
    origin = tmp_path / "origin"
    run_checked(["git", "init", "--bare", "-q", "-b", "main", str(origin)])
    work = tmp_path / "work"
    run_checked(["git", "clone", "-q", str(origin), str(work)])
    git_in(work, "config", "user.email", "t@example.com")
    git_in(work, "config", "user.name", "Test")

    (work / "base.txt").write_text("base\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "base")
    git_in(work, "push", "-q", "-u", "origin", "main")
    git_in(work, "checkout", "-q", "-b", "feature")
    return work


def _fix_commit(work, name, content=None) -> str:
    """A fix-pass commit adding *name*, under the pass's own subject and date."""
    (work / name).write_text(content if content is not None else f"{name}\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "--date", _FIX_DATE, "-m", _FIX_SUBJECT)
    return _short_sha(work)


def _rebase_onto_moved_main(work) -> None:
    """Move `main` on and replay `feature` over it, the way `pr rebase` does."""
    git_in(work, "checkout", "-q", "main")
    (work / "upstream.txt").write_text("upstream\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "upstream work")
    git_in(work, "push", "-q", "origin", "main")
    git_in(work, "checkout", "-q", "feature")
    git_in(work, "rebase", "-q", "origin/main")


def _held_fix_branch(tmp_path, *, rebase=True, drop=False, push=True):
    """A branch carrying a fix commit the pass held.

    `rebase` replays the fix commit onto upstream work the way `pr rebase` does,
    `drop` throws it away instead, and `push` decides whether what survives ever
    reached the remote. `.held` is the SHA the fix pass recorded; `.replay` is
    what the branch carries afterwards.
    """
    work = _feature_off_origin(tmp_path)
    held = _fix_commit(work, "fix.txt")

    if drop:
        git_in(work, "reset", "-q", "--hard", "HEAD~1")
        return SimpleNamespace(path=work, held=held, replay="")

    if rebase:
        _rebase_onto_moved_main(work)
    if push:
        git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, held=held, replay=_short_sha(work))


def _two_held_rounds(tmp_path):
    """Two rounds of held fixes on one branch, then a rebase over both.

    The pair a snapshot cannot tell apart by anything but content: same static
    subject, same author date, two different commits it has to map separately.
    """
    work = _feature_off_origin(tmp_path)
    first = _fix_commit(work, "one.txt")
    second = _fix_commit(work, "two.txt")
    _rebase_onto_moved_main(work)
    git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(
        path=work, first=first, second=second,
        first_replay=_short_sha(work, "HEAD~1"), second_replay=_short_sha(work),
    )


def _duplicated_fix(tmp_path):
    """A branch where the held fix's patch appears twice: applied, undone, redone.

    Two commits with one patch id, so "which commit replays the orphan?" has no
    answer — the state a closeout must refuse to guess at rather than pick from.
    """
    work = _feature_off_origin(tmp_path)
    held = _fix_commit(work, "fix.txt")
    (work / "fix.txt").unlink()
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "revert: back that out")
    _fix_commit(work, "fix.txt")
    _rebase_onto_moved_main(work)
    git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, held=held)


class TestFollowHistoryRewrite:
    """A rebase renames the held commit; it does not unpublish the work.

    `pr rebase --fix` is what a supersession warning tells the operator to run,
    and it rewrote every SHA the fix pass had recorded. The closeout then read
    its own commit as unpushed forever, with no way forward that did not discard
    the reviewed replies.
    """

    def _state(self, repo, status=CommitStatus.PUSH_HELD):
        return _make_state(_fix(
            commit_sha=repo.held, commit_status=status, head_sha=repo.held,
            items=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED,
                                   commit_sha=repo.held, read_sha=repo.held)],
        ))

    def test_a_rebased_commit_is_followed_to_its_replay(self, tmp_path):
        repo = _held_fix_branch(tmp_path)
        state = self._state(repo)
        pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.replay
        assert state.fix.fix.head_sha == repo.replay
        assert state.fix.fix.items[0].commit_sha == repo.replay
        assert state.fix.fix.items[0].read_sha == repo.replay

    def test_the_replay_is_what_the_remote_has(self, tmp_path):
        """The point of following it: the hold is over a name, not the work."""
        repo = _held_fix_branch(tmp_path)
        assert git.push.holds(repo.path, repo.held) is False
        state = self._state(repo)
        pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert git.push.holds(repo.path, state.fix.fix.commit_sha) is True

    def test_the_closeout_stops_holding_after_a_rebase(
        self, tmp_path, publishing_on,
    ):
        """Regression: --finish blocked on a SHA the rebase it advised orphaned."""
        repo = _held_fix_branch(tmp_path)
        pr.state.save_state(repo.path / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="feature", pr_number=42,
                                head_sha=repo.held, worktree_root=str(repo.path)),
            fix=_fix(commit_sha=repo.held, commit_status=CommitStatus.PUSH_HELD,
                           head_sha=repo.held),
        ))
        ctx = make_ctx(branch="feature", worktree_root=repo.path,
                       head_sha=repo.replay, target_dir=repo.path / "target")
        with patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(ctx, PRReport())
        saved = pr.state.load_state(repo.path / "target")
        assert saved.fix.fix.commit_sha == repo.replay
        assert saved.fix.fix.commit_status == CommitStatus.PUSHED

    def test_two_rounds_each_reach_their_own_replay(self, tmp_path):
        """Every fix pass commits under one static subject, so identity is content.

        A snapshot spans rounds — a thread fixed two commits ago still cites the
        commit that fixed it — so mapping both orphans onto whichever replay was
        found first would post one round's permalink under the other's work.
        """
        repo = _two_held_rounds(tmp_path)
        state = _make_state(_fix(
            commit_sha=repo.second, commit_status=CommitStatus.PUSH_HELD,
            head_sha=repo.second,
            items=[
                ItemOutcome(id="t1", outcome=FixOutcome.FIXED,
                              commit_sha=repo.first, read_sha=repo.first),
                ItemOutcome(id="t2", outcome=FixOutcome.FIXED,
                              commit_sha=repo.second, read_sha=repo.second),
            ],
        ))
        pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert repo.first_replay != repo.second_replay
        assert state.fix.fix.items[0].commit_sha == repo.first_replay
        assert state.fix.fix.items[1].commit_sha == repo.second_replay
        assert state.fix.fix.commit_sha == repo.second_replay

    def test_two_commits_carrying_one_patch_are_not_guessed_between(
        self, tmp_path,
    ):
        """Applied, undone, redone: the branch offers two answers, so there is none."""
        repo = _duplicated_fix(tmp_path)
        state = _make_state(_fix(
            commit_sha=repo.held, commit_status=CommitStatus.PUSH_HELD,
            head_sha=repo.held,
        ))
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert any(repo.held in w and "pr comments --fix" in w for w in warned)

    def test_the_deferred_replies_are_let_out(self, tmp_path):
        """The other gate the orphan jammed: every reply cites the commit."""
        repo = _held_fix_branch(tmp_path)
        state = self._state(repo)
        state.identity.worktree_root = str(repo.path)
        state.fix.replies_pending = True
        logged = []
        with patch.object(core.log, "info", side_effect=logged.append), \
                patch.object(pr.thread_replies, "reply_to_fixed", return_value=1) as reply, \
                patch.object(pr.settlement, "resolve_fixed_threads"):
            pr.history_rewrite.follow_history_rewrite(state, repo.path)
            review.closeout.post_pending_fix_replies(state, "owner/repo", 42, {})
        assert not any("Push still pending" in m for m in logged)
        assert reply.call_args[0][4].sha == repo.replay

    def test_a_rebase_nobody_pushed_still_holds(self, tmp_path):
        """The replay is real and local — which is an ordinary unpushed commit."""
        repo = _held_fix_branch(tmp_path, push=False)
        state = self._state(repo)
        pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.replay
        assert git.push.holds(repo.path, state.fix.fix.commit_sha) is False
        review.closeout.push_held_commit(state, repo.path)
        assert state.fix.fix.commit_status == CommitStatus.PUSH_HELD

    def test_a_commit_that_was_never_pushed_is_left_alone(self, tmp_path):
        """No rewrite happened: the SHA is on the branch and simply not sent."""
        repo = _held_fix_branch(tmp_path, rebase=False, push=False)
        state = self._state(repo)
        pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert git.push.holds(repo.path, state.fix.fix.commit_sha) is False
        review.closeout.push_held_commit(state, repo.path)
        assert state.fix.fix.commit_status == CommitStatus.PUSH_HELD

    def test_an_orphan_with_no_replay_holds_and_says_how_to_recover(
        self, tmp_path,
    ):
        """Dropped, squashed, reworded: the work is not there under any name."""
        repo = _held_fix_branch(tmp_path, drop=True)
        state = self._state(repo)
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert any(repo.held in w and "pr comments --fix" in w for w in warned)

    def test_an_orphan_says_nothing_once_the_hold_is_over(self, tmp_path):
        """A pushed status has nothing to unblock, so the warning is only noise."""
        repo = _held_fix_branch(tmp_path, drop=True)
        state = self._state(repo, status=CommitStatus.PUSHED)
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            pr.history_rewrite.follow_history_rewrite(state, repo.path)
        assert warned == []

    def test_a_snapshot_with_no_shas_asks_git_nothing(self):
        def boom(*a, **kw):
            raise AssertionError(f"a snapshot with nothing recorded ran git: {a}")

        state = _make_state(_fix(items=[ItemOutcome(id="t1")]))
        with patch.object(git.client, "run", boom):
            pr.history_rewrite.follow_history_rewrite(state, Path("/fake"))
        assert state.fix.fix.commit_sha == ""
