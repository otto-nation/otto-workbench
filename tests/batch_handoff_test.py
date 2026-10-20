"""The hand-off: a request queued against a holder is applied once that holder lets go.

The two-process tests park a real holder in a child interpreter, submit from
this one, then release the holder. That is the only way to put a submitter's
refused take between a holder's last drain and its release. The other tests
pin each branch of `batch.store._hand_off`, plus two properties the hand-off
relies on: a prober never makes a request read as `queued`, and polling
`status` never refuses a take.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
TESTS_DIR = REPO_ROOT / "tests"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import batch.inbox  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
import core.run_lock  # noqa: E402
from batch.model import (Decision, DecisionKind, Item, ItemStatus, Run,  # noqa: E402
                         RunStatus, Step, StepRecord)
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch.requests import Cancel, Decide  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import NullInbox, open_session, seed  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402

RID = "20261014T130000-beef"
# Bounds every child process a test here starts. It is generous because a
# loaded runner schedules a fresh interpreter slowly, and a flake here would
# read as a lock bug.
_PROC_TIMEOUT = 60
_POLL_S = 0.05
# How long a prober keeps the flock: well inside core's retry budget and well
# past its first pauses, so a take that gets in has really waited.
_PROBE_S = 0.3
_POLLER = "batch-status-poller"
# A record with `released` set reads as vacated whatever its pid.
_VACATED = {"pid": os.getpid(), "command": "probe", "started": "t", "released": "t"}
_STRANDED = ("pr batch: run {rid} still has {n} queued request(s) nobody could apply; "
             "`pr batch resume {rid}` or another resolve will apply them")


def _no_command(*args, **kwargs) -> int:
    """A resolve runner whose every command succeeds without running."""
    return 0


_CTX = batch.inbox.ApplyContext(pr_bin="pr", runner=_no_command)


class _Stop(Exception):
    """Raised inside a session block to end it exceptionally."""


def _run(*, status=RunStatus.WAITING):
    """One item awaiting a FAILED decision `d1`, which `drop-pr` answers with no command."""
    item = Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                status=ItemStatus.AWAITING_DECISION, steps=[StepRecord(Step.REBASE)])
    failed = Decision(id="d1", item="o/r#1", step="rebase", kind=DecisionKind.FAILED,
                      payload={"reason": "error", "exit_code": 1, "log_tail": []})
    return Run(id=RID, started_at="t", steps=[Step.REBASE], pool=1, auto_publish=[],
               status=status, items=[item], decisions=[failed])


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _lock_file() -> Path:
    return batch.store.run_lock(RID).path


def _holder_record(pid, role):
    return {"pid": pid, "command": f"pr batch ({role})", "started": "t", "released": None,
            "role": role}


@pytest.fixture
def other_pid():
    """The pid of a live process that is not this one."""
    child = subprocess.Popen(["sleep", "60"])
    yield child.pid
    child.kill()
    child.wait()


class _LateArrival(NullInbox):
    """A NullInbox that queues *request* from inside the first settle after it is armed.

    That settle runs in `finish`, after its drain peeked the inbox and before the
    lock is released, so the request lands in exactly the window the hand-off
    closes.
    """

    def __init__(self, request):
        super().__init__()
        self.request = request
        self.armed = False

    def settle(self, run):
        if self.armed:
            self.armed = False
            batch.store.write_request(run.id, self.request)
        return super().settle(run)


def _probe_for(seconds):
    """Hold the run's flock under a released record from our own descriptor; free it after *seconds*."""
    handle = open(_lock_file(), "a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.seek(0)
    handle.truncate()
    json.dump(_VACATED, handle)
    handle.flush()
    timer = threading.Timer(seconds, handle.close)
    timer.start()
    return timer


# ── the hand-off, in one process ─────────────────────────────────────────────

def test_a_request_landing_after_a_sessions_last_drain_is_applied_on_release():
    seed(_run())
    inbox = _LateArrival(Decide("d1", "drop-pr"))
    with open_session(RID, Role.APPLIER, inbox=inbox):
        inbox.armed = True
    saved = batch.store.load(RID)
    assert saved.decision("d1").resolution == "drop-pr"
    assert saved.status is RunStatus.DONE
    assert batch.store.pending_requests(RID) == 0
    assert [o.request for o in inbox.outcomes] == [Decide("d1", "drop-pr")]


def test_a_request_landing_after_creates_last_drain_is_applied_on_release():
    inbox = _LateArrival(Decide("d1", "drop-pr"))
    with batch.store.create(_run(), inbox=inbox, command="pr batch run") as opened:
        opened.commit()
        inbox.armed = True
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"
    assert batch.store.pending_requests(RID) == 0


def test_an_exceptional_exit_hands_nothing_off(monkeypatch):
    seed(_run())
    calls = []
    monkeypatch.setattr(batch.store, "_hand_off", lambda *args: calls.append(args))
    with pytest.raises(_Stop), open_session(RID):
        batch.store.write_request(RID, Decide("d1", "drop-pr"))
        raise _Stop()
    assert calls == []
    assert batch.store.pending_requests(RID) == 1
    assert batch.store.load(RID).decision("d1").open
    with open_session(RID):
        pass
    assert [args[0] for args in calls] == [RID]


def test_an_exceptional_exit_of_create_hands_nothing_off(monkeypatch):
    calls = []
    monkeypatch.setattr(batch.store, "_hand_off", lambda *args: calls.append(args))
    with pytest.raises(_Stop), batch.store.create(_run(), inbox=NullInbox(),
                                                 command="pr batch run") as opened:
        opened.commit()
        batch.store.write_request(RID, Decide("d1", "drop-pr"))
        raise _Stop()
    assert calls == []
    assert batch.store.pending_requests(RID) == 1


def test_a_live_draining_holder_carries_the_duty(other_pid, monkeypatch, capsys):
    seed(_run())
    batch.store.write_request(RID, Decide("d1", "drop-pr"))
    slept = []
    monkeypatch.setattr(batch.store.time, "sleep", slept.append)
    with held_elsewhere(_lock_file(), _holder_record(other_pid, "driver")):
        batch.store._hand_off(RID, NullInbox(), "pr batch resolve")
    assert slept == []
    assert batch.store.pending_requests(RID) == 1
    assert capsys.readouterr().err == ""


def test_a_gc_holder_is_waited_out(other_pid, monkeypatch):
    seed(_run())
    batch.store.write_request(RID, Decide("d1", "drop-pr"))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0]))
    gc = contextlib.ExitStack()
    gc.enter_context(held_elsewhere(_lock_file(), _holder_record(other_pid, "gc")))
    # The first pause is where the GC holder finishes its reap and lets go.
    monkeypatch.setattr(batch.store.time, "sleep", lambda seconds: gc.close())
    try:
        batch.store._hand_off(RID, NullInbox(), "pr batch resolve")
    finally:
        gc.close()
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"
    assert batch.store.pending_requests(RID) == 0


