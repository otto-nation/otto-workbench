"""Decide whether the host can take one more batch step right now.

Concurrency is not a number chosen up front. A step starts only when free
memory, minus a reserve kept for every other service on the host, covers what
that step has been seen to need, and neither CPU nor memory is under pressure.
Running steps are never paused or killed; admission only gates the next start.

The host is shared by every batch driver on it, so the caller passes in what
the others are running (`batch.machine`): their pending memory comes off the
sample, and their live steps count against `batch.pool_default` where there
are no host metrics. Whether a driver may start a step regardless — the floor
slot — is the caller's to establish and pass as `floor`; `decide` no longer
infers it from this run having nothing running.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import core.serde
from batch.model import Step
from batch.store import batch_root
from config.workbench_config import BatchConfig

GB = 1024 ** 3
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": GB, "T": 1024 ** 4}

# Seeds until a run has observed real peaks for a (repo, step); the homelab
# measurement (spec A17) replaces them.
DEFAULT_ESTIMATES: dict[Step, int] = {
    Step.REBASE: 512 * 1024 ** 2,
    Step.CI: 1 * GB,
    Step.COMMENTS: 1 * GB,
    Step.REVIEW: int(1.5 * GB),
}


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)I?B?\s*", text.upper())
    if not m:
        raise ValueError(f"not a size: {text!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2)])


@dataclass(frozen=True)
class HostSample:
    mem_available: int | None
    cpu_some_avg10: float | None
    mem_some_avg10: float | None

    @property
    def supports_admission(self) -> bool:
        return None not in (self.mem_available, self.cpu_some_avg10, self.mem_some_avg10)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _meminfo_available(text: str) -> int | None:
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", text, re.M)
    return int(m.group(1)) * 1024 if m else None


def _psi_some_avg10(text: str) -> float | None:
    m = re.search(r"^some avg10=([0-9.]+)", text, re.M)
    return float(m.group(1)) if m else None


def _cgroup_free(root: Path) -> int | None:
    limit = _read(root / "sys/fs/cgroup/memory.max").strip() or "max"
    current = _read(root / "sys/fs/cgroup/memory.current").strip()
    if limit == "max" or not current.isdigit():
        return None
    return max(0, int(limit) - int(current))


def read_host(root: Path = Path("/")) -> HostSample:
    frees = [v for v in (_meminfo_available(_read(root / "proc/meminfo")), _cgroup_free(root))
             if v is not None]
    return HostSample(
        mem_available=min(frees) if frees else None,
        cpu_some_avg10=_psi_some_avg10(_read(root / "proc/pressure/cpu")),
        mem_some_avg10=_psi_some_avg10(_read(root / "proc/pressure/memory")),
    )


def ceiling(requested: int | None, cfg: BatchConfig) -> int:
    return max(1, min(requested if requested is not None else cfg.pool_max, cfg.pool_max))


@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str


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
    try:
        reserve = parse_size(cfg.mem_reserve)
    except ValueError:
        # A malformed batch.mem_reserve must not crash the scheduler mid-run;
        # refuse admission instead so the next-start gate stays safe and the
        # bad config is visible as a wait reason rather than a traceback.
        return Verdict(False, f"batch.mem_reserve is not a size: {cfg.mem_reserve!r}")
    spare = sample.mem_available - reserve
    if spare < estimate:
        return Verdict(False, f"waiting for memory: {max(spare, 0) / GB:.1f} of "
                              f"{estimate / GB:.1f} GB")
    if sample.cpu_some_avg10 >= cfg.cpu_pressure_max:
        return Verdict(False, f"CPU pressure {sample.cpu_some_avg10:g}% "
                              f">= {cfg.cpu_pressure_max:g}%")
    if sample.mem_some_avg10 >= cfg.mem_pressure_max:
        return Verdict(False, f"memory pressure {sample.mem_some_avg10:g}% "
                              f">= {cfg.mem_pressure_max:g}%")
    return Verdict(True, "host has room")


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
        root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root / self.LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
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
        parent = _ppid(_read(d / "stat"))
        if parent is not None:
            children.setdefault(parent, []).append(int(d.name))
    total, stack = 0, [pid]
    while stack:
        p = stack.pop()
        m = re.search(r"^VmRSS:\s+(\d+)\s+kB", _read(proc / str(p) / "status"), re.M)
        total += int(m.group(1)) * 1024 if m else 0
        stack.extend(children.get(p, []))
    return total
