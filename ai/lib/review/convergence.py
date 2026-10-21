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

from dataclasses import dataclass, replace
from pathlib import Path

import core.log
import git.client
from core.phases import Mode
from core.proc import CmdResult
from core.trail import Trail, tinfo
from pr.fix import FixOutcome, ItemOutcome
from review.collect import uncommitted_files
from review.document import covers
from review.paths import read_review_meta, write_review_meta
from review.record import Disposition, RecordFinding, ReviewRecord
from review.types import (
    SEVERITY_IDIOMS, SEVERITY_NIT, Finding, FixInterruption, ReviewJob, StickySkip,
)


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

    Only findings `apply_outcomes` actually set to SKIPPED (`Finding.skipped`).
    A `NEEDS_HUMAN` that `_answered` left OPEN — an unverified tail from an
    earlier round — is not a skip and gets no mark. None for a finding whose
    file is in *committed*, the pass's own commit scope: that file changed in
    this very pass, so the change a mark waits for has already happened, and
    the blob it would record is one nobody reviewed.
    """
    marks: dict[str, StickySkip] = {}
    for outcome in outcomes:
        f = findings.get(outcome.id)
        if outcome.outcome is not FixOutcome.NEEDS_HUMAN or f is None or not f.path:
            continue
        if not f.skipped:
            continue
        if f.path in committed:
            continue
        marks[anchor_key(f.path, f.line)] = StickySkip(
            blob_of(wt_path, f.path), f.severity, outcome.reason or "needs a person")
    return marks


# The severities an incremental round may not add on a file outside its delta
# (D3), and withholds from the fix pass until a full round when it raised them (D13).
NIT_SEVERITIES = frozenset({SEVERITY_NIT, SEVERITY_IDIOMS})


def prior_ids(job: ReviewJob) -> frozenset[str]:
    """Every stable id and sid the prior round's findings carry; empty with no prior."""
    if job.prior is None:
        return frozenset()
    found = job.prior.record.findings
    return frozenset({f.stable_id for f in found} | {f.sid for f in found}) - {""}


def is_new_nit(severity: str, sid: str, stable_id: str, prior: frozenset[str]) -> bool:
    """Whether a finding is a nit or idiom this round raised for the first time.

    New means no carried `sid` and a `stable_id` the prior record does not
    hold. A re-run group restating a prior nit gives it a fresh ID and no sid
    but the same stable id — a carry, not a new finding.
    """
    return severity in NIT_SEVERITIES and not sid and stable_id not in prior


@dataclass(frozen=True)
class NitFilter:
    """A record without this round's out-of-delta nits, and the nits it dropped."""

    record: ReviewRecord
    dropped: tuple[RecordFinding, ...] = ()


def _out_of_delta_nit(f: RecordFinding, delta: set[str], prior: frozenset[str]) -> bool:
    return (is_new_nit(f.severity, f.sid, f.stable_id, prior)
            and bool(f.path) and f.path not in delta)


def drop_out_of_delta_nits(
    record: ReviewRecord, delta_files: list[str], prior: frozenset[str],
) -> NitFilter:
    """*record* without the new nits and idioms it raises on files outside the delta.

    New is `is_new_nit`'s answer against *prior*, the prior round's ids. A
    group re-runs on any one changed file and is shown the whole group's diff,
    so without this a round could ask for a rename in a file nobody touched —
    and the fix pass would make it, moving HEAD again. Must-fix and should-fix
    are kept wherever they are, and a finding about the whole PR names no file
    to be outside of.
    """
    delta = set(delta_files)
    dropped = tuple(f for f in record.findings if _out_of_delta_nit(f, delta, prior))
    if not dropped:
        return NitFilter(record)
    files = sorted({f.path for f in dropped})
    core.log.info(f"Incremental: dropped {len(dropped)} new nit/idiom finding(s) on "
                  f"files outside the delta ({', '.join(files)})")
    kept = tuple(f for f in record.findings if not _out_of_delta_nit(f, delta, prior))
    return NitFilter(replace(record, findings=kept), dropped)


def new_nit_ids(record: ReviewRecord, prior: frozenset[str]) -> frozenset[str]:
    """The stable ids of the nits and idioms *record* raises for the first time.

    Read off the record while it still carries sids — `review.outcome` calls
    this before `post_process_record` clears them — because a carried finding
    is known by its sid even when synthesis rewords it into a new stable id. A
    finding with no stable id is never counted: there is nothing to hold it
    by, and a path-less carried nit with no identity must not be withheld.
    """
    return frozenset(f.stable_id for f in record.findings
                     if f.stable_id and is_new_nit(f.severity, f.sid, f.stable_id, prior))


def held_nits(job: ReviewJob, *, incremental: bool) -> tuple[str, ...]:
    """What this round's sidecar records as `ReviewMeta.held_nits` (D13).

    On an incremental round, the prior round's held nits plus this round's new
    ones (`job.new_nits`), so a nit stays held while later incremental rounds
    carry it. A full round holds nothing, which is how a held nit reaches the
    fix pass. *incremental* is the caller's to answer
    (`review.prompt_sections._is_incremental`), which keeps this module clear
    of `review.collect`.
    """
    if not incremental:
        return ()
    previous = job.prior.meta.held_nits if job.prior else ()
    return tuple(sorted({*previous, *job.new_nits}))


