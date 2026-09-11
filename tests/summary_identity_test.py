"""Tests for `summary_model.row_key_from_cells` — one row, one identity.

A summary row is rendered to markdown, published, and re-read on the next round
to recover what the comment already carried. The fresh row and the published one
have to key alike or the round restates a row it already published, so identity
has one definition and both paths go through it.

The renderer used to obtain a fresh row's key by rendering the row and parsing
it straight back. That is what these tests replace, and the equivalence matrix
below is why the replacement is safe to make: for every shape a row can take,
keying the cells directly gives the answer the round trip gave.

The suite before this file could not have caught getting it wrong. A
deliberately broken typed key passed all of it, because the idempotence tests
all use anchored rows and never reach the fallback tier through a real render —
see `TestTheFallbackTierIsReached`.
"""

import sys

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from core import markdown  # noqa: E402
from pr import summary_model, summary_row  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

REPO = "owner/repo"
SHA = "9f2e1a0"

# Every shape a row's cells can take, named by what makes it interesting. The
# two that matter most are the ones a key built from the typed entry gets wrong:
# a summary that renders a nested markdown link, and a file cell whose line
# anchor the renderer dropped.
ROW_SHAPES = {
    "plain": dict(summary="drop the guard", file="a.py", line=2),
    "nested_link": dict(summary="[errors.Is](https://pkg.go.dev/errors#Is) here",
                        file="a.py", line=2),
    "pipe_in_summary": dict(summary="use a || b, not a | b", file="a.py", line=2),
    "no_file": dict(summary="a point about the PR", file="", line=0),
    "no_line": dict(summary="whole-file point", file="a.py", line=0),
    "spaces_in_path": dict(summary="rename it", file="d/e f.go", line=7),
    "empty_summary": dict(summary="", file="a.py", line=1),
    "long_summary": dict(summary="x" * 300, file="a.py", line=1),
    "brackets": dict(summary="[not a link] just brackets", file="a.py", line=3),
    "backticks": dict(summary="`code` in the summary", file="a.py", line=4),
    "unicode": dict(summary="— em dash and ünïcode", file="a.py", line=5),
    "html_comment": dict(summary="text <!-- hidden --> more", file="a.py", line=6),
}

STATUSES = [
    "Fixed in [`9f2e1a0`](https://github.com/owner/repo/commit/9f2e1a0)",
    "Deferred",
    "Deferred → [ENG-1](https://linear.app/i/ENG-1)",
    "Already addressed",
    "Dismissed (invalid)",
    "Addressed outside the fix pass",
    "Needs discussion",
]


def _entry(shape: dict, tid: str = "t1") -> CommentItem:
    return CommentItem(
        id=tid, summary=shape["summary"], reviewer="kgn",
        file=shape["file"], line=shape["line"],
    )


def _key_via_text(row: str) -> str:
    """The identity the published-text path recovers from a rendered row.

    This is the path a carried-over row takes: it exists only as markdown, so
    its cells are parsed back out before it can be keyed.
    """
    return summary_model.row_key_from_cells(markdown.row_cells(row))


class TestTheTwoPathsAgree:
    """The equivalence matrix: cells-keyed and text-keyed give one answer.

    The fresh path keys the cells it built the row from; the published path
    parses the row back into cells and keys those. Every row on a PR goes
    through the first on the round that writes it and the second on every round
    after, so a shape where they disagree is a row that duplicates itself.
    """

    @pytest.mark.parametrize("shape_name", sorted(ROW_SHAPES))
    @pytest.mark.parametrize("status", STATUSES)
    @pytest.mark.parametrize("head_sha", ["", SHA])
    def test_keying_cells_matches_keying_the_rendered_row(
        self, shape_name, status, head_sha,
    ):
        entry = _entry(ROW_SHAPES[shape_name])
        cells = summary_row.row_cells_for(entry, status, {}, REPO, 1, head_sha)
        row = summary_row.render_row(cells)
        assert summary_model.row_key_from_cells(cells) == _key_via_text(row)

    @pytest.mark.parametrize("shape_name", sorted(ROW_SHAPES))
    def test_a_row_keys_the_same_through_a_full_round_trip(self, shape_name):
        """Render, publish, read back, key — the sequence a real round runs."""
        entry = _entry(ROW_SHAPES[shape_name])
        cells = summary_row.row_cells_for(entry, "Deferred", {}, REPO, 1, SHA)
        published = "\n".join([
            summary_model.TABLE_HEADER,
            summary_model.TABLE_DIVIDER,
            summary_row.render_row(cells),
        ])
        recovered = [
            line for line in published.splitlines()
            if line.startswith("|") and line.strip("|-: ")
        ][-1]
        assert _key_via_text(recovered) == summary_model.row_key_from_cells(cells)


