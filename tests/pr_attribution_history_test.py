"""pr.attribution: which commit each row cites, across rebases and hand-landed work."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _AFTER_THE_REVIEW, _BEFORE_THE_REVIEW, _PASS_SHA, _ROUND_1_SHA, _THE_REVIEW_COMMENT,
    _git_ran, _no_published_summary, content,
)
from conftest import git_out, run_checked
import git.client
import git.push
import git.topology
from git.land import CommitStatus
import pr.thread_replies
import pr.attribution
import pr.thread_context
import pr.history_rewrite
import pr.summary_model
import pr.summary_publish
import pr.summary_render
from pr.summary_model import ActionCell
from pr.fix import FixOutcome, FixRecord, RECONCILED_REASON, SETTLED_REASON, SettledBy
from pr.thread_models import CommentItem, ReportThread


# ── per-row attribution when work landed by hand across commits ────────────


def _git_at(wt, *args, when=""):
    env = dict(os.environ)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    run_checked(["git", "-C", str(wt), *args], env=env)


def _rev(wt, rev):
    return git_out(wt, "rev-parse", rev).strip()


@pytest.fixture
def hand_landed_branch(worktree):
    """Two hand-landed commits after the review, each at its own line.

    `snapshot` is where the fix pass left the branch, so both commits are
    outside it and `_reconciled_commit` reads UNDETERMINED — the state the
    per-row resolver exists for, and the one a row the pass never landed must
    not be resolved through. `stale` predates the review and is the control: a
    line whose only commit is older cannot be what carried a fix made later.
    """
    hooks = worktree / ".git" / "empty-hooks"
    hooks.mkdir()
    _git_at(worktree, "config", "user.email", "test@example.com")
    _git_at(worktree, "config", "user.name", "Test")
    _git_at(worktree, "config", "commit.gpgsign", "false")
    _git_at(worktree, "config", "core.hooksPath", str(hooks))
    (worktree / "a.py").write_text("one\ntwo\nthree\n")
    _git_at(worktree, "add", "-A")
    _git_at(worktree, "commit", "-qm", "base")
    _git_at(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
    (worktree / "a.py").write_text("one\ntwo\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line three, before the review",
            when=_BEFORE_THE_REVIEW)
    stale = _rev(worktree, "HEAD")
    snapshot = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("ONE\ntwo\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line one, by hand", when=_AFTER_THE_REVIEW)
    first = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("ONE\nTWO\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line two, by hand", when=_AFTER_THE_REVIEW)
    return SimpleNamespace(
        path=worktree, snapshot=snapshot, stale=stale,
        first=first, second=_rev(worktree, "HEAD"),
    )


def _reviewed(tid, database_id):
    return ReportThread(id=tid, comments=[
        {"databaseId": database_id, "createdAt": _THE_REVIEW_COMMENT},
    ])


def _undetermined_pass(branch):
    """The pass's own view of the branch: nothing recorded, HEAD moved on."""
    record = FixRecord(
        commit_status=CommitStatus.NO_CHANGES, head_sha=branch.snapshot,
    )
    with patch.object(git.push, "holds", return_value=True):
        cp = pr.history_rewrite.reconciled_commit(record, CommitStatus.NO_CHANGES, branch.path)
    assert cp.claim is pr.attribution.CommitClaim.UNDETERMINED, "fixture must reach the gap"
    return cp


def _row(tid, line, summary, **kw):
    return CommentItem(
        id=tid, file="a.py", line=line, reviewer="kgn", summary=summary, **kw,
    )


def _summary_over(content, branch, entries, threads):
    cp = _undetermined_pass(branch)
    with patch.object(git.topology, "default_branch_cached", return_value="main"):
        return pr.summary_render.build_summary_body(
            content(fixed=entries), cp, "owner/repo", 42, threads,
            wt_path=branch.path,
        )