def fix_work_set(findings: list[Finding], held: tuple[str, ...]) -> list[Finding]:
    """*findings* without the nits and idioms the review withheld from fix work (D13).

    Every fix commit moves HEAD and re-arms a round, and on the rounds that
    prompted this most of what was fixed was nits. A nit an incremental round
    raised, and every later incremental round carried, stays open on the page —
    reported, counted, there for a person or the next full review — but buys
    no turns and moves HEAD for nothing. *held* is the review's
    `ReviewMeta.held_nits`, written at publish, so a resumed or later pass gets
    the answer the publishing round gave; a full round's is empty and hands
    every severity. Matched by stable id and severity, never by an empty id.
    """
    # ceiling: a carried nit that synthesis rewords takes a new stable id and
    # so leaves the held set. Upgrade trigger: if a fix commit is seen fixing a
    # nit an earlier incremental round held, hold by sid as well.
    kept = set(held)
    withheld = sorted(f.id for f in findings
                      if f.severity in NIT_SEVERITIES and f.stable_id and f.stable_id in kept)
    if withheld:
        core.log.info(f"Incremental: {len(withheld)} nit/idiom finding(s) left open for a "
                      f"person or the next full review ({', '.join(withheld)})")
    return [f for f in findings if f.id not in withheld]


def interrupted_fix(review_dir: Path, wt_path: str) -> FixInterruption | None:
    """The interruption the review at *review_dir* records, if HEAD is still its head."""
    meta = read_review_meta(review_dir)
    stopped = meta.fix_interrupted
    if stopped is None:
        return None
    live = git.client.head_sha(cwd=wt_path)
    anchor = stopped.head_before or meta.head_sha
    return stopped if live and covers(anchor, live) else None


def _stash_ref(wt_path: str) -> str:
    return git.client.out("rev-parse", "-q", "--verify", "refs/stash", cwd=wt_path)


def _clear_interruption(review_dir: Path, extra_dirty: tuple[str, ...] = ()) -> None:
    meta = read_review_meta(review_dir)
    write_review_meta(review_dir, replace(
        meta, fix_interrupted=None,
        dirty_files=tuple(sorted(set(meta.dirty_files) | set(extra_dirty)))))


def _stash_push_failed(
    review_dir: Path, wt_path: str, stashed: CmdResult, before: str,
    trail: Trail | None, moved: list[str], untouched: list[str],
) -> str:
    after = _stash_ref(wt_path)
    if after and after != before:
        names = ", ".join(untouched)
        core.log.warn(
            f"stash push failed but refs/stash moved to {after} — "
            f"a partial stash of {names} exists; restore with `git stash pop {after}`. "
            f"{stashed.stderr.strip()}")
        _clear_interruption(review_dir)
        tinfo(trail, "fix_leftovers_set_aside", "partial stash; record cleared",
              data={"stashed": untouched, "left": moved, "partial": after})
        return ""
    core.log.warn(f"could not set the interrupted pass's edits aside: "
                  f"{stashed.stderr.strip()}")
    return ""


def set_aside_leftovers(
    review_dir: Path, wt_path: str, stopped: FixInterruption, trail: Trail | None,
) -> str:
    """Stash the stopped pass's own untouched edits and clear its record.

    Returns the stash message, or "" when nothing was stashed. Set aside rather
    than adopted: the fix pass never commits a file that was dirty when it
    started (`fix.scope.agent_changed`), so a resumed pass over these leftovers
    could never land further work in them. A file changed since the pass
    stopped is someone's edit and is left in place. Modified, new and deleted
    files are all stashed alike — a deletion was recorded as "" and still
    matches while the file is absent.

    The stashed paths join `meta.dirty_files`, so the run that follows treats
    them as changed: without `--fix` it reviews them incrementally rather than
    reviewing an unchanged commit in full.
    """
    leftover_paths = sorted(stopped.leftovers)
    ignored = [p for p in leftover_paths
               if git.client.ok("check-ignore", "-q", "--", p, cwd=wt_path)]
    if ignored:
        core.log.warn(f"{len(ignored)} leftover file(s) are now ignored — dropped: "
                      f"{', '.join(ignored)}")
    remaining = {p: blob for p, blob in stopped.leftovers.items() if p not in set(ignored)}
    untouched = sorted(p for p, blob in remaining.items() if blob_of(wt_path, p) == blob)
    moved = sorted(set(remaining) - set(untouched))
    if moved:
        core.log.warn(f"{len(moved)} file(s) the interrupted fix pass edited have changed "
                      f"since — left in place: {', '.join(moved)}")
    message = f"pr review: interrupted fix pass at {git.client.abbrev(stopped.head_before)}"
    if not untouched:
        _clear_interruption(review_dir)
        tinfo(trail, "fix_leftovers_set_aside", f"0 stashed, {len(moved)} left",
              data={"stashed": untouched, "left": moved})
        return ""
    staged = git.client.lines("diff", "--cached", "--name-only", cwd=wt_path)
    foreign = sorted(p for p in staged if p not in set(untouched))
    if foreign:
        core.log.warn("not setting interrupted leftovers aside — staged files "
                      f"outside them: {', '.join(foreign)}")
        return ""
    before = _stash_ref(wt_path)
    stashed = git.client.run("stash", "push", "--include-untracked", "-m", message,
                             "--", *(f":(literal){p}" for p in untouched), cwd=wt_path)
    if not stashed.ok:
        return _stash_push_failed(
            review_dir, wt_path, stashed, before, trail, moved, untouched)
    core.log.warn(f"Set the interrupted fix pass's edits aside ({message}): "
                  f"{', '.join(untouched)} — `git stash pop` restores them")
    _clear_interruption(review_dir, tuple(untouched))
    tinfo(trail, "fix_leftovers_set_aside", f"{len(untouched)} stashed, {len(moved)} left",
          data={"stashed": untouched, "left": moved})
    return message