class TestTheActionCellIsNeverIdentity:
    """A round that changes a row's outcome must key it as the same row.

    Deferred becoming fixed is the case the whole carry-forward design exists to
    handle, so the Action cell is excluded from every form of the key.
    """

    @pytest.mark.parametrize("shape_name", sorted(ROW_SHAPES))
    def test_every_status_keys_the_row_alike(self, shape_name):
        entry = _entry(ROW_SHAPES[shape_name])
        keys = {
            summary_model.row_key_from_cells(
                summary_row.row_cells_for(entry, status, {}, REPO, 1, SHA))
            for status in STATUSES
        }
        assert len(keys) == 1


class TestTheFallbackTierIsReached:
    """The tier no existing test exercised through a real render.

    An entry with no resolvable permalink renders a row with no anchor at all,
    so its identity falls back to the row's own text. Every idempotence test in
    the suite uses anchored rows, which is why a broken fallback passed them
    all: the tier was only ever asserted against literal strings that bypassed
    the renderer.
    """

    def _unanchored_cells(self, summary: str, status: str = "Deferred"):
        # No thread id and no comment-item source, so neither anchor is written.
        entry = CommentItem(id="local-1", summary=summary, reviewer="kgn",
                            file="a.py", line=2)
        return summary_row.row_cells_for(entry, status, {}, REPO, 1, SHA)

    def test_a_row_with_no_anchor_still_has_an_identity(self):
        key = summary_model.row_key_from_cells(self._unanchored_cells("a point"))
        assert key
        assert "#discussion_r" not in key
        assert "#issuecomment" not in key

    def test_the_fallback_key_is_stable_across_rounds(self):
        """Idempotence at the tier the suite never reached through `emit`."""
        first = self._unanchored_cells("a point", "Deferred")
        later = self._unanchored_cells("a point", "Fixed in [`9f2e1a0`](u)")
        assert (summary_model.row_key_from_cells(first)
                == summary_model.row_key_from_cells(later))

    def test_the_fallback_key_survives_the_text_path(self):
        cells = self._unanchored_cells("a point")
        row = summary_row.render_row(cells)
        assert _key_via_text(row) == summary_model.row_key_from_cells(cells)

    def test_two_unanchored_rows_at_one_location_key_apart_by_summary(self):
        """The summary cell is all that separates them, so it has to count."""
        a = summary_model.row_key_from_cells(self._unanchored_cells("first point"))
        b = summary_model.row_key_from_cells(self._unanchored_cells("second point"))
        assert a != b


class TestTheDroppedAnchorShape:
    """The case a key built from the typed entry gets wrong.

    `permalinks.anchored_line` returns 0 when the line cannot be held in the SHA
    being linked, and the file cell then renders without `:line`. An entry that
    still carries `line=9` therefore renders — and must key as — a row with no
    line in it. Keying the entry's own field instead gives an identity the
    published row does not have.
    """

    def test_the_key_follows_the_rendered_cell_not_the_entry(self):
        entry = CommentItem(id="t1", summary="s", reviewer="kgn", file="a.py", line=9)
        # No head_sha, so no permalink and no anchor decision to make.
        bare = summary_row.row_cells_for(entry, "Deferred", {}, REPO, 1, "")
        key = summary_model.row_key_from_cells(bare)
        assert _key_via_text(summary_row.render_row(bare)) == key


def _thread(tid: str, db_id: int) -> ReportThread:
    """A thread whose first comment carries the database id the anchor is built from."""
    return ReportThread(
        id=tid, state=ThreadState.NEW, is_resolved=False, reviewer="kgn",
        file="a.py", line=2, comments=[{"databaseId": db_id, "author": {"login": "kgn"}}],
    )


