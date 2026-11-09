"""The behaviours ledger: source filter, writer check, record entries, merge.

The field is tri-state like claims: `None` is not assessed, `()` is
assessed empty, and entries are the list. Groups write it unchecked;
`from_group` drops empty-text and empty-path reports and truncates past
the cap. Writers (single, synthesis) are checked: `has_behaviour_source`
is the hint filter, `behaviours_problems` is the check, `behaviours_of`
numbers reports `B1…` after it. `merged_behaviours` concatenates group
lists and yields `None` if any is `None`. A group gap makes the merged
list `None`; skipped groups contribute prior entries whose paths all lie
in those groups' files (`carried_behaviours`). There is no claims-style
carry and no sidecar: groups write the list on every round, and
behaviours derive no finding, so there is nothing to answer. `_publish`
verifies each cited test. Render never says "tested".
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace

import core.log
import review.budget
import review.grouping
from review.behaviour_types import BehaviourEntry, BehaviourReport
from review.grammar import strip_line_suffix
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.record import ReviewRecord
from review.types import ReviewJob

BEHAVIOUR_KINDS = frozenset({
    IngestErrorKind.MISSING_BEHAVIOURS,
    IngestErrorKind.BEHAVIOUR_COUNT,
    IngestErrorKind.BEHAVIOUR_SIZE,
    IngestErrorKind.BAD_BEHAVIOUR,
})

_SIZE_FIELDS = ("text", "test_cited")


def _error(kind: IngestErrorKind, at: str, detail: str) -> IngestError:
    return IngestError(kind, 0, detail, at=at)


def _has_bad_chars(value: str) -> bool:
    return any(ch.isspace() or not ch.isprintable() for ch in value)


def has_behaviour_source(job: ReviewJob) -> bool:
    """Whether the PR's files include a subject the hint filter keeps."""
    return bool(review.grouping.hint_subjects(job.pr.files))


def behaviours_problems(
    behaviours: tuple[BehaviourReport, ...] | None,
    *,
    has_behaviour_source: bool,
) -> tuple[IngestError, ...]:
    """Writer-check errors for `behaviours`; () when there is no source."""
    if not has_behaviour_source:
        return ()
    if behaviours is None:
        return (_error(
            IngestErrorKind.MISSING_BEHAVIOURS, "$.behaviours",
            "write behaviours for the changed source",
        ),)
    out: list[IngestError] = []
    cap_n = review.budget.MAX_BEHAVIOURS
    if len(behaviours) > cap_n:
        out.append(_error(
            IngestErrorKind.BEHAVIOUR_COUNT, "$.behaviours",
            f"{len(behaviours)} behaviours; at most {cap_n}",
        ))
    for i, entry in enumerate(behaviours):
        out.extend(_behaviour_problems(entry, i))
    return tuple(out)


def _behaviour_problems(entry: BehaviourReport, index: int) -> list[IngestError]:
    at = f"$.behaviours[{index}]"
    out: list[IngestError] = []
    cap = review.budget.MAX_BEHAVIOUR_BYTES
    for name in _SIZE_FIELDS:
        size = len(getattr(entry, name).encode())
        if size > cap:
            out.append(_error(
                IngestErrorKind.BEHAVIOUR_SIZE, f"{at}.{name}",
                f"{size} bytes; a behaviour field is capped at {cap}",
            ))
    if not entry.text.strip():
        out.append(_error(
            IngestErrorKind.BAD_BEHAVIOUR, f"{at}.text", "text is empty",
        ))
    if not entry.paths:
        out.append(_error(
            IngestErrorKind.BAD_BEHAVIOUR, f"{at}.paths", "paths is empty",
        ))
    path, sep, name = entry.test_cited.partition("::")
    cited_path = path if sep else entry.test_cited
    if entry.test_cited and (
        _has_bad_chars(entry.test_cited) or (sep and (not path or not name))
    ):
        out.append(_error(
            IngestErrorKind.BAD_BEHAVIOUR, f"{at}.test_cited",
            "test_cited is empty-sided or has whitespace or control characters",
        ))
    elif strip_line_suffix(cited_path) != cited_path:
        out.append(_error(
            IngestErrorKind.BAD_BEHAVIOUR, f"{at}.test_cited",
            f"{entry.test_cited!r} carries a line number; drop the :N suffix",
        ))
    for path in entry.paths:
        if not path or _has_bad_chars(path):
            out.append(_error(
                IngestErrorKind.BAD_BEHAVIOUR, f"{at}.paths",
                "path is empty or has whitespace or control characters",
            ))
        elif strip_line_suffix(path) != path:
            out.append(_error(
                IngestErrorKind.BAD_BEHAVIOUR, f"{at}.paths",
                f"{path!r} carries a line number; drop the :N suffix",
            ))
    return out


def behaviours_of(
    job: ReviewJob, outcome: ArtifactOutcome,
) -> tuple[BehaviourEntry, ...] | None:
    """Record behaviours from `outcome`, or `None` when the check degraded.

    `job` decides whether a source exists (`()` with none). `outcome.errors`
    of a behaviours kind, or a missing `outcome.artifact.behaviours`, yield
    `None`.
    """
    if not has_behaviour_source(job):
        return ()
    if any(e.kind in BEHAVIOUR_KINDS for e in outcome.errors):
        return None
    if outcome.artifact.behaviours is None:
        return None
    return tuple(
        BehaviourEntry(
            id=f"B{i}", text=b.text, test_cited=b.test_cited,
            paths=tuple(b.paths),
        )
        for i, b in enumerate(outcome.artifact.behaviours, start=1)
    )


def merged_behaviours(
    groups: Sequence[ReviewRecord],
) -> tuple[BehaviourEntry, ...] | None:
    """Concatenated group lists, renumbered `B1…`, or `None` if any is missing."""
    if not groups or any(group.behaviours is None for group in groups):
        return None
    entries = [entry for group in groups for entry in group.behaviours]
    return tuple(
        replace(entry, id=f"B{i}") for i, entry in enumerate(entries, start=1)
    )


def from_group(
    reports: Sequence[BehaviourReport] | None,
) -> tuple[BehaviourEntry, ...] | None:
    """Group reports as record entries; `None` passes through.

    Empty-text and empty-`paths` reports are dropped. Past `MAX_BEHAVIOURS`
    the list is truncated with a warning rather than rejected.
    """
    if reports is None:
        return None
    kept = [r for r in reports if r.text.strip() and r.paths]
    cap = review.budget.MAX_BEHAVIOURS
    if len(kept) > cap:
        core.log.warn(
            f"group behaviours truncated from {len(kept)} to {cap}",
        )
        kept = kept[:cap]
    return tuple(
        BehaviourEntry(
            id=f"B{i}", text=r.text, test_cited=r.test_cited,
            paths=tuple(r.paths),
        )
        for i, r in enumerate(kept, start=1)
    )


def carried_behaviours(
    prior: tuple[BehaviourEntry, ...] | None,
    files: Collection[str],
) -> tuple[BehaviourEntry, ...] | None:
    """Prior entries whose paths are non-empty and all in `files`.

    `None` when `prior` is `None`. Ids are unchanged; notes are cleared so
    `_publish` re-verifies each cited test.
    """
    if prior is None:
        return None
    owned = set(files)
    return tuple(
        replace(entry, note="")
        for entry in prior
        if entry.paths and all(path in owned for path in entry.paths)
    )