@pytest.mark.parametrize("holder", ["gc", "vacated"])
def test_a_hand_off_that_cannot_get_in_warns_and_leaves_the_request(holder, other_pid,
                                                                      monkeypatch, capsys):
    seed(_run())
    batch.store.write_request(RID, Decide("d1", "drop-pr"))
    # `_hand_off` looks up retry_delays() first; core looks it up inside a pass.
    # Tracking next() on that first iterator pins that a vacated record does not
    # spend a second budget in `_hand_off` itself.
    hand_off_pauses = []
    started = []

    def retry_delays():
        source = iter([0.0, 0.0])
        first = not started
        started.append(True)
        if not first:
            return source

        def tracked():
            for delay in source:
                hand_off_pauses.append(delay)
                yield delay
        return tracked()

    monkeypatch.setattr(core.run_lock, "retry_delays", retry_delays)
    slept = []
    monkeypatch.setattr(batch.store.time, "sleep", slept.append)
    record = _holder_record(other_pid, "gc") if holder == "gc" else _VACATED
    with held_elsewhere(_lock_file(), record):
        batch.store._hand_off(RID, NullInbox(), "pr batch resolve")
    assert _STRANDED.format(rid=RID, n=1) in capsys.readouterr().err
    if holder == "vacated":
        assert hand_off_pauses == []
    else:
        assert len(slept) >= 2
    assert batch.store.pending_requests(RID) == 1
    assert batch.store.load(RID).decision("d1").open


