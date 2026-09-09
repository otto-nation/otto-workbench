"""Tests for `core.markdown` — table cells written and read back.

The round trip is the point. A summary comment this workbench publishes is
re-read on the next round to recover which rows it carried, so a cell that does
not survive `escape_cell` → `row_cells` → `plain_cell` unchanged is a row that
looks new every round.
"""

import sys
from pathlib import Path

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from core import markdown  # noqa: E402


class TestEscapeCell:
    def test_a_pipe_is_escaped(self):
        assert markdown.escape_cell("a | b") == "a \\| b"

    def test_text_without_a_pipe_is_unchanged(self):
        assert markdown.escape_cell("plain summary") == "plain summary"

    def test_every_pipe_is_escaped_not_just_the_first(self):
        assert markdown.escape_cell("a || b") == "a \\|\\| b"


class TestRowCells:
    def test_a_row_splits_into_its_cells(self):
        row = "| one | two | three | four |"
        assert markdown.row_cells(row) == ["one", "two", "three", "four"]

    def test_an_escaped_pipe_stays_inside_its_cell(self):
        """The whole reason the split is a regex and not `str.split`."""
        row = "| a \\| b | @kgn | f.py | Dismissed |"
        assert markdown.row_cells(row) == ["a \\| b", "@kgn", "f.py", "Dismissed"]

    def test_padding_is_stripped(self):
        assert markdown.row_cells("|   a   |   b   |") == ["a", "b"]

    def test_an_empty_cell_survives_as_empty(self):
        """Position is identity, so a blank cell may not collapse away."""
        assert markdown.row_cells("| a |  | c |") == ["a", "", "c"]


class TestPlainCell:
    def test_a_link_reduces_to_its_label(self):
        assert markdown.plain_cell("[label](https://example.com/x)") == "label"

    def test_code_ticks_are_dropped(self):
        assert markdown.plain_cell("`f.py:12`") == "f.py:12"

    def test_a_linked_code_span_reduces_to_the_path(self):
        cell = "[`f.py:12`](https://github.com/o/r/blob/abc1234/f.py#L12)"
        assert markdown.plain_cell(cell) == "f.py:12"

    def test_the_sha_inside_a_link_does_not_reach_the_result(self):
        """Why identity is taken from the label: the target changes each round."""
        first = markdown.plain_cell("[`f.py`](https://github.com/o/r/blob/aaa/f.py)")
        second = markdown.plain_cell("[`f.py`](https://github.com/o/r/blob/bbb/f.py)")
        assert first == second == "f.py"

    def test_plain_text_is_returned_unchanged(self):
        assert markdown.plain_cell("just words") == "just words"


class TestTheRoundTrip:
    """What the published comment has to survive to be read back."""

    @pytest.mark.parametrize("text", [
        "plain",
        "a | b",
        "a || b, not a | b",
        "trailing pipe |",
        "| leading pipe",
    ])
    def test_an_escaped_summary_comes_back_as_one_cell(self, text):
        row = f"| {markdown.escape_cell(text)} | @kgn |"
        assert markdown.row_cells(row)[0] == markdown.escape_cell(text)

    def test_a_cell_holding_a_pipe_does_not_shift_the_ones_after_it(self):
        row = f"| {markdown.escape_cell('a | b')} | @kgn | f.py | Dismissed |"
        assert markdown.row_cells(row)[1:] == ["@kgn", "f.py", "Dismissed"]
