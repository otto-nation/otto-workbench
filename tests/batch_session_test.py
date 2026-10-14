"""Tests for `batch.store` sessions: the one route from a run's state back to disk."""

import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import batch.plan  # noqa: E402
import batch.requests  # noqa: E402
import batch.store  # noqa: E402
import core.run_lock  # noqa: E402
import core.serde  # noqa: E402
from batch.model import (CancelState, Decision, DecisionKind, Item, ItemStatus, Run,  # noqa: E402
                         RunStatus, Step, StepRecord)
from batch.requests import Cancel, Decide, Malformed  # noqa: E402
from batch.store import FailedRequest, Holder, Role  # noqa: E402
from batch_session_support import NullInbox, open_session, seed  # noqa: E402
from run_lock_support import held_elsewhere, live_pid  # noqa: E402

RID = "20261014T120000-abcd"
# Request names that sort before any request write_request stamps with the clock.
EARLY = "19700101T000000000000"


class _Stop(Exception):
    """Raised inside a session block to end it exceptionally."""


def _run(*, status=RunStatus.WAITING, ref_namespace="", ref_dirs=()):
    """One item awaiting a FAILED decision `d1`, which `drop-pr` answers with no command."""
    item = Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                status=ItemStatus.AWAITING_DECISION, steps=[StepRecord(Step.REBASE)])
    failed = Decision(id="d1", item="o/r#1", step="rebase", kind=DecisionKind.FAILED,
                      payload={"reason": "error", "exit_code": 1, "log_tail": []})
    return Run(id=RID, started_at="t", steps=[Step.REBASE], pool=1, auto_publish=[],
               status=status, items=[item], decisions=[failed],
               ref_namespace=ref_namespace, ref_dirs=list(ref_dirs))


def _lock_file() -> Path:
    return batch.store.run_lock(RID).path


def _requests() -> Path:
    return batch.store.run_dir(RID) / "requests"


def _write_record(record) -> None:
    """Overwrite the run's holder record without taking its flock."""
    _lock_file().write_text(json.dumps(record))


def _dead_pid() -> int:
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


# ── the lock ──────────────────────────────────────────────────────────────────

def test_the_run_lock_is_the_run_dirs_own_lock_file():
    lock = batch.store.run_lock(RID)
    assert lock.path == batch.store.run_dir(RID) / "run.lock"
    assert lock.subject == f"batch run {RID}"


def test_a_session_loads_only_after_it_holds_the_lock(monkeypatch):
    seed(_run())
    seen = []
    real = batch.store.load

    def spy(run_id):
        seen.append(core.run_lock.holds(batch.store.run_lock(run_id).path))
        return real(run_id)

    monkeypatch.setattr(batch.store, "load", spy)
    with open_session(RID):
        pass
    assert seen == [True]


def test_the_holder_record_names_the_role_and_the_run():
    seed(_run())
    with open_session(RID, Role.APPLIER) as session:
        record = core.run_lock.read_holder(_lock_file())
        current = batch.store.holder(RID)
        role = session.role
    assert role is Role.APPLIER
    assert record["role"] == "applier" and record["run"] == RID
    assert current == Holder(role=Role.APPLIER, pid=os.getpid(), command="test session",
                             started=record["started"])
    assert batch.store.holder(RID) is None


def test_a_session_on_a_run_with_no_saved_state_is_not_found():
    with pytest.raises(batch.store.RunNotFound):
        with open_session(RID):
            pytest.fail("a run with no state must not open")
    assert not batch.store.run_dir(RID).exists()


def test_a_live_holder_refuses_the_session_naming_the_run_and_its_role():
    seed(_run())
    with live_pid() as pid, \
         held_elsewhere(_lock_file(), {"pid": pid, "command": "pr batch resolve",
                                       "started": "t0", "released": None,
                                       "role": "applier", "run": RID}), \
         pytest.raises(batch.store.RunBusy) as exc:
        seen = pid
        with open_session(RID):
            pytest.fail("a live holder must refuse the session")
    assert exc.value.run_id == RID
    assert exc.value.holder == Holder(role=Role.APPLIER, pid=seen,
                                      command="pr batch resolve", started="t0")
    assert str(exc.value) == (f"run {RID} is busy: applying a decision "
                              f"(pr batch resolve, pid {seen}, started t0)")


