"""Where a `pr batch` run lives on disk; the only module that touches it.

    <state_dir>/batch/<run-id>/state.json       authoritative run state
    <state_dir>/batch/<run-id>/run.lock         the run's resource lock (holder record carries role)
    <state_dir>/batch/claims/<hex>.json         in-flight refs/pr-batch/<hex> ownership (plan.py)
    <state_dir>/locks/batch.lock                held by whichever `pr batch run`/`resume` is live
    <state_dir>/batch/<run-id>/requests/*.json  the inbox: decisions and cancels waiting to be applied
    <state_dir>/batch/<run-id>/requests/rejected/<name>  a request that failed to apply (<name> ends in .json)
    <state_dir>/batch/<run-id>.reaping          tombstone during reap; run_ids() ignores it
    <state_dir>/batch/<run-id>/logs/<slug>-<pr>-<step>-<n>.log     stderr of each step attempt
    <state_dir>/batch/<run-id>/logs/<slug>-<pr>-publish-<n>.log    output of each publish attempt

`state.json` is written only by a `Session` that loaded it under the run's
resource. The resource has no `WORKBENCH_RUN_LOCK` marker: a child neither
inherits it nor passes through it. Lock order is `Global("batch")`, then the
run resource, then item TARGETs, then CHECKOUT. Every take is non-blocking, so
the order cannot deadlock. One exception: `pr gc`'s dispatcher takes its own
TARGET before `reap` takes run resources.

`reap` renames the run directory to the tombstone while still holding the
flock. `os.rename` moves `run.lock` with the directory; the flock stays on
the same inode, so the hold remains valid across the rename. `run_ids()`
skips names ending in `.reaping` because a tombstone still contains
`state.json`.

Hand-off. A request can land after a holder's last drain and before it
releases. The submitter's take is refused, so it reports `queued`. To
close that window, `session()` and `create()` check the inbox once the
lock is released, after a normal exit only. While requests wait, they open
the run again as an APPLIER whose own entry drain and finish apply them.

- A live holder that drains ends the loop. It held the lock when the request
  was written, so its own post-release check sees the request.
- A GC holder is retried along `core.run_lock.retry_delays`. A vacated
  record that already exhausted that budget inside the take is not: the
  hand-off warns at once. Once the outer delays are spent, the run and its
  pending count are named on stderr.

A submitter reports `queued` only when a live drainer refused it. So a
request reported queued is applied by that holder or a successor, unless the
holder dies. A dead holder leaves the request in the inbox for the next
session's entry drain. Each pass applies or rejects every request it saw, so
the loop ends.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import enum
import json
import os
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import batch.plan
import batch.requests
import core.run_lock
import core.serde
import core.workbench_paths
from batch.model import FINISHED_RUN, CancelState, Run
from batch.requests import Cancel, Decide, Envelope, Malformed, Outcome, Request

STATE_FILE = "state.json"
# The cancel sidecar older `pr batch cancel` wrote. `session()` folds it into
# `run.cancel`; see `_fold_legacy_cancel`.
LEGACY_CANCEL_FILE = "cancel"
REQUESTS_DIR = "requests"
REJECTED_DIR = "rejected"

# The machine-wide lock every `pr batch run` and `resume` holds for its life.
# The lock itself refuses a second batch; there is no separate active-run
# check. Outside run_dir: it guards the choice of run, not one run's files.
BATCH_LOCK = core.run_lock.Global("batch")


class RunNotFound(LookupError):
    pass


class Role(enum.Enum):
    """Why a process holds a run's lock; written into its holder record."""

    # A scheduler: `pr batch run` or `resume`.
    DRIVER = "driver"
    # Draining requests with no scheduler: resolve, cancel, the hand-off.
    APPLIER = "applier"
    # `pr gc` deciding whether to remove a finished run.
    GC = "gc"


_DOING: dict[Role | None, str] = {
    Role.DRIVER: "driving it",
    Role.APPLIER: "applying a decision",
    Role.GC: "removing it",
    None: "holding it",
}


@dataclass(frozen=True)
class Holder:
    """A live process holding a run's lock, as its holder record names it.

    `role` is None for a record with no role this code knows, which is what
    a holder written before roles existed leaves. Such a holder is treated
    as a driver, since that is what a held run lock meant then.
    """

    role: Role | None
    pid: int
    command: str
    started: str

    @property
    def drains(self) -> bool:
        """Whether this holder applies what is in the inbox before it releases."""
        return self.role in (Role.DRIVER, Role.APPLIER, None)

    def describe(self) -> str:
        """What the holder is doing to the run, as a refusal names it."""
        return _DOING[self.role]


