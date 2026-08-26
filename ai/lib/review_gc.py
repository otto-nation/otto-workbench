"""Removal of review artifacts, at every lifecycle that removes one.

Three sweeps, one module, so that nothing else in the review system deletes a
review's files: the sweep at the end of a successful run (`cleaned_on_success`),
the stale-intermediate sweep and orphan collection `pr gc` runs, and the prune
of reviews whose PR has been merged or closed.

They differ only in what makes a file collectable — the run being over, age, or
the PR being gone — and all of them read what a review directory holds from
`review_common.phase_artifacts` rather than naming files themselves.

`pr gc` collects loose files at the reviews root once they are a week old, prunes
review directories and run-target directories for merged and closed PRs (skipping
its own target), and sweeps the `state.json`, `run.lock`, and `trail.jsonl` the
pre-target layout left behind in a worktree's `.workbench/`. The directory itself
goes only when nothing else is in it. A flat `<name>.md` and its suffixed
siblings are left alone: those are input to the startup migration that folds the
old flat layout into directories.

The scheduled maintenance job (`otto-workbench maintenance start`) runs `pr gc`
each cycle, alongside its sync and stale-worktree cleanup — so this sweep, and
the terminal `pr_outcome` event it fires, no longer depends on someone typing
`pr gc` by hand. The step is skipped on an install without the ai component,
which is what puts `pr` on the path.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import fix_engine
import gh_client
import log
import pr_state
import pr_target
import run_lock
import workbench_paths
from pr_domains import ReviewStatus
from pr_state import PRClosure, PRCloseState
from review_common import (
    FILENAME_PIPELINE_STATE,
    FILENAME_PROMPT_STATS,
    REVIEW_EXT,
    ReviewEntry,
    ReviewEntryKind,
    iter_review_entries,
    phase_artifacts,
)
from review_state import read_pipeline_status
from trail import Trail

GC_STALE_DAYS = 7
GC_FAILED_STALE_DAYS = 30
PRUNE_MAX_FILES = 10


def _age_days(f: Path, now: float) -> float:
    """How long ago *f* was last written, in days, as of the *now* timestamp.

    Every staleness check here reads an mtime, which is seconds, against a
    threshold stated in days; this is the one place the two meet. `now` is the
    caller's because a sweep compares a whole directory against a single
    instant — reading the clock per file would let a slow walk age its own
    later entries past the threshold.
    """
    return (now - f.stat().st_mtime) / timedelta(days=1).total_seconds()


def cleanup_intermediates(review_dir: Path) -> None:
    """Leave a finished review directory holding only its deliverable.

    What to remove is read off the directory rather than named by the caller,
    so a phase the run happened to take — disprove, or one added later — is
    cleaned without the call site listing it.

    This also sweeps fix.jsonl: a `--fix` pass's log is diagnostic, not a
    finding, so it goes the same way as any other phase log rather than
    surviving the run that wrote it. Its tracking file goes with it, and is
    named rather than derived because it is the fix engine's file rather than
    the phase registry's — every domain's pass writes one under that name.

    When this runs is not this function's decision — a review run sweeps
    through `cleaned_on_success`, which is what knows the run is over.
    """
    cleanup = phase_artifacts(review_dir)
    cleanup.append(review_dir / FILENAME_PIPELINE_STATE)
    cleanup.append(review_dir / fix_engine.TRACKING_FILENAME)
    cleanup.extend(
        p for p in review_dir.glob("prompt-*") if p.name != FILENAME_PROMPT_STATS
    )

    for path in cleanup:
        path.unlink(missing_ok=True)


@contextmanager
def cleaned_on_success(review_dir: Path):
    """Every phase of a review run, swept once all of them have succeeded.

    The sweep is scoped rather than tacked onto the last phase because which
    phase is last keeps changing. The pipeline used to clean up as it returned,
    and then the `--fix` pass — which the orchestrator runs after it — wrote
    fix.jsonl into a directory that had already been swept. A phase added
    inside this `with` is cleaned for free; one added after it leaks.

    A run that did not finish keeps everything, because its phase artifacts and
    session logs are the only record of what went wrong. Leaving the scope
    through an exception skips the sweep and does not swallow it — `sys.exit`
    included, which is how a phase reports that it produced no review — and so
    does a pipeline whose state records a failure, since `pr review --recover`
    resumes from exactly those artifacts.

    Failing to tidy up, on the other hand, does not undo a run that worked. The
    sweep is best-effort for that reason — see the comment on its guard.
    """
    yield
    if read_pipeline_status(review_dir) != ReviewStatus.COMPLETED.value:
        return
    # Best-effort: the deliverable is already written, and the orchestrator
    # prints the result JSON its caller parses only after this scope closes. An
    # OSError here — `unlink(missing_ok=True)` still raises on a permissions or
    # read-only-filesystem failure — would otherwise throw away a review that
    # succeeded to report leftover files nobody asked about. The warning is what
    # keeps that from being silent; the leftovers are the next `pr gc`'s work.
    try:
        cleanup_intermediates(review_dir)
    except OSError as exc:
        log.warn(f"could not sweep {review_dir} ({exc}) — leaving its intermediates in place")


def _dir_is_all_stale(d: Path, stale_days: int = GC_STALE_DAYS) -> bool:
    """Return True if every file in *d* is older than *stale_days*."""
    try:
        files = [f for f in d.rglob("*") if f.is_file()]
    except OSError:
        return False
    if not files:
        return True
    now = datetime.now().timestamp()
    return all(_age_days(f, now) > stale_days for f in files)


def _clean_stale_intermediates(review_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale intermediate files from a completed review directory.

    The age filter is what separates this from `cleanup_intermediates`: this
    sweep runs over directories no run owns any more, where a recent file may
    still belong to a review in flight.
    """
    count = 0
    now = datetime.now().timestamp()
    for f in phase_artifacts(review_dir):
        if _age_days(f, now) > stale_days:
            f.unlink(missing_ok=True)
            count += 1
    return count