@pytest.fixture
def one_hand_landed_commit(worktree):
    """One hand-landed commit after the snapshot, over an earlier round's work.

    The shape a count of landed commits waves through: the branch moved by
    exactly one commit, so reconciliation used to read that as "the operator
    landed the pass's work" and credit it to every row the record held.
    `round_one` is a prior round's fix — landed after the review, before the
    snapshot, and recorded against no SHA — so a row anchored to its line is one
    the new commit demonstrably does not carry. `stale` predates the review and
    is the control.
    """
    hooks = worktree / ".git" / "empty-hooks"
    hooks.mkdir()
    _git_at(worktree, "config", "user.email", "test@example.com")
    _git_at(worktree, "config", "user.name", "Test")
    _git_at(worktree, "config", "commit.gpgsign", "false")
    _git_at(worktree, "config", "core.hooksPath", str(hooks))
    (worktree / "a.py").write_text("one\ntwo\nthree\n")
    _git_at(worktree, "add", "-A")
    _git_at(worktree, "commit", "-qm", "base")
    _git_at(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
    (worktree / "a.py").write_text("one\ntwo\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line three, before the review",
            when=_BEFORE_THE_REVIEW)
    stale = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("one\nTWO\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line two, round one", when=_AFTER_THE_REVIEW)
    snapshot = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("ONE\nTWO\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line one, by hand", when=_AFTER_THE_REVIEW)
    landed = git_out(worktree, "rev-list", f"{snapshot}..HEAD").split()
    assert len(landed) == 1, "one commit is the shape under test"
    return SimpleNamespace(
        path=worktree, snapshot=snapshot, stale=stale,
        round_one=snapshot, landed=landed[0],
    )


# ── _attribute_commit ───────────────────────────────────────────────────────


class TestAttributeCommit:
    """One resolver answers "which commit carries this thread?" for every surface.

    Each renderer used to derive that itself, and an empty `commit_sha` meant
    something different to each of them — so a fix that made one honest
    inverted another. The claim is the discriminator that lets them disagree
    about *rendering* without disagreeing about the facts.
    """

    @staticmethod
    def _entry(**kw):
        return CommentItem(id="t1", summary="fix it", file="a.py", line=1, **kw)

    def test_a_recorded_commit_outranks_the_running_pass(self):
        """An earlier round's commit is the one that carries the change."""
        got = pr.attribution.attribute_commit(
            self._entry(commit_sha=_ROUND_1_SHA),
            pr.attribution.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is pr.attribution.CommitClaim.RECORDED
        assert got.sha == _ROUND_1_SHA

    def test_an_entry_the_pass_landed_rides_the_pass_commit(self):
        got = pr.attribution.attribute_commit(
            self._entry(commit_sha=_PASS_SHA),
            pr.attribution.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is pr.attribution.CommitClaim.PASS
        assert got.sha == _PASS_SHA

    def test_an_unpublished_pass_commit_is_not_citable(self):
        """A SHA the remote does not have would 404 for whoever clicks it."""
        got = pr.attribution.attribute_commit(
            self._entry(commit_sha=_PASS_SHA),
            pr.attribution.CommitPushResult(_PASS_SHA, "push_failed", "rejected"),
        )
        assert got.claim is pr.attribution.CommitClaim.PASS
        assert got.cited is False

    def test_an_entry_the_pass_never_recorded_claims_nothing(self):
        """The pass committed and this entry is not in that commit."""
        got = pr.attribution.attribute_commit(
            self._entry(), pr.attribution.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is pr.attribution.CommitClaim.UNRECORDED
        assert got.cited is False

    def test_an_undetermined_pass_lends_nothing(self):
        """Commits landed outside the pass; none of them answers for a row."""
        got = pr.attribution.attribute_commit(
            self._entry(),
            pr.attribution.CommitPushResult(_PASS_SHA, "pushed", "",
                                claim=pr.attribution.CommitClaim.UNDETERMINED),
        )
        assert got.claim is pr.attribution.CommitClaim.UNDETERMINED
        assert got.cited is False

    def test_a_pass_with_no_commit_leaves_the_row_to_the_pass(self):
        """Nothing was committed by anyone, so there is nothing row-specific to say."""
        got = pr.attribution.attribute_commit(
            self._entry(), pr.attribution.CommitPushResult(None, "no_changes", ""),
        )
        assert got.claim is pr.attribution.CommitClaim.PASS
        assert got.cited is False

    def test_the_pass_stamps_the_entries_it_landed(self):
        """The one write of thread → commit; every reader goes through the resolver."""
        fresh, earlier = self._entry(), self._entry(commit_sha=_ROUND_1_SHA)
        pr.attribution.stamp_pass_commit([fresh, earlier], _PASS_SHA)
        assert fresh.commit_sha == _PASS_SHA
        assert earlier.commit_sha == _ROUND_1_SHA


# ── per-line addressing commits ─────────────────────────────────────────────


class TestAddressingCommitIsPerLine:
    """Two threads on one file must not both be sent to the same commit.

    The already-addressed reply asked which branch commit last touched the
    *file*, so on a file two reviewers had both commented on, whichever commit
    landed last was cited to both of them — including the reviewer whose lines
    that commit never touched. The lookup is over the thread's line now, which
    is the only mechanism that can answer for a change no fix pass committed.
    """

    @staticmethod
    def _git(wt, *args):
        git_out(wt, *args)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def branch(self, worktree):
        """A branch off origin/main with one commit per line of `a.py`."""
        # Empty hooks dir for the same reason the fix-pass fixture has one: a
        # global core.hooksPath would run the developer's own pre-commit here.
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "a.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        (worktree / "a.py").write_text("ONE\ntwo\n")
        self._git(worktree, "commit", "-qam", "address line one")
        first = self._sha(worktree, "HEAD")
        (worktree / "a.py").write_text("ONE\nTWO\n")
        self._git(worktree, "commit", "-qam", "address line two")
        return SimpleNamespace(path=worktree, first=first,
                               second=self._sha(worktree, "HEAD"))

    def test_each_line_resolves_to_the_commit_that_changed_it(self, branch):
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            assert pr.attribution.find_addressing_commit(branch.path, "a.py", 1) == branch.first
            assert pr.attribution.find_addressing_commit(branch.path, "a.py", 2) == branch.second

    def test_a_thread_with_no_line_claims_no_commit(self, branch):
        """A file-wide thread has no line history to read, so it cites nothing."""
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            assert pr.attribution.find_addressing_commit(branch.path, "a.py", 0) is None

    def test_a_line_past_the_end_of_the_file_claims_no_commit(self, branch):
        """git refuses the range rather than answering — nothing is invented."""
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            assert pr.attribution.find_addressing_commit(branch.path, "a.py", 99) is None

    def test_two_threads_on_one_file_cite_different_commits(self, branch):
        entries = [
            CommentItem(id="t1", summary="line one", file="a.py", line=1),
            CommentItem(id="t2", summary="line two", file="a.py", line=2),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 111}]),
            "t2": ReportThread(id="t2", comments=[{"databaseId": 222}]),
        }
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.post_already_addressed_replies(
                entries, threads_by_id, "owner/repo", 42, branch.path,
            )
        # The "Addressed in" line only: the blob permalink beside it pins HEAD
        # for both threads, because where to read the code is the same question
        # for both and when it became true is not.
        cited = {
            call[0][2]: [ln for ln in call[0][3].splitlines()
                         if ln.startswith("Addressed in")]
            for call in post.call_args_list
        }
        assert cited[111] == [
            f"Addressed in [`{branch.first[:7]}`]"
            f"(https://github.com/owner/repo/commit/{branch.first}).",
        ]
        assert cited[222] == [
            f"Addressed in [`{branch.second[:7]}`]"
            f"(https://github.com/owner/repo/commit/{branch.second}).",
        ]


# ── attribution survives a history rewrite ─────────────────────────────────


class TestAttributionSurvivesARebase:
    """A rebase must not turn "no evidence" into a confident wrong citation.

    Per-row attribution rests on two independent guards, and a rebase destroys
    both in one stroke. The line the row recorded is a coordinate in the tree it
    was read in, so once the file moves underneath it the `git log -L` walk
    answers about whatever code inherited the number; and the committer date the
    staleness check reads is reset on every commit by the rebase, so the base
    commit of the branch suddenly postdates every review comment.

    Both shapes published the branch's *base* commit as the commit carrying a
    fix — a commit a reviewer can open and find nothing in.
    """

    @staticmethod
    def _git(wt, *args, when=""):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        run_checked(["git", "-C", str(wt), *args], env=env)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def rebased(self, worktree):
        """A branch whose commits all predate the review, then rebased.

        `moved.py` loses its opening lines after the row was read, so the
        recorded line number slides onto code the row was never about — the
        stale-coordinate shape, and the one that published the base commit four
        times. `still.py` is untouched after the read, so its coordinate stays
        honest, leaving only the rewritten committer date to catch — the
        timestamp shape. Every commit is authored before the review, so a
        correct run cites neither.
        """
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "moved.py").write_text("alpha\nbeta\ngamma\n")
        (worktree / "still.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        # The branch's own base commit — the one wrongly published. It is the
        # last commit to touch both recorded lines.
        (worktree / "moved.py").write_text("alpha\nbeta\nGAMMA\n")
        (worktree / "still.py").write_text("one\nTWO\n")
        self._git(worktree, "commit", "-qam", "feat: the branch's base work",
                  when=_BEFORE_THE_REVIEW)
        read = self._sha(worktree, "HEAD")
        # Lands after the row was read and shifts moved.py up, so the recorded
        # line 3 now names line 1's code and resolves to the base commit.
        (worktree / "moved.py").write_text("GAMMA\n")
        self._git(worktree, "commit", "-qam", "drop the header",
                  when=_BEFORE_THE_REVIEW)
        # The rebase: every committer date becomes now, so the branch's base
        # commit postdates the review comment it predates in authorship.
        # `--force-rebase` because the branch is already on origin/main here and
        # a fast-forward would rewrite nothing — leaving the dates intact and the
        # defect unreproduced, which is a fixture that cannot fail.
        self._git(worktree, "rebase", "--force-rebase", "origin/main")
        return SimpleNamespace(
            path=worktree,
            read=self._sha(worktree, "HEAD~1"),
            base=self._sha(worktree, "HEAD~1"),
        )

    @staticmethod
    def _thread(tid):
        return ReportThread(id=tid, comments=[
            {"databaseId": 1, "createdAt": _THE_REVIEW_COMMENT},
        ])

    def _framing(self, entry, wt_path):
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            history = pr.attribution.AddressingHistory(wt_path)
            return history.framing(entry, self._thread(entry.id))

    def test_a_stale_line_coordinate_cites_no_commit(self, rebased):
        """The recorded line now points at code the row was never about."""
        entry = CommentItem(id="t1", summary="guard it", file="moved.py",
                            line=1, read_sha=rebased.read)
        assert not self._framing(entry, rebased.path).cited

    def test_a_rewritten_committer_date_claims_no_fix(self, rebased):
        """The line is honest; only the rebase makes the commit look recent.

        This row's coordinate still points at the code it was read against, so
        the commit genuinely is the one behind that line and naming it as
        context ("Addressed in") stays true. What the rebase must not buy is the
        stronger reading: `in_response` is the claim that the work landed
        *because* the reviewer asked, and a commit authored before the comment
        did not, however recently the rebase re-stamped it.
        """
        entry = CommentItem(id="t2", summary="rename it", file="still.py",
                            line=2, read_sha=rebased.read)
        assert not self._framing(entry, rebased.path).in_response

    def test_the_summary_row_reports_no_fix_after_a_rebase(self, rebased):
        """End to end: the shape that published the base commit as "Fixed in".

        The reviewer-facing defect was the whole row, not the SHA alone — a
        rebased branch turned every satisfied row into a claimed fix, counted it
        as one, and linked the base commit as the thing that carried it.
        """
        entry = CommentItem(id="t1", summary="guard it", file="moved.py",
                            line=1, read_sha=rebased.read)
        cp = pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            body = pr.summary_render.build_summary_body(
                pr.summary_model.RoundContent(
                    by_outcome={FixOutcome.ALREADY_ADDRESSED: [entry]},
                    issue_comments=[], review_body_comments=[],
                ),
                cp, "owner/repo", 42, {entry.id: self._thread("t1")},
                wt_path=rebased.path,
            )
        assert f"Fixed in [`{rebased.base}`]" not in body
        assert "Fixed in" not in body
        assert "Already addressed" in body
        assert "1 already addressed" in body


class TestRowsResolveTheirOwnCommitAcrossHandLandedWork:
    """Several commits outside the pass is a fact about the branch, not the row.

    Reconciliation could only ask "did this branch move?", so more than one
    commit left it with nothing per-row to say and it declined for every row at
    once — correctly, since the SHA it would otherwise stamp is HEAD, picked for
    having no relationship to any of them. The line a thread is anchored to is a
    different question with a different answer, and it is answerable: the commit
    that changed that line after the reviewer asked is evidence about that
    thread.

    The reply path has resolved rows this way since #820. Only the table
    declined, so one PR carried a thread reply reading "Fixed in `abc1234`"
    beside a summary row reading "Fix applied (commit not recorded)".
    """

    def test_each_row_cites_the_commit_that_carried_it(
        self, content, hand_landed_branch,
    ):
        """The defect: both rows rendered "commit not recorded" together."""
        branch = hand_landed_branch
        body = _summary_over(
            content,
            branch,
            [_row("t1", 1, "first point"), _row("t2", 2, "second point")],
            {"t1": _reviewed("t1", 111), "t2": _reviewed("t2", 222)},
        )
        assert f"Fixed in [`{branch.first}`]" in body
        assert f"Fixed in [`{branch.second}`]" in body
        assert "commit not recorded" not in body
        assert "**2 fixed**" in body

    def test_a_row_whose_line_predates_the_review_is_not_credited(
        self, content, hand_landed_branch,
    ):
        """A commit older than the comment cannot be the fix that answered it."""
        body = _summary_over(
            content, hand_landed_branch, [_row("t3", 3, "third point")],
            {"t3": _reviewed("t3", 333)},
        )
        assert hand_landed_branch.stale not in body
        assert "Fix applied (commit not recorded)" in body

    def test_a_row_with_no_line_stays_uncited(self, content, hand_landed_branch):
        """A file-wide thread has no line history, so nothing resolves it."""
        body = _summary_over(
            content, hand_landed_branch, [_row("t4", 0, "file-wide point")],
            {"t4": _reviewed("t4", 444)},
        )
        assert "Fix applied (commit not recorded)" in body

    def test_a_render_with_no_worktree_still_declines(
        self, content, hand_landed_branch,
    ):
        """No tree to read is the case reconciliation was right to decline."""
        cp = _undetermined_pass(hand_landed_branch)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            body = pr.summary_render.build_summary_body(
                content(fixed=[_row("t1", 1, "first point")]), cp,
                "owner/repo", 42, {"t1": _reviewed("t1", 111)},
            )
        assert hand_landed_branch.first not in body
        assert "Fix applied (commit not recorded)" in body

    def test_the_table_and_the_reply_name_the_same_commit(
        self, content, hand_landed_branch,
    ):
        """One thread, two surfaces — they read the same resolver or they lie."""
        branch = hand_landed_branch
        entry = CommentItem(id="t1", summary="first point", file="a.py", line=1)
        cp = _undetermined_pass(branch)
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t1": _reviewed("t1", 111)}, "owner/repo", 42,
                cp, branch.path,
            )
        reply = post.call_args[0][3]
        row = _summary_over(
            content, branch, [_row("t1", 1, "first point")],
            {"t1": _reviewed("t1", 111)},
        )
        assert branch.first[:7] in reply
        assert f"Fixed in [`{branch.first}`]" in row

    def test_a_resolved_row_is_not_warned_about(self, hand_landed_branch, capsys):
        """The warning counts rows the table publishes without a claim.

        A row the table now cites is attributed, so counting it would report an
        attribution problem no reader of that table can find.
        """
        cp = _undetermined_pass(hand_landed_branch)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            pr.summary_publish._warn_unattributed_fixes(
                [_row("t1", 1, "first point")], cp, None,
                pr.attribution.AddressingHistory(hand_landed_branch.path),
                {"t1": _reviewed("t1", 111)},
            )
        assert "no commit to attribute" not in capsys.readouterr().err

    def test_a_row_that_stays_uncited_is_still_warned_about(
        self, hand_landed_branch, capsys,
    ):
        cp = _undetermined_pass(hand_landed_branch)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            pr.summary_publish._warn_unattributed_fixes(
                [_row("t3", 3, "third point")], cp, None,
                pr.attribution.AddressingHistory(hand_landed_branch.path),
                {"t3": _reviewed("t3", 333)},
            )
        assert "1 fixed row(s) have no commit" in capsys.readouterr().err


