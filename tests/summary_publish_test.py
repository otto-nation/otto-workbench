"""Tests for `summary_publish.publish` — the round's two answers, together.

Whether a round's summary went out and whether the round still owes one are
separate questions, and the answer that matters is neither alone. The pair was
spelled out at four call sites that all had to agree: two building a
`FixSummary` and two building a `CommentFixResult`. Nothing tested the
conjunction directly — the end-to-end suites drive it through the fix pass,
where a wrong answer reads as a summary quietly re-rendered, or quietly not.

Covered here so a change to the pairing fails on the pairing.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git.land import CommitStatus  # noqa: E402
from pr import attribution, summary_publish  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.summary_model import RoundContent  # noqa: E402
from pr.summary_publish import SummaryOutcome  # noqa: E402
from pr.thread_models import CommentItem, PRReport  # noqa: E402


def _content(**buckets):
    return RoundContent(
        by_outcome={k: v for k, v in buckets.items()},
        issue_comments=[], review_body_comments=[],
    )


def _entry(eid="t1"):
    return CommentItem(id=eid, file="f.go", line=3, reviewer="kgn",
                       summary=f"{eid} summary")


class TestSummaryOutcomePairsThePost:
    """`deferred` is the conjunction the four call sites used to write out."""

    def test_a_post_that_landed_owes_nothing_further(self):
        assert SummaryOutcome("https://u", owed=True).deferred is False

    def test_a_draft_of_an_owed_round_is_deferred(self):
        assert SummaryOutcome(None, owed=True).deferred is True

    def test_a_round_that_owes_nothing_is_not_deferred(self):
        """Nothing went out and nothing is owed: the round simply had no table."""
        assert SummaryOutcome(None, owed=False).deferred is False

    def test_the_recorded_url_is_empty_rather_than_none(self):
        """`FixSummary.summary_url` merges by truthiness — None breaks the round trip."""
        assert SummaryOutcome(None, owed=True).recorded_url == ""

    def test_a_url_records_as_itself(self):
        assert SummaryOutcome("https://u", owed=False).recorded_url == "https://u"


class TestPublishAsksBothHalvesOfOneRound:
    """The debt is this round's, measured against the round that was posted."""

    def _publish(self, content, *, status=CommitStatus.PUSHED,
                 has_unaccounted=False, url="https://u"):
        cp = attribution.CommitPushResult("abc1234", status, "")
        with patch.object(summary_publish, "post_or_defer_summary",
                          return_value=url) as post:
            outcome = summary_publish.publish(
                content, cp, "owner/repo", 1, {}, PRReport(),
                has_comment_items=False, has_unaccounted=has_unaccounted,
                head_sha="abc1234",
            )
        return outcome, post

    def test_a_posted_round_carries_its_url(self):
        outcome, post = self._publish(_content(**{FixOutcome.FIXED: [_entry()]}))
        assert outcome.url == "https://u"
        assert outcome.deferred is False
        assert post.called

    def test_an_unaccounted_thread_leaves_the_round_owed(self):
        """The interim table went out and the closeout still has work to do."""
        outcome, _ = self._publish(
            _content(**{FixOutcome.DISMISSED: [_entry()]}), has_unaccounted=True)
        assert outcome.owed is True
        assert outcome.url == "https://u"

    def test_a_refused_post_of_an_owed_round_defers_it(self):
        outcome, _ = self._publish(
            _content(**{FixOutcome.DEFERRED: [_entry()]}), url=None)
        assert outcome.deferred is True

    def test_a_round_with_nothing_to_say_owes_nothing(self):
        outcome, _ = self._publish(_content(), url=None)
        assert outcome.owed is False
        assert outcome.deferred is False

    def test_an_unpushed_commit_leaves_the_round_owed(self):
        outcome, _ = self._publish(
            _content(**{FixOutcome.FIXED: [_entry()]}),
            status=CommitStatus.PUSH_HELD, url=None)
        assert outcome.deferred is True

    def test_an_unaccounted_thread_alone_leaves_the_round_owed(self):
        """The one input `has_unaccounted` decides on its own.

        Every other route to `owed` — a deferred entry, someone needing to
        answer, an unpushed commit, a table with rows — is absent here, so a
        `publish` that failed to pass the flag through reads as a round with
        nothing left to say while a thread on the PR still has nobody on it.
        """
        outcome, _ = self._publish(_content(), has_unaccounted=True, url=None)
        assert outcome.owed is True
        assert outcome.deferred is True
