"""Tests for the record we write into what we publish, and the readers of it.

A published summary row and a generated thread reply each declare their own
identity and a digest of the text we wrote. These cover the round trip, every
way the digest is meant to catch a person's edit, the boundary with comments
written before the marker existed, and the overflow fallback that keeps a
marked summary under GitHub's comment limit.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.comments  # noqa: E402
import pr.published_record  # noqa: E402
import pr.summary_publish  # noqa: E402
import pr.summary_rounds  # noqa: E402
import pr.summary_row  # noqa: E402
import pr.summary_scope  # noqa: E402
import pr.thread_replies  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.summary_model import TABLE_DIVIDER, TABLE_HEADER  # noqa: E402

_THREAD = "https://github.com/o/r/pull/1#discussion_r101"
_OTHER = "https://github.com/o/r/pull/1#discussion_r202"
_ITEM = "https://github.com/o/r/pull/1#issuecomment-55"
_FIXED = "Fixed in [`abc1234`](https://github.com/o/r/commit/abc1234)"


def _cells(summary, url, file="a.py:3", action=_FIXED, reviewer="@kgn"):
    return [f"[{summary}]({url})", reviewer, f"`{file}`", action]


def _marked(summary="guard the nil", url=_THREAD, **kw):
    cells = _cells(summary, url, **kw)
    return pr.summary_row.marked_row(cells, cells[3])


def _body(*rows, marked=True):
    head = ["<!-- pr-comments:summary -->"]
    if marked:
        head.append(pr.published_record.FORMAT_MARKER)
    return "\n".join([*head, "", TABLE_HEADER, TABLE_DIVIDER, *rows, ""])


def _legacy_line(summary="guard the nil", url=_THREAD, **kw):
    return pr.summary_row.render_row(_cells(summary, url, **kw))


# --- the row marker ------------------------------------------------------------


def test_a_marked_row_declares_the_key_and_outcome_it_was_built_from():
    row = _marked()
    published = pr.summary_scope.published_rows(_body(row.line))
    assert [(r.key, r.outcome, r.ours) for r in published] == [
        (row.key, FixOutcome.FIXED, True)]
    assert row.key == pr.published_record.fingerprint("#discussion_r101")


def test_the_marker_survives_the_browser_saving_crlf():
    row = _marked()
    body = _body(row.line).replace("\n", "\r\n") + "  "
    assert pr.summary_scope.published_rows(body)[0].ours


def test_an_edit_to_any_cell_makes_the_row_a_persons():
    row = _marked()
    for edited in (
        row.line.replace("guard the nil", "guard the nil (see thread)"),
        row.line.replace("@kgn", "@someone"),
        row.line.replace("a.py:3", "a.py:4"),
        row.line + " — but see below",
    ):
        [published] = pr.summary_scope.published_rows(_body(edited))
        assert (published.key, published.ours, published.outcome) == (row.key, False, None)


def test_a_marker_value_cannot_split_a_cell_or_close_the_comment():
    row = _marked(summary="a | b --> c")
    marker = row.line[row.line.index("<!--"): row.line.index("-->") + 3]
    assert "|" not in marker and marker.count("-->") == 1


def test_only_comment_item_rows_carry_fold_keys():
    thread = pr.published_record.read_row_marker(_marked().line)
    item = pr.published_record.read_row_marker(_marked(url=_ITEM).line)
    lineless = pr.published_record.read_row_marker(_marked(url=_ITEM, file="a.py").line)
    assert (thread.location, thread.text_key) == ("", "")
    assert item.location == pr.published_record.fingerprint("kgn|a.py:3") and not item.text_key
    assert not lineless.location and lineless.text_key


# --- reading a body ------------------------------------------------------------


def test_a_row_without_a_marker_in_a_marked_comment_is_carried_and_never_keyed():
    added = "| a note a person added | @me | — | keep this |"
    fresh = _body(_marked(url=_OTHER).line)
    published = _body(_marked().line, added)
    [row] = [r for r in pr.summary_scope.published_rows(published) if not r.key]
    assert row.line == added and not row.ours
    carried = pr.summary_scope.carried_over_rows(published, fresh)
    assert added in carried
    assert pr.summary_scope.hand_written_rows([published], fresh) == []


def test_a_legacy_row_is_stamped_as_ours_when_carried():
    legacy = _legacy_line()
    [carried] = pr.summary_scope.carried_over_rows(
        _body(legacy, marked=False), _body(_marked(url=_OTHER).line))
    [read_back] = pr.summary_scope.published_rows(_body(carried))
    assert pr.published_record.unmarked(carried) == legacy
    assert (read_back.key, read_back.ours, read_back.outcome) == (
        pr.published_record.fingerprint("#discussion_r101"), True, FixOutcome.FIXED)


def test_a_hand_written_legacy_row_is_held_and_stays_held_once_stamped():
    legacy = _legacy_line(action="Left as is — we agreed on the call")
    fresh = _body(_marked().line)
    [held] = pr.summary_scope.hand_written_rows([_body(legacy, marked=False)], fresh)
    assert pr.published_record.unmarked(held.published) == legacy
    # The stamped row is what the next round reads: still a person's.
    [again] = pr.summary_scope.hand_written_rows([_body(held.published)], fresh)
    assert again.published == held.published


def test_a_held_marked_row_stays_held_across_rounds_with_no_state():
    row = _marked()
    edited = row.line.replace("guard the nil", "guard the nil — reworded by me")
    fresh = _body(row.line)
    [held] = pr.summary_scope.hand_written_rows([_body(edited)], fresh)
    [again] = pr.summary_scope.hand_written_rows([_body(held.published)], fresh)
    assert held.published == again.published == edited


def test_folded_rows_are_matched_on_the_hashed_fold_keys():
    item = _marked(url=_ITEM)
    fresh = _body(_marked().line)
    assert pr.summary_scope.carried_over_rows(
        _body(item.line), fresh, folded=frozenset({"kgn|a.py:3"})) == []
    assert pr.summary_scope.carried_over_rows(_body(item.line), fresh) == [item.line]


def test_round_scope_reads_legacy_and_marked_comments_in_one_key_space():
    legacy = _body(_legacy_line(), marked=False)
    marked = _body(_marked(url=_OTHER, action="Deferred to #9").line)
    history = pr.comments.MarkerHistory(found=True, comments=(
        pr.comments.MarkerComment(found=True, comment_id=1, body=legacy),
        pr.comments.MarkerComment(found=True, comment_id=2, body=marked),
    ))
    scope = pr.summary_rounds.round_scope(history, answered=False)
    first, second = pr.published_record.fingerprint("#discussion_r101"), pr.published_record.fingerprint("#discussion_r202")
    assert scope.elsewhere_keys == {first}
    assert scope.target_keys == {second}
    assert scope.published_outcomes == {first: FixOutcome.FIXED, second: FixOutcome.DEFERRED}


# --- thread replies --------------------------------------------------------------


_REPLY = "Applied: guard the nil\n\nFixed in [`abc1234`](https://x/commit/abc1234)."
_AFTER = "2026-10-03T00:00:00Z"
_BEFORE = "2026-09-01T00:00:00Z"


def test_a_stamped_reply_is_ours_until_anyone_touches_it():
    stamped = pr.published_record.stamp_reply(_REPLY)
    assert pr.thread_replies.is_generated_reply(stamped, _AFTER)
    assert pr.thread_replies.is_generated_reply(stamped.replace("\n", "\r\n"), _AFTER)
    assert not pr.thread_replies.is_generated_reply(
        stamped.replace("guard the nil", "guard the nil, as discussed"), _AFTER)
    assert not pr.thread_replies.is_generated_reply(stamped + "\n\nAlso: see #12.", _AFTER)


def test_a_reply_whose_marker_was_deleted_is_dated_rather_than_guessed():
    # The legacy template match accepts this body, so only the date decides.
    assert pr.thread_replies.is_generated_reply(_REPLY, _BEFORE)
    assert pr.thread_replies.is_generated_reply(_REPLY, "")
    assert not pr.thread_replies.is_generated_reply(_REPLY, _AFTER)


def test_generated_replies_are_stamped_and_deliberate_ones_are_not():
    thread = pr.thread_replies.ReportThread(
        id="T1", comments=[{"databaseId": 7, "author": {"login": "kgn"}}], my_login="me")
    sent = []
    with patch.object(pr.thread_replies, "upsert_thread_reply",
                      side_effect=lambda t, r, n, body, e: sent.append(body) or True):
        pr.thread_replies._post_thread_replies(
            [pr.thread_replies.CommentItem(id="T1")], {"T1": thread}, "o/r", 1,
            lambda entry: _REPLY)
    assert sent == [pr.published_record.stamp_reply(_REPLY)]
    assert pr.published_record.reply_marker_intact(sent[0])


# --- size ------------------------------------------------------------------------


def test_an_edit_past_the_comment_limit_is_posted_fresh_instead():
    existing = pr.comments.MarkerComment(
        found=True, comment_id=9, body=_body(_marked().line), created_at="2026-10-02T00:00:00Z")
    history = pr.comments.MarkerHistory(found=True, comments=(existing,))
    seen_scopes = []

    def build_body(carried_over, scope, chain, hand_held=None):
        seen_scopes.append(scope)
        # Edits carry the target's rows forward; a fresh post does not.
        return _body(_marked(url=_OTHER).line, *carried_over)

    posts = []
    with patch.object(pr.comments, "find_marker_comments", return_value=history), \
            patch.object(pr.comments, "post_issue_comment",
                         side_effect=lambda *a, **kw: posts.append(kw) or "url"), \
            patch.object(pr.summary_publish, "GITHUB_COMMENT_LIMIT",
                         len(_body(_marked(url=_OTHER).line)) + 10):
        assert pr.summary_publish.publish_summary("o/r", 1, build_body) == "url"
    assert posts == [{}]
    assert seen_scopes[-1].target_keys == frozenset()


def test_a_body_too_large_even_scoped_to_its_round_is_not_posted():
    history = pr.comments.MarkerHistory(found=True)
    with patch.object(pr.comments, "find_marker_comments", return_value=history), \
            patch.object(pr.comments, "post_issue_comment") as post, \
            patch.object(pr.summary_publish, "GITHUB_COMMENT_LIMIT", 10):
        assert pr.summary_publish.publish_summary(
            "o/r", 1, lambda **kw: _body(_marked().line)) is None
    post.assert_not_called()