class TestAThreadAnchorIsIdentityOnItsOwn:
    """One `#discussion_r` id names one review thread, which is one row.

    The anchor is searched for across the whole row rather than in the cell that
    carries it, because that is what the published-row path has always done.
    Narrowing it to the summary cell is a semantic change wearing a refactor's
    clothes: a row whose anchor rides in the file cell would key by its text
    instead, and stop matching the row it published last round.
    """

    def _cells(self, status="Deferred", **kw):
        entry = CommentItem(id="t1", summary=kw.get("summary", "drop the guard"),
                            reviewer="kgn", file="a.py", line=2)
        return summary_row.row_cells_for(
            entry, status, {"t1": _thread("t1", 111)}, REPO, 1, kw.get("head_sha", ""))

    def test_the_anchor_alone_is_the_key(self):
        assert summary_model.row_key_from_cells(self._cells()) == "#discussion_r111"

    def test_the_summary_does_not_enter_a_thread_row_key(self):
        """Rewording a thread's summary must not present it as a new row."""
        a = summary_model.row_key_from_cells(self._cells(summary="first wording"))
        b = summary_model.row_key_from_cells(self._cells(summary="second wording"))
        assert a == b == "#discussion_r111"

    def test_the_anchor_is_found_wherever_it_sits_in_the_row(self):
        """What M1 breaks: keying only the summary cell loses the anchor."""
        cells = ["plain text", "@kgn",
                 "[`a.py:2`](https://github.com/owner/repo/pull/1#discussion_r222)",
                 "Deferred"]
        assert summary_model.row_key_from_cells(cells) == "#discussion_r222"


class TestSiblingItemsKeyApartUnderOneAnchor:
    """N items cut from one comment share one anchor, so it cannot be the key.

    Triage decomposes a top-level comment into several findings and every one of
    them links back to the single `#issuecomment` permalink the comment has.
    Keying them by the anchor alone collapses all N into one row, because every
    map built on this key keeps one row per key.
    """

    def _cells(self, item_id: str, summary: str):
        entry = CommentItem(id=item_id, summary=summary, reviewer="kgn",
                            file="a.py", line=2)
        return summary_row.row_cells_for(entry, "Deferred", {}, REPO, 1, "")

    def test_each_sibling_gets_its_own_key(self):
        """What M2 breaks: three findings reach the table as one row."""
        keys = {
            summary_model.row_key_from_cells(self._cells(f"ic-500-{i}", s))
            for i, s in enumerate(["first point", "second point", "third point"])
        }
        assert len(keys) == 3

    def test_the_anchor_is_still_half_the_key(self):
        key = summary_model.row_key_from_cells(self._cells("ic-500-0", "a point"))
        assert key.startswith("#issuecomment-500")
        assert "a point" in key

    def test_a_sibling_keys_alike_across_a_status_change(self):
        first = self._cells("ic-500-0", "a point")
        later = summary_row.row_cells_for(
            CommentItem(id="ic-500-0", summary="a point", reviewer="kgn",
                        file="a.py", line=2),
            "Fixed in [`9f2e1a0`](u)", {}, REPO, 1, "")
        assert (summary_model.row_key_from_cells(first)
                == summary_model.row_key_from_cells(later))


class TestDecorationIsStrippedFromTheKey:
    """A cell keys by what a reader sees, not by how it was decorated.

    The SHA inside a permalink changes every round, so a key holding the link
    target would never match itself twice — the row would be republished for the
    life of the PR. Backticks go for the same reason.
    """

    def test_a_link_target_does_not_reach_the_key(self):
        """What M4 breaks: the pinned SHA makes every round a new row."""
        first = ["a point", "@kgn",
                 "[`a.py:2`](https://github.com/owner/repo/blob/aaaaaaa/a.py#L2)",
                 "Deferred"]
        later = ["a point", "@kgn",
                 "[`a.py:2`](https://github.com/owner/repo/blob/bbbbbbb/a.py#L2)",
                 "Deferred"]
        assert (summary_model.row_key_from_cells(first)
                == summary_model.row_key_from_cells(later))

    def test_backticks_do_not_reach_the_key(self):
        plain = ["a point", "@kgn", "a.py:2", "Deferred"]
        ticked = ["a point", "@kgn", "`a.py:2`", "Deferred"]
        assert (summary_model.row_key_from_cells(plain)
                == summary_model.row_key_from_cells(ticked))


class TestPipesCannotBreakIdentity:
    """One unescaped pipe shifts every later cell and corrupts the key."""

    def test_a_summary_pipe_keys_the_same_both_ways(self):
        entry = _entry(ROW_SHAPES["pipe_in_summary"])
        cells = summary_row.row_cells_for(entry, "Deferred", {}, REPO, 1, SHA)
        assert len(cells) == len(summary_model.TABLE_COLUMNS)
        row = summary_row.render_row(cells)
        assert len(markdown.row_cells(row)) == len(summary_model.TABLE_COLUMNS)
        assert _key_via_text(row) == summary_model.row_key_from_cells(cells)