class RunBusy(RuntimeError):
    """A live process holds the run's lock; `holder` is who, when its record says."""

    def __init__(self, run_id: str, holder: Holder | None):
        self.run_id = run_id
        self.holder = holder
        detail = ""
        if holder is not None:
            detail = (f": {holder.describe()} ({holder.command}, pid {holder.pid}, "
                      f"started {holder.started})")
        super().__init__(f"run {run_id} is busy{detail}")


class RunUnreachable(RuntimeError):
    """The run's lock stayed held past the retry budget while its record named no live holder."""

    def __init__(self, run_id: str, reason: str = ""):
        self.run_id = run_id
        detail = reason or (
            "its lock is held but its holder record says released or names a dead "
            "process, and it did not free within the retry budget")
        super().__init__(f"run {run_id} is unreachable: {detail}")


class InboxPolicy(Protocol):
    """What a session drains with: how to apply one request, settle an idle run, report."""

    def apply(self, run: Run, envelope: Envelope) -> Outcome: ...

    def settle(self, run: Run) -> bool: ...

    def report(self, run: Run, outcome: Outcome) -> None: ...


@dataclass(frozen=True)
class FailedRequest:
    """A request a drain rejected, and why."""

    name: str
    error: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def batch_root() -> Path:
    return core.workbench_paths.state_dir() / "batch"


def new_run_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def run_dir(run_id: str) -> Path:
    return batch_root() / run_id


def logs_dir(run_id: str) -> Path:
    path = run_dir(run_id) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def attempt_log_path(run_id: str, repo: str, pr: int, step: str) -> Path:
    """The log for the next attempt at *step* on PR *pr* of *repo*: `<slug>-<pr>-<step>-<n>.log`.

    *step* is a Step value or "publish". `n` counts the attempts already logged,
    so a caller must create the file before the next call sees it.
    """
    slug = repo.replace("/", "__")
    logs = logs_dir(run_id)
    attempt = sum(1 for _ in logs.glob(f"{slug}-{pr}-{step}-*"))
    return logs / f"{slug}-{pr}-{step}-{attempt}.log"


def load(run_id: str) -> Run:
    path = run_dir(run_id) / STATE_FILE
    if not path.is_file():
        raise RunNotFound(run_id)
    return core.serde.from_dict(Run, json.loads(path.read_text()))


def run_ids() -> list[str]:
    """Every saved run, oldest first; a directory with no state file is not a run.

    Tombstones (``<id>.reaping``) are ignored even though they contain
    ``state.json``: they are a run directory mid-deletion, not a run.
    """
    root = batch_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if (p / STATE_FILE).is_file() and not p.name.endswith(".reaping")
    )


def latest_run_id() -> str | None:
    """The newest saved run on this machine, finished included."""
    ids = run_ids()
    return ids[-1] if ids else None


def _try_load(run_id: str) -> Run | None:
    """`load`, or None for a run that is unreadable or reaped since `run_ids()` listed it."""
    try:
        return load(run_id)
    except (RunNotFound, ValueError, KeyError, TypeError, OSError):
        return None


def _covering_runs(repo: str) -> list[tuple[str, Run]]:
    """Readable runs with an item whose repo matches *repo*, case-folded, oldest first."""
    wanted = repo.casefold()
    runs = ((run_id, _try_load(run_id)) for run_id in run_ids())
    return [(run_id, run) for run_id, run in runs
            if run and any(item.repo.casefold() == wanted for item in run.items)]


def ids_covering(repo: str) -> list[str]:
    """Run ids with an item whose repo matches *repo*, case-folded, oldest first.

    A run whose state cannot be read is skipped, as it is by `unfinished_covering`.
    """
    return [run_id for run_id, _ in _covering_runs(repo)]


def unfinished_covering(repo: str) -> list[str]:
    """`ids_covering(repo)` narrowed to runs not yet finished, oldest first."""
    return [run_id for run_id, run in _covering_runs(repo) if run.status not in FINISHED_RUN]


