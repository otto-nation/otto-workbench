"""Tests for text's shared formatting helpers."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.text import age_of, join_or, plural, relative_time


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


# ── age_of / relative_time ────────────────────────────────────────────────


def _stamp(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


@pytest.mark.parametrize("kwargs,expected", [
    ({"minutes": 5}, "5 minutes ago"),
    ({"minutes": 59}, "59 minutes ago"),
    ({"hours": 1}, "1 hours ago"),
    ({"hours": 23}, "23 hours ago"),
    ({"hours": 24}, "1 day ago"),
    ({"days": 3}, "3 days ago"),
])
def test_relative_time_at_each_unit_boundary(kwargs, expected):
    assert relative_time(_stamp(**kwargs)) == expected


@pytest.mark.parametrize("stamp", ["not a timestamp", "", None])
def test_relative_time_reads_an_unusable_stamp_as_no_age(stamp):
    assert relative_time(stamp) == ""


def test_age_of_measures_from_the_stamp_to_now():
    age = age_of(_stamp(hours=5))
    assert timedelta(hours=5) <= age < timedelta(hours=5, minutes=1)


@pytest.mark.parametrize("stamp", ["not a timestamp", "", None])
def test_age_of_is_none_rather_than_zero_for_an_unusable_stamp(stamp):
    """Zero would read as "just now", which is the direction that hides an
    unreadable stamp behind the freshest possible answer."""
    assert age_of(stamp) is None


def test_age_of_reads_a_naive_stamp_as_utc():
    """`datetime.now().isoformat()` carries no offset, and subtracting it from an
    aware now raises rather than returning an age."""
    naive = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None)
    age = age_of(naive.isoformat())
    assert timedelta(hours=2) <= age < timedelta(hours=2, minutes=1)


def test_age_of_accepts_the_z_suffix_state_files_are_written_with():
    """`pr.state.now_iso` writes `...Z`, which `fromisoformat` rejects before 3.11."""
    stamp = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    age = age_of(stamp)
    assert timedelta(hours=3) <= age < timedelta(hours=3, minutes=1)
