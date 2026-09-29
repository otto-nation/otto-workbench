"""Tell an agent run that has stopped apart from one that is merely slow.

`core.timeouts` argues at length that a bound on *duration* cannot work for
work whose cost is whatever the input costs: a breach is indistinguishable
from "the repository is large" or "this hook runs a test suite", and a fixed
number there converts a big repo into a broken tool. That argument is right,
and it is why no wall-clock timeout was put on agent runs when the rest of the
review pipeline was fixed.

What it leaves uncovered is a run that has genuinely stopped. One was observed
silent for 38 minutes at 0.0% CPU, and nothing anywhere ended it: the turn and
budget caps are counted at `turn_end`, which a wedged tool call never reaches,
and Pi's bash tool has no default timeout of its own — its schema says
"Timeout in seconds (optional, no default timeout)", and the model rarely
passes one.

So this bounds liveness rather than duration, which is a different predicate
and not the one `timeouts` rejects. A large tree and a slow hook are both
*working*, and this asks whether anything is.

## Why the event gap alone is not the answer

The obvious version — bound the gap between RPC events — does not work, and
Pi's source is what says so. The bash tool drives its updates entirely from
child output:

    const handleData = (data) => {
        if (!acceptingOutput) return;
        output.append(data); scheduleOutputUpdate();
    };

with one unconditional opening update before the child starts and nothing
periodic behind it. A command that runs for ten minutes printing nothing
therefore emits exactly one update and then silence — the same bytes, on the
same stream, as the hang above. Since the agent's commands run under a pipe
rather than a tty, block buffering makes that the common case and not an
exotic one: a `pytest` whose output has not yet filled its 8KB buffer, a
`go build`, a `git clone` resolving deltas.

A gap is therefore grounds for suspicion and never for a verdict.

## What separates them

Two measurements over the Pi process's descendants, either of which is
evidence of work:

| Case | CPU delta | pid churn | Verdict |
|---|---|---|---|
| Many short-lived children (a build) | 0.12s | yes | live |
| One spinning child (busy-wait) | 2.69s | no | live |
| Blocked, producing nothing (the hang) | 0.00s | no | stall |

They are complementary rather than belt-and-braces. `ps` reports a process's
own CPU time and a reaped child's is unrecoverable on this platform, so a
workload made of many short children reads as idle by CPU alone and is caught
only by churn; a single long-lived spinner never changes the pid set and is
caught only by CPU.

## Three things that would each defeat it

The tree is walked by **PPID, not PGID**. Pi spawns every bash child
`detached`, so each one leads its own process group and a pgid filter reads
zero CPU for a maximally busy child — which would declare every long command a
stall rather than none.

The watch is armed **only during a tool call**. Waiting on the model is itself
a silent zero-CPU stretch, routinely tens of seconds, and a watch left armed
across it aborts healthy runs.

Work that predates the call **does not count**. The descendant set is
snapshotted when the watch arms and only pids outside it are measured, so a
background process left running by an earlier turn cannot mask a stall
indefinitely.

## The spin case is deliberately not caught here

A busy-wait deadlock burns CPU while emitting nothing, so the liveness test
reads it as live. That is the test answering honestly — the process *is*
running — so the answer is a second predicate rather than a fudge to the
first: `ABSOLUTE_CAP` bounds any single tool call regardless of what it is
doing. Keeping it separate is what stops it from weakening the liveness test
into the duration bound `timeouts` rejects.
"""

# doc-group: pipeline

from __future__ import annotations

import threading
import time
from collections.abc import Container, Mapping
from dataclasses import dataclass, field

from core import log, proc, timeouts

# How long a tool call may emit nothing before liveness is even sampled. Only
# arms suspicion, so it is set above the longest silence a healthy call
# plausibly produces rather than at the edge of one: a build between two
# compiler invocations, a test run whose output has not filled its buffer.
GAP_WINDOW = 120.0