def test_a_second_session_on_one_run_in_one_process_is_a_programming_error():
    seed(_run())
    with open_session(RID), pytest.raises(core.run_lock.LockReentry):
        with open_session(RID):
            pytest.fail("a run must not be opened twice in one process")
    assert not core.run_lock.holds(_lock_file())


def test_a_vacated_record_that_never_frees_is_unreachable_not_busy(monkeypatch):
    seed(_run())
    # A short budget: the flock below is held for the whole test, so every retry fails.
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0]))
    record = {"pid": os.getpid(), "command": "pr batch status", "started": "t0",
              "released": "t1"}
    with held_elsewhere(_lock_file(), record), \
         pytest.raises(batch.store.RunUnreachable) as exc:
        with open_session(RID):
            pytest.fail("a held flock must refuse the session")
    assert exc.value.run_id == RID
    assert RID in str(exc.value)


# ── holder(): read, never taken ──────────────────────────────────────────────

def test_holder_reads_the_record_and_never_flocks(monkeypatch):
    seed(_run())
    _write_record({"pid": os.getpid(), "command": "pr batch run", "started": "t0",
                   "released": None, "role": "driver"})
    calls = []
    monkeypatch.setattr(core.run_lock.fcntl, "flock", lambda *args: calls.append(args))
    current = batch.store.holder(RID)
    assert calls == []
    assert current == Holder(role=Role.DRIVER, pid=os.getpid(), command="pr batch run",
                             started="t0")


def test_holder_is_none_for_an_empty_released_or_dead_record():
    seed(_run())
    records = [{}, {"pid": os.getpid(), "command": "c", "started": "t", "released": "t1"},
               {"pid": _dead_pid(), "command": "c", "started": "t", "released": None}]
    for record in records:
        _write_record(record)
        assert batch.store.holder(RID) is None, record


@pytest.mark.parametrize("record_role", [None, "pilot"])
def test_a_live_record_without_a_known_role_reads_as_a_draining_holder(record_role):
    seed(_run())
    record = {"pid": os.getpid(), "command": "pr batch run", "started": "t0", "released": None}
    if record_role is not None:
        record["role"] = record_role
    _write_record(record)
    current = batch.store.holder(RID)
    assert current == Holder(role=None, pid=os.getpid(), command="pr batch run", started="t0")
    assert current.drains
    assert current.describe() == "holding it"


@pytest.mark.parametrize(("role", "drains", "doing"), [
    (Role.DRIVER, True, "driving it"),
    (Role.APPLIER, True, "applying a decision"),
    (Role.GC, False, "removing it"),
    (None, True, "holding it"),
])
def test_which_holders_drain_and_how_a_refusal_describes_them(role, drains, doing):
    held = Holder(role=role, pid=1, command="c", started="t")
    assert held.drains is drains
    assert held.describe() == doing


# ── commit ────────────────────────────────────────────────────────────────────

def test_commit_refuses_once_the_session_has_closed():
    seed(_run())
    with open_session(RID) as session:
        pass
    session.run.status = RunStatus.CANCELLED
    with pytest.raises(RuntimeError, match="run lock"):
        session.commit()
    assert batch.store.load(RID).status is RunStatus.WAITING


def test_commit_writes_the_sessions_run_and_takes_no_other():
    seed(_run())
    stale = batch.store.load(RID)
    stale.status = RunStatus.CANCELLED
    with open_session(RID) as session:
        assert session.run is not stale
        session.run.items[0].wait_reason = "fresh"
        session.commit()
    saved = batch.store.load(RID)
    assert saved.status is RunStatus.WAITING
    assert saved.items[0].wait_reason == "fresh"
    assert list(inspect.signature(batch.store.Session.commit).parameters) == ["self"]