def default_run_id(repo: str | None) -> str | None:
    """The run a no-id `pr batch` read should show.

    Newest unfinished run covering *repo*; else the newest covering *repo*;
    else `latest_run_id()`. *repo* None, empty, or unmatched is the last arm.
    """
    if not repo:
        return latest_run_id()
    covering = _covering_runs(repo)
    unfinished = [i for i, run in covering if run.status not in FINISHED_RUN]
    if unfinished:
        return unfinished[-1]
    return covering[-1][0] if covering else latest_run_id()


def state_mtime(run_id: str) -> float:
    """When *run_id*'s state was last written, as a timestamp of the last real change.

    A no-op `Session.commit` does not rewrite the file, so this stays the last
    real change — for a finished run, its terminal transition — which `batch.gc`
    measures retention from.
    """
    return (run_dir(run_id) / STATE_FILE).stat().st_mtime


def _requests_dir(run_id: str) -> Path:
    return run_dir(run_id) / REQUESTS_DIR


def write_request(run_id: str, request: Decide | Cancel) -> str:
    """Queue *request* in *run_id*'s inbox and return the file name it was written under.

    Names sort by write time, which is the order a drain applies them in, after
    every cancel. Raises RunNotFound when the run directory is gone, including
    when it is removed between the inbox `mkdir` and the write: neither step
    creates a missing parent, so a removed run is never brought back as an
    orphan `<run-id>/requests/`. `reap`'s tombstone check relies on this.
    """
    directory = run_dir(run_id)
    if not directory.is_dir():
        raise RunNotFound(run_id)
    reqs = directory / REQUESTS_DIR
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    path = reqs / f"{stamp}-{secrets.token_hex(2)}.json"
    try:
        reqs.mkdir(exist_ok=True)
        core.serde.write_json(path, batch.requests.to_dict(request), create_parents=False)
    except FileNotFoundError as exc:
        raise RunNotFound(run_id) from exc
    return path.name


def _read_request(path: Path) -> Request:
    """The request in *path*; an unreadable file or a non-object is Malformed."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return Malformed(f"malformed request {path.name}: {exc}")
    if not isinstance(raw, dict):
        return Malformed(f"malformed request {path.name}: not an object")
    return batch.requests.parse(raw)


def peek_requests(run_id: str) -> list[Envelope]:
    """Every queued request of *run_id*, by file name, read without being taken.

    A file that cannot be read or parsed comes back Malformed instead of
    raising, so one bad file never stops the requests behind it. A
    half-written request is never seen: `write_json`'s temp files do not
    match `*.json`.
    """
    reqs = _requests_dir(run_id)
    if not reqs.is_dir():
        return []
    return [Envelope(path.name, _read_request(path)) for path in sorted(reqs.glob("*.json"))]


def pending_requests(run_id: str) -> int:
    """How many requests wait in *run_id*'s inbox."""
    reqs = _requests_dir(run_id)
    return len(list(reqs.glob("*.json"))) if reqs.is_dir() else 0


def _rejection_error(path: Path) -> str:
    """The error a rejected request was recorded with; "malformed" when none was."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return "malformed"
    # Older code moved the raw request here with no error beside it.
    error = data.get("error") if isinstance(data, dict) and "request" in data else None
    return error if isinstance(error, str) and error else "malformed"


def failed_requests(run_id: str) -> list[FailedRequest]:
    """Requests a drain rejected, by file name, each with the error it was rejected for.

    A file older code left in `rejected/` holds only the raw request and
    reads as "malformed".
    """
    rejected = _requests_dir(run_id) / REJECTED_DIR
    if not rejected.is_dir():
        return []
    return [FailedRequest(path.name, _rejection_error(path))
            for path in sorted(rejected.glob("*.json"))]


def _raw_body(path: Path) -> object:
    """*path*'s JSON as written, or None when it cannot be read as JSON."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _retire(run_id: str, name: str, outcome: Outcome) -> None:
    """Take request *name* out of the inbox once its outcome is committed.

    A request that applied is unlinked. One that failed is moved to
    `requests/rejected/<name>` as `{"request": <raw body or null>, "error": …}`,
    where `failed_requests` and its submitter can find it.
    """
    path = _requests_dir(run_id) / name
    if outcome.ok:
        path.unlink(missing_ok=True)
        return
    record = {"request": _raw_body(path), "error": outcome.error}
    core.serde.write_json(path.parent / REJECTED_DIR / name, record)
    path.unlink(missing_ok=True)