def _is_migration_input(f: Path, reviews_dir: Path) -> bool:
    """Whether the flat-layout migration would still fold this loose file into a dir.

    The migration keys off a flat `<name>.md` and moves its suffixed siblings along
    with it. A `.md` is always its own input; anything else is only claimable while
    that `.md` is still there. Once it is gone the sibling is stranded, not pending.
    """
    if f.suffix == REVIEW_EXT:
        return True
    stem = f.name.split(".", 1)[0]
    return (reviews_dir / f"{stem}{REVIEW_EXT}").is_file()


def _collect_stray(f: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove one stale loose file at the reviews root. Returns 1 if it went.

    Reviews live in per-review directories, so a loose file is either an unclaimable
    leftover of the flat layout or an agent's scratch file. Staleness is the guard:
    a run still in flight must not have its working files pulled out from under it.
    """
    if _is_migration_input(f, f.parent):
        return 0
    if _age_days(f, datetime.now().timestamp()) <= stale_days:
        return 0
    f.unlink(missing_ok=True)
    log.info(f"GC: removed stray {f.name}")
    return 1


def _gc_one_review_dir(entry: ReviewEntry, stale_days: int = GC_STALE_DAYS) -> int:
    """Collect what one review directory has to spare. Returns items cleaned.

    A directory with no deliverable is only worth its artifacts, so it goes
    whole once every one of them is stale. One that has a deliverable keeps it
    and loses its intermediates, but only once no pipeline state says a run
    might still resume from them.
    """
    if entry.kind is ReviewEntryKind.ORPHAN:
        if not _dir_is_all_stale(entry.path, stale_days):
            return 0
        shutil.rmtree(entry.path, ignore_errors=True)
        log.info(f"GC: removed orphaned {entry.path.name}")
        return 1
    if (entry.path / FILENAME_PIPELINE_STATE).is_file():
        return 0
    return _clean_stale_intermediates(entry.path, stale_days)


def gc_reviews(reviews_dir: Path | None = None) -> int:
    """Remove orphaned review dirs and stale intermediates. Returns items cleaned.

    Strays and directories are collected in the same pass over the shared walk:
    they were two walks with two notions of what an entry at the root is, and
    the classification now belongs to the walk rather than to either sweep.
    """
    reviews_dir = reviews_dir or workbench_paths.reviews_dir()
    if not reviews_dir.is_dir():
        return 0

    cleaned = 0
    for entry in iter_review_entries(reviews_dir):
        if entry.kind is ReviewEntryKind.STRAY:
            cleaned += _collect_stray(entry.path)
            continue
        cleaned += _gc_one_review_dir(entry)

    return cleaned


def _has_pipeline_failure(review_dir: Path) -> bool:
    return read_pipeline_status(review_dir) == ReviewStatus.ERROR.value


def prune_merged_reviews(reviews_dir: Path | None = None, max_files: int = PRUNE_MAX_FILES) -> int:
    """Remove review directories for merged/closed PRs. Returns count pruned."""
    reviews_dir = reviews_dir or workbench_paths.reviews_dir()
    if not reviews_dir.is_dir():
        return 0

    pruned = 0
    checked = 0

    for entry in iter_review_entries(reviews_dir):
        if checked >= max_files:
            break

        # A stray file carries no meta, so this is also what keeps the loose
        # files at the root out of a sweep that only prunes whole directories.
        meta = entry.meta
        if not meta.repo or not meta.pr_number:
            continue

        checked += 1

        review_dir = entry.path
        stale_days = GC_FAILED_STALE_DAYS if _has_pipeline_failure(review_dir) else GC_STALE_DAYS
        if not _dir_is_all_stale(review_dir, stale_days):
            continue

        closure = _pr_closure(meta.repo, meta.pr_number)
        if closure:
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"Pruned {meta.repo}#{meta.pr_number} ({closure.state.value})")
            pruned += 1

    return pruned


def _pr_closure(repo: str, pr_number: int) -> PRClosure | None:
    """How the PR ended, or None — still open, or a question we could not ask.

    Collapsing "open" and "could not ask" into the same absence is deliberate:
    both mean keep the artifacts. gc that deletes on a network blip is worse
    than gc that runs again tomorrow. The collapse is in the return value only —
    every way of failing to ask warns, so an unattended sweep that never prunes
    says why.

    A closure rather than a state alone because the timestamp rides along on a
    call we are making anyway, and it is the only record of when the PR actually
    ended — gc's own clock says when we noticed, which can be a week later.
    Which field carries it belongs to `PRCloseState`, so this reads the one the
    state names instead of choosing between `mergedAt` and `closedAt` itself.
    """
    r = gh_client.run(
        "pr", "view", str(pr_number), "--repo", repo,
        "--json", pr_state.GH_STATE_JSON_FIELDS,
    )
    if not r.ok:
        # The answer stays None — the artifacts are kept either way — but a gh
        # that cannot answer is not a PR that is still open, and the sweep now
        # runs unattended on a schedule. Unlogged, an expired token would read
        # as "nothing was ever ready to prune" for as long as it took anyone to
        # look.
        detail = r.detail or f"exit {r.returncode}"
        log.warn(f"GC: gh could not report {repo}#{pr_number} ({detail}) — leaving it in place")
        return None
    try:
        fields = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        # Surfaced rather than swallowed: "gh said OPEN" and "gh's response was
        # unparseable" both keep the artifacts, and a bare except here would make
        # the second look exactly like the first — a response-format change would
        # then silently stop every future prune from asking a real question.
        log.warn(f"GC: could not parse gh's response for {repo}#{pr_number} — leaving it in place")
        return None
    state = PRCloseState.parse(fields.get("state"))
    if state is None:
        # A state the enum does not carry is the JSONDecodeError case wearing a
        # 0 exit: it parses cleanly and means nothing to us.
        detail = fields.get("state") or "no state field"
        log.warn(f"GC: gh reported an unrecognized state for {repo}#{pr_number} ({detail}) — leaving it in place")
        return None
    if not state.is_terminal:
        return None
    return PRClosure(state, fields.get(state.ended_at_field) or "")


def _remove_target(target: Path) -> None:
    """Empty *target* and remove it, unlinking its lock file last.

    Raises OSError if anything is left — an entry that would not unlink, or a
    run that recreated one in the window below, which surfaces as ENOTEMPTY
    from the non-recursive rmdir.
    """
    lock_path = target / run_lock.LOCK_FILE
    state_path = target / pr_state.STATE_FILE
    # ceiling: a target directory is flat — state.json and run.lock — so an
    # entry that is itself a directory raises OSError here (EISDIR on Linux,
    # EPERM on macOS) and the target is reported as not pruned rather than
    # removed. Upgrade trigger: anything that starts writing a subdirectory
    # under a target. state.json goes after that loop so a target we fail to
    # empty keeps the file the next sweep's glob finds it by.
    for entry in target.iterdir():
        if entry not in (lock_path, state_path):
            entry.unlink()
    state_path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
    target.rmdir()


def _prune_one_target(target: Path) -> bool:
    """Remove a target directory, unless a live run holds it.

    The lock is what tells us no run is *already* in flight. It cannot tell us
    about one that arrives mid-removal: a flock only excludes processes that
    agree on one inode, so once run.lock is unlinked the next contender creates
    a fresh file and takes an uncontended lock on a new inode while we still
    hold the old one. `rmtree` unlinks it partway through its walk and then
    keeps deleting, which is how a target's state.json could be pulled out from
    under a run that had every right to it.

    So the lock file goes last and the directory goes with a non-recursive
    `os.rmdir`, which fails with ENOTEMPTY precisely when a run has recreated
    something in that window. That failure is the answer we want, not an error
    to paper over: we report the target as not pruned and leave it to its new
    owner.

    Returns whether the directory is actually gone, rather than whether the
    removal raised — a caller that logged and counted a target still on disk
    would also lose it from the next sweep, whose glob only finds targets that
    still have a state.json.
    """
    try:
        with run_lock.acquire(target, command="pr gc", started=pr_state.now_iso()):
            _remove_target(target)
    except run_lock.LockBusy:
        return False
    except OSError:
        # Reported, not raised: one target we could not empty must not abort
        # the sweep, and `not target.exists()` below already says what happened.
        log.warn(f"GC: could not remove {target.name} — leaving it in place")
    return not target.exists()


def _prune_and_count(target: Path, message: str) -> int:
    """Prune *target*, logging *message* after a successful prune. Returns 1 if it happened.

    A busy or partially-failed prune returns 0 and logs nothing.

    Factored out of prune_merged_targets's loop body so neither of its two
    call sites nests a second `if` inside the loop's `if`.
    """
    if not _prune_one_target(target):
        return 0
    log.info(message)
    return 1


def _emit_terminal_summary(
    trail: Trail, state: pr_state.PRState, closure: PRClosure,
) -> None:
    """Record a pruned target's outcome. Reported, not raised, on failure.

    Factored out of `prune_merged_targets`'s loop body so the try/except does
    not add a third level of nesting there — the target is already gone by the
    time this runs, so losing this one record must not also cost every
    remaining target its own prune, the same tradeoff `_prune_one_target` makes.
    """
    try:
        trail.summary(
            pr_state.TERMINAL_SUMMARY_ACTION,
            f"{state.identity.repo}#{state.identity.pr_number} "
            f"{closure.state.value.lower()}",
            data=pr_state.terminal_summary(state, closure),
            context={
                "repo": state.identity.repo,
                "pr": state.identity.pr_number,
                "branch": state.identity.branch,
            },
        )
    # OSError is the expected failure (an unwritable trail root). TypeError and
    # ValueError cover a payload json.dumps cannot serialize, which a later
    # change to terminal_summary could introduce: one unrecordable outcome must
    # not abort the sweep for every target behind it.
    except (OSError, TypeError, ValueError) as exc:
        log.warn(
            f"GC: could not record {state.identity.repo}#{state.identity.pr_number}'s "
            f"outcome ({exc}) — continuing the sweep")


def prune_merged_targets(targets_dir: Path | None = None,
                         max_files: int = PRUNE_MAX_FILES,
                         skip: Path | None = None,
                         *,
                         trail: Trail) -> int:
    """Remove target directories for merged/closed PRs. Returns count pruned.

    Replaces the free cleanup a worktree-local state file used to get from
    `wt remove` — target state outlives any single checkout by design, so
    nothing else deletes it.

    `skip` is the caller's own target. It cannot be detected by trying the lock:
    the caller already holds it, so LOCK_ENV would pass us straight through into
    deleting live state.

    `max_files` bounds how many PRs this call asks GitHub about, not how many
    targets it can recover. A corrupt state file is dropped for free, without
    touching the budget, because deleting it costs no network call — the
    budget exists to bound `gh` questions, not local unlinks.

    `trail` is required rather than optional: this sweep is the only code that
    learns a PR has ended, and it is about to delete the state that answers how
    it went. An optional trail is a summary that silently never fires.
    """
    targets_dir = targets_dir or pr_target.targets_root()
    if not targets_dir.is_dir():
        return 0

    pruned = 0
    checked = 0
    for state_file in sorted(targets_dir.glob(f"*/{pr_state.STATE_FILE}")):
        if checked >= max_files:
            break
        target = state_file.parent
        if skip is not None and target == skip:
            continue
        state = pr_state.load_state(target)
        if state is None:
            # The glob found the file, so this is corrupt rather than absent.
            # Nothing here is authoritative, so dropping it is a clean recovery.
            pruned += _prune_and_count(target, f"Pruned unreadable target state at {target.name}")
            continue
        # ceiling: a target for a branch that never opens a PR is never
        # reclaimed, because the only liveness signal this sweep has is the
        # PR's close state. Upgrade trigger: if these accumulate enough to
        # matter, add a second signal (the branch no longer existing on the
        # remote) rather than an age cutoff.
        if not state.identity.pr_number or not state.identity.repo:
            continue
        checked += 1
        closure = _pr_closure(state.identity.repo, state.identity.pr_number)
        if closure is None:
            continue
        removed = _prune_and_count(
            target,
            f"Pruned {state.identity.repo}#{state.identity.pr_number} "
            f"({closure.state.value})")
        pruned += removed
        # After the prune, not before: a target that would not unlink is still
        # live, and its outcome is not history yet.
        if removed:
            _emit_terminal_summary(trail, state, closure)

    return pruned
