"""What keeps successive self-review rounds converging instead of circling.

A round reviews what changed, settles what it can without an agent, and leaves
the next round less to do. Each rule here closes one way the loop used to fail
to terminate: a decline re-stated every round, a design call handed back to
the fix agent every round, new nits on files nobody touched, a killed pass
whose leftovers looked like author edits, and a fix commit that always moved
HEAD past the review that produced it.

`change_set` is what "did not change" means for all of them.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import git.client
from core.phases import Mode
from pr.fix import FixOutcome, ItemOutcome
from review.collect import uncommitted_files
from review.document import covers
from review.record import Disposition, ReviewRecord
from review.types import Finding, ReviewJob, StickySkip


def change_set(job: ReviewJob) -> frozenset[str] | None:
    """The files this run's review treats as changed since the prior round, or None.

    The delta when the run is incremental. When the prior review already covers
    HEAD, a self-review's same-commit change set is the working tree's
    (uncommitted edits plus the prior round's dirty files); in PR mode it is
    empty — a file changed only by merging the base counts as unchanged. None
    when neither holds: a full review with a moved HEAD measured no delta, so
    "not changed" cannot be answered and nothing may be settled on its strength.
    """
    preflight = job.preflight
    if preflight is not None and preflight.prior_head_sha:
        return frozenset(preflight.delta_files)
    if job.prior is not None and covers(job.prior.meta.head_sha, job.pr.head_sha):
        if job.mode != Mode.SELF:
            return frozenset()
        dirty = ()
        prior = job.prior
        if prior is not None and prior.meta is not None:
            dirty = prior.meta.dirty_files
        return frozenset(uncommitted_files(job.wt_path, job.pr.head_sha)) | frozenset(dirty)
    return None


def anchor_key(path: str, line: int | None) -> str:
    """How a sticky skip names its anchor: `path:line`, with 0 for a line-less finding."""
    return f"{path}:{line or 0}"


def blob_of(wt_path: str | Path, path: str) -> str:
    """The object id *path* has in the working tree at *wt_path*, or "" when absent."""
    if not path or not (Path(wt_path) / path).is_file():
        return ""
    return git.client.out("hash-object", "--", path, cwd=wt_path)


@dataclass(frozen=True)
class StickyApplied:
    """A record with live sticky skips applied, and the marks still live."""

    record: ReviewRecord
    marks: dict[str, StickySkip]


def apply_sticky_skips(
    record: ReviewRecord, marks: dict[str, StickySkip], wt_path: str | Path,
) -> StickyApplied:
    """*record* with every open finding at a live mark's anchor and severity skipped.

    A mark is live while its anchor file still hashes to the recorded blob. A
    stale mark is dropped from what is returned, so the sidecar sheds it on the
    write that follows and the next round's finding there is open again.
    """
    live = {key: mark for key, mark in marks.items()
            if mark.blob and blob_of(wt_path, key.rsplit(":", 1)[0]) == mark.blob}
    for f in record.visible:
        mark = live.get(anchor_key(f.path, f.line))
        if mark and f.disposition is Disposition.OPEN and mark.severity == f.severity:
            record = record.with_finding(f.id, disposition=Disposition.SKIPPED,
                                         reason=mark.reason)
    return StickyApplied(record, live)


def mark_skips(
    findings: dict[str, Finding], outcomes: list[ItemOutcome], wt_path: str | Path,
    committed: set[str] | frozenset[str] = frozenset(),
) -> dict[str, StickySkip]:
    """A mark for each finding this pass handed to a person, keyed by its anchor.

    None for a finding whose file is in *committed*, the pass's own commit
    scope: that file changed in this very pass, so the change a mark waits for
    has already happened, and the blob it would record is one nobody reviewed.
    """
    marks: dict[str, StickySkip] = {}
    for outcome in outcomes:
        f = findings.get(outcome.id)
        if outcome.outcome is not FixOutcome.NEEDS_HUMAN or f is None or not f.path:
            continue
        if f.path in committed:
            continue
        marks[anchor_key(f.path, f.line)] = StickySkip(
            blob_of(wt_path, f.path), f.severity, outcome.reason or "needs a person")
    return marks
