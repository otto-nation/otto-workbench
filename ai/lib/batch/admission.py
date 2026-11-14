"""Decide whether the host can take one more batch step right now.

Concurrency is not a number chosen up front. A step starts only when free
memory, minus a reserve kept for every other service on the host, covers what
that step has been seen to need, and neither CPU nor memory is under pressure.
Running steps are never paused or killed; admission only gates the next start.

Free memory is the smaller of `MemAvailable` and the tightest headroom on the
chain from this process's own cgroup (`/proc/self/cgroup`) up to the hierarchy
root. Each cgroup's limit is the tighter of its `memory.max` and `memory.high`.
A caller may name the chain to read (`read_host(cgroup=)`): the queue worker
reads `workbench.slice`'s, found by `slice_cgroup`. Reading only
`/sys/fs/cgroup/memory.max` saw nothing on a host whose cap sits on a nested
user slice: the root cgroup has no limit file there, so a 6.5 GiB `MemoryMax`
was invisible to admission.

On macOS free memory comes from `vm_stat` (free, inactive and speculative
pages); there is no pressure reading, so batch's `decide` still takes its
`pool_default` path there.

The host is shared by every batch driver on it for the steps batch still
admits itself (non-AI: plain rebase, and CI without `--fix`). AI steps are
queue entries. The caller passes in what the others are running
(`batch.machine`): their pending memory comes off the sample, and their live
non-AI steps count against `batch.pool_default` where there are no host
metrics. Whether a driver may start a step regardless — the floor slot — is
the caller's to establish and pass as `floor`; `decide` no longer infers it
from this run having nothing running.

It is also the queue's admission policy. `QueuePolicy` is what
`otto_queue.slots` asks, so "is there room" has one owner — the `room`
function — whether the asker is a batch step or a queue entry. The queue's class limits are
`class_limit`: heavy entries per platform (`HEAVY_LIMITS`), light ones every
core but one.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import core.cgroup
import core.job_slots
import core.proc
import core.serde
import core.timeouts
from batch.model import Step
from batch.store import batch_root
from config.workbench_config import BatchConfig
from otto_queue.model import EntryClass

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


def _node_free(node: Path) -> int | None:
    """Headroom under one cgroup's tighter limit, `memory.max` or `memory.high`; None where it sets neither.

    `memory.high` is where the kernel starts reclaiming and throttling, so work
    admitted past it runs slowly from its first page. `workbench.slice` sets it
    below its `memory.max`.
    """
    current = _read(node / "memory.current").strip()
    limits = [int(value) for name in ("memory.max", "memory.high")
              if (value := _read(node / name).strip()).isdigit()]
    if not limits or not current.isdigit():
        return None
    return max(0, min(limits) - int(current))


def _vm_stat_available(text: str) -> int | None:
    """Free plus reclaimable bytes from `vm_stat`: free, inactive and speculative pages."""
    size = re.search(r"page size of (\d+) bytes", text)
    pages = [re.search(rf"^Pages {name}:\s+(\d+)\.", text, re.M)
             for name in ("free", "inactive", "speculative")]
    if size is None or any(m is None for m in pages):
        return None
    return int(size.group(1)) * sum(int(m.group(1)) for m in pages)


def _vm_stat() -> str:
    """`vm_stat`'s output, or "" where it cannot run: no memory reading, not an error."""
    try:
        result = core.proc.run(["vm_stat"], timeout=core.timeouts.LOCAL)
    except OSError:
        return ""
    return result.stdout if result.ok else ""


def _cgroup_free(root: Path, cgroup: PurePosixPath | None = None) -> int | None:
    """The tightest headroom from *cgroup* (this process's own when None) up to the hierarchy root.

    The kernel enforces every ancestor's limit, so the binding one is the
    smallest headroom on the chain. That is the nearest limit when the nearest
    is the tightest, and a farther one when it is not.
    """
    base = root / "sys/fs/cgroup"
    parts = (cgroup or core.cgroup.own_cgroup(root)).parts[1:]
    frees = [free for depth in range(len(parts), -1, -1)
             if (free := _node_free(base.joinpath(*parts[:depth]))) is not None]
    return min(frees) if frees else None


def read_host(root: Path = Path("/"), *, cgroup: PurePosixPath | None = None) -> HostSample:
    """Free memory and pressure, for the cgroup chain the work will run in.

    *cgroup* names that chain when it is not this process's own: the queue
    worker admits jobs that run in `workbench.slice`, not in the worker's unit.
    A ticket runs its command in its caller's cgroup, so it reads its own.
    """
    if sys.platform == "darwin" and root == Path("/"):
        # macOS has no /proc and no PSI: free memory from vm_stat, pressure unknown.
        return HostSample(_vm_stat_available(_vm_stat()), None, None)
    frees = [v for v in (_meminfo_available(_read(root / "proc/meminfo")), _cgroup_free(root, cgroup))
             if v is not None]
    return HostSample(
        mem_available=min(frees) if frees else None,
        cpu_some_avg10=_psi_some_avg10(_read(root / "proc/pressure/cpu")),
        mem_some_avg10=_psi_some_avg10(_read(root / "proc/pressure/memory")),
    )