# Samples taken once suspicion is armed, and the spacing between them. A
# verdict needs all of them to agree, so a single unlucky window straddling a
# genuine pause cannot end a run — one live interval is enough to clear it.
CONFIRM_SAMPLES = 4
SAMPLE_INTERVAL = 15.0

# CPU seconds across one interval below which a subtree counts as having done
# nothing. `ps` quantises TIME to hundredths, so this is ten ticks — clear of
# the quantisation, and under one percent of one core. Measured idle subtrees
# report 0.00s and measured busy ones report whole seconds, so the threshold
# sits two orders of magnitude from both sides rather than between them.
CPU_FLOOR = 0.10

# The longest any single tool call may run, whatever it is doing. This is the
# duration bound the module docstring declines to use for liveness, kept for
# the one case liveness cannot see: a wedge that spins. Half an hour is far
# above any real call measured here and far below the point at which a person
# would have given up on it.
ABSOLUTE_CAP = 1800.0

# How often the watcher wakes to decide whether anything is worth sampling.
# Cheap: the common path is one clock read against `GAP_WINDOW`.
POLL_INTERVAL = 5.0

_ABORT_STALLED = (
    "Your `{tool}` call was aborted after {seconds:.0f}s with no output and no "
    "CPU used by it or anything it started — nothing was happening. Do not "
    "re-run it unchanged. Write your output file now with what you have, and "
    "record whatever that call was meant to establish as unverified."
)

_ABORT_CAPPED = (
    "Your `{tool}` call was aborted after {seconds:.0f}s, the longest a single "
    "call may run. Write your output file now with what you have, and record "
    "whatever that call was meant to establish as unverified."
)


@dataclass(frozen=True)
class Sample:
    """One reading of a process subtree: who was in it and what each had used.

    CPU is held per pid rather than as a subtree total because the total
    cannot answer the question the baseline exclusion asks. A background
    process left over from an earlier turn contributes to every total, so a
    pair of totals says "something used CPU" for as long as it runs — which is
    exactly the masking the exclusion exists to prevent. Summing a chosen
    subset is only possible per pid.

    Both halves are needed to answer "did anything happen" across a pair of
    these, and for opposite reasons — see the module docstring's table. Held
    together on one type so a caller cannot take a CPU reading from one moment
    and a pid set from another and compare them as though they were one.
    """

    cpu_by_pid: Mapping[int, float]

    @property
    def pids(self) -> frozenset[int]:
        return frozenset(self.cpu_by_pid)

    @property
    def readable(self) -> bool:
        """Whether this sample measured anything at all.

        False means `ps` failed or was killed by its own bound, not that the
        tree is idle: the root process is always in a successful reading, so
        an empty one cannot have come from a live run. The distinction is the
        difference between a stall and a blind watchdog, and treating the two
        alike would let a failure of the instrument end a healthy run — the
        one false positive the design cannot argue its way out of, because it
        is not about the workload at all.
        """
        return bool(self.cpu_by_pid)

    def cpu_over(self, pids: Container[int]) -> float:
        """Total CPU across the members of this sample that are in *pids*."""
        return sum(cpu for pid, cpu in self.cpu_by_pid.items() if pid in pids)


def _parse_time(field_text: str) -> float:
    """Seconds from a `ps` TIME field — `MM:SS.ss`, or `HH:MM:SS.ss` past an hour.

    A field that will not parse reads as zero rather than raising. This runs on
    a watchdog thread whose failure mode should be declining to act, not taking
    down the run it is watching over a format it did not expect.
    """
    parts = field_text.split(":")
    try:
        seconds = float(parts[-1]) + 60.0 * float(parts[-2])
    except (IndexError, ValueError):
        return 0.0
    if len(parts) > 2:
        try:
            seconds += 3600.0 * float(parts[-3])
        except ValueError:
            return 0.0
    return seconds


