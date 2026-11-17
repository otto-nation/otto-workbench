"""Decide whether the host can take one more batch step right now.

Concurrency is not a number chosen up front. A step starts only when free
memory, minus a reserve kept for every other service on the host, covers what
that step has been seen to need, and neither CPU nor memory is under pressure.
Running steps are never paused or killed; admission only gates the next start.

Host reading, `room` and the queue's own policy are `otto_queue.admission`;
this module is what batch adds on top: the pool (`decide`), the step estimates
(`Estimates`) and the process-tree RSS that feeds them (`tree_rss`).

The host is shared by every batch driver on it for the steps batch still
admits itself (non-AI: plain rebase, and CI without `--fix`). AI steps are
queue entries. The caller passes in what the others are running
(`batch.machine`): their pending memory comes off the sample, and their live
non-AI steps count against `batch.pool_default` where there are no host
metrics. Whether a driver may start a step regardless — the floor slot — is
the caller's to establish and pass as `floor`; `decide` no longer infers it
from this run having nothing running.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from pathlib import Path

import core.serde
from batch.model import Step
from batch.store import batch_root
from config.workbench_config import BatchConfig
from otto_queue.admission import GB, QUEUE_HEAVY_ESTIMATE, HostSample, Verdict, read_proc, room

# Seeds until a run has observed real peaks for a (repo, step); the homelab
# measurement (spec A17) replaces them.
DEFAULT_ESTIMATES: dict[Step, int] = {
    Step.REBASE: 512 * 1024 ** 2,
    Step.CI: 1 * GB,
    Step.COMMENTS: 1 * GB,
    Step.REVIEW: QUEUE_HEAVY_ESTIMATE,
}


def ceiling(requested: int | None, cfg: BatchConfig) -> int:
    return max(1, min(requested if requested is not None else cfg.pool_max, cfg.pool_max))


def decide(sample: HostSample, *, running: int, limit: int, estimate: int,
           cfg: BatchConfig, floor: bool = False, elsewhere: int = 0) -> Verdict:
    """Whether one more step may start: *running* is this run's live steps, *limit*
    its pool, *elsewhere* the live steps of every other driver on the machine.

    *floor* is the machine-wide floor slot (`batch.machine`): a step started
    under it is admitted whatever the host says, so a starved host still makes
    progress one step at a time. *sample* should already have other drivers'
    pending memory taken off it.
    """
    if running >= limit:
        return Verdict(False, f"at the concurrency limit ({limit})")
    if floor:
        return Verdict(True, "holding the floor slot; one step is always admitted")
    if not sample.supports_admission:
        # ceiling: static pool where the host exposes no pressure metrics (macOS);
        # upgrade to host-metric admission if batch runs on macOS become routine.
        machine = running + elsewhere
        if machine < max(1, cfg.pool_default):
            return Verdict(True, "no host metrics; within batch.pool_default machine-wide")
        return Verdict(False, f"no host metrics; {machine} step(s) running machine-wide, "
                              f"at batch.pool_default ({cfg.pool_default})")
    return room(sample, estimate=estimate, cfg=cfg)


def _read_peaks(path: Path) -> dict[str, int]:
    try:
        raw = json.loads(path.read_text())
        return {k: int(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


class Estimates:
    """The largest RSS seen per (repo, step), shared by every driver on the machine.

    `observe` writes under an `flock` on `estimates.lock`, re-reading the file
    and keeping the larger of each peak, so two drivers recording at once both
    land — a plain read-modify-write kept whichever wrote last. The write is
    atomic, so a `load` never sees half a file.
    """

    FILE = "estimates.json"
    LOCK = "estimates.lock"

    def __init__(self, peaks: dict[str, int]):
        self._peaks = peaks

    @classmethod
    def load(cls) -> Estimates:
        return cls(_read_peaks(batch_root() / cls.FILE))

    def get(self, repo: str, step: Step) -> int:
        return self._peaks.get(f"{repo}:{step.value}", DEFAULT_ESTIMATES[step])

    def observe(self, repo: str, step: Step, peak_rss: int) -> None:
        key = f"{repo}:{step.value}"
        if peak_rss <= self._peaks.get(key, 0):
            return
        self._peaks[key] = peak_rss
        root = batch_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            fd = os.open(root / self.LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        except OSError:
            return  # advisory estimate: keep it in memory, never stop the run
        try:
            with contextlib.suppress(OSError):
                # No flock on this filesystem: the re-read and max-merge still
                # narrow a concurrent writer's window to the write itself, and
                # an estimate is advisory, so this must not stop the run.
                fcntl.flock(fd, fcntl.LOCK_EX)
            merged = _read_peaks(root / self.FILE)
            for k, v in self._peaks.items():
                merged[k] = max(merged.get(k, 0), v)
            core.serde.write_json(root / self.FILE, merged)
            self._peaks = merged
        finally:
            os.close(fd)


def _ppid(stat: str) -> int | None:
    # `comm` may hold spaces and parens; fields after the last ')' are fixed.
    tail = stat.rsplit(")", 1)[-1].split()
    return int(tail[1]) if len(tail) > 1 and tail[1].isdigit() else None


def tree_rss(pid: int, root: Path = Path("/")) -> int:
    proc = root / "proc"
    children: dict[int, list[int]] = {}
    for d in sorted(proc.glob("[0-9]*")):
        parent = _ppid(read_proc(d / "stat"))
        if parent is not None:
            children.setdefault(parent, []).append(int(d.name))
    total, stack = 0, [pid]
    while stack:
        p = stack.pop()
        m = re.search(r"^VmRSS:\s+(\d+)\s+kB", read_proc(proc / str(p) / "status"), re.M)
        total += int(m.group(1)) * 1024 if m else 0
        stack.extend(children.get(p, []))
    return total