def test_refs_are_dropped_only_after_the_terminal_state_is_saved(monkeypatch):
    seen = []
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, namespace: seen.append(
        (batch.store.load(RID).status, tuple(dirs), namespace)) or True)
    seed(_run(ref_namespace="refs/workbench-batch/x", ref_dirs=["/r"]))
    assert seen == []
    with open_session(RID) as session:
        session.run.status = RunStatus.CANCELLED
        session.commit()
    assert seen
    assert set(seen) == {(RunStatus.CANCELLED, ("/r",), "refs/workbench-batch/x")}


# ── exits ─────────────────────────────────────────────────────────────────────

def test_a_normal_exit_commits_what_the_block_changed():
    seed(_run())
    with open_session(RID) as session:
        session.run.items[0].wait_reason = "set in the block"
    assert batch.store.load(RID).items[0].wait_reason == "set in the block"


def test_a_normal_exit_drains_a_request_written_during_the_block():
    seed(_run())
    with open_session(RID):
        batch.store.write_request(RID, Decide("d1", "drop-pr"))
    saved = batch.store.load(RID)
    assert saved.decision("d1").resolution == "drop-pr"
    assert saved.status is RunStatus.DONE
    assert batch.store.pending_requests(RID) == 0


def test_an_exceptional_exit_commits_nothing():
    seed(_run())
    with pytest.raises(_Stop):
        with open_session(RID) as session:
            session.run.status = RunStatus.CANCELLED
            raise _Stop()
    assert batch.store.load(RID).status is RunStatus.WAITING
    assert not core.run_lock.holds(_lock_file())


def test_an_exceptional_exit_leaves_requests_written_during_the_block_queued():
    seed(_run())
    with pytest.raises(_Stop):
        with open_session(RID):
            batch.store.write_request(RID, Decide("d1", "drop-pr"))
            raise _Stop()
    assert batch.store.pending_requests(RID) == 1
    assert batch.store.load(RID).decision("d1").open


# ── create ────────────────────────────────────────────────────────────────────

def test_create_writes_no_state_until_the_first_commit():
    run = _run()
    with batch.store.create(run, inbox=NullInbox(), command="pr batch run") as session:
        assert batch.store.run_dir(RID).is_dir()
        assert not (batch.store.run_dir(RID) / batch.store.STATE_FILE).exists()
        assert batch.store.holder(RID).role is Role.DRIVER
        session.commit()
        assert batch.store.load(RID) == run
    assert batch.store.load(RID).status is RunStatus.WAITING


def test_a_create_never_committed_leaves_no_directory():
    with batch.store.create(_run(), inbox=NullInbox(), command="pr batch run"):
        pass
    assert not batch.store.run_dir(RID).exists()


def test_a_create_that_fails_before_committing_leaves_no_directory():
    with pytest.raises(_Stop):
        with batch.store.create(_run(), inbox=NullInbox(), command="pr batch run"):
            raise _Stop()
    assert not batch.store.run_dir(RID).exists()


def test_a_create_that_fails_after_its_first_commit_keeps_the_run():
    with pytest.raises(_Stop):
        with batch.store.create(_run(), inbox=NullInbox(), command="pr batch run") as session:
            session.commit()
            raise _Stop()
    assert batch.store.load(RID).status is RunStatus.WAITING


def test_create_refuses_a_run_id_already_on_disk():
    seed(_run())
    with pytest.raises(FileExistsError):
        with batch.store.create(_run(), inbox=NullInbox(), command="pr batch run"):
            pytest.fail("a second create of one run id must not enter the block")
    assert batch.store.load(RID).status is RunStatus.WAITING


# ── the legacy cancel sidecar ────────────────────────────────────────────────