def run_lock(run_id: str) -> core.run_lock.Resource:
    """*run_id*'s own lock, `run_dir/run.lock`, held by whichever session has the run open."""
    return core.run_lock.Resource(run_dir(run_id) / core.run_lock.LOCK_FILE,
                                  f"batch run {run_id}")


def _role(value: object) -> Role | None:
    """The Role a holder record names, or None for a missing or unknown one."""
    try:
        return Role(value)
    except ValueError:
        return None


def _holder_from(record: dict) -> Holder | None:
    """The Holder a lock record describes, or None when it names no pid."""
    pid = record.get("pid")
    if not isinstance(pid, int):
        return None
    return Holder(role=_role(record.get("role")), pid=pid,
                  command=str(record.get("command", "unknown command")),
                  started=str(record.get("started", "unknown time")))


def holder(run_id: str) -> Holder | None:
    """Who holds *run_id*'s lock, read from its record; None when nobody live does.

    Never takes the lock, so a status poll cannot refuse a real take. The cost
    is a moment after a new holder's flock and before its record write, when
    the run reads as unheld. That is acceptable for a status view; every
    decision that matters goes through a take.
    """
    # ceiling: liveness is `os.kill(pid, 0)`, so a SIGKILLed holder whose pid
    # the kernel reuses reads as a ghost holder. Upgrade to recording and
    # comparing the process start time if a ghost holder is ever observed.
    record = core.run_lock.read_holder(run_lock(run_id).path)
    if not core.run_lock.is_live(record, allow_self=True):
        return None
    return _holder_from(record)


def _refusal(run_id: str, exc: core.run_lock.LockBusy) -> RuntimeError:
    """RunBusy for a live holder; RunUnreachable once core gave up on a vacated one."""
    if core.run_lock.classify_busy(exc) is core.run_lock.Busy.LIVE:
        return RunBusy(run_id, _holder_from(exc.holder))
    return RunUnreachable(run_id)


def _enter_lock(stack: contextlib.ExitStack, run_id: str, role: Role, *,
                command: str, create: bool) -> None:
    """Take *run_id*'s lock as *role* onto *stack*, or raise what the refusal means.

    A missing run directory is RunNotFound: without *create* the take never
    makes it, so a late taker cannot resurrect a removed run.
    """
    held = core.run_lock.hold_resource(run_lock(run_id), command=command, started=now_iso(),
                                       fields={"role": role.value, "run": run_id},
                                       create=create)
    try:
        stack.enter_context(held)
    except core.run_lock.LockBusy as exc:
        raise _refusal(run_id, exc) from exc
    except FileNotFoundError as exc:
        raise RunNotFound(run_id) from exc


