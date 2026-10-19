"""`pr.owed_feedback` — the one definition of feedback a PR is owed an answer for."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.owed_feedback  # noqa: E402
from gh.pr_data import PRData  # noqa: E402
from pr.domains import CommentsSummary  # noqa: E402
from pr.owed_feedback import OwedFeedback, owed_feedback  # noqa: E402


# ── Seen-ness is about the text, not the comment id ─────────────────────────
#
# A comment marked seen is dropped from triage decomposition entirely
# (`triage.collect_unseen_comments`), so a reviewer who edits a comment to add
# a demand used to have that demand silently discarded: the id was unchanged,
# so the rewritten comment still read as already handled.


class TestSeenTracking:
    """`mark_seen` and `seen_record`, the pair that decides what triage sees."""

    @staticmethod
    def _comment(cid: int, edited: str = "") -> dict:
        """A comment as the fetch layer hands it over.

        No body: seen-tracking reads the id and the edit stamp and nothing
        else, and carrying text here would imply the comparison looks at it.
        """
        return {"id": cid, "last_edited_at": edited}

    def test_a_comment_the_last_round_read_is_seen(self):
        comments = [self._comment(1)]
        pr.owed_feedback.mark_seen(comments, {1: ""})
        assert comments[0]["seen"] is True

    def test_a_comment_never_read_is_unseen(self):
        comments = [self._comment(1)]
        pr.owed_feedback.mark_seen(comments, {})
        assert comments[0]["seen"] is False

    def test_an_edited_comment_is_unseen_again(self):
        """The defect: same id, new text, and it used to stay seen."""
        comments = [self._comment(1, edited="2026-02-01T00:00:00Z")]
        pr.owed_feedback.mark_seen(comments, {1: ""})
        assert comments[0]["seen"] is False

    def test_a_comment_edited_again_since_the_last_round_is_unseen(self):
        """A second edit must not match the stamp recorded for the first."""
        comments = [self._comment(1, edited="2026-03-01T00:00:00Z")]
        pr.owed_feedback.mark_seen(comments, {1: "2026-02-01T00:00:00Z"})
        assert comments[0]["seen"] is False

    def test_an_edit_already_read_stays_seen(self):
        """Re-reporting every edited comment on every round would be noise."""
        comments = [self._comment(1, edited="2026-02-01T00:00:00Z")]
        pr.owed_feedback.mark_seen(comments, {1: "2026-02-01T00:00:00Z"})
        assert comments[0]["seen"] is True

    def test_a_missing_edit_field_reads_as_never_edited(self):
        """A payload without the field must not differ from one carrying ''."""
        comments = [{"id": 1, "body": "t"}]
        pr.owed_feedback.mark_seen(comments, {1: ""})
        assert comments[0]["seen"] is True
        assert pr.owed_feedback.seen_record(comments) == {1: ""}

    def test_a_null_edit_field_reads_as_never_edited(self):
        """GitHub sends null, not '', for a comment nobody has edited."""
        comments = [{"id": 1, "body": "t", "last_edited_at": None}]
        pr.owed_feedback.mark_seen(comments, {1: ""})
        assert comments[0]["seen"] is True

    def test_the_record_carries_the_stamp_each_comment_arrived_with(self):
        record = pr.owed_feedback.seen_record([
            self._comment(1),
            self._comment(2, edited="2026-02-01T00:00:00Z"),
        ])
        assert record == {1: "", 2: "2026-02-01T00:00:00Z"}

    def test_a_round_that_records_what_it_read_sees_it_seen_next_time(self):
        """The two halves agree: what one writes, the other reads as seen."""
        comments = [self._comment(1), self._comment(2, edited="2026-02-01T00:00:00Z")]
        record = pr.owed_feedback.seen_record(comments)
        fresh = [self._comment(1), self._comment(2, edited="2026-02-01T00:00:00Z")]
        pr.owed_feedback.mark_seen(fresh, record)
        assert [c["seen"] for c in fresh] == [True, True]

    def test_a_comment_with_no_id_is_left_out_of_the_record(self):
        """A None key would cost the whole state file, not one field.

        Both builders take the id from `databaseId`, which GraphQL can omit.
        The mapping is keyed `int`, so a None key serialises to the JSON string
        "null", which serde refuses to coerce back — and `load_state` discards
        an unreadable file wholesale, losing every verdict and round with it.
        """
        record = pr.owed_feedback.seen_record([
            {"id": None, "last_edited_at": ""},
            {"id": 5, "last_edited_at": ""},
        ])
        assert record == {5: ""}

    def test_a_comment_with_no_id_is_unseen_rather_than_a_crash(self):
        comments = [{"id": None}, {"last_edited_at": ""}]
        pr.owed_feedback.mark_seen(comments, {5: ""})
        assert [c["seen"] for c in comments] == [False, False]

    def test_the_record_survives_a_state_file_round_trip(self, tmp_path):
        """End to end: what seen_record writes must load back unchanged."""
        import pr.state
        import pr.domains
        record = pr.owed_feedback.seen_record([
            {"id": None, "last_edited_at": ""},
            {"id": 111, "last_edited_at": ""},
            {"id": 222, "last_edited_at": "2026-02-01T00:00:00Z"},
        ])
        state = pr.state.new_state("acme/w", "feat/x", pr_number=1,
                                   head_sha="a", worktree_root=str(tmp_path))
        pr.state.apply(state, pr.domains.CommentsSummary(
            seen_issue_comments=record, updated_at=pr.state.now_iso()))
        pr.state.save_state(tmp_path, state)
        loaded = pr.state.load_state(tmp_path)
        assert loaded is not None, "state file was discarded as unreadable"
        assert loaded.comments.seen_issue_comments == {
            111: "", 222: "2026-02-01T00:00:00Z",
        }


# ── owed_feedback: threads, review bodies and PR comments, minus the seen record ──


def _data(*, threads=(), reviews=(), comments=()):
    return PRData(viewer_login="me", head_sha="h", head_ref="b", base_ref="main",
                  review_threads=list(threads), reviews=list(reviews),
                  issue_comments=list(comments))


def _review(rid, *, state="COMMENTED", body="please fix", login="rev", minimized=None,
            edited=None):
    return {"databaseId": rid, "state": state, "body": body, "minimizedReason": minimized,
            "lastEditedAt": edited, "author": {"login": login}}


def _comment(cid, *, login="rev", typename="User", edited=None):
    return {"databaseId": cid, "body": "why?", "lastEditedAt": edited,
            "author": {"login": login, "__typename": typename}}


def test_review_bodies_drop_own_pending_minimized_and_empty_ones():
    data = _data(reviews=[
        _review(1), _review(2, login="ME"), _review(3, state="PENDING"),
        _review(4, minimized="OUTDATED"), _review(5, body="  "),
    ])
    assert owed_feedback(data, set(), None) == OwedFeedback(review_bodies=1)


def test_pr_comments_drop_own_and_bot_ones_and_seen_ones():
    data = _data(comments=[_comment(1), _comment(2, login="me"),
                           _comment(3, typename="Bot"), _comment(4)])
    seen = CommentsSummary(seen_issue_comments={4: ""})
    assert owed_feedback(data, set(), seen) == OwedFeedback(issue_comments=1)


def test_threads_drop_resolved_and_settled_ones():
    data = _data(threads=[{"id": "T1", "isResolved": False},
                          {"id": "T2", "isResolved": True},
                          {"id": "T3", "isResolved": False}])
    assert owed_feedback(data, {"T3"}, None) == OwedFeedback(threads=1)


def test_an_edited_review_body_is_owed_again():
    data = _data(reviews=[_review(1, edited="2026-02-01T00:00:00Z")])
    seen = CommentsSummary(seen_review_body_comments={1: ""})
    assert owed_feedback(data, set(), seen).review_bodies == 1


def test_describe_names_each_kind_and_nothing_when_none_is_owed():
    assert OwedFeedback().describe() == "" and not OwedFeedback().owed
    assert OwedFeedback(threads=2, review_bodies=1, issue_comments=3).describe() == (
        "2 unresolved threads, 1 unanswered review body, 3 unanswered PR comments")
