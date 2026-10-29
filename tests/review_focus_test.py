"""Focus areas as data: operator input, the merge across rounds, and group scoping."""

import argparse
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.budget import MAX_FOCUS_AREAS, MAX_FOCUS_BYTES
from review.focus import (
    FocusInputError, add_focus_args, adopt, areas_for_group, carry_derived,
    focus_changed, needs_derived, operator_areas, operator_texts, resolve_user_areas,
    user_areas, with_run_focus,
)
from review.record import FocusSource
from review.store import StoredReview
from review.types import ReviewMeta
from review_focus_support import area, derived_report, user_area
from review_record_support import record


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_focus_args(parser)
    return parser.parse_args(list(argv))


def _prior(*areas) -> StoredReview:
    return StoredReview(record(focus=tuple(areas)), ReviewMeta())


def _titles(areas) -> list[str]:
    return [a.title for a in areas]


# ── operator_texts ───────────────────────────────────────────────────────────

def test_texts_then_files_in_order(tmp_path):
    one, two = tmp_path / "one.md", tmp_path / "two.md"
    one.write_text("  from file one\n")
    two.write_text("from file two")
    args = _args("--focus-file", str(one), "--focus", " inline a ", "--focus-file", str(two),
                 "--focus", "inline b")
    assert operator_texts(args) == ("inline a", "inline b", "from file one", "from file two")


def test_no_flags_is_no_texts():
    assert operator_texts(_args()) == ()


def test_a_file_over_4_kb_is_refused(tmp_path):
    big, exact = tmp_path / "big.md", tmp_path / "exact.md"
    big.write_bytes(b"x" * (MAX_FOCUS_BYTES + 1))
    exact.write_bytes(b"x" * MAX_FOCUS_BYTES)
    with pytest.raises(FocusInputError) as caught:
        operator_texts(_args("--focus-file", str(big)))
    assert str(big) in str(caught.value) and str(MAX_FOCUS_BYTES) in str(caught.value)
    assert operator_texts(_args("--focus-file", str(exact))) == ("x" * MAX_FOCUS_BYTES,)