class Session:
    """One open run: its state, loaded under its lock, and the only route back to disk.

    Built only by `session()` and `create()`. `run` is the object loaded after
    the lock was taken, and `commit()` writes that object and nothing else, so
    a `load()` snapshot has no way back to disk.
    """

    def __init__(self, run: Run, role: Role, inbox: InboxPolicy, *,
                 snapshot: dict | None = None) -> None:
        self._run = run
        self._role = role
        self._inbox = inbox
        # Whether finish() has run, so a normal exit does not finish twice.
        self._finished = False
        # Whether anything was ever written, so create() knows to clean up.
        self._committed = False
        # Last serialized run loaded or written; None until the first write.
        self._snapshot = snapshot

    @property
    def run(self) -> Run:
        """The run as loaded under the lock, with every change made since."""
        return self._run

    @property
    def role(self) -> Role:
        """The role this session holds the run's lock as."""
        return self._role

    def drain(self, *, settle: bool) -> list[Outcome]:
        """Apply every queued request, cancels first, committing after each one.

        A request's file is unlinked, or moved to `requests/rejected/` with its
        error, only after the commit that records its effect. A crash in
        between therefore replays the request instead of losing it. With
        *settle*, idle settlement runs last and is committed when it changes
        the status. Each outcome goes to the inbox's `report`, the only
        emitter, and is also returned, for tests and for `deliver`'s own/others
        split. Callers must not emit from the returned list.
        """
        envelopes = batch.requests.drain_order(peek_requests(self._run.id))
        outcomes = [self._drain_one(envelope) for envelope in envelopes]
        if settle and self._inbox.settle(self._run):
            self.commit()
        return outcomes

    def _drain_one(self, envelope: Envelope) -> Outcome:
        request = envelope.request
        if isinstance(request, Malformed):
            outcome = Outcome(name=envelope.name, request=request, error=request.reason)
        else:
            outcome = self._inbox.apply(self._run, envelope)
        self.commit()
        _retire(self._run.id, envelope.name, outcome)
        self._inbox.report(self._run, outcome)
        return outcome

    def apply(self, request: Decide | Cancel) -> Outcome:
        """Apply a request that never sat in the inbox, and report it as a drained one is.

        For a holder that synthesizes requests (the scheduler's auto-publish
        and stacked retry). Nothing is committed: the holder's next `commit()`
        persists the effect, as it does for any other change to `run`.
        """
        outcome = self._inbox.apply(self._run, Envelope("", request))
        self._inbox.report(self._run, outcome)
        return outcome

    def commit(self) -> None:
        """Write `run` to `state.json` when it differs from the last load or write.

        Refused unless this process holds the run's lock. Raises RuntimeError
        when the lock is not held, for example after the session closed. A
        commit whose serialized run equals the last snapshot skips the file
        write so `state_mtime` stays the last real change. A commit that saves
        DONE or CANCELLED then drops the plan's refs, even when the write is
        skipped. Saving first means a failed cleanup never leaves the stored
        run short of its terminal status. The drop is idempotent and is retried
        on every terminal commit.
        """
        lock_file = run_lock(self._run.id).path
        if not core.run_lock.holds(lock_file):
            raise RuntimeError(f"batch run {self._run.id}: commit without holding its run lock; "
                               f"open the run with batch.store.session()")
        payload = core.serde.to_dict(self._run)
        if payload != self._snapshot:
            core.serde.write_json(run_dir(self._run.id) / STATE_FILE, payload)
            self._snapshot = payload
            self._committed = True
        if self._run.status in FINISHED_RUN:
            batch.plan.drop_refs(self._run.ref_dirs, self._run.ref_namespace)

    def finish(self) -> None:
        """Drain with settlement, then commit.

        A normal exit calls this unless the holder already did. A driver calls
        it itself, inside its item hold, so its final publishes stay narrowed.
        """
        self.drain(settle=True)
        self.commit()
        self._finished = True


def _upgraded_cancel(current: CancelState | None, kill: bool) -> CancelState:
    """*current* with *kill* folded in: kill only goes False to True, the first time is kept."""
    if current is None:
        return CancelState(kill=kill, requested_at=now_iso())
    return CancelState(kill=current.kill or kill, requested_at=current.requested_at)


