"""Tests for `pr.fix_state` — assembling the record, and writing it once.

The three functions that turn a round's buckets into what the state file holds.
They were tested through `test_review_threads.py`, which could only reach them
by loading a binary; they have a module now, so they are tested against it.

The write is the part worth isolating. It is one transaction by design — the
comment tally on disk predates the pass and learns of the threads it resolved
only from the delta applied alongside the fix record — and a second save, or a
delta dropped on the way in, leaves `pr status` reporting threads open that
GitHub has already closed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import REPO_ROOT, make_ctx

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git.land import CommitStatus  # noqa: E402
from pr import fix_state  # noqa: E402
from pr.comments_fix import FixSummary  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome, FixRecord  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402
from pr.thread_models import CommentItem  # noqa: E402

_STATE_WORKTREE = "/wt"


def _fix(items=(), *, commit_sha="", commit_status=None, head_sha="", **kwargs):
    """A comment fix pass carrying these outcomes, as the domain stores them."""
    return FixSummary(
        fix=FixRecord(
            items=list(items), commit_sha=commit_sha,
            commit_status=CommitStatus(commit_status) if commit_status else None,
            head_sha=head_sha,
        ),
        **kwargs,
    )


def _make_state(fix=None):
    return PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1,
            head_sha="abc1234", worktree_root=_STATE_WORKTREE,
        ),
        fix=fix or _fix(),
    )


class TestFixPassResolutionsReachTheTally:
    """The fix pass resolves after the counts were saved, same as the drain.

    This is the commoner path of the two: a pass that fixed, pushed, replied and
    resolved in one run leaves `replies_pending` false, so the drain returns
    early and never sees those threads. `fix_state.persist` is where the pass
    writes its own results, and so where the delta has to land.
    """

    def _persist(self, by_state, resolved):
        ctx = make_ctx()
        state = _make_state(_fix())
        state.comments.by_state = dict(by_state)
        with patch("pr.state.load_or_init", return_value=state), \
             patch("pr.state.save_state") as save:
            fix_state.persist(_fix(), Path("/wt"), ctx, None,
                                  resolved=resolved)
        assert save.called, "the pass must still save what it persisted"
        return state.comments

    def test_the_pass_moves_what_it_resolved(self):
        comments = self._persist(
            {"new": 2, "addressed": 1},
            [ThreadState.NEW, ThreadState.ADDRESSED],
        )
        assert comments.by_state[ThreadState.NEW] == 1
        assert comments.by_state[ThreadState.ADDRESSED] == 0
        assert comments.by_state[ThreadState.RESOLVED] == 2

    def test_a_pass_that_resolved_nothing_leaves_the_tally_alone(self):
        """The default, and the shape of every caller that predates the delta."""
        assert self._persist({"new": 2}, []).by_state == {"new": 2}

    def test_omitting_the_argument_is_the_same_as_none_resolved(self):
        ctx = make_ctx()
        state = _make_state(_fix())
        state.comments.by_state = {"new": 2}
        with patch("pr.state.load_or_init", return_value=state), \
             patch("pr.state.save_state"):
            fix_state.persist(_fix(), Path("/wt"), ctx, None)
        assert state.comments.by_state == {"new": 2}


class TestTheFixRecordCarriesEveryOutcome:
    """What the pass persists about each entry, one outcome at a time."""

    def _entry(self, verification):
        return CommentItem(
            id="t1", file="f.go", line=10, reviewer="kgn",
            summary="drop the nil-logger guard",
            classification="actionable_suggestion",
            verification=verification, complexity="low", state=ThreadState.NEW,
        )

    def test_the_record_carries_the_already_addressed_outcome(self):
        entry = self._entry("already_addressed")
        record = fix_state.fix_record_for(
            {FixOutcome.ALREADY_ADDRESSED: [entry]},
        )
        assert len(record.items) == 1
        assert record.items[0].outcome == FixOutcome.ALREADY_ADDRESSED

    def test_an_outcome_the_caller_did_not_name_records_nothing(self):
        """The mapping is the whole vocabulary of a call — nothing is implied."""
        assert fix_state.fix_record_for({}).items == []

    def test_a_declined_thread_is_recorded_as_declined(self):
        """Not folded into needs-human: the state file keeps the two apart."""
        entry = CommentItem(id="t9", reviewer="kgn", reason="premise does not hold")
        record = fix_state.fix_record_for({FixOutcome.DECLINED: [entry]})
        assert record.items[0].outcome == FixOutcome.DECLINED
        assert record.items[0].reason == "premise does not hold"

    def test_the_reviewer_is_kept_beside_the_record_not_on_it(self):
        """`ItemOutcome` is every domain's; a login is only the comment pass's."""
        by_outcome = {FixOutcome.DECLINED: [
            CommentItem(id="t9", reviewer="kgn"),
            CommentItem(id="t8"),
        ]}
        assert fix_state.reviewers_for(by_outcome) == {"t9": "kgn"}

    def test_only_fixed_outcomes_carry_the_pass_commit(self):
        """A deferred thread was not fixed by this commit — or any."""
        fixed = self._entry("valid")
        deferred = CommentItem(id="t2", file="b.py", line=2, reviewer="kgn",
                               summary="too complex")
        record = fix_state.fix_record_for({
            FixOutcome.FIXED: [fixed],
            FixOutcome.DEFERRED: [deferred],
        }, commit_sha="deadbee")
        by_id = {o.id: o.commit_sha for o in record.items}
        assert by_id == {"t1": "deadbee", "t2": ""}

    def test_no_commit_leaves_the_sha_empty(self):
        record = fix_state.fix_record_for(
            {FixOutcome.FIXED: [self._entry("valid")]}, commit_sha="",
        )
        assert record.items[0].commit_sha == ""


class TestThePassWritesOnce:
    """The fix record and the resolution delta reach disk in the same save.

    The comment tally was snapshotted before the pass ran, so it accounts for
    the threads the pass resolved only if the delta rides along with the
    record. Two saves would write the record against a tally that had not
    moved, and the window between them is a crash away from a state file that
    says threads are open which GitHub has closed.
    """

    def _saved(self, resolved):
        state = _make_state(_fix())
        state.comments.by_state = {"new": 2}
        with patch("pr.state.load_or_init", return_value=state), \
             patch("pr.state.save_state") as save:
            fix_state.persist(_fix(), Path("/wt"), make_ctx(), None,
                              resolved=resolved)
        return save, state

    def test_one_save_carries_both(self):
        save, state = self._saved([ThreadState.NEW])
        assert save.call_count == 1
        assert state.comments.by_state[ThreadState.RESOLVED] == 1

    def test_the_delta_is_applied_before_the_save(self):
        """Applied after, the tally would be written one round behind forever."""
        seen = {}
        state = _make_state(_fix())
        state.comments.by_state = {"new": 1}
        with patch("pr.state.load_or_init", return_value=state), \
             patch("pr.state.save_state",
                   side_effect=lambda *_: seen.update(state.comments.by_state)):
            fix_state.persist(_fix(), Path("/wt"), make_ctx(), None,
                              resolved=[ThreadState.NEW])
        assert seen.get(ThreadState.RESOLVED) == 1


class TestAFailedWriteDoesNotTakeThePassDown:
    """By the time this runs the pass has committed, replied and posted.

    Those acts are done and cannot be undone by raising. A state file one round
    behind is recoverable on the next run; a traceback here leaves the outward
    acts unrecorded and the operator with no state to resume from.
    """

    def test_the_failure_is_logged_and_swallowed(self, capsys):
        with patch("pr.state.load_or_init", side_effect=OSError("disk full")):
            fix_state.persist(_fix(), Path("/wt"), make_ctx(), None)
        assert "fix state update failed" in capsys.readouterr().err

    def test_the_failure_reaches_the_trail(self):
        trail = MagicMock()
        with patch("pr.state.load_or_init", side_effect=OSError("disk full")):
            fix_state.persist(_fix(), Path("/wt"), make_ctx(), trail)
        assert trail.error.called
        assert "disk full" in trail.error.call_args[0][1]


class TestTheRecordAndTheIdentityNameDifferentCommits:
    """`FixRecord.head_sha` is post-commit; the state's identity stays pre-pass.

    The record must name the commit its outcomes were measured against, which
    is HEAD after the pass committed. The identity is the PR's, resolved before
    the pass ran, and is what the state file is keyed by — overwriting it with
    the fix commit would file this round's state under a SHA no earlier round
    shares.
    """

    def test_the_identity_comes_from_the_context_not_the_record(self):
        state = _make_state(_fix())
        captured = {}
        with patch("pr.state.load_or_init",
                   side_effect=lambda **kw: captured.update(kw) or state), \
             patch("pr.state.save_state"):
            fix_state.persist(
                _fix(head_sha="fff9999"), Path("/wt"),
                make_ctx(head_sha="aaa1111"), None,
            )
        assert captured["head_sha"] == "aaa1111"

    def test_the_record_keeps_the_sha_it_was_built_with(self):
        state = _make_state(_fix())
        with patch("pr.state.load_or_init", return_value=state), \
             patch("pr.state.save_state"):
            fix_state.persist(
                _fix(head_sha="fff9999"), Path("/wt"),
                make_ctx(head_sha="aaa1111"), None,
            )
        assert state.fix.fix.head_sha == "fff9999"


class TestTheStampIsIdempotent:
    """`fix_record_for` may re-stamp what a caller already stamped.

    The pass stamps its fixed entries before building the record, so that "no
    SHA on the entry" means the entry did not land in this commit. The record
    builder stamps again, and must not overwrite a SHA an earlier round put
    there — an entry fixed two commits ago still cites the commit that fixed it.
    """

    def test_an_entry_already_naming_a_commit_keeps_it(self):
        entry = CommentItem(id="t1", reviewer="kgn", commit_sha="olde123")
        record = fix_state.fix_record_for(
            {FixOutcome.FIXED: [entry]}, commit_sha="newc456")
        assert record.items[0].commit_sha == "olde123"

    def test_an_unstamped_entry_takes_this_pass_s_commit(self):
        entry = CommentItem(id="t1", reviewer="kgn")
        record = fix_state.fix_record_for(
            {FixOutcome.FIXED: [entry]}, commit_sha="newc456")
        assert record.items[0].commit_sha == "newc456"
