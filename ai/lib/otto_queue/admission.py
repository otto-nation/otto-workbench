"""The queue's admission policy: host room, class limits, and what a queue entry may start.

`QueuePolicy` is what `otto_queue.slots` asks, so "is there room" has one owner
(`room`). A queue entry is admitted by `admit_entry`: light work on count
alone, heavy work on `room`. The class limits are
`class_limit`: heavy entries per platform (`HEAVY_LIMITS`), light ones every
core but one.

Free memory is the smaller of `MemAvailable` and the tightest headroom on the
chain from this process's own cgroup (`/proc/self/cgroup`) up to the hierarchy
root. Each cgroup's limit is the tighter of its `memory.max` and `memory.high`.
A caller may name the chain to read (`read_host(cgroup=)`): the queue worker
reads `workbench.slice`'s, found by `slice_cgroup`. Reading only
`/sys/fs/cgroup/memory.max` saw nothing on a host whose cap sits on a nested
user slice: the root cgroup has no limit file there, so a 6.5 GiB `MemoryMax`
was invisible to admission.

On macOS free memory comes from `vm_stat` (free, inactive and speculative
pages); there is no pressure reading there.

`core.job_slots` is a different concern and stays apart: it bounds how wide one
already-started run may go (a `pr` command's own parallelism), not whether the
machine may start another unit. Light class limits borrow its `pool_size` only
for the core count.

The budget (`batch.mem_reserve`, `batch.cpu_pressure_max`, `batch.mem_pressure_max`)
is the machine-wide `BatchConfig`, never a repo's.
"""

# doc-group: queue

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import core.cgroup
import core.job_slots
import core.proc
import core.timeouts
from config.workbench_config import BatchConfig
from otto_queue.model import EntryClass

GB = 1024 ** 3


_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": GB, "T": 1024 ** 4}


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


def read_proc(path: Path) -> str:
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
    current = read_proc(node / "memory.current").strip()
    limits = [int(value) for name in ("memory.max", "memory.high")
              if (value := read_proc(node / name).strip()).isdigit()]
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
    frees = [v for v in (_meminfo_available(read_proc(root / "proc/meminfo")), _cgroup_free(root, cgroup))
             if v is not None]
    return HostSample(
        mem_available=min(frees) if frees else None,
        cpu_some_avg10=_psi_some_avg10(read_proc(root / "proc/pressure/cpu")),
        mem_some_avg10=_psi_some_avg10(read_proc(root / "proc/pressure/memory")),
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


@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str


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


# What one heavy entry is expected to claim: a review-sized step.
QUEUE_HEAVY_ESTIMATE = int(1.5 * GB)


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


def admit_entry(sample: HostSample, *, entry_class: EntryClass,
                cfg: BatchConfig, host_room: bool = True) -> Verdict:
    """Whether one more queue entry may start.

    Light entries and in-process work (`host_room=False`) are admitted on count
    alone: refusing a board read for memory a review is using would stall the
    board, not save the host. Heavy work with `host_room=True` still needs
    `room`, including the first entry. Slot counts stay in `otto_queue.slots`.
    """
    if entry_class is EntryClass.LIGHT:
        return Verdict(True, "light work is bounded by count alone")
    if not host_room:
        return Verdict(True, "in-process work is bounded by count alone")
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

    def verdict(self, entry_class: EntryClass, running: int, *,
                host_room: bool = True) -> Verdict:
        del running
        return admit_entry(self.host(), entry_class=entry_class, cfg=self.cfg,
                           host_room=host_room)