def _legacy_kill(path: Path) -> bool:
    """The `kill` flag a legacy cancel sidecar asked for; False when it cannot be read."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("kill"))


# ceiling: read-compat for the `cancel` sidecar older `pr batch cancel` wrote.
# Delete LEGACY_CANCEL_FILE and this fold once no run directory written before
# run sessions can still be resumed.
def _fold_legacy_cancel(opened: Session) -> None:
    """Record a legacy cancel sidecar in `run.cancel` and commit it, then delete the file.

    A finished run keeps its status and only loses the file.
    """
    path = run_dir(opened.run.id) / LEGACY_CANCEL_FILE
    if not path.is_file():
        return
    if opened.run.status not in FINISHED_RUN:
        opened.run.cancel = _upgraded_cancel(opened.run.cancel, _legacy_kill(path))
        opened.commit()
    path.unlink(missing_ok=True)


@contextlib.contextmanager
def _open(run_id: str, role: Role, *, inbox: InboxPolicy, command: str) -> Iterator[Session]:
    """`session()` without the hand-off: lock, load, fold, drain, yield, finish, release.

    The hand-off opens its own passes through this, so a pass never starts a
    hand-off of its own and `_hand_off` stays one loop, never a recursion.
    """
    if not (run_dir(run_id) / STATE_FILE).is_file():
        raise RunNotFound(run_id)
    with contextlib.ExitStack() as stack:
        _enter_lock(stack, run_id, role, command=command, create=False)
        loaded = load(run_id)
        opened = Session(loaded, role, inbox, snapshot=core.serde.to_dict(loaded))
        _fold_legacy_cancel(opened)
        opened.drain(settle=True)
        yield opened
        if not opened._finished:
            opened.finish()


class _Pass(enum.Enum):
    """What one hand-off attempt to open the run found."""

    # It took the lock; its entry drain and finish applied or rejected every
    # request they saw.
    APPLIED = "applied"
    # A live holder that drains has the run. It held the lock when the request
    # was written, so its own post-release check carries the duty on.
    CARRIED = "carried"
    # A GC holder, a RunBusy whose holder is None, or a run directory renamed
    # away mid-reap: worth another try after a pause.
    BLOCKED = "blocked"
    # Core already spent the vacated retry budget inside `_enter_lock`; another
    # pause would spend it again.
    EXHAUSTED = "exhausted"


def _hand_off_pass(run_id: str, inbox: InboxPolicy, command: str) -> _Pass:
    """Open *run_id* once as an APPLIER that does not itself hand off, and say what it found.

    `RunBusy` with `holder is None` counts as blocked: no record shows it drains.
    """
    try:
        with _open(run_id, Role.APPLIER, inbox=inbox, command=f"{command} (hand-off)"):
            pass
    except RunNotFound:
        return _Pass.BLOCKED
    except RunUnreachable:
        return _Pass.EXHAUSTED
    except RunBusy as exc:
        drains = exc.holder is not None and exc.holder.drains
        return _Pass.CARRIED if drains else _Pass.BLOCKED
    return _Pass.APPLIED


def _warn_stranded(run_id: str) -> None:
    """Name on stderr a run whose queued requests the hand-off could not apply."""
    count = pending_requests(run_id)
    print(f"pr batch: run {run_id} still has {count} queued request(s) nobody could apply; "
          f"`pr batch resume {run_id}` or another resolve will apply them", file=sys.stderr)


def _hand_off(run_id: str, inbox: InboxPolicy, command: str) -> None:
    """Apply what reached *run_id*'s inbox after its holder's last drain. Runs after release.

    While requests wait, it opens the run as an APPLIER whose entry drain and
    finish apply them. It stops on any of these:
    - the inbox is empty;
    - a live draining holder refuses it, and that holder carries the duty;
    - `RunUnreachable`: core already spent the vacated budget inside
      `_enter_lock`, so this loop does not pause again;
    - the pauses in `core.run_lock.retry_delays()` run out against GC holders
      (`RunBusy` whose holder does not drain, including holder None) or
      `RunNotFound`. Exhaustion names the run and its pending count on stderr.

    It always ends. Every pass that gets in applies or rejects every request
    it peeked, so the inbox shrinks unless new requests keep arriving, and
    each of those is real work. A run directory that is truly gone takes its
    inbox with it, which ends the loop.

    An error from inside a pass is not caught. It propagates from the exit
    of the block that ran the hand-off, after that block's own work was
    committed.
    """
    # ceiling: one retry budget covers the whole hand-off, not each blocked
    # stretch between successful passes. RunUnreachable already spent core's
    # vacated budget inside `_enter_lock`, so it is not retried here.
    # Upgrade to a fresh budget after each APPLIED pass if a hand-off that
    # applied requests is ever reported stranding later ones.
    delays = core.run_lock.retry_delays()
    while pending_requests(run_id) > 0:
        found = _hand_off_pass(run_id, inbox, command)
        if found is _Pass.CARRIED:
            return
        if found is _Pass.APPLIED:
            continue
        if found is _Pass.EXHAUSTED:
            _warn_stranded(run_id)
            return
        pause = next(delays, None)
        if pause is None:
            _warn_stranded(run_id)
            return
        time.sleep(pause)


@contextlib.contextmanager
def session(run_id: str, role: Role, *, inbox: InboxPolicy, command: str) -> Iterator[Session]:
    """Open *run_id* as *role*: take its lock, load, fold the legacy sidecar, drain, yield.

    Raises:
    - RunNotFound when the run has no saved state.
    - RunBusy when a live process holds its lock.
    - RunUnreachable when a released or dead holder record kept it locked past
      core.run_lock's retry budget.
    - core.run_lock.LockReentry when this process already holds the run: two
      sessions on one run in one process is a programming error, not contention.

    The entry drain settles, so a pending cancel is CANCELLED before the block
    sees the run. A normal exit calls `finish()` unless the block already did,
    releases, then hands off. The hand-off applies whatever a submitter queued
    after the last drain, whose own take this session refused. An exceptional
    exit only releases, so nothing half-applied is committed, nothing is handed
    off, and anything unapplied stays in the inbox for the next session.
    """
    with _open(run_id, role, inbox=inbox, command=command) as opened:
        yield opened
    _hand_off(run_id, inbox, command)


def _discard_uncommitted(opened: Session) -> None:
    """Remove a created run's directory if nothing was ever committed to it."""
    if not opened._committed:
        shutil.rmtree(run_dir(opened.run.id))