def test_an_inline_text_over_4_kb_is_refused():
    with pytest.raises(FocusInputError, match=str(MAX_FOCUS_BYTES)):
        operator_texts(_args("--focus", "é" * (MAX_FOCUS_BYTES // 2 + 1)))
    assert operator_texts(_args("--focus", "é" * (MAX_FOCUS_BYTES // 2)))


@pytest.mark.parametrize("content", [b"", b"  \n\t\n", None, b"\xff\xfe bad"],
                         ids=["empty", "whitespace", "missing", "not-utf8"])
def test_an_empty_or_unreadable_file_is_refused(tmp_path, content):
    path = tmp_path / "focus.md"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(FocusInputError, match=str(path)):
        operator_texts(_args("--focus-file", str(path)))


def test_an_empty_inline_text_is_refused():
    with pytest.raises(FocusInputError):
        operator_texts(_args("--focus", "   "))


def test_more_than_five_focus_texts_are_refused(tmp_path):
    files = []
    for n in range(3):
        path = tmp_path / f"f{n}.md"
        path.write_text(f"file {n}")
        files += ["--focus-file", str(path)]
    with pytest.raises(FocusInputError, match=str(MAX_FOCUS_AREAS)):
        operator_texts(_args("--focus", "a", "--focus", "b", "--focus", "c", *files))
    assert len(operator_texts(_args("--focus", "a", "--focus", "b", *files))) == MAX_FOCUS_AREAS


# ── operator_areas ───────────────────────────────────────────────────────────

def test_the_first_line_is_the_title_and_the_rest_the_rationale():
    (only,) = operator_areas(["# Retry\n\nCheck backoff callers  \n"])
    assert (only.title, only.rationale) == ("Retry", "Check backoff callers")


@pytest.mark.parametrize("text", ["\n\n- Retry", "* Retry", "## Retry"])
def test_list_and_heading_markers_leave_the_title(text):
    assert operator_areas([text])[0].title == "Retry"


def test_areas_are_user_and_numbered():
    areas = operator_areas(["one", "two\nwhy"])
    assert [a.id for a in areas] == ["F1", "F2"]
    assert {a.source for a in areas} == {FocusSource.USER}
    assert all(a.paths == () and a.coverage == "" for a in areas)


def test_a_marker_only_first_line_does_not_become_the_title():
    (only,) = operator_areas(["---\n\n# Retry\n\nCheck callers"])
    assert (only.title, only.rationale) == ("Retry", "Check callers")


def test_text_with_no_title_is_refused():
    with pytest.raises(FocusInputError):
        operator_areas(["#\n---\n- "])


# ── user_areas / needs_derived ───────────────────────────────────────────────

# passes-at-base: documents the filter these helpers already apply
def test_user_areas_keeps_requested_areas_in_order():
    held = (area(title="d"), user_area("a"),
            area(id="F3", title="b", source=FocusSource.USER))
    assert [a.title for a in user_areas(held)] == ["a", "b"]


# passes-at-base: documents the filter these helpers already apply
def test_user_areas_is_empty_when_none_are_requested():
    assert user_areas(()) == ()
    assert user_areas((area(title="d"),)) == ()


# passes-at-base: documents the filter these helpers already apply
def test_needs_derived_when_no_derived_area_is_held():
    assert needs_derived(())
    assert needs_derived((user_area("asked"),))


# passes-at-base: documents the filter these helpers already apply
def test_needs_derived_when_a_derived_area_is_held():
    assert not needs_derived((area(title="d"),))
    assert not needs_derived((user_area("asked"), area(id="F2", title="d")))


# ── adopt ────────────────────────────────────────────────────────────────────

def test_requested_areas_come_first_and_derived_are_capped_at_three():
    derived = [derived_report(f"d{n}") for n in range(4)]
    out = adopt([user_area("asked")], derived, [])
    assert _titles(out) == ["asked", "d0", "d1", "d2"]
    assert [a.id for a in out] == ["F1", "F2", "F3", "F4"]
    assert [a.source for a in out] == [FocusSource.USER] + [FocusSource.DERIVED] * 3


def test_a_derived_area_restating_a_requested_one_is_dropped():
    derived = [derived_report("retry   BACKOFF"), derived_report("Locking")]
    out = adopt([user_area("Retry backoff")], derived, [])
    assert _titles(out) == ["Retry backoff", "Locking"]


def test_paths_outside_the_pr_are_dropped_and_an_area_left_without_paths_reaches_every_group():
    derived = [derived_report("kept", paths=("a.py", "gone.py")),
               derived_report("orphaned", paths=("gone.py",))]
    out = adopt([], derived, ["a.py", "b.py"])
    assert [a.paths for a in out] == [("a.py",), ()]
    assert _titles(areas_for_group(out, ["b.py"])) == ["orphaned"]


def test_coverage_survives_adoption():
    out = adopt([], [derived_report("d", coverage="checked every caller")], [])
    assert out[0].coverage == "checked every caller"


# ── areas_for_group ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, file, reached", [
    ("pkg", "pkg/a.go", True),
    ("pkg/", "pkg/a.go", True),
    ("pkg", "pkgx/a.go", False),
    ("a.go", "a.go", True),
])
def test_an_area_reaches_a_group_on_a_path_boundary(path, file, reached):
    scoped = area(paths=(path,))
    assert (areas_for_group([scoped], [file]) == (scoped,)) is reached


def test_a_pathless_area_reaches_every_group():
    pathless = area(title="everywhere")
    assert areas_for_group([pathless, area(id="F2", paths=("x.py",))], ["a.py"]) == (pathless,)


# ── resolve_user_areas ───────────────────────────────────────────────────────

def test_flags_replace_the_prior_requested_areas():
    out = resolve_user_areas(["new"], _prior(user_area("old")), None)
    assert _titles(out) == ["new"]


def test_without_flags_a_resumed_run_s_areas_win():
    resumed = [user_area("resumed")]
    out = resolve_user_areas([], _prior(user_area("old")), resumed)
    assert _titles(out) == ["resumed"]


def test_a_resumed_run_with_no_areas_keeps_none():
    assert resolve_user_areas([], _prior(user_area("old")), []) == ()


def test_resumed_user_areas_carry_without_coverage():
    resumed = [area(title="resumed", source=FocusSource.USER, coverage="stale")]
    (only,) = resolve_user_areas([], _prior(user_area("old")), resumed)
    assert (only.title, only.coverage, only.source) == ("resumed", "", FocusSource.USER)


def test_without_either_the_prior_requested_areas_carry_without_coverage():
    prior = _prior(area(title="derived"),
                   area(id="F2", title="old", source=FocusSource.USER, coverage="done"))
    (only,) = resolve_user_areas([], prior, None)
    assert (only.id, only.title, only.source, only.coverage) == ("F1", "old", FocusSource.USER, "")


def test_without_anything_there_are_no_requested_areas():
    assert resolve_user_areas([], None, None) == ()


# ── focus_changed ────────────────────────────────────────────────────────────

def test_same_titles_and_rationales_are_unchanged():
    prior = _prior(area(title="derived"), user_area("Retry", "why"))
    assert not focus_changed([user_area("Retry", "why"), area(title="other")], prior)


def test_a_new_area_is_a_change():
    assert focus_changed([user_area("Retry"), user_area("Locks")], _prior(user_area("Retry")))
    assert focus_changed([user_area("Retry", "new why")], _prior(user_area("Retry", "why")))


def test_no_prior_is_no_change():
    assert not focus_changed([user_area("Retry")], None)


# ── carry_derived ────────────────────────────────────────────────────────────

def test_prior_derived_areas_follow_this_run_s_requested_ones_without_coverage():
    prior = _prior(user_area("old ask"), area(id="F2", title="derived", paths=("a.py",),
                                             coverage="checked"))
    out = carry_derived([user_area("ask")], prior, ["a.py"])
    assert _titles(out) == ["ask", "derived"]
    assert [a.id for a in out] == ["F1", "F2"]
    assert (out[1].source, out[1].paths, out[1].coverage) == (FocusSource.DERIVED, ("a.py",), "")


def test_carried_paths_outside_the_pr_are_dropped():
    out = carry_derived([], _prior(area(title="derived", paths=("gone.py",))), ["a.py"])
    assert [(a.title, a.paths) for a in out] == [("derived", ())]


def test_without_a_prior_only_the_requested_areas_carry():
    assert _titles(carry_derived([user_area("ask"), area(title="d")], None, [])) == ["ask"]


# ── with_run_focus ───────────────────────────────────────────────────────────

def test_an_agent_s_focus_is_kept():
    written = record(focus=(area(title="agent", coverage="looked"),))
    assert with_run_focus(written, [user_area("run")]) is written


def test_an_empty_record_gets_the_run_s_areas():
    out = with_run_focus(record(), [area(title="run", coverage="stale")])
    assert [(a.title, a.coverage) for a in out.focus] == [("run", "")]