def test_a_legacy_cancel_sidecar_is_folded_into_the_run_and_deleted():
    seed(_run())
    sidecar = batch.store.run_dir(RID) / batch.store.LEGACY_CANCEL_FILE
    sidecar.write_text(json.dumps({"kill": True}))
    with open_session(RID):
        pass
    assert not sidecar.exists()
    saved = batch.store.load(RID)
    assert saved.cancel.kill is True and saved.cancel.requested_at
    assert saved.status is RunStatus.CANCELLED


@pytest.mark.parametrize(("recorded", "sidecar_kill"), [(False, True), (True, False)])
def test_folding_a_sidecar_only_ever_upgrades_kill(recorded, sidecar_kill):
    seed(_run())
    with pytest.raises(_Stop):
        with open_session(RID) as session:
            session.run.cancel = CancelState(kill=recorded, requested_at="t0")
            session.commit()
            raise _Stop()
    sidecar = batch.store.run_dir(RID) / batch.store.LEGACY_CANCEL_FILE
    sidecar.write_text(json.dumps({"kill": sidecar_kill}))
    with open_session(RID) as session:
        folded = session.run.cancel
    assert folded == CancelState(kill=True, requested_at="t0")


def test_a_sidecar_on_a_finished_run_is_deleted_and_changes_nothing():
    seed(_run(status=RunStatus.DONE))
    sidecar = batch.store.run_dir(RID) / batch.store.LEGACY_CANCEL_FILE
    sidecar.write_text(json.dumps({"kill": True}))
    with open_session(RID):
        pass
    assert not sidecar.exists()
    saved = batch.store.load(RID)
    assert saved.cancel is None
    assert saved.status is RunStatus.DONE


# ── the inbox ─────────────────────────────────────────────────────────────────

def test_write_request_returns_the_file_name_and_writes_the_tagged_request():
    seed(_run())
    name = batch.store.write_request(RID, Cancel(kill=True))
    assert name.endswith(".json")
    body = json.loads((_requests() / name).read_text())
    assert body == batch.requests.to_dict(Cancel(kill=True))


def test_write_request_never_creates_a_missing_run_directory():
    with pytest.raises(batch.store.RunNotFound):
        batch.store.write_request(RID, Cancel())
    assert not batch.store.run_dir(RID).exists()


def test_write_request_racing_a_removal_is_not_found_and_leaves_no_orphan(monkeypatch):
    """A removal landing between the inbox mkdir and the write must not recreate `requests/`."""
    seed(_run())
    real = core.serde.write_json

    def removed_first(path, data, **kwargs):
        shutil.rmtree(batch.store.run_dir(RID))
        return real(path, data, **kwargs)

    monkeypatch.setattr(core.serde, "write_json", removed_first)
    with pytest.raises(batch.store.RunNotFound):
        batch.store.write_request(RID, Cancel())
    assert not batch.store.run_dir(RID).exists()


def test_apply_reports_a_synthesized_request_and_leaves_the_commit_to_the_caller():
    seed(_run())
    inbox = NullInbox()
    with open_session(RID, inbox=inbox) as session:
        outcome = session.apply(Decide("d1", "drop-pr"))
        still_open_on_disk = batch.store.load(RID).decision("d1").open
    assert outcome.ok and outcome.name == ""
    assert inbox.outcomes == [outcome]
    assert still_open_on_disk
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"


def test_peek_reads_without_taking_and_marks_unreadable_files_malformed():
    seed(_run())
    name = batch.store.write_request(RID, Decide("d1", "drop-pr"))
    (_requests() / f"{EARLY}-dir.json").mkdir()
    (_requests() / f"{EARLY}-list.json").write_text("[]")
    envelopes = batch.store.peek_requests(RID)
    assert [e.name for e in envelopes] == [f"{EARLY}-dir.json", f"{EARLY}-list.json", name]
    assert isinstance(envelopes[0].request, Malformed)
    assert isinstance(envelopes[1].request, Malformed)
    assert envelopes[2].request == Decide("d1", "drop-pr")
    assert batch.store.pending_requests(RID) == 3