@contextlib.contextmanager
def create(run: Run, *, inbox: InboxPolicy, command: str) -> Iterator[Session]:
    """Make *run*'s directory and hold its lock as DRIVER. Nothing is saved until the caller commits.

    Raises FileExistsError when the run id is already on disk. If the block
    ends, normally or not, with no commit made, the directory (which holds
    only its lock file) is removed while the lock is still held. A refused or
    failed start therefore leaves no orphan run. After a commit, a normal exit
    calls `finish()`, releases, then hands off as `session()` does. A new run
    has no inbox, so there is no entry drain.
    """
    directory = run_dir(run.id)
    directory.mkdir(parents=True, exist_ok=False)
    with contextlib.ExitStack() as stack:
        # ceiling-permanent: a refused take here leaves the new directory behind.
        # The id is freshly minted and mkdir refused an existing one, so only a
        # process holding this same new id could refuse it; removing the directory
        # would then delete that holder's run, which is worse than an empty leftover.
        _enter_lock(stack, run.id, Role.DRIVER, command=command, create=True)
        opened = Session(run, Role.DRIVER, inbox)
        try:
            yield opened
        except BaseException:
            _discard_uncommitted(opened)
            raise
        if not opened._committed:
            shutil.rmtree(directory)
            return
        if not opened._finished:
            opened.finish()
    _hand_off(run.id, inbox, command)


class ReapResult(enum.Enum):
    """What ``reap`` did with a run directory."""

    REMOVED = "removed"
    BUSY = "busy"
    PENDING = "pending"
    FRESH = "fresh"
    REFS_KEPT = "refs_kept"
    MISSING = "missing"


def reap(run_id: str, *, expired: Callable[[Run], bool], command: str) -> ReapResult:
    """Take the run as ``Role.GC`` and delete it when *expired* says so.

    Does not drain and does not commit: a maintenance sweep must never push.
    ``LockBusy`` of any classification is ``BUSY`` — gc does not wait. Missing
    run dir or state is ``MISSING``. A request that lands in the tombstone
    window, or refs that would not delete, restore the original path and keep
    the run. ``OSError`` from ``os.rename`` / ``shutil.rmtree`` (after the
    tombstone is moved back when it still exists) propagates for gc to log.
    A ``write_request`` racing the rename cannot recreate the run directory
    (it writes with ``create_parents=False``), so the tombstone check sees
    every request that will ever belong to this run.
    """
    lock = run_lock(run_id)
    try:
        with core.run_lock.hold_resource(
            lock, command=command, started=now_iso(),
            fields={"role": Role.GC.value, "run": run_id}, create=False,
        ):
            return _reap_held(run_id, expired=expired)
    except core.run_lock.LockBusy:
        return ReapResult.BUSY
    except FileNotFoundError:
        return ReapResult.MISSING


def _reap_held(run_id: str, *, expired: Callable[[Run], bool]) -> ReapResult:
    """Body of ``reap`` once the resource is held. Split so ``reap``'s take stays flat."""
    try:
        run = load(run_id)
    except RunNotFound:
        return ReapResult.MISSING
    if pending_requests(run_id) > 0:
        return ReapResult.PENDING
    if not expired(run):
        return ReapResult.FRESH
    live = run_dir(run_id)
    tombstone = batch_root() / f"{run_id}.reaping"
    # os.rename, not os.replace: this moves a directory, and core.serde owns the
    # only write-temp-then-replace for files.
    os.rename(live, tombstone)
    if _tombstone_has_requests(tombstone):
        os.rename(tombstone, live)
        return ReapResult.PENDING
    if not batch.plan.drop_refs(run.ref_dirs, run.ref_namespace):
        os.rename(tombstone, live)
        return ReapResult.REFS_KEPT
    try:
        shutil.rmtree(tombstone)
    except OSError:
        if tombstone.exists():
            os.rename(tombstone, live)
        raise
    return ReapResult.REMOVED


def _tombstone_has_requests(tombstone: Path) -> bool:
    """Whether the renamed run dir still holds an inbox file.

    Must glob the tombstone path, not ``pending_requests(run_id)``: after
    ``os.rename`` the original ``run_dir`` is gone, so a lookup there would
    miss every racing write.
    """
    reqs = tombstone / REQUESTS_DIR
    return reqs.is_dir() and any(reqs.glob("*.json"))
