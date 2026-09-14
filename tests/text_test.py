"""Tests for text's shared formatting helpers."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.text import join_or, plural


def test_plural_only_singular_at_one():
    assert [f"{n} file{plural(n)}" for n in (0, 1, 2)] == ["0 files", "1 file", "2 files"]


def test_join_or_one_value_is_the_value_alone():
    """The case the hand-rolled join got wrong: `[:-1]` is empty, so slicing and
    concatenating emits a stray leading comma before the last item."""
    assert join_or(["a dash"]) == "a dash"


def test_join_or_two_values_take_no_comma():
    assert join_or(["a dash", "a colon"]) == "a dash or a colon"


def test_join_or_three_or_more_values_take_the_serial_comma():
    assert join_or(["a dash", "a colon", "italics"]) == "a dash, a colon, or italics"


def test_join_or_nothing_is_the_empty_string():
    assert join_or([]) == ""
