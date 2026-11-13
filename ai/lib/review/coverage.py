"""Coverage misses a PR review records at publish, judged later by the verdict.

`required_triage` is the changed-file set a PR review must account for:
delta paths on an incremental run, otherwise every PR file, minus generated
(tier 3) paths. `coverage_gaps` is the one owner of that comparison against
the record's File Triage and requested focus. `_publish` calls it once and
stores the result on `ReviewMeta.coverage_gaps`; `write_unchanged_review`
passes the prior sidecar's gaps through rather than recomputing, because it
carries the prior findings unchanged.

# ceiling: `--repair` keeps the stored coverage_gaps rather than recomputing.
# Upgrade trigger: if `--repair` starts rebuilding triage independently of the group files.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Sequence

from core.phases import Mode
from review.grouping import classify_tier
from review.prompt_sections import _is_incremental
from review.record import FocusSource, ReviewRecord
from review.types import CoverageGap, CoverageGapKind, ReviewJob

_MAX_NAMES = 5


def _norm_path(path: str) -> str:
    """`path` with a leading `./` and surrounding backticks stripped."""
    text = path.strip().strip("`").strip()
    if text.startswith("./"):
        text = text[2:]
    return text


def required_triage(job: ReviewJob) -> tuple[str, ...]:
    """Sorted paths a PR review must have a File Triage entry for.

    Self-reviews return nothing: their contract is `triage: []`, not a
    per-file account. Generated files are never required.
    """
    if job.mode is Mode.SELF:
        return ()
    if _is_incremental(job):
        paths = job.preflight.delta_files
    else:
        paths = [f["path"] for f in job.pr.files]
    required: set[str] = set()
    for path in paths:
        normalised = _norm_path(path)
        if normalised and classify_tier(normalised) != 3:
            required.add(normalised)
    return tuple(sorted(required))


def coverage_gaps(job: ReviewJob, record: ReviewRecord) -> tuple[CoverageGap, ...]:
    """Untriaged required files and requested focus areas with empty coverage.

    Returns nothing for self-reviews. Derived areas do not gate. Called after
    `with_run_focus` so a record that carried no focus still sees this run's
    requested areas.
    """
    if job.mode is Mode.SELF:
        return ()
    triaged = {_norm_path(entry.path) for entry in (record.triage or ())}
    gaps: list[CoverageGap] = [
        CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject=path)
        for path in required_triage(job)
        if path not in triaged
    ]
    for area in record.focus:
        if area.source is not FocusSource.USER or area.coverage:
            continue
        subject = f"{area.id} ({area.title})" if area.title else area.id
        gaps.append(CoverageGap(kind=CoverageGapKind.FOCUS_UNASSESSED, subject=subject))
    return tuple(gaps)


def _named(subjects: Sequence[str]) -> str:
    if len(subjects) <= _MAX_NAMES:
        return ", ".join(subjects)
    shown = subjects[:_MAX_NAMES]
    more = len(subjects) - _MAX_NAMES
    return f"{', '.join(shown)} and {more} more"


def gaps_prose(gaps: Sequence[CoverageGap]) -> str:
    """The coverage clause the verdict line names, or "" when there are no gaps.

    At most five subjects are listed, then "and N more".
    """
    untriaged = [g.subject for g in gaps if g.kind == CoverageGapKind.UNTRIAGED]
    focus = [g.subject for g in gaps if g.kind == CoverageGapKind.FOCUS_UNASSESSED]
    parts: list[str] = []
    if untriaged:
        n = len(untriaged)
        noun = "file" if n == 1 else "files"
        parts.append(f"no triage entry for {n} changed {noun} ({_named(untriaged)})")
    for subject in focus:
        parts.append(f"requested focus {subject} not assessed")
    return "; ".join(parts)
