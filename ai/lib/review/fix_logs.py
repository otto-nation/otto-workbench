"""Each fix pass's session logs, kept per pass in its review directory.

A fix pass writes `fix.jsonl` and one `fix_verify-<n>.jsonl` per gate chunk.
Those names are fixed, so the next pass overwrote them, and the success sweep
removed them — which is how a gate's "pytest unavailable" could not be traced
to the guard that refused it. A pass now clears the live names before it runs
(the pass before kept its copy) and copies them aside when it ends, however it
ends, into `fix-logs/<started_at>/`. Only the newest `PASSES_KEPT` are kept.

The run's summary reads the copy back, which is how `Usage:` comes to count
the fix pass and its gate: by the time the summary prints, the sweep has
removed the live logs it used to read.
"""

# doc-group: pipeline

from __future__ import annotations

import re
import shutil
from pathlib import Path

from agent.registry import PHASES
from core.phases import Phase

FIX_LOGS_DIRNAME = "fix-logs"

# Enough to compare a pass against the few before it; a review that needs more
# history than this has a problem `otto-log show` is the tool for.
PASSES_KEPT = 5

_STAMP_UNSAFE = re.compile(r"[^0-9A-Za-z]")
_STAMP_RE = re.compile(r"^\d{8}T\d+$")


def _live_logs(review_dir: Path) -> list[Path]:
    names = [PHASES[Phase.FIX].log_filename]
    verify = sorted(review_dir.glob(PHASES[Phase.FIX_VERIFY].log_filename.format("*")))
    return [review_dir / n for n in names if (review_dir / n).is_file()] + verify


def pass_dir(review_dir: Path, started_at: str) -> Path:
    """Where the pass of the run that started at *started_at* keeps its logs."""
    return review_dir / FIX_LOGS_DIRNAME / (_STAMP_UNSAFE.sub("", started_at) or "unstamped")


def clear(review_dir: Path) -> None:
    """Remove the live fix and gate logs, so this pass's copy holds only its own."""
    for log in _live_logs(review_dir):
        log.unlink(missing_ok=True)


def keep(review_dir: Path, started_at: str) -> Path | None:
    """Copy the live fix and gate logs aside for this pass; None when there were none."""
    present = _live_logs(review_dir)
    if not present:
        return None
    target = pass_dir(review_dir, started_at)
    target.mkdir(parents=True, exist_ok=True)
    for log in present:
        shutil.copy2(log, target / log.name)
    # Non-stamp names (empty started_at → "unstamped") sort oldest; stamp
    # names then sort lexicographically, which is chronological for the form
    # pass_dir writes. The pass just written is never a candidate.
    others = [p for p in (review_dir / FIX_LOGS_DIRNAME).iterdir() if p.is_dir() and p != target]
    others.sort(key=lambda p: (bool(_STAMP_RE.fullmatch(p.name)), p.name))
    for stale in others[:max(0, len(others) - (PASSES_KEPT - 1))]:
        shutil.rmtree(stale, ignore_errors=True)
    return target


def logs_for(review_dir: Path, started_at: str) -> list[str]:
    """The kept logs of the pass the run that started at *started_at* made, if any."""
    target = pass_dir(review_dir, started_at)
    return [str(p) for p in sorted(target.glob("*.jsonl"))] if target.is_dir() else []
