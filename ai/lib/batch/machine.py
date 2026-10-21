"""What every batch driver on this machine is running, so admission sees all of them.

Each driver decides alone whether the host can take one more step
(`batch.admission.decide`), but memory and CPU are the machine's, not the
run's. Counting only its own steps, and always admitting a run's first one,
let N concurrent batches start N steps whatever memory said. This module is
the shared view that closes that.

`<state>/batch/admission/` holds, per live driver:

| File | Held / written | Read as |
|---|---|---|
| `<run>.lock` | `LOCK_EX`, blocking, for the driver's life | live while a non-blocking probe of it fails |
| `<run>.json` | replaced atomically at the end of every admission pass | `pid`, `updated_at`, `waiting_since`, `live[]` |
| `floor.lock` | `LOCK_EX`, by at most one driver at a time | the machine-wide floor slot |

A peer counts only while its lock is held, so a driver killed with SIGKILL
drops out the moment the kernel releases it; its JSON is removed by whichever
peer next finds the lock free. Machine-wide pending memory is the sum, over
live peers, of each running step's estimate less the peak it has reached.

The floor slot replaces the per-run "nothing running, so one step is always
admitted" rule with one slot for the whole machine. A driver with nothing
running that the host refuses may start one step anyway only while it holds
`floor.lock`, and only the longest-waiting driver may try for it: the one with
the smallest `waiting_since` among live peers whose JSON is fresh. It gives
the slot back once its own running count returns to zero and joins the back
of the queue before admitting again, so under sustained pressure every
waiting driver gets a turn within about (N-1) times the longest step. A plain
shared slot without the queue starves: whoever finishes first wins it again.
"Fresh" excludes a driver blocked in an inline publish, which still holds its
lock but is not taking steps.

`PerRun` is the same interface with no peers and the old per-run floor. It is
what a scheduler uses when nothing else is supplied, and what `register`
returns, after logging, on a filesystem where `flock` is unsupported.

ceiling: admissions are not serialized across drivers — a step a peer started
is invisible until that peer's next JSON write, at most one pass later, so two
drivers can each admit against the same headroom in that window. Upgrade
trigger: if hosts are seen overcommitting by one step per concurrent driver,
hold a machine-wide `admit.lock` across read-peers, decide, start, write.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import core.log
import core.serde
from batch.store import batch_root

FLOOR = "floor.lock"

# How old a peer's JSON may be and still hold a place in the floor queue. A
# driver rewrites it every pass (half a second apart); one that has not for
# this long is blocked — an inline publish — and must not hold up the queue.
FRESH_SECONDS = 30.0


def admission_root() -> Path:
    return batch_root() / "admission"


@dataclass(frozen=True)
class LiveStep:
    """One step a driver has running: what it was expected to need and has used so far."""

    item: str
    step: str
    repo: str
    estimate: int
    peak: int

    @property
    def pending(self) -> int:
        """Memory the step is still expected to claim."""
        return max(0, self.estimate - self.peak)


@dataclass(frozen=True)
class DriverState:
    """A driver's `<run>.json`."""

    run: str
    pid: int
    updated_at: float
    waiting_since: float | None = None
    live: list[LiveStep] = field(default_factory=list)


@dataclass(frozen=True)
class Peers:
    """What every other live driver has running."""

    pending: int = 0
    live: int = 0


class PerRun:
    """No peers, and the floor is this run's own: nothing running admits one step."""

    def sync(self, live: list[LiveStep]) -> Peers:
        return Peers()

    def take_floor(self) -> bool:
        return True

    def settle(self, live: list[LiveStep], *, waiting: bool) -> None:
        return None

    def close(self) -> None:
        return None