# ── two real processes ────────────────────────────────────────────────────────

# One process holding the run open through `batch.store.session`, parked until
# the test writes `release`.
# - "applier" queues a `park` request inside its own block. Its `finish`
#   drain parks in that request's apply, after the peek that will not see
#   anything submitted later.
# - "driver" calls `finish()` itself and parks after it, still inside the
#   block.
# Either way the run's lock is held while the test submits, and the last
# drain never sees what the test submitted.
_PARKED_HARNESS = textwrap.dedent("""\
    import pathlib, sys, time
    lib, tests, signals, run_id, mode = sys.argv[1:6]
    sys.path[:0] = [lib, tests]
    import batch.store
    from batch.requests import Decide, Outcome
    from batch.store import Role
    from batch_session_support import NullInbox
    signals = pathlib.Path(signals)

    def park():
        (signals / "parked").touch()
        deadline = time.monotonic() + 30
        while not (signals / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.05)

    class ParkingInbox(NullInbox):
        def apply(self, run, envelope):
            if getattr(envelope.request, "decision", "") != "park":
                return super().apply(run, envelope)
            park()
            return Outcome(name=envelope.name, request=envelope.request)

    role = Role.APPLIER if mode == "applier" else Role.DRIVER
    with batch.store.session(run_id, role, inbox=ParkingInbox(),
                             command=f"test {mode}") as opened:
        if mode == "applier":
            batch.store.write_request(run_id, Decide("park", "accept"))
        else:
            opened.finish()
            park()
""")


def _parked(signals, mode):
    argv = [sys.executable, "-c", _PARKED_HARNESS, str(LIB_DIR), str(TESTS_DIR),
            str(signals), RID, mode]
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait_for(path, proc):
    """Poll for *path* while *proc* is alive; fail naming what *proc* printed if it never appears."""
    deadline = time.monotonic() + _PROC_TIMEOUT
    while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(_POLL_S)
    assert path.exists(), f"never saw {path.name}: {proc.communicate(timeout=_PROC_TIMEOUT)}"


def test_a_cancel_queued_against_a_parked_applier_is_applied_by_its_hand_off(tmp_path, capsys):
    seed(_run())
    child = _parked(tmp_path, "applier")
    try:
        _wait_for(tmp_path / "parked", child)
        code = _main(["batch", "cancel", RID])
        parked = batch.store.load(RID)
    finally:
        (tmp_path / "release").touch()
        _, err = child.communicate(timeout=_PROC_TIMEOUT)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["queued"] is True and out["run"] == RID
    assert parked.status is RunStatus.WAITING
    assert child.returncode == 0, err
    saved = batch.store.load(RID)
    assert saved.status is RunStatus.CANCELLED
    assert saved.cancel is not None
    assert batch.store.pending_requests(RID) == 0


def test_a_resolve_queued_after_a_drivers_finish_is_applied_by_its_hand_off(tmp_path, capsys):
    seed(_run())
    child = _parked(tmp_path, "driver")
    try:
        _wait_for(tmp_path / "parked", child)
        code = _main(["batch", "resolve", RID, "d1", "--action", "drop-pr"])
        parked = batch.store.load(RID)
    finally:
        (tmp_path / "release").touch()
        _, err = child.communicate(timeout=_PROC_TIMEOUT)
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "applied": False, "queued": True, "decision": "d1",
        "holder": {"role": "driver", "pid": child.pid, "command": "test driver"},
    }
    assert parked.decision("d1").open
    assert child.returncode == 0, err
    saved = batch.store.load(RID)
    assert saved.decision("d1").resolution == "drop-pr"
    assert saved.status is RunStatus.DONE
    assert batch.store.pending_requests(RID) == 0


