"""Tests for `pr.thread_replies` — the reply paths nothing exercised directly.

The four builders and the two predicates are covered heavily through
`test_review_threads.py`, which drives them end to end. What had no test at all
is the machinery underneath: the upsert's three branches, and the two log lines
the driver emits about what it edited and what it refused to touch.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT, git_in, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from git.land import CommitStatus  # noqa: E402
import pr.attribution  # noqa: E402
import pr.thread_replies  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402
import core.log
import pr.comments

_REPO = "owner/repo"
_PR = 42
_SHA = "abc1234"


def _thread(*, db_id=111, comments=None, my_login="me"):
    if comments is None:
        comments = [{"databaseId": db_id, "author": {"login": "kgn"}}]
    return ReportThread(id="t1", my_login=my_login, comments=comments)


class TestHandledPrefixesTrackTheGeneratedSet:
    """The handled subset is derived, so a fifth opening cannot miss it.

    Reconciliation reads a thread as handled when our standing reply opens with
    one of these. That set was listed by hand in `review-threads`, which meant a
    new generated opening added here was recognised as ours everywhere except
    reconciliation — where the thread would then stay open for the life of the
    PR. Deriving it makes the omission impossible; these tests pin the
    exclusions that are deliberate.
    """

    def test_only_the_openings_that_say_work_remains_are_excluded(self):
        """Both say the opposite of handled, so counting either would make the
        thread reconcile itself on the second --finish."""
        assert set(pr.thread_replies.GENERATED_REPLY_PREFIXES) - set(
            pr.thread_replies.HANDLED_REPLY_PREFIXES,
        ) == {
            pr.thread_replies.DEFERRED_REPLY_PREFIX,
            pr.thread_replies.NEEDS_HUMAN_REPLY_PREFIX,
        }

    def test_every_handled_opening_is_a_generated_one(self):
        assert set(pr.thread_replies.HANDLED_REPLY_PREFIXES) <= set(
            pr.thread_replies.GENERATED_REPLY_PREFIXES,
        )

    def test_deferring_is_not_handling(self):
        assert pr.thread_replies.DEFERRED_REPLY_PREFIX not in (
            pr.thread_replies.HANDLED_REPLY_PREFIXES
        )


class TestWhatCountsAsNamingAVerdict:
    """Two ways a reply says the thread was handled, and one asymmetry.

    A template opening is ours by construction. A hand-typed verdict is ordinary
    English, so the vocabulary is sized against the cost of being wrong: a false
    match publishes a claim about someone else's code and resolves their thread,
    while a miss leaves the thread open for a person to settle. This predicate
    answers only "does this body name a verdict" — whose body it is belongs to
    the caller, and `settlement._our_verdict` is where that is enforced.
    """

    @pytest.mark.parametrize("prefix", pr.thread_replies.HANDLED_REPLY_PREFIXES)
    def test_every_handled_template_still_names_a_verdict(self, prefix):
        assert pr.thread_replies.names_a_verdict(f"{prefix}: dropped the guard.")

    def test_the_deferred_template_still_names_none(self):
        assert not pr.thread_replies.names_a_verdict(
            f"{pr.thread_replies.DEFERRED_REPLY_PREFIX} tracked in ENG-1.",
        )

    @pytest.mark.parametrize("word", pr.thread_replies.HANDWRITTEN_VERDICT_WORDS)
    @pytest.mark.parametrize("tail", ["", ".", ":", " — and here is why", " in abc1234."])
    def test_each_word_stands_alone_and_before_a_delimiter(self, word, tail):
        assert pr.thread_replies.names_a_verdict(f"{word}{tail}")

    def test_leading_whitespace_does_not_hide_a_verdict(self):
        assert pr.thread_replies.names_a_verdict("\n  Fixed — dropped the guard.")

    @pytest.mark.parametrize(
        "body",
        ["Fixing this now.", "Doneness is not a word.", "Fixedly staring.",
         "Dismissive of the point."],
    )
    def test_a_word_that_merely_starts_the_same_is_not_a_verdict(self, body):
        """The delimiter lookahead is what separates the verdict from the prefix."""
        assert not pr.thread_replies.names_a_verdict(body)

    @pytest.mark.parametrize(
        "body",
        ["Addressed your first point but not the second.",
         "Resolved the conflict, the API question stands.",
         "Updated the guard — not sure that is right."],
    )
    def test_a_scope_ambiguous_opening_is_left_out(self, body):
        """These open on a verdict and settle nothing. Only `Already addressed`
        — a template, and unambiguous — qualifies.
        """
        assert not pr.thread_replies.names_a_verdict(body)

    @pytest.mark.parametrize(
        "body", ["Good catch — will sort it.", "Agreed, that needs doing.",
                 "Thanks, makes sense.", "Will do."],
    )
    def test_an_acknowledgement_is_not_a_verdict(self, body):
        """It says the reviewer was heard, not that anything changed."""
        assert not pr.thread_replies.names_a_verdict(body)

    @pytest.mark.parametrize(
        "body", ["This is fixed now.", "Should be fixed — have a look.",
                 "I think that is done."],
    )
    def test_a_verdict_buried_mid_sentence_is_not_chased(self, body):
        """Anchoring at the start is the whole safety mechanism."""
        assert not pr.thread_replies.names_a_verdict(body)

    def test_an_empty_body_names_nothing(self):
        assert not pr.thread_replies.names_a_verdict("")

    @pytest.mark.parametrize("word", pr.thread_replies.HANDWRITTEN_VERDICT_WORDS)
    def test_the_handwritten_match_is_case_sensitive(self, word):
        """Unlike the login match, a verdict word must be capitalised.

        These are sentence openers a person capitalises when writing one — see
        `thread_replies.HANDWRITTEN_VERDICT_WORDS`'s own comment. A lowercase
        opening reads as ordinary prose, not a verdict.
        """
        assert not pr.thread_replies.names_a_verdict(
            f"{word.lower()} — dropped the guard.",
        )

    def test_deferring_is_still_no_part_of_the_typed_vocabulary(self):
        """Counting it would settle every thread on the second --finish."""
        assert "Deferred" not in pr.thread_replies.HANDWRITTEN_VERDICT_WORDS
        assert not pr.thread_replies.names_a_verdict("Deferred — tracked in ENG-1.")


class TestTheFollowupPatternKnowsEveryLead:
    """The pattern must recognise every body a builder actually writes.

    `is_generated_reply` accepts a body only when every trailing paragraph
    matches `_GENERATED_FOLLOWUP_RE`, so a builder that gains a sentence the
    pattern does not know makes its own reply unrecognisable — and an
    unrecognised reply reads as hand-written and is never updated again for the
    life of the PR. The comment above the pattern asks a human to keep the two
    in step; this asks the suite instead.

    Driven through the real builders rather than a transcribed list of bodies,
    following `TestGeneratedActionCell` on the cell side: a copy of the wording
    would keep passing after the wording changed, which is the failure being
    guarded against.

    Retired by #1219, which marks generated bodies rather than recognising them
    by their prose. Delete this with the pattern.
    """

    @pytest.fixture
    def captured(self):
        """Every body the builder under test hands to the upsert."""
        bodies = []

        def record(thread, repo, pr_number, body, existing_id):
            bodies.append(body)
            return True

        with patch.object(pr.thread_replies, "upsert_thread_reply", record):
            yield bodies

    @pytest.fixture
    def entry(self):
        return CommentItem(
            id="t1", summary="drop the guard", reviewer="kgn",
            reasoning="the premise does not hold", file="f.py", line=2,
            read_sha=_SHA, commit_sha=_SHA,
        )

    @pytest.fixture
    def threads(self):
        return {"t1": _thread()}

    @pytest.fixture
    def tree(self, tmp_path):
        """A real repo holding the cited file.

        Both halves are needed or the trailing sentence never renders and the
        case proves nothing: `code_link` returns "" for a file the tree does
        not have, and it also returns "" when `head_sha` is empty — which is
        what `git_client.head_sha` answers for a directory that is not a repo.
        """
        run_checked(["git", "init", "-q", "-b", "main", str(tmp_path)])
        git_in(tmp_path, "config", "user.email", "t@example.com")
        git_in(tmp_path, "config", "user.name", "Test")
        (tmp_path / "f.py").write_text("one\ntwo\nthree\n")
        git_in(tmp_path, "add", "-A")
        git_in(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
        return tmp_path

    def _assert_all_ours(self, bodies):
        assert bodies, "the builder wrote nothing — the case proves nothing"
        for body in bodies:
            assert pr.thread_replies.is_generated_reply(body) is True, body

    def test_a_fixed_reply_is_recognised(self, captured, entry, threads):
        pr.thread_replies.post_fix_replies(
            [entry], threads, _REPO, _PR,
            pr.attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        self._assert_all_ours(captured)
        assert "Result is in" in captured[0]

    def test_an_unverified_fix_reply_is_recognised(self, captured, threads):
        """The hedge is a trailing paragraph like any other.

        A reply the pattern does not recognise reads as hand-written and is
        never updated again for the life of the PR, so a new sentence has to be
        added to `_GENERATED_FOLLOWUP_RE` as well as to the builder.
        """
        unverified = CommentItem(
            id="t1", summary="s", reviewer="kgn", commit_sha=_SHA,
            verified=False, verify_detail="no runnable check for this path",
        )
        pr.thread_replies.post_fix_replies(
            [unverified], threads, _REPO, _PR,
            pr.attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        self._assert_all_ours(captured)

    def test_a_fixed_reply_with_no_file_is_recognised(self, captured, threads):
        bare = CommentItem(id="t1", summary="s", reviewer="kgn", commit_sha=_SHA)
        pr.thread_replies.post_fix_replies(
            [bare], threads, _REPO, _PR,
            pr.attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        self._assert_all_ours(captured)

    @pytest.mark.parametrize("acted", [True, False])
    def test_an_addressed_reply_is_recognised(
            self, captured, entry, threads, tree, acted):
        pr.thread_replies.post_already_addressed_replies(
            [entry], threads, _REPO, _PR, tree, acted=acted)
        self._assert_all_ours(captured)
        assert "Current behaviour is at" in captured[0]

    @pytest.mark.parametrize("reasoning", ["the premise does not hold", ""])
    def test_a_dismissal_is_recognised(
            self, captured, threads, tree, reasoning):
        item = CommentItem(id="t1", summary="s", reviewer="kgn",
                           reasoning=reasoning, file="f.py", line=2,
                           evidence_file="f.py", evidence_line=2,
                           read_sha=_SHA)
        pr.thread_replies.post_dismissed_replies(
            [item], threads, _REPO, _PR, tree)
        self._assert_all_ours(captured)
        assert "See " in captured[0]

    @pytest.mark.parametrize("issue_url", ["https://linear.app/i/ENG-1", ""])
    def test_a_deferral_is_recognised(
            self, captured, entry, threads, tree, issue_url):
        pr.thread_replies.post_deferred_replies(
            [entry], threads, _REPO, _PR, "ENG-1", issue_url, tree)
        self._assert_all_ours(captured)
        assert "Unchanged at" in captured[0]

    def test_a_lead_the_pattern_does_not_know_is_not_ours(self):
        """The failure this guards, shown once.

        A builder gaining "Landed in ..." without the pattern gaining it too
        writes a reply the next round refuses to touch.
        """
        assert pr.thread_replies.is_generated_reply(
            "Applied: x\n\nLanded in [`abc1234`](u).") is False


class TestUpsertThreadReply:
    """Three branches, none of which had a test naming this function."""

    def test_an_existing_reply_is_edited_in_place(self):
        with patch.object(pr.comments, "patch_thread_reply",
                          return_value=True) as edit, \
             patch.object(pr.comments, "post_thread_reply") as post:
            assert pr.thread_replies.upsert_thread_reply(
                _thread(), _REPO, _PR, "body", 999) is True

        edit.assert_called_once_with(_REPO, 999, "body")
        post.assert_not_called()

    def test_no_existing_reply_posts_under_the_thread_root(self):
        with patch.object(pr.comments, "post_thread_reply",
                          return_value=True) as post, \
             patch.object(pr.comments, "patch_thread_reply") as edit:
            assert pr.thread_replies.upsert_thread_reply(
                _thread(db_id=222), _REPO, _PR, "body", None) is True

        post.assert_called_once_with(_REPO, _PR, 222, "body")
        edit.assert_not_called()

    def test_a_thread_whose_root_has_no_id_is_left_alone(self):
        """The one branch no end-to-end test reaches: nothing to reply under."""
        with patch.object(pr.comments, "post_thread_reply") as post, \
             patch.object(pr.comments, "patch_thread_reply") as edit:
            assert pr.thread_replies.upsert_thread_reply(
                _thread(comments=[{"author": {"login": "kgn"}}]),
                _REPO, _PR, "body", None) is False

        post.assert_not_called()
        edit.assert_not_called()


class TestWhatTheDriverReports:
    """The two log lines, which no test asserted.

    They are the operator's only signal that a reply was edited rather than
    posted, and that one was deliberately not written at all.
    """

    @pytest.fixture
    def entry(self):
        return CommentItem(id="t1", summary="s", reviewer="kgn")

    def _run(self, entries, threads):
        return pr.thread_replies._post_thread_replies(
            entries, threads, _REPO, _PR, lambda e: "generated body")

    def test_editing_one_reply_reads_as_singular(self, entry, caplog):
        thread = _thread(comments=[
            {"databaseId": 111, "author": {"login": "kgn"}},
            {"databaseId": 222, "author": {"login": "me"},
             "body": "Applied: earlier"},
        ])
        with patch.object(pr.comments, "patch_thread_reply",
                          return_value=True), \
             patch.object(pr.comments, "last_comment_is_mine",
                          return_value=True), \
             patch.object(core.log, "info") as info:
            assert self._run([entry], {"t1": thread}) == 1

        assert any("Edited 1 standing reply in place" in c.args[0]
                   for c in info.call_args_list)

    def test_a_hand_written_reply_is_reported_with_the_way_out(self, entry):
        """Refusing to write is only defensible if the operator is told how."""
        thread = _thread(comments=[
            {"databaseId": 111, "author": {"login": "kgn"}},
            {"databaseId": 222, "author": {"login": "me"},
             "body": "I disagree, and here is why"},
        ])
        with patch.object(pr.comments, "patch_thread_reply") as edit, \
             patch.object(pr.comments, "post_thread_reply") as post, \
             patch.object(core.log, "info") as info:
            assert self._run([entry], {"t1": thread}) == 0

        edit.assert_not_called()
        post.assert_not_called()
        message = " ".join(c.args[0] for c in info.call_args_list)
        assert "Left 1 hand-written reply untouched" in message
        assert "--reply <id> --body-file <f>" in message

    def test_a_thread_the_report_does_not_carry_is_skipped(self, entry):
        with patch.object(pr.comments, "post_thread_reply") as post:
            assert self._run([entry], {}) == 0
        post.assert_not_called()

    def test_a_thread_with_no_comments_is_skipped(self, entry):
        with patch.object(pr.comments, "post_thread_reply") as post:
            assert self._run([entry], {"t1": _thread(comments=[])}) == 0
        post.assert_not_called()

    def test_a_failed_post_does_not_count_as_replied(self, entry):
        with patch.object(pr.comments, "post_thread_reply",
                          return_value=False):
            assert self._run([entry], {"t1": _thread()}) == 0


class TestAReplySaysWhatWasEstablished:
    """A fix reply is a claim about behaviour, and claims what was checked.

    The defect behind #1275: a fix that was applied but never exercised reads
    identically to one that was run and passed. Both closed the thread and
    spent the reviewer's trust; only one had earned it.

    Overlaps `TestTheFollowupPatternKnowsEveryLead` on purpose, and the two
    should not be merged: that one asserts the hedge is still *recognised* as
    our own reply (an unrecognised one is never updated again for the life of
    the PR), this one asserts it *says* the right thing. A single test would
    drop whichever property its assertions did not happen to cover.
    """

    @pytest.fixture
    def captured(self):
        bodies = []

        def record(thread, repo, pr_number, body, existing_id):
            bodies.append(body)
            return True

        with patch.object(pr.thread_replies, "upsert_thread_reply", record):
            yield bodies

    @pytest.fixture
    def threads(self):
        return {"t1": _thread()}

    def _post(self, entry, captured, threads):
        pr.thread_replies.post_fix_replies(
            [entry], threads, _REPO, _PR,
            pr.attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        return captured[0]

    def test_an_unverified_fix_says_so(self, captured, threads):
        body = self._post(
            CommentItem(id="t1", summary="s", reviewer="kgn", commit_sha=_SHA,
                        verified=False, verify_detail="no runnable check"),
            captured, threads,
        )
        assert "not verified" in body.lower()
        assert "no runnable check" in body

    def test_a_verified_fix_does_not_hedge(self, captured, threads):
        body = self._post(
            CommentItem(id="t1", summary="s", reviewer="kgn", commit_sha=_SHA,
                        verified=True, verify_detail="suite green"),
            captured, threads,
        )
        assert "not verified" not in body.lower()

    def test_an_unverified_fix_with_no_detail_still_hedges(self, captured, threads):
        """The hedge is the claim being weakened, not the detail decorating it."""
        body = self._post(
            CommentItem(id="t1", summary="s", reviewer="kgn", commit_sha=_SHA,
                        verified=False),
            captured, threads,
        )
        assert "not verified" in body.lower()


class TestPostFixRepliesAnnotationsResolve:
    """Regression: `history` carried the pre-codemod bare name `attribution`
    in its string annotation, which this module no longer imports (it imports
    `pr.attribution`). The module runs under PEP 563, so the stale name is
    inert until something reads it with `typing.get_type_hints`."""

    def test_post_fix_replies_signature_resolves(self):
        import typing
        hints = typing.get_type_hints(pr.thread_replies.post_fix_replies)
        assert hints["history"] == pr.attribution.AddressingHistory | None
