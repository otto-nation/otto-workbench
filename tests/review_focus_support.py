"""Helpers shared by the focus suites: focus areas and what a phase derives."""

import sys
from dataclasses import dataclass
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.record import FocusArea, FocusSource


def area(id="F1", title="t", rationale="", source=FocusSource.DERIVED, paths=(), coverage=""):
    """A focus area, derived unless `source` says otherwise."""
    return FocusArea(
        id=id, title=title, rationale=rationale, source=source,
        paths=tuple(paths), coverage=coverage,
    )


def user_area(title, rationale=""):
    """An area the operator requested: pathless, uncovered, numbered F1."""
    return area(title=title, rationale=rationale, source=FocusSource.USER)


@dataclass(frozen=True)
class Derived:
    """What `adopt` reads off a phase's derived focus entry."""

    title: str
    rationale: str = ""
    paths: tuple[str, ...] = ()
    coverage: str = ""


def derived_report(title, rationale="", paths=(), coverage=""):
    """A derived focus entry as a phase reports it, with `paths` as a tuple."""
    return Derived(title=title, rationale=rationale, paths=tuple(paths), coverage=coverage)


DERIVED_FOCUS = [{"id": "", "title": "Retry", "rationale": "r", "paths": [], "coverage": "checked"}]