def slice_cgroup(name: str, root: Path = Path("/")) -> PurePosixPath | None:
    """The cgroup of the user manager's slice *name*, from this process's own path; None outside a user manager.

    The path is returned even before the slice directory exists (systemd creates it
    when the first scope starts in it); until then the slice's own limit goes unseen
    and admission falls back to the manager's and root's limits.
    """
    manager = core.cgroup.user_manager_cgroup(root)
    if manager is None:
        return None
    return manager / name


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
    return room(sample, estimate=estimate, cfg=cfg)


def room(sample: HostSample, *, estimate: int, cfg: BatchConfig) -> Verdict:
    """Whether the host has *estimate* bytes beyond `batch.mem_reserve` and no CPU or
    memory pressure at its limit. A metric the sample lacks is not checked.

    The one owner of "is there room", for a batch step (`decide`) and a queue
    entry (`admit_entry`) alike.
    """
    if sample.mem_available is not None:
        try:
            reserve = parse_size(cfg.mem_reserve)
        except ValueError:
            # A malformed batch.mem_reserve must not crash the caller; refuse
            # admission so the bad config shows up as a wait reason instead.
            return Verdict(False, f"batch.mem_reserve is not a size: {cfg.mem_reserve!r}")
        spare = sample.mem_available - reserve
        if spare < estimate:
            return Verdict(False, f"waiting for memory: {max(spare, 0) / GB:.1f} of "
                                  f"{estimate / GB:.1f} GB")
    if sample.cpu_some_avg10 is not None and sample.cpu_some_avg10 >= cfg.cpu_pressure_max:
        return Verdict(False, f"CPU pressure {sample.cpu_some_avg10:g}% "
                              f">= {cfg.cpu_pressure_max:g}%")
    if sample.mem_some_avg10 is not None and sample.mem_some_avg10 >= cfg.mem_pressure_max:
        return Verdict(False, f"memory pressure {sample.mem_some_avg10:g}% "
                              f">= {cfg.mem_pressure_max:g}%")
    return Verdict(True, "host has room")


# Heavy queue entries run at once, by `sys.platform`. agent-maximum (Linux)
# shares 4 cores and a 6.5 GiB user cap with interactive agents; this Mac has
# 18 cores and 48 GB.
# ceiling: per-platform constants rather than config. Upgrade trigger: once a
# machine needs a heavy count its platform's entry does not give it, make this
# a `queue.heavy_max` config key.
HEAVY_LIMITS = {"linux": 2, "darwin": 4}

# Any other platform gets the tighter of the two.
DEFAULT_HEAVY_LIMIT = min(HEAVY_LIMITS.values())

# What one heavy entry is expected to claim: a review, the heaviest step batch
# has an estimate for.
QUEUE_HEAVY_ESTIMATE = DEFAULT_ESTIMATES[Step.REVIEW]


def class_limit(entry_class: EntryClass, *, platform: str, cores: int) -> int:
    """How many *entry_class* queue entries may run at once on this machine.

    Heavy: `HEAVY_LIMITS` for *platform*. Light: `core.job_slots.pool_size`, every
    core but one. Light work (a board read) is short and small, so what bounds it
    is how many processes the cores can serve, not memory. Four concurrent board
    reads are what filled Herdr's 512M cgroup, and a count catches that.
    """
    if entry_class is EntryClass.LIGHT:
        return core.job_slots.pool_size(cores)
    return HEAVY_LIMITS.get(platform, DEFAULT_HEAVY_LIMIT)


def admit_entry(sample: HostSample, *, entry_class: EntryClass, running: int,
                cfg: BatchConfig) -> Verdict:
    """Whether one more queue entry may start, with *running* of its class already running.

    Light entries are admitted on count alone: refusing a board read for memory a
    review is using would stall the board, not save the host. A heavy entry with
    no other heavy one running is always admitted — the floor, so a starved host
    still makes progress one entry at a time, as `decide`'s floor slot does for
    batch. Otherwise `room` decides.
    """
    # ceiling: batch's non-AI steps keep batch's own floor and memory check, so
    # a starved host can still run one plain step beside one heavy queue entry.
    # Upgrade trigger: once plain steps are seen overcommitting a host
    # alongside heavy entries, move them onto light tickets and retire
    # batch.machine.
    if entry_class is EntryClass.LIGHT:
        return Verdict(True, "light work is bounded by count alone")
    if running == 0:
        return Verdict(True, "no other heavy entry is running; one is always admitted")
    return room(sample, estimate=QUEUE_HEAVY_ESTIMATE, cfg=cfg)


@dataclass(frozen=True)
class QueuePolicy:
    """This module as the queue's admission policy (`otto_queue.slots.Policy`)."""

    cfg: BatchConfig
    host: Callable[[], HostSample] = read_host
    platform: str = sys.platform
    cores: int = field(default_factory=lambda: os.cpu_count() or 1)

    def limit(self, entry_class: EntryClass) -> int:
        return class_limit(entry_class, platform=self.platform, cores=self.cores)

    def verdict(self, entry_class: EntryClass, running: int) -> Verdict:
        return admit_entry(self.host(), entry_class=entry_class, running=running,
                           cfg=self.cfg)


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
