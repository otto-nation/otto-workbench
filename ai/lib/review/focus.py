"""Focus areas as data: what the operator asked for, what phases derive, and who sees which.

A review's focus is a short list of `review.record.FocusArea`s. Requested
(`source=user`) areas come first and are always kept; derived ones follow,
at most `review.artifacts.MAX_DERIVED_FOCUS` of them. This module is the one
owner of how that list is built, merged across rounds, numbered (`F1…`) and
scoped to a group. Renderers take the areas off the record and never import
it, so the list is decided once and only shown elsewhere.

Operator input is validated where it enters. `review` and `pr batch run` read
every `--focus-file` at parse time and refuse, before anything is spent, an
unreadable or non-UTF-8 file, an empty text, one over `MAX_FOCUS_BYTES` raw
bytes (inline `--focus` texts included), or more than `MAX_FOCUS_AREAS` texts
in total. The focus section is fixed overhead in every prompt and is never cut
to fit, so it is bounded here rather than truncated later. What travels on is
the text, never a path. A text becomes an area by one rule: its first
non-blank line, leading `#`, `-` and `*` markers stripped, is the title, and
the rest is the rationale.

Across rounds, the requested areas are this invocation's flags, else the
resumed run's own areas whenever there is a resumed run (an empty list
included, so a run that recorded none keeps none), else the prior record's
requested areas. A prior record's derived areas are carried behind them. No
coverage is ever carried: it describes the round that wrote it.

An area's path matches a file on a boundary — the file itself, or anything
under it as a directory — so `pkg` does not reach `pkgx/a.go`. A derived or
carried path matching no file in the PR is dropped, and an area left with no
path is pathless and reaches every group, so a misspelt path cannot hide an
area from the whole review.
"""

# doc-group: pipeline

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from review.artifacts import MAX_DERIVED_FOCUS
from review.budget import MAX_FOCUS_AREAS, MAX_FOCUS_BYTES
from review.record import FocusArea, FocusSource, ReviewRecord
from review.store import StoredReview

_TITLE_MARKERS = "#-* \t"


class FocusInputError(ValueError):
    """Operator focus `review`/`pr batch run` refuse, with the message to print."""


def add_focus_args(parser: argparse.ArgumentParser) -> None:
    """`--focus TEXT` and `--focus-file PATH`, both repeatable (dest `focus`, `focus_file`)."""
    parser.add_argument(
        "--focus", action="append", default=[], metavar="TEXT",
        help=f"An area the review must cover (repeatable; at most {MAX_FOCUS_BYTES} bytes each)",
    )
    parser.add_argument(
        "--focus-file", action="append", default=[], metavar="PATH",
        help=f"A file whose contents are a focus area (repeatable; at most {MAX_FOCUS_BYTES} bytes)",
    )


def _checked(raw: bytes, where: str) -> str:
    """`raw` decoded and stripped, or FocusInputError naming `where`."""
    if len(raw) > MAX_FOCUS_BYTES:
        raise FocusInputError(f"{where} is {len(raw)} bytes; focus is capped at {MAX_FOCUS_BYTES}")
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise FocusInputError(f"{where} is not UTF-8: {exc}") from exc
    if not text:
        raise FocusInputError(f"{where} is empty")
    return text


def _read_file(path: str) -> str:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise FocusInputError(f"--focus-file {path} could not be read: {exc.strerror or exc}") from exc
    return _checked(raw, f"--focus-file {path}")


def operator_texts(args: argparse.Namespace) -> tuple[str, ...]:
    """Every --focus text, then every --focus-file's contents, stripped.

    Raises FocusInputError: unreadable, not UTF-8, empty, over MAX_FOCUS_BYTES (raw bytes),
    or more than MAX_FOCUS_AREAS texts in total.
    """
    inline = list(getattr(args, "focus", None) or [])
    files = list(getattr(args, "focus_file", None) or [])
    if len(inline) + len(files) > MAX_FOCUS_AREAS:
        raise FocusInputError(
            f"{len(inline) + len(files)} focus texts given; at most {MAX_FOCUS_AREAS} are allowed"
        )
    texts = [_checked(t.encode("utf-8"), f"--focus text {n}") for n, t in enumerate(inline, 1)]
    return tuple(texts + [_read_file(p) for p in files])


def _renumber(areas: Sequence[FocusArea]) -> tuple[FocusArea, ...]:
    """`areas` numbered F1… in order — the one numbering an area gets."""
    return tuple(replace(a, id=f"F{n}") for n, a in enumerate(areas, 1))


def _matches(path: str, file: str) -> bool:
    """Whether area path `path` reaches PR file `file`, on a boundary."""
    return file == path or file.startswith(path.rstrip("/") + "/")


