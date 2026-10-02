"""Decide whether the host can take one more batch step right now.

Concurrency is not a number chosen up front. A step starts only when free
memory, minus a reserve kept for every other service on the host, covers what
that step has been seen to need, and neither CPU nor memory is under pressure.
Running steps are never paused or killed; admission only gates the next start.
"""

# doc-group: batch

from __future__ import annotations

import json
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
           cfg: BatchConfig) -> Verdict:
    if running >= limit:
        return Verdict(False, f"at the concurrency limit ({limit})")
    if running == 0:
        return Verdict(True, "nothing running; one step is always admitted")
    if not sample.supports_admission:
        # ceiling: static pool where the host exposes no pressure metrics (macOS);
        # upgrade to host-metric admission if batch runs on macOS become routine.
        if running < max(1, cfg.pool_default):
            return Verdict(True, "no host metrics; within batch.pool_default")
        return Verdict(False, f"no host metrics; at batch.pool_default ({cfg.pool_default})")
    spare = sample.mem_available - parse_size(cfg.mem_reserve)
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


class Estimates:
    FILE = "estimates.json"

    def __init__(self, peaks: dict[str, int]):
        self._peaks = peaks

    @classmethod
    def load(cls) -> Estimates:
        try:
            raw = json.loads((batch_root() / cls.FILE).read_text())
            return cls({k: int(v) for k, v in raw.items()})
        except (OSError, ValueError, AttributeError):
            return cls({})

    def get(self, repo: str, step: Step) -> int:
        return self._peaks.get(f"{repo}:{step.value}", DEFAULT_ESTIMATES[step])

    def observe(self, repo: str, step: Step, peak_rss: int) -> None:
        key = f"{repo}:{step.value}"
        if peak_rss <= self._peaks.get(key, 0):
            return
        self._peaks[key] = peak_rss
        batch_root().mkdir(parents=True, exist_ok=True)
        core.serde.write_json(batch_root() / self.FILE, self._peaks)


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
