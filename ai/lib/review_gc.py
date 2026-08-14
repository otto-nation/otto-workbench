"""Garbage collection for review artifacts.

Used by pr gc for explicit cleanup of stale and merged reviews.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import log
import pr_state
import pr_target
import run_lock
from pr_state import ReviewStatus
from review_common import (
    FILENAME_META,
    FILENAME_PIPELINE_STATE,
    REVIEW_EXT,
    REVIEWS_DIR,
    phase_artifacts,
    read_pipeline_status,
    read_review_meta,
)

GC_STALE_DAYS = 7
GC_FAILED_STALE_DAYS = 30
PRUNE_MAX_FILES = 10


def _dir_is_all_stale(d: Path, stale_days: int = GC_STALE_DAYS) -> bool:
    """Return True if every file in *d* is older than *stale_days*."""
    try:
        files = [f for f in d.rglob("*") if f.is_file()]
    except OSError:
        return False
    if not files:
        return True
    now = datetime.now().timestamp()
    return all((now - f.stat().st_mtime) / 86400 > stale_days for f in files)


def _clean_intermediates(review_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale intermediate files from a completed review directory."""
    count = 0
    now = datetime.now().timestamp()
    for f in phase_artifacts(review_dir):
        age_days = (now - f.stat().st_mtime) / 86400
        if age_days > stale_days:
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


def _collect_strays(reviews_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale loose files at the reviews root. Returns the count removed.

    Reviews live in per-review directories, so a loose file is either an unclaimable
    leftover of the flat layout or an agent's scratch file. Staleness is the guard:
    a run still in flight must not have its working files pulled out from under it.
    """
    now = datetime.now().timestamp()
    cleaned = 0
    for f in reviews_dir.iterdir():
        if not f.is_file() or _is_migration_input(f, reviews_dir):
            continue
        if (now - f.stat().st_mtime) / 86400 <= stale_days:
            continue
        f.unlink(missing_ok=True)
        log.info(f"GC: removed stray {f.name}")
        cleaned += 1
    return cleaned


def gc_reviews(reviews_dir: Path | None = None) -> int:
    """Remove orphaned review dirs and stale intermediates. Returns items cleaned."""
    reviews_dir = reviews_dir or REVIEWS_DIR
    if not reviews_dir.is_dir():
        return 0

    cleaned = _collect_strays(reviews_dir)
    for review_dir in reviews_dir.iterdir():
        if not review_dir.is_dir():
            continue

        has_review = (review_dir / f"review{REVIEW_EXT}").is_file()

        if not has_review and _dir_is_all_stale(review_dir):
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"GC: removed orphaned {review_dir.name}")
            cleaned += 1
            continue

        has_pipeline = (review_dir / FILENAME_PIPELINE_STATE).is_file()
        if has_review and not has_pipeline:
            cleaned += _clean_intermediates(review_dir)

    return cleaned


def _has_pipeline_failure(review_dir: Path) -> bool:
    return read_pipeline_status(review_dir) == ReviewStatus.ERROR.value


def prune_merged_reviews(reviews_dir: Path | None = None, max_files: int = PRUNE_MAX_FILES) -> int:
    """Remove review directories for merged/closed PRs. Returns count pruned."""
    reviews_dir = reviews_dir or REVIEWS_DIR
    if not reviews_dir.is_dir():
        return 0

    pruned = 0
    checked = 0

    for meta_file in reviews_dir.glob(f"*/{FILENAME_META}"):
        if checked >= max_files:
            break

        meta = read_review_meta(meta_file.parent)
        if not meta.repo or not meta.pr_number:
            continue

        checked += 1

        review_dir = meta_file.parent
        stale_days = GC_FAILED_STALE_DAYS if _has_pipeline_failure(review_dir) else GC_STALE_DAYS
        if not _dir_is_all_stale(review_dir, stale_days):
            continue

        state = _pr_close_state(meta.repo, meta.pr_number)
        if state:
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"Pruned {meta.repo}#{meta.pr_number} ({state})")
            pruned += 1

    return pruned


def _pr_close_state(repo: str, pr_number: int) -> str:
    """"MERGED", "CLOSED", or "" for an open PR or a question we could not ask.

    Collapsing "open" and "could not ask" is deliberate: both mean keep the
    artifacts. gc that deletes on a network blip is worse than gc that runs
    again tomorrow. Returns the state rather than a bool so callers can name it
    in their log line.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "state", "--jq", ".state"],
            capture_output=True, text=True,
        )
    except Exception:
        return ""
    state = r.stdout.strip()
    return state if state in ("MERGED", "CLOSED") else ""


def _prune_one_target(target: Path) -> bool:
    """Remove a target directory, unless a live run holds it.

    rmtree unlinks run.lock, and a flock only excludes processes that agree on
    one inode — deleting it out from under a running review would let the next
    contender create a fresh file and take an uncontended lock while the first
    is still working. Taking the lock first is how we learn nobody is there.

    Returns whether the directory is actually gone. `ignore_errors=True` keeps
    a permission-denied subdir from aborting the rest of the sweep, but that
    means rmtree can fail partway through, so a caller that trusted a bare
    "did not raise" would log and count a target that is still there — and if
    state.json alone went, the next sweep's glob would never revisit it.
    """
    try:
        with run_lock.acquire(target, command="pr gc", started=""):
            shutil.rmtree(target, ignore_errors=True)
    except run_lock.LockBusy:
        return False
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


def prune_merged_targets(targets_dir: Path | None = None,
                         max_files: int = PRUNE_MAX_FILES,
                         skip: Path | None = None) -> int:
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
        close_state = _pr_close_state(state.identity.repo, state.identity.pr_number)
        if not close_state:
            continue
        pruned += _prune_and_count(
            target, f"Pruned {state.identity.repo}#{state.identity.pr_number} ({close_state})")

    return pruned