# ── probers never yield `queued` ──────────────────────────────────────────────

def test_deliver_waits_out_a_prober_and_applies_the_request():
    seed(_run())
    started = time.monotonic()
    timer = _probe_for(_PROBE_S)
    try:
        delivery = batch.inbox.deliver(RID, Decide("d1", "drop-pr"), _CTX,
                                       command="pr batch resolve")
        waited = time.monotonic() - started
    finally:
        timer.join()
    assert waited >= _PROBE_S / 2
    assert delivery.queued is False
    assert delivery.own is not None and delivery.own.ok
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"
    assert batch.store.pending_requests(RID) == 0


def test_deliver_against_a_prober_that_never_lets_go_is_unreachable_not_queued(monkeypatch):
    seed(_run())
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.001]))
    with held_elsewhere(_lock_file(), _VACATED), pytest.raises(batch.store.RunUnreachable):
        batch.inbox.deliver(RID, Decide("d1", "drop-pr"), _CTX, command="pr batch resolve")
    assert batch.store.pending_requests(RID) == 1
    assert batch.store.load(RID).decision("d1").open


def test_a_hand_off_waits_out_a_prober_and_applies_the_request():
    seed(_run())
    batch.store.write_request(RID, Decide("d1", "drop-pr"))
    timer = _probe_for(_PROBE_S)
    try:
        batch.store._hand_off(RID, NullInbox(), "pr batch resolve")
    finally:
        timer.join()
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"
    assert batch.store.pending_requests(RID) == 0


# ── R7: status polling never refuses a take ──────────────────────────────────

def _poll_status(stop, first, codes):
    """What `pr batch status`/`next` run after dispatch (NO_LOCK), in a loop until *stop*."""
    while not stop.is_set():
        for sub in ("status", "next"):
            codes.append(cli.pr_batch.cmd_batch([sub, RID], None, bin_dir=BIN_DIR))
        first.set()


def test_status_polling_never_refuses_a_resume(monkeypatch, capsys):
    seed(_run())
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: RunStatus.WAITING)
    real_flock = fcntl.flock
    flocked_by = []

    def spy(fd, operation):
        flocked_by.append(threading.current_thread().name)
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", spy)
    stop, first, polls, resumes = threading.Event(), threading.Event(), [], []
    poller = threading.Thread(target=_poll_status, args=(stop, first, polls), name=_POLLER,
                              daemon=True)
    poller.start()
    try:
        assert first.wait(_PROC_TIMEOUT)
        for _ in range(10):
            resumes.append(_main(["batch", "resume", RID]))
    finally:
        stop.set()
        poller.join(_PROC_TIMEOUT)
    assert resumes == [10] * 10
    assert polls and set(polls) == {0}
    assert _POLLER not in flocked_by
    assert "is busy" not in capsys.readouterr().err


# ── a late cancel applied by a driver's finish ───────────────────────────────

def test_a_drivers_finish_that_applies_a_late_cancel_ends_cancelled_and_exits_0(monkeypatch,
                                                                              capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False, {Step.REVIEW: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", [row]))

    def late_cancel(self):
        batch.store.write_request(self.run.id, Cancel())
        return RunStatus.WAITING

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", late_cancel)
    code = _main(["batch", "run", "--checkout", "/r"])
    lines = capsys.readouterr().out.splitlines()
    events = [json.loads(line) for line in lines if line.startswith("{")]
    kinds = [event["kind"] for event in events]
    assert code == 0
    assert batch.store.load(batch.store.latest_run_id()).status is RunStatus.CANCELLED
    finished = [event for event in events if event["kind"] == "run_finished"]
    assert finished and finished[-1]["status"] == "cancelled"
    # The summary is the last line, so the run_finished found above precedes it.
    assert kinds[-1] == "run_summary"
    assert events[-1]["run"]["status"] == "cancelled"
    assert events[-1]["run"]["exit_hint"] == 0