def _ps_rows() -> list[tuple[int, int, float]]:
    """Every process as `(pid, ppid, cpu_seconds)`, or empty if `ps` failed.

    `ps -A -o pid=,ppid=,time=` is the POSIX spelling and is used rather than
    any per-group selector on purpose. `-g` is the trap: it selects by process
    group on BSD and by *effective group ID* on procps, so the same flag reads
    two different things on the two platforms this has to run on. Selecting
    everything and filtering here costs one exec and cannot be misread.

    A tree walk also needs the global table regardless, since a subtree is only
    computable from every PPID edge.
    """
    result = proc.run(
        ["ps", "-A", "-o", "pid=,ppid=,time="], timeout=timeouts.LOCAL,
    )
    if not result.ok:
        return []
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            rows.append((int(fields[0]), int(fields[1]), _parse_time(fields[2])))
        except ValueError:
            continue
    return rows


def sample_subtree(root_pid: int) -> Sample:
    """What `root_pid` and everything under it have used, right now.

    The root is included: it is the Pi process, and a tool implemented in
    process rather than as a child burns its CPU there. That also makes an
    empty result unambiguous — a live run always has at least its own process
    in the tree, so nothing to report means the reading failed rather than
    that nothing is running. `Sample.readable` is what callers check.
    """
    rows = _ps_rows()
    children: dict[int, list[int]] = {}
    for pid, ppid, _ in rows:
        children.setdefault(ppid, []).append(pid)
    seen = _walk(root_pid, children)
    return Sample(cpu_by_pid={pid: cpu for pid, _, cpu in rows if pid in seen})


def _walk(root_pid: int, children: dict[int, list[int]]) -> set[int]:
    """Every pid reachable from *root_pid* by PPID edges, the root included.

    Iterative rather than recursive: the depth is whatever the agent's tools
    happen to nest to, and a shell pipeline inside a script inside a hook is
    not a bound anything here controls.
    """
    seen = {root_pid}
    stack = [root_pid]
    while stack:
        unseen = [c for c in children.get(stack.pop(), []) if c not in seen]
        seen.update(unseen)
        stack.extend(unseen)
    return seen


def shows_work(
    before: Sample, after: Sample, baseline: frozenset[int], root_pid: int = 0,
) -> bool:
    """Whether anything happened between two samples, ignoring pre-existing work.

    `baseline` is what was already running when the call started. Excluding it
    is what keeps a long-lived background process from answering this question
    forever on behalf of a tool call that has actually stopped.

    The root is the one exception to that exclusion, and it has to be: it is
    the agent process itself, so it is in the baseline by construction, and a
    tool that does its work in process rather than by spawning would otherwise
    have all of its CPU discarded and be read as a stall while running.

    Either signal alone is sufficient, and neither is sufficient alone for
    every workload, which is the whole point of reading both.
    """
    if (before.pids - baseline) != (after.pids - baseline):
        return True
    counted = (after.pids - baseline) | ({root_pid} if root_pid else set())
    burned = after.cpu_over(counted) - before.cpu_over(counted)
    # `ps` reports hundredths, so a comparison against a threshold expressed in
    # the same units lands on a float boundary; the tolerance is below one tick
    # and cannot admit a reading that is genuinely idle.
    return burned > (CPU_FLOOR - 0.001)