class TestRowsTheFixPassDidNotLandCiteNoCommit:
    """A row somebody else settled outranks the line history.

    The resolver above answers "which commit changed the line this thread is
    anchored to, after the reviewer asked". For a row the pass fixed that is
    evidence. For a row it did not, it is a coincidence: reconciliation used to
    stamp `fixed` on any thread that merely looked settled on GitHub — resolved
    covers answered, deferred and declined — so a busy file's newest commit was
    credited to a thread whose own standing reply said it was tracked elsewhere
    rather than fixed here.

    Same tree as the class above, so the only difference between a cited row and
    an uncited one is who the record says settled it.
    """

    def test_a_reconciled_row_declines_the_commit_that_touched_its_line(
        self, content, hand_landed_branch,
    ):
        body = _summary_over(
            content, hand_landed_branch,
            [_row("t1", 1, "first point", settled_by=SettledBy.RECONCILIATION)],
            {"t1": _reviewed("t1", 111)},
        )
        assert hand_landed_branch.first not in body
        assert ActionCell.RECONCILED in body

    def test_a_settled_row_declines_it_too(self, content, hand_landed_branch):
        """`--settle` already promises this cell when no commit resolves."""
        body = _summary_over(
            content, hand_landed_branch,
            [_row("t1", 1, "first point", settled_by=SettledBy.OPERATOR)],
            {"t1": _reviewed("t1", 111)},
        )
        assert hand_landed_branch.first not in body
        assert ActionCell.RECONCILED in body

    def test_the_reply_declines_the_commit_the_table_declined(
        self, hand_landed_branch,
    ):
        """The drain path carries the provenance, and the reply reads it too.

        `CommentItem.from_outcome` copies `settled_by` off the record for
        exactly this: a reply built from a replayed entry has to reach the same
        answer the summary row beside it reaches, and the reason text it also
        carries is prose for the reviewer rather than a signal.
        """
        branch = hand_landed_branch
        entry = CommentItem(id="t1", summary="first point", file="a.py", line=1,
                            settled_by=SettledBy.RECONCILIATION)
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t1": _reviewed("t1", 111)}, "owner/repo", 42,
                _undetermined_pass(branch), branch.path,
            )
        reply = post.call_args[0][3]
        assert branch.first[:7] not in reply
        assert "/commit/" not in reply

    def test_a_row_the_pass_settled_is_cited_however_its_reason_reads(
        self, content, hand_landed_branch,
    ):
        """The reason channel is dead: prose alone withholds no commit."""
        body = _summary_over(
            content, hand_landed_branch,
            [_row("t1", 1, "first point", reason=RECONCILED_REASON)],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{hand_landed_branch.first}`]" in body

    def test_acted_entry_with_a_handled_outside_reason_still_reads_applied(
        self, hand_landed_branch,
    ):
        """`acted` outranks `_handled_outside` in the reply's wording, not its citation.

        `_attribute_commit` already declined a commit for this row, which is why
        it lands in `_reply_to_fixed`'s unattributed bucket and reaches
        `_post_already_addressed_replies` with `acted=True`. `line 3`'s only
        commit predates the review, so `landed_after` is False there too — but
        `AddressingHistory.framing` checks `acted and not landed_after` before
        `_handled_outside`, so the reply still reads "Applied", not "Already
        addressed": the pass is the one that reported this outcome, and a commit
        that predates the review cannot outrank what the pass itself said
        happened.
        """
        branch = hand_landed_branch
        entry = CommentItem(id="t3", summary="third point", file="a.py", line=3,
                            settled_by=SettledBy.RECONCILIATION)
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
                patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t3": _reviewed("t3", 333)}, "owner/repo", 42,
                _undetermined_pass(branch), branch.path,
            )
        reply = post.call_args[0][3]
        assert reply.startswith(f"{pr.thread_replies.APPLIED_REPLY_PREFIX}:")
        assert pr.thread_replies.ADDRESSED_REPLY_PREFIX not in reply
        assert branch.stale[:7] not in reply

    def test_a_recorded_commit_survives_the_decline(
        self, content, hand_landed_branch,
    ):
        """Only inference is refused. A SHA `--settle` resolved is a record."""
        body = _summary_over(
            content, hand_landed_branch,
            [_row("t1", 1, "first point", settled_by=SettledBy.OPERATOR,
                  commit_sha=hand_landed_branch.second)],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{hand_landed_branch.second}`]" in body

    def test_a_row_the_pass_itself_settled_still_cites_its_line(
        self, content, hand_landed_branch,
    ):
        """The control: the decline is the provenance's doing, not the fixture's."""
        body = _summary_over(
            content, hand_landed_branch, [_row("t1", 1, "first point")],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{hand_landed_branch.first}`]" in body

    @pytest.mark.parametrize(
        "settled_by", [SettledBy.RECONCILIATION, SettledBy.OPERATOR],
    )
    def test_every_provenance_but_the_pass_reads_as_handled_outside(
        self, settled_by,
    ):
        assert pr.attribution.handled_outside(CommentItem(id="t1", settled_by=settled_by))

    def test_the_pass_own_entry_is_not_one_of_them(self):
        """Including one whose reason happens to read like the reconciler's."""
        assert not pr.attribution.handled_outside(CommentItem(id="t1"))
        assert not pr.attribution.handled_outside(
            CommentItem(id="t1", reason=RECONCILED_REASON),
        )
        assert not pr.attribution.handled_outside(
            CommentItem(id="t1", reasoning=SETTLED_REASON),
        )


class TestOneHandLandedCommitIsStillAskedOfEachRow:
    """A branch that moved by one commit says nothing about which row it carries.

    Reconciliation treated the single-commit case as settled: every row with no
    commit of its own was stamped with the one that landed, and the summary told
    reviewers a finding was fixed in a commit that does not contain the fix.
    Counting the landed commits could not catch it — that asks whether the
    *branch* moved by more than one commit, and a row's fix being in a given
    commit is a fact about the row.

    So the single-commit case asks each row what the multi-commit case already
    asked: which commit changed the line this thread is anchored to, after the
    reviewer raised it. The class above is the same questions over a branch that
    moved by two.
    """

    def test_a_prior_round_row_keeps_the_commit_that_carried_it(
        self, content, one_hand_landed_commit,
    ):
        """The defect: this row was credited to the commit landed after it."""
        branch = one_hand_landed_commit
        body = _summary_over(
            content, branch, [_row("t2", 2, "second point")],
            {"t2": _reviewed("t2", 222)},
        )
        assert f"Fixed in [`{branch.round_one}`]" in body
        assert f"Fixed in [`{branch.landed}`]" not in body

    def test_the_row_the_new_commit_carries_is_still_cited(
        self, content, one_hand_landed_commit,
    ):
        """The control: declining is the row's evidence talking, not the fixture."""
        branch = one_hand_landed_commit
        body = _summary_over(
            content, branch, [_row("t1", 1, "first point")],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{branch.landed}`]" in body

    def test_a_row_whose_line_predates_the_review_is_not_credited(
        self, content, one_hand_landed_commit,
    ):
        branch = one_hand_landed_commit
        body = _summary_over(
            content, branch, [_row("t3", 3, "third point")],
            {"t3": _reviewed("t3", 333)},
        )
        assert "Fixed in [`" not in body
        assert ActionCell.UNATTRIBUTED in body
        # Where to look stays knowable even when who landed it does not: the
        # file cell pins the tree that holds the work.
        assert f"/blob/{branch.landed[:7]}/a.py" in body

    def test_a_decomposed_body_row_is_not_credited_either(
        self, content, one_hand_landed_commit,
    ):
        """A review-level comment anchors to no line, so no history reads for it.

        The honest answer is the one an anchored row with nothing at its line
        gets: no citation. Line history is the only per-row evidence there is,
        and a row that cannot supply it is a row nothing supports.
        """
        branch = one_hand_landed_commit
        entry = CommentItem(
            id="c-9-1", summary="a body point", reviewer="kgn",
            source_type="issue", source_id="9",
        )
        body = _summary_over(content, branch, [entry], {})
        assert "Fixed in [`" not in body
        assert ActionCell.UNATTRIBUTED in body

    def test_the_reply_names_the_commit_the_table_names(
        self, one_hand_landed_commit,
    ):
        """One prior-round thread, two surfaces, one resolver."""
        branch = one_hand_landed_commit
        entry = CommentItem(id="t2", summary="second point", file="a.py", line=2)
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t2": _reviewed("t2", 222)}, "owner/repo", 42,
                _undetermined_pass(branch), branch.path,
            )
        reply = post.call_args[0][3]
        assert f"Fixed in [`{branch.round_one}`]" in reply
        assert f"/commit/{branch.landed}" not in reply


# ── default-branch resolution in commit lookups ────────────────────────────


class TestCommitLookupsUseDefaultBranch:
    """`origin/main` is not universal — a hardcoded base silently returns nothing."""

    def test_branch_commit_log_uses_resolved_branch(self, tmp_path):
        with (
            patch.object(git.topology, "default_branch_cached", return_value="trunk"),
            patch.object(git.client, "run") as run,
        ):
            run.return_value = _git_ran(0, stdout="abc1234 fix: thing\n")
            assert pr.thread_context.branch_commit_log(tmp_path) == "abc1234 fix: thing"
        assert "origin/trunk..HEAD" in run.call_args[0]

    def test_find_addressing_commit_uses_resolved_branch(self, tmp_path):
        with (
            patch.object(git.topology, "default_branch_cached", return_value="trunk"),
            patch.object(git.client, "run") as run,
        ):
            run.return_value = _git_ran(0, stdout="deadbeef\n")
            assert pr.attribution.find_addressing_commit(tmp_path, "a.py", 10) == "deadbeef"
        assert "origin/trunk..HEAD" in run.call_args[0]

    def test_branch_commit_log_without_worktree(self):
        assert pr.thread_context.branch_commit_log(None) == ""