def _in_pr(paths: Sequence[str], pr_files: Sequence[str]) -> tuple[str, ...]:
    """The paths of `paths` that match some PR file, in order."""
    return tuple(p for p in paths if any(_matches(p, f) for f in pr_files))


def _title_key(title: str) -> str:
    return " ".join(title.split()).casefold()


def _operator_area(text: str) -> FocusArea:
    lines = text.strip().splitlines()
    return FocusArea(
        id="", title=lines[0].lstrip(_TITLE_MARKERS).strip(),
        rationale="\n".join(lines[1:]).strip(), source=FocusSource.USER,
    )


def operator_areas(texts: Sequence[str]) -> tuple[FocusArea, ...]:
    """One USER area per text: first non-blank line (leading #, -, * markers stripped) is the
    title, the rest the rationale, stripped. Numbered F1…"""
    return _renumber([_operator_area(t) for t in texts if t.strip()])


def user_areas(areas: Sequence[FocusArea]) -> tuple[FocusArea, ...]:
    """The requested areas of `areas`, in order."""
    return tuple(a for a in areas if a.source is FocusSource.USER)


def needs_derived(areas: Sequence[FocusArea]) -> bool:
    """Whether `areas` holds no derived area yet, so a phase must derive some."""
    return not any(a.source is FocusSource.DERIVED for a in areas)


def _uncovered(areas: Sequence[FocusArea]) -> list[FocusArea]:
    return [replace(a, coverage="") for a in areas]


def resolve_user_areas(texts: Sequence[str], prior: StoredReview | None,
                       resumed: Sequence[FocusArea] | None) -> tuple[FocusArea, ...]:
    """The requested areas for this round, in order of precedence.

    This invocation's texts; else the resumed run's areas whenever `resumed is not None`, an
    empty list included; else the prior record's requested areas, coverage cleared.
    """
    if texts:
        return operator_areas(texts)
    if resumed is not None:
        return _renumber(user_areas(resumed))
    if prior is None:
        return ()
    return _renumber(_uncovered(user_areas(prior.record.focus)))


def _asked(areas: Sequence[FocusArea]) -> list[tuple[str, str]]:
    return [(a.title, a.rationale) for a in user_areas(areas)]


def focus_changed(areas: Sequence[FocusArea], prior: StoredReview | None) -> bool:
    """Whether the USER areas differ from the prior record's, on (title, rationale)."""
    if prior is None:
        return False
    return _asked(areas) != _asked(prior.record.focus)


def carry_derived(areas: Sequence[FocusArea], prior: StoredReview | None,
                  pr_files: Sequence[str]) -> tuple[FocusArea, ...]:
    """areas' USER areas, then the prior record's DERIVED areas (coverage cleared), renumbered.

    Carried paths matching no PR file are dropped; an area left with none is pathless.
    """
    held = prior.record.focus if prior is not None else ()
    carried = [
        replace(a, coverage="", paths=_in_pr(a.paths, pr_files))
        for a in held if a.source is FocusSource.DERIVED
    ]
    return _renumber([*user_areas(areas), *carried])


def _derived_area(item: Any, pr_files: Sequence[str]) -> FocusArea:
    return FocusArea(
        id="", title=item.title.strip(), rationale=item.rationale.strip(),
        paths=_in_pr(tuple(item.paths), pr_files), source=FocusSource.DERIVED,
        coverage=getattr(item, "coverage", ""),
    )


def adopt(areas: Sequence[FocusArea], derived: Sequence[Any],
          pr_files: Sequence[str]) -> tuple[FocusArea, ...]:
    """areas plus up to MAX_DERIVED_FOCUS new DERIVED areas.

    A title equal (casefold, whitespace collapsed) to one already held is dropped; paths not
    matching a PR file are dropped; `coverage` is kept when the item has one. Renumbered F1…
    Each item is read for `title`, `rationale`, `paths` and, if it has one, `coverage`.
    """
    held = {_title_key(a.title) for a in areas}
    added: list[FocusArea] = []
    for item in derived:
        key = _title_key(item.title)
        if len(added) >= MAX_DERIVED_FOCUS or not key or key in held:
            continue
        held.add(key)
        added.append(_derived_area(item, pr_files))
    return _renumber([*areas, *added])


def areas_for_group(areas: Sequence[FocusArea], files: Sequence[str]) -> tuple[FocusArea, ...]:
    """Pathless areas, and those with a path matching one of files on a boundary."""
    return tuple(
        a for a in areas
        if not a.paths or any(_matches(p, f) for p in a.paths for f in files)
    )


def with_run_focus(record: ReviewRecord, areas: Sequence[FocusArea]) -> ReviewRecord:
    """record unchanged when it carries focus, else with `areas` (uncovered)."""
    if record.focus:
        return record
    return replace(record, focus=tuple(_uncovered(areas)))
