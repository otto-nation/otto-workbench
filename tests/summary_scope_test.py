"""pr.summary_scope: row keys, table rows and what carries over."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import (  # noqa: E402
    ROUND_ONE_ROW, _SIBLING_ITEMS, _no_published_summary, _published, _published_summary,
    _unmarked, content,
)
import core.markdown
import pr.attribution
import pr.summary_model
import pr.summary_publish
import pr.summary_scope
import pr.summary_row
from pr.thread_models import CommentItem, ReportThread


class TestSummaryRowKey:
    """Two renders of one thread must key the same, across rounds."""

    def test_anchor_identifies_the_row(self):
        assert pr.summary_scope.row_key(ROUND_ONE_ROW) == "#discussion_r111"

    def test_action_and_sha_may_change(self):
        later = ROUND_ONE_ROW.replace("9f2e1a0", "bbbbbbb").replace("aaaaaaa", "ccccccc")
        assert pr.summary_scope.row_key(later) == pr.summary_scope.row_key(ROUND_ONE_ROW)

    def test_comment_item_anchors_do_not_collide_with_threads(self):
        thread = "| [x](https://x/pull/1#discussion_r7) | @a | `f.go` | Fixed |"
        item = "| [x](https://x/pull/1#issuecomment-7) | @a | `f.go` | Fixed |"
        assert pr.summary_scope.row_key(thread) != pr.summary_scope.row_key(item)

    def test_falls_back_to_the_row_text_without_a_permalink(self):
        row = "| plain summary | @kgn | `f.go:2` | Fixed in `abc` |"
        assert pr.summary_scope.row_key(row) == "plain summary | @kgn | f.go:2"

    def test_the_fallback_ignores_the_action_cell(self):
        row = "| plain summary | @kgn | `f.go:2` | Deferred |"
        later = "| plain summary | @kgn | `f.go:2` | Fixed in `abc` |"
        assert pr.summary_scope.row_key(row) == pr.summary_scope.row_key(later)


class TestSummaryTableRows:
    def test_header_and_divider_are_not_rows(self):
        assert pr.summary_scope.table_rows(_published_summary(ROUND_ONE_ROW)) == [ROUND_ONE_ROW]

    def test_a_body_without_a_table_has_no_rows(self):
        assert pr.summary_scope.table_rows("## Review Comments Addressed\n\nnothing yet\n") == []


class TestCarriedOverRows:
    def test_a_row_state_never_saw_is_carried(self):
        fresh = _published_summary(
            "| [new work](https://github.com/owner/repo/pull/1#discussion_r222) "
            "| @kgn | `new.go:1` | Fixed in `bbbbbbb` |")
        assert _unmarked(pr.summary_scope.carried_over_rows(
            _published_summary(ROUND_ONE_ROW), fresh)) == [ROUND_ONE_ROW]

    def test_a_row_state_still_holds_is_not_duplicated(self):
        fresh = _published_summary(ROUND_ONE_ROW.replace("Fixed in", "Deferred —"))
        assert pr.summary_scope.carried_over_rows(_published_summary(ROUND_ONE_ROW), fresh) == []

    def test_nothing_published_carries_nothing(self):
        assert pr.summary_scope.carried_over_rows("", _published_summary(ROUND_ONE_ROW)) == []


class TestSiblingItemsKeyApart:
    """One anchor, N rows: the anchor names the source, not the row."""

    def _row(self, item, status="Fixed", sha="aaaaaaa"):
        return pr.summary_row.render_row(pr.summary_row.row_cells_for(item, status, {}, "owner/repo", 1, sha))

    def test_each_sibling_gets_its_own_key(self):
        keys = {pr.summary_scope.row_key(self._row(i)) for i in _SIBLING_ITEMS}
        assert len(keys) == len(_SIBLING_ITEMS)

    def test_the_anchor_is_still_half_the_key(self):
        """Two comments raising the same point are two rows, not one."""
        elsewhere = CommentItem(id="ic-901-0", summary="drop the guard",
                                reviewer="kgn", file="old.go", line=4)
        assert (pr.summary_scope.row_key(self._row(_SIBLING_ITEMS[0]))
                != pr.summary_scope.row_key(self._row(elsewhere)))

    def test_a_sibling_keys_the_same_across_rounds(self):
        first = self._row(_SIBLING_ITEMS[0], status="Deferred", sha="aaaaaaa")
        later = self._row(_SIBLING_ITEMS[0], status="Fixed in `bbbbbbb`",
                          sha="ccccccc")
        assert pr.summary_scope.row_key(first) == pr.summary_scope.row_key(later)

    def test_a_thread_row_keys_on_its_anchor_alone(self):
        """A thread renders one row, so its summary must stay out of the key —
        a reworded summary is the same finding, not a new one."""
        reworded = ROUND_ONE_ROW.replace("drop the guard", "remove the guard")
        assert pr.summary_scope.row_key(reworded) == pr.summary_scope.row_key(ROUND_ONE_ROW)


class TestFoldedRowsAreNotCarriedBack:
    """A round that folded a duplicate must not read its own removal as a loss."""

    THREAD_ROW = ("| [drop the retry](https://github.com/o/r/pull/1#discussion_r5) "
                  "| @kgn | [`a.go:7`](https://github.com/o/r/blob/abc/a.go#L7) | Fixed |")
    ITEM_ROW = ("| [also drop the retry](https://github.com/o/r/pull/1"
                "#issuecomment-77) | @kgn | `a.go:7` | contested |")
    # What the renderer writes when the SHA is known but the line cannot be
    # placed in it: a permalink with no `#L7`, and a label with no `:7`.
    UNANCHORED_THREAD_ROW = (
        "| [drop the retry](https://github.com/o/r/pull/1#discussion_r5) "
        "| @kgn | [`a.go`](https://github.com/o/r/blob/abc/a.go) | Fixed |")

    FOLDED = frozenset({"kgn|a.go:7"})

    def test_the_published_duplicate_is_accounted_for(self):
        published = f"{self.THREAD_ROW}\n{self.ITEM_ROW}"
        assert pr.summary_scope.carried_over_rows(published, self.THREAD_ROW, folded=self.FOLDED) == []

    def test_an_item_row_elsewhere_is_still_carried(self):
        elsewhere = self.ITEM_ROW.replace("a.go:7", "b.go:3")
        published = f"{self.THREAD_ROW}\n{elsewhere}"
        assert _unmarked(pr.summary_scope.carried_over_rows(
            published, self.THREAD_ROW, folded=self.FOLDED)) == [elsewhere]

    def test_a_published_thread_row_is_carried_as_before(self):
        """Only comment items fold; a thread row this render lost is still a loss."""
        other = self.THREAD_ROW.replace("discussion_r5", "discussion_r9")
        published = f"{self.THREAD_ROW}\n{other}"
        assert _unmarked(pr.summary_scope.carried_over_rows(
            published, self.THREAD_ROW, folded=self.FOLDED)) == [other]

    def test_a_dropped_line_anchor_still_accounts_for_the_duplicate(self):
        """The fold is decided from entries, so the rendered File cell cannot undo it.

        `permalinks.anchored_line` returns 0 on an unfetched SHA, on drift, and
        with no worktree, and the row then carries neither `:7` nor `#L7`.
        Recovering the fold from that cell yielded "", so the published item row
        restating the thread was carried forward and the duplicate came back.
        """
        published = f"{self.THREAD_ROW}\n{self.ITEM_ROW}"
        assert pr.summary_scope.carried_over_rows(
            published, self.UNANCHORED_THREAD_ROW, folded=self.FOLDED) == []

    def test_the_reviewer_cell_keys_without_its_at_sign(self):
        """`location_from_cells` and `finding_location` must spell the reviewer alike.

        The rendered cell is `@kgn` and the typed key is `kgn`; the two are
        compared against each other, so a key keeping the `@` matches nothing.
        """
        assert pr.summary_model.location_from_cells(
            core.markdown.row_cells(self.ITEM_ROW)) == "kgn|a.go:7"

    def test_nothing_folded_carries_everything(self):
        """A round with no fold to report leaves the published rows alone."""
        published = f"{self.THREAD_ROW}\n{self.ITEM_ROW}"
        assert _unmarked(pr.summary_scope.carried_over_rows(
            published, self.THREAD_ROW)) == [self.ITEM_ROW]

    def test_the_publish_path_folds_without_a_placeable_line(self, content):
        """End to end: the fix pass posting against an unfetched SHA.

        No worktree is passed, so `permalinks.anchored_line` cannot place the
        line and every File cell renders bare. The published item row must
        still be recognised as the duplicate this round folded.
        """
        thread = CommentItem(id="t1", file="a.go", line=7, reviewer="kgn",
                             summary="drop the retry")
        item = CommentItem(id="ic-77-0", file="a.go", line=7, reviewer="kgn",
                           summary="also drop the retry", reason="contested")
        threads = {"t1": ReportThread(id="t1", file="a.go", line=7, reviewer="kgn",
                                      comments=[{"databaseId": 5}])}
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        with _published(_published_summary(self.ITEM_ROW)), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[thread], needs_human=[item]),
                cp, "owner/repo", 1, threads, head_sha="abc1234",
            )
        body = post.call_args[0][2]
        assert "#issuecomment-77" not in body
        assert "carried over" not in body
        assert len(pr.summary_scope.table_rows(body)) == 1