def test_a_half_written_request_is_invisible_to_the_inbox():
    """`write_json`'s temp file is `<name>.json.<rand>.tmp`, which `*.json` does not match."""
    seed(_run())
    _requests().mkdir(exist_ok=True)
    (_requests() / f"{EARLY}-x.json.ab12.tmp").write_text("{")
    assert batch.store.peek_requests(RID) == []
    assert batch.store.pending_requests(RID) == 0


def test_a_drain_applies_every_cancel_before_any_decision():
    seed(_run())
    decide = batch.store.write_request(RID, Decide("d1", "drop-pr"))
    cancel = batch.store.write_request(RID, Cancel(kill=True))
    assert decide < cancel
    inbox = NullInbox()
    with open_session(RID, inbox=inbox):
        pass
    assert [o.name for o in inbox.outcomes] == [cancel, decide]
    assert all(o.ok for o in inbox.outcomes)
    assert batch.store.pending_requests(RID) == 0
    saved = batch.store.load(RID)
    assert saved.cancel.kill is True
    assert saved.status is RunStatus.CANCELLED


def test_a_drain_commits_each_request_before_it_unlinks_it(monkeypatch):
    """A crash between the commit and the unlink replays the request; it never loses it."""
    seed(_run())
    batch.store.write_request(RID, Decide("d1", "drop-pr"))
    real_unlink = Path.unlink
    crashed = []

    def unlink(self, *args, **kwargs):
        if self.parent.name == "requests" and not crashed:
            crashed.append(self.name)
            raise OSError("crash between commit and unlink")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError, match="crash between commit and unlink"):
        with open_session(RID):
            pytest.fail("the entry drain must crash before the block")
    assert batch.store.load(RID).decision("d1").resolution == "drop-pr"
    assert batch.store.pending_requests(RID) == 1
    inbox = NullInbox()
    with open_session(RID, inbox=inbox):
        pass
    assert [o.ok for o in inbox.outcomes] == [True]
    assert batch.store.pending_requests(RID) == 0
    assert batch.store.load(RID).status is RunStatus.DONE


def test_a_rejected_request_moves_to_rejected_with_its_error():
    seed(_run())
    _requests().mkdir()
    (_requests() / f"{EARLY}-bad.json").write_text("not json")
    unknown = batch.store.write_request(RID, Decide("nope", "accept"))
    inbox = NullInbox()
    with open_session(RID, inbox=inbox):
        pass
    assert batch.store.pending_requests(RID) == 0
    assert [o.ok for o in inbox.outcomes] == [False, False]
    rejected = _requests() / "rejected"
    bad = json.loads((rejected / f"{EARLY}-bad.json").read_text())
    assert bad["request"] is None
    assert bad["error"].startswith(f"malformed request {EARLY}-bad.json")
    body = json.loads((rejected / unknown).read_text())
    assert body == {"request": batch.requests.to_dict(Decide("nope", "accept")),
                    "error": "'nope'"}


def test_failed_requests_lists_rejections_and_reads_older_ones_as_malformed():
    seed(_run())
    assert batch.store.failed_requests(RID) == []
    _requests().mkdir()
    (_requests() / f"{EARLY}-bad.json").write_text("not json")
    unknown = batch.store.write_request(RID, Decide("nope", "accept"))
    with open_session(RID):
        pass
    old = _requests() / "rejected" / "19691231T000000000000-old.json"
    old.write_text(json.dumps({"decision": "d9", "action": "retry"}))
    failed = batch.store.failed_requests(RID)
    assert [f.name for f in failed] == [old.name, f"{EARLY}-bad.json", unknown]
    assert failed[0] == FailedRequest(old.name, "malformed")
    assert failed[1].error.startswith(f"malformed request {EARLY}-bad.json")
    assert failed[2] == FailedRequest(unknown, "'nope'")