def _probe_dead(lock: Path) -> int | None:
    """An fd holding *lock* when no driver does, else None. Missing reads as dead."""
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
    except FileNotFoundError:
        fd = -1
    except OSError:
        return None
    if fd < 0:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _load(path: Path) -> DriverState | None:
    try:
        raw = json.loads(path.read_text())
        live = [LiveStep(**entry) for entry in raw.get("live", [])]
        return DriverState(run=raw["run"], pid=int(raw["pid"]),
                           updated_at=float(raw["updated_at"]),
                           waiting_since=raw.get("waiting_since"), live=live)
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Machine:
    """This driver's entry in the machine-wide registry; see the module docstring."""

    def __init__(self, run_id: str, fd: int, root: Path,
                 clock: Callable[[], float] = time.time):
        self.run_id, self._fd, self._root, self._clock = run_id, fd, root, clock
        self._floor_fd: int | None = None
        self._waiting_since: float | None = None
        self._live: list[LiveStep] = []
        self._peers: list[DriverState] = []

    # ── own state ────────────────────────────────────────────────────────

    def _write(self) -> None:
        state = DriverState(self.run_id, os.getpid(), self._clock(), self._waiting_since,
                            list(self._live))
        core.serde.write_json(self._root / f"{self.run_id}.json", asdict(state))

    def _release_floor(self) -> None:
        if self._floor_fd is None:
            return
        os.close(self._floor_fd)
        self._floor_fd = None

    # ── peers ────────────────────────────────────────────────────────────

    def _peer(self, path: Path) -> DriverState | None:
        """The live driver *path* describes, or None — removing it when its driver is gone."""
        fd = _probe_dead(self._root / f"{path.stem}.lock")
        if fd is None:
            return _load(path)
        # Held by us now, so no driver of that run is writing it.
        path.unlink(missing_ok=True)
        if fd >= 0:
            os.close(fd)
        return None

    def _read_peers(self) -> list[DriverState]:
        paths = [p for p in sorted(self._root.glob("*.json")) if p.stem != self.run_id]
        return [state for state in map(self._peer, paths) if state is not None]

    def _queue_head(self) -> bool:
        """Whether this driver has waited longest among live drivers still taking steps."""
        cutoff = self._clock() - FRESH_SECONDS
        mine = (self._waiting_since, self.run_id)
        return all(mine <= (p.waiting_since, p.run) for p in self._peers
                   if p.waiting_since is not None and p.updated_at >= cutoff)

    # ── the admission pass ───────────────────────────────────────────────

    def sync(self, live: list[LiveStep]) -> Peers:
        """Read every live peer, and give the floor back if this driver's floor
        step has finished. `settle` publishes this driver's own state."""
        if self._floor_fd is not None and not live:
            self._release_floor()
            # The back of the queue: the next refusal stamps a new waiting_since.
            self._waiting_since = None
        self._live = list(live)
        self._peers = self._read_peers()
        return Peers(pending=sum(s.pending for p in self._peers for s in p.live),
                     live=sum(len(p.live) for p in self._peers))

    def take_floor(self) -> bool:
        """Whether this driver, refused with nothing running, may start one step anyway."""
        if self._floor_fd is not None:
            return True
        if self._waiting_since is None:
            self._waiting_since = self._clock()
            self._write()
        if not self._queue_head():
            return False
        fd = os.open(self._root / FLOOR, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._floor_fd = fd
        return True

    def settle(self, live: list[LiveStep], *, waiting: bool) -> None:
        """Publish the pass's outcome; a driver no longer waiting leaves the queue."""
        if not waiting:
            self._waiting_since = None
        self._live = list(live)
        self._write()

    def close(self) -> None:
        """Leave the registry. Unlinked while still locked, so no peer reads a half-gone entry."""
        self._release_floor()
        for name in (f"{self.run_id}.json", f"{self.run_id}.lock"):
            (self._root / name).unlink(missing_ok=True)
        os.close(self._fd)


@contextlib.contextmanager
def register(run_id: str, root: Path | None = None,
             clock: Callable[[], float] = time.time) -> Iterator[Machine | PerRun]:
    """This driver's registry entry for the life of the block.

    Blocks on the run's own lock rather than failing: the only other holder is
    a peer's liveness probe, which lets go at once. Where the filesystem has no
    `flock`, yields `PerRun` and says so — admission is then per-run as before.
    """
    root = root or admission_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root / f"{run_id}.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    except OSError as exc:
        core.log.warn(f"batch admission is per-run: {root} is not writable ({exc})")
        yield PerRun()
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(fd)
        core.log.warn(f"batch admission is per-run: flock is unsupported under {root} ({exc})")
        yield PerRun()
        return
    machine = Machine(run_id, fd, root, clock)
    try:
        yield machine
    finally:
        machine.close()