@dataclass
class StallWatch:
    """Watches one agent run for a tool call that has stopped doing anything.

    Armed and disarmed by the stream loop as tool calls start and end, and
    otherwise self-contained: the thread reads a timestamp the loop stamps and
    writes nothing the loop reads except `aborted_reason`, which it sets once.

    `generation` is what makes the abort safe. A tool call can finish between
    the watcher deciding to abort and the write landing, and an abort arriving
    then would kill the *next* call instead. The generation the watcher decided
    under is compared against the current one under the same lock that
    serialises the write, so a decision about a call that has since ended is
    dropped rather than delivered late.
    """

    root_pid: int
    send: object
    prefix: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _wake: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _generation: int = 0
    _armed_at: float = 0.0
    _last_event_at: float = 0.0
    _baseline: frozenset[int] = frozenset()
    _tool: str = ""
    aborted_reason: str = ""

    def stamp(self) -> None:
        """Record that the stream produced an event. Called per line."""
        self._last_event_at = time.monotonic()

    def arm(self, tool: str) -> None:
        """Start watching a tool call, and stop counting work that predates it.

        A watch with no root pid never arms. Liveness here is read entirely
        from the process tree, so without a root there is no evidence to reach
        a verdict on — and a detector that cannot see whether work is
        happening must not be the thing that ends a run.
        """
        if not self.root_pid:
            return
        baseline = sample_subtree(self.root_pid).pids
        with self._lock:
            self._generation += 1
            self._armed_at = time.monotonic()
            self._last_event_at = self._armed_at
            self._baseline = baseline
            self._tool = tool or "tool"

    def disarm(self) -> None:
        """Stop watching. Waiting on the model is silent and must not count."""
        with self._lock:
            self._generation += 1
            self._armed_at = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeouts.QUICK)

    def _state(self) -> tuple[int, float, float, frozenset[int], str]:
        with self._lock:
            return (
                self._generation, self._armed_at, self._last_event_at,
                self._baseline, self._tool,
            )

    def _run(self) -> None:
        while not self._wake.wait(POLL_INTERVAL):
            generation, armed_at, last_event_at, baseline, tool = self._state()
            if not armed_at:
                continue
            now = time.monotonic()
            if now - armed_at >= ABSOLUTE_CAP:
                self._abort(generation, _ABORT_CAPPED, tool, now - armed_at)
                continue
            if now - last_event_at < GAP_WINDOW:
                continue
            if self._confirm_stalled(generation, baseline):
                self._abort(
                    generation, _ABORT_STALLED, tool,
                    time.monotonic() - last_event_at,
                )

    def _confirm_stalled(self, generation: int, baseline: frozenset[int]) -> bool:
        """Sample until something shows work, or until nothing has for long enough.

        Returns False the moment any interval shows work, so the cost of being
        wrong about a quiet build is one extra `ps` rather than a dead run.
        """
        previous = sample_subtree(self.root_pid)
        for _ in range(CONFIRM_SAMPLES):
            if self._wake.wait(SAMPLE_INTERVAL):
                return False
            if self._state()[0] != generation:
                return False
            current = sample_subtree(self.root_pid)
            # An unreadable pair is no evidence either way, and a watchdog
            # with no evidence must not be the thing that ends a run. Declining
            # here only costs a confirm window: the gap is still open, so the
            # next poll starts a fresh one.
            if not (previous.readable and current.readable):
                return False
            if shows_work(previous, current, baseline, self.root_pid):
                return False
            previous = current
        return True

    def _abort(self, generation: int, template: str, tool: str, seconds: float) -> None:
        """Abort the run, unless the call it was decided about has already ended.

        Abort rather than steer, because a steer cannot arrive. Pi drains its
        steering queue at three points in the agent loop and every one of them
        is outside tool execution, so a steer sent to a wedged call is
        delivered when the call returns — which for the runs this exists to end
        is never. An abort is dispatched on Pi's event loop and reaches the
        `AbortSignal` the bash tool holds, which kills the child's whole group.

        The run still ends through `agent_end`, so the salvage prompt that asks
        for the deliverable is unaffected and a run killed here keeps whatever
        it had found.
        """
        with self._lock:
            if generation != self._generation:
                return
            self._armed_at = 0.0
            self.aborted_reason = template.format(tool=tool, seconds=seconds)
            reason = self.aborted_reason
        log.warn(f"{self.prefix}stalled: {tool} idle {seconds:.0f}s — aborting")
        self.send({"type": "abort"})
        self.send({"type": "follow_up", "message": reason})
