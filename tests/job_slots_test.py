"""Tests for the machine-wide test-parallelism slot pool.

The pool's whole claim is that a grant reflects what other runs are *holding*
rather than what the load average has got round to reporting, so most of these
assert against a second claim taken while the first is still open.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from core import job_slots
from core.job_slots import LOCK_ENV, claim, holders, pool_size
from core.job_slots_cli import GRANT_ENV

CLI = REPO_ROOT / "bin" / "local" / "claim-job-slots"


@pytest.fixture(autouse=True)
def _pool_in_tmp(tmp_path, monkeypatch):
    """Point the state root at a scratch dir and drop both inherited markers.

    The state root has to move or a test would claim slots out of the pool the
    developer's own suite run is holding — and then report a grant that says
    more about the machine than about the code.

    Both markers have to go, and not only for the in-process claims. This suite
    normally runs *under* `run-tests → claim-job-slots → pytest`, so the outer
    run has exported its own marker and grant into this process; the `_cli`
    helpers below pass no `env=`, so a subprocess inherits whatever
    `os.environ` holds at call time. Left in place, `LOCK_ENV` makes every CLI
    child take the pass-through branch and report the outer run's grant rather
    than claiming anything. `monkeypatch.delenv` mutates the live environment,
    which is what the subprocesses read — so this is the guard for them too,
    deliberately and not incidentally.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv(LOCK_ENV, raising=False)
    monkeypatch.delenv(GRANT_ENV, raising=False)
    yield


def test_an_uncontended_claim_gets_what_it_asked_for():
    with claim(want=4, floor=2, cores=18) as granted:
        assert granted == 4


def test_a_second_claim_gets_only_what_the_first_left():
    """The property the whole pool exists for.

    Both claims ask for 4 from a pool of 5, and the second must see the first's
    holdings rather than an idle machine. A load average cannot answer this at
    all within its one-minute window, which is the bug being fixed.
    """
    with claim(want=4, floor=1, cores=6) as first:
        assert first == 4
        # Drop the marker the outer claim just set, so the inner one is a fresh
        # run rather than a pass-through. Without this the test would exercise
        # the nested case above instead of contention.
        os.environ.pop(LOCK_ENV, None)
        with claim(want=4, floor=1, cores=6) as second:
            assert second == 1


def test_slots_are_released_when_the_claim_exits():
    """Sized so a leak has nowhere to hide.

    Against a 17-slot pool this assertion cannot fail: a first claim that
    stranded slots 0-2 would simply have the second take 3-5, and 3 is what a
    correct release and a total leak both look like. A pool of 3 leaves the
    second claim nothing to find.
    """
    with claim(want=3, floor=1, cores=4):
        pass
    with claim(want=3, floor=1, cores=4) as granted:
        assert granted == 3


def test_a_claim_on_an_exhausted_pool_falls_back_to_the_floor():
    """Never zero, and never a wait.

    A run that queued behind another would make the third worktree's pre-push
    sit silent for two suites; overshooting by the floor is the deliberate
    trade.
    """
    with claim(want=2, floor=1, cores=3):
        os.environ.pop(LOCK_ENV, None)
        with claim(want=4, floor=2, cores=3) as granted:
            assert granted == 2


def test_a_partly_satisfied_claim_below_the_floor_keeps_what_it_got():
    """The common path on a contended machine, and the one the floor is for.

    `max(len(held), floor)` and `len(held) or floor` agree whenever the pool is
    empty or full, which is what the two tests either side of this one cover —
    so the difference only shows when a claim gets *some* slots but fewer than
    its floor. That is the middle run of the 12/5/2 split the module advertises,
    and it read as 1 under the `or` spelling.
    """
    with claim(want=3, floor=1, cores=5):
        os.environ.pop(LOCK_ENV, None)
        with claim(want=4, floor=3, cores=5) as granted:
            assert granted == 3


def test_the_grant_never_exceeds_what_was_asked_for():
    with claim(want=2, floor=1, cores=18) as granted:
        assert granted == 2


def test_a_nested_claim_passes_through_with_the_parents_grant():
    """run-tests re-execs itself under two wrappers; the inner must not re-claim.

    A second claim from inside the first would compete with slots this process
    already holds, and would report a smaller number than the run is using.

    Two things make this falsifiable, and it needs both. The pool is exactly
    the size of the request, so a re-entering inner claim cannot find five more
    and drops to the floor; and the holder count is asserted inside the block,
    which separates "passed through" from "claimed a second five" directly.
    Against a 17-slot pool the bare grant assertion held while the process
    quietly sat on ten slots.
    """
    with claim(want=5, floor=1, cores=6) as outer:
        with claim(want=5, floor=1, cores=6) as inner:
            assert inner == outer == 5
            assert len(holders()) == 5


def test_a_junk_marker_is_not_honoured_as_a_grant(monkeypatch):
    """A value nobody wrote deliberately must not size a run."""
    monkeypatch.setenv(LOCK_ENV, "not-a-number")
    with claim(want=3, floor=1, cores=18) as granted:
        assert granted == 3


def test_a_holder_record_names_the_command():
    with claim(want=1, floor=1, cores=18, command="bin/local/run-tests"):
        records = holders()
    assert [r["command"] for r in records] == ["bin/local/run-tests"]
    assert records[0]["pid"] == os.getpid()


def test_a_released_slot_still_has_its_record():
    """A record is a diagnostic, not a claim that the slot is held.

    Getting this backwards would have a reader treat every slot the machine has
    ever used as permanently occupied.
    """
    with claim(want=1, floor=1, cores=18, command="earlier run"):
        pass
    assert [r["command"] for r in holders()] == ["earlier run"]
    os.environ.pop(LOCK_ENV, None)
    with claim(want=1, floor=1, cores=18) as granted:
        assert granted == 1


def test_holders_ignores_a_malformed_record():
    with claim(want=2, floor=1, cores=18, command="real"):
        pass
    (job_slots.slots_dir() / "slot-999.lock").write_text("{not json")
    assert all(r["command"] == "real" for r in holders())


def test_holders_is_empty_before_any_claim():
    assert holders() == []


def test_an_unwritable_state_root_still_yields_a_grant(monkeypatch, tmp_path):
    """No pool is a reason to size like a single run, not to refuse to test."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(blocker))
    with claim(want=7, floor=2, cores=18) as granted:
        assert granted == 7


def test_the_no_pool_path_still_sets_the_marker(monkeypatch, tmp_path):
    """The marker is how a re-execing caller knows the claim already happened.

    run-tests execs itself under the claim wrapper and skips that exec when the
    marker is set. Yielding without it — which the unwritable-state-root path
    did — has the child re-exec, and its child re-exec, unbounded, with nothing
    on screen. The grant alone does not close it: the guard reads the marker.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(blocker))
    with claim(want=3, floor=1, cores=18):
        assert os.environ.get(LOCK_ENV) == "3"
    assert LOCK_ENV not in os.environ


def test_a_run_with_no_pool_does_not_re_exec_itself(tmp_path):
    """End to end through the wrapper, because the loop is what it prevents.

    The child re-runs the wrapper the way run-tests does, guarded on the
    marker. An unset marker makes that recursion unbounded; the depth counter
    is what turns a hang into an assertion.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(blocker / "state"))
    depth = tmp_path / "depth"
    child = (
        f'echo x >> {depth}; '
        f'[ $(wc -l < {depth}) -gt 3 ] && exit 9; '
        f'[ -n "$WORKBENCH_TEST_SLOTS" ] && exit 0; '
        f'exec {CLI} --want 2 --cores 4 -- sh -c "$0" "$0"'
    )
    result = subprocess.run([str(CLI), "--want", "2", "--cores", "4", "--",
                             "sh", "-c", child, child],
                            capture_output=True, timeout=60, env=env)
    assert result.returncode == 0
    assert depth.read_text().count("x") == 1


def test_the_pool_leaves_a_core_for_the_rest_of_the_machine():
    assert pool_size(18) == 17


def test_the_pool_is_never_empty_on_a_one_core_machine():
    assert pool_size(1) == 1


def test_the_pool_is_not_capped_at_the_per_run_cap():
    """The two limits answer different questions.

    Capping the pool at the per-run cap would idle every core above it whenever
    a second run was up, since the first would be holding the entire pool.
    """
    assert pool_size(64) == 63


def _cli(*args, **kwargs):
    return subprocess.run([str(CLI), *args], capture_output=True, text=True,
                          timeout=60, **kwargs)


def test_the_cli_passes_the_grant_to_its_child():
    result = _cli("--want", "3", "--cores", "18", "--",
                  "sh", "-c", "echo $WORKBENCH_TEST_SLOTS_GRANTED")
    assert result.stdout.strip() == "3"


def test_the_fixture_leaves_no_marker_for_a_cli_child_to_inherit():
    """The nested case this suite actually runs in.

    Under `task test` or pre-push an outer run-tests has already exported its
    marker and grant into this process, and `_cli` passes no `env=`, so a child
    reads whatever `os.environ` holds. Were they still set, every CLI test here
    would take the pass-through branch, claim no slots, and assert on a number
    the pool never issued — green, and testing nothing.

    Asserted on this process's own environment rather than on a grant: a grant
    of 3 is what a correct claim and an inherited 3 both look like, and the
    wrapper legitimately sets the marker for its own child, so the child cannot
    answer the question either. What the fixture guarantees is that nothing is
    set *here*, which is what every `_cli` call forwards.
    """
    assert LOCK_ENV not in os.environ
    assert GRANT_ENV not in os.environ
    # And a claim taken from this state really does reach the pool, rather than
    # passing through and echoing a number nothing reserved.
    with claim(want=3, floor=1, cores=18):
        assert len(holders()) == 3


def test_the_cli_returns_the_childs_exit_status():
    """The status is the whole point of a wrapper the gate runs."""
    assert _cli("--want", "1", "--cores", "18", "--", "sh", "-c", "exit 7").returncode == 7


def test_the_cli_reports_a_command_it_cannot_run():
    """A missing child is a message, not a traceback.

    Every other failure on this surface reports itself as `job_slots_cli: ...`;
    an uncaught OSError here would be the one path that does not.
    """
    result = _cli("--want", "1", "--cores", "18", "--", "no-such-command-xyz")
    assert result.returncode == 127
    assert "cannot run no-such-command-xyz" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_failed_spawn_releases_its_slots_before_the_process_exits():
    """In-process, because across processes the kernel answers regardless.

    A first version of this ran two CLI subprocesses and asserted the second
    got a full grant — which no change to `_release` can falsify, since the
    first process had already exited and the kernel drops a dead holder's
    flocks either way. Observed inside one process, the release is the code's
    to get right: the pool is the size of the request, so a claim that failed
    to free its slots leaves the next one nothing.
    """
    with pytest.raises(OSError):
        with claim(want=3, floor=1, cores=4):
            raise OSError("a child that could not be spawned")
    with claim(want=3, floor=1, cores=4) as granted:
        assert granted == 3


def test_the_cli_reports_a_signalled_child_as_128_plus_the_signal(tmp_path):
    """The shell convention a caller of a wrapper script expects.

    Popen reports a signal death as a negative returncode, and `sys.exit(-N)`
    becomes `256-N` — so without the translation a SIGTERMed suite exits 241
    and the gate reads it as an ordinary non-zero. This is the most novel part
    of the wrapper and nothing else exercises it.
    """
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "state"))
    result = subprocess.run(
        [str(CLI), "--want", "1", "--cores", "18", "--",
         "sh", "-c", "kill -TERM $$"],
        capture_output=True, timeout=60, env=env,
    )
    assert result.returncode == 128 + signal.SIGTERM


def test_a_signal_to_the_wrapper_reaches_the_child(tmp_path):
    """The child runs in its own session, so it is not reached by group delivery.

    The forwarding handlers are the only thing that deliver a Ctrl-C or a
    terminal hangup to the suite. Without them the wrapper dies, the flocks
    drop, and the tests keep running outside the pool — which is the failure
    the handlers exist to prevent, and it is invisible from the outside.
    """
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "state"))
    started, caught = tmp_path / "started", tmp_path / "caught"
    proc = subprocess.Popen(
        [str(CLI), "--want", "1", "--cores", "18", "--", "sh", "-c",
         f"trap 'touch {caught}; exit 0' TERM; touch {started}; "
         f"while [ ! -f {caught} ]; do sleep 0.05; done"],
        env=env,
    )
    try:
        _await(started, "the child never started")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    assert caught.exists(), "the child never saw the signal sent to the wrapper"


def test_the_cli_rejects_a_flag_with_no_value():
    result = _cli("--want", "--", "sh", "-c", "true")
    assert result.returncode == 2
    assert "requires an argument" in result.stderr


def test_the_cli_rejects_a_missing_command():
    result = _cli("--want", "3", "--cores", "18", "--")
    assert result.returncode == 2
    assert "no command given" in result.stderr


def test_the_cli_does_not_claim_the_childs_flags_as_its_own():
    """`-- sh -c 'exit 7'` must not be parsed as options to the wrapper."""
    result = _cli("--want", "1", "--cores", "18", "--", "sh", "-c", "echo ok")
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_the_cli_show_reports_a_holder(tmp_path, monkeypatch):
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "state"))
    subprocess.run([str(CLI), "--want", "1", "--cores", "18", "--label", "a run",
                    "--", "sh", "-c", "true"], check=True, timeout=60, env=env)
    result = subprocess.run([str(CLI), "--show"], capture_output=True, text=True,
                            timeout=60, env=env)
    assert "a run" in result.stdout


def test_the_cli_show_names_an_unlabelled_holder(tmp_path):
    """A library claim writes an empty command, which a dict default never fills.

    `record.get("command", fallback)` returns the empty string rather than the
    fallback, so the column renders blank and the row reads as a corrupt record
    rather than an unlabelled one.
    """
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "state"))
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\n" % str(LIB_DIR) +
         "from core.job_slots import claim\n"
         "with claim(want=1, floor=1, cores=18):\n"
         "    pass\n"],
        check=True, timeout=60, env=env,
    )
    result = subprocess.run([str(CLI), "--show"], capture_output=True, text=True,
                            timeout=60, env=env)
    assert "unknown command" in result.stdout


def test_the_cli_show_is_quiet_with_no_pool(tmp_path):
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "empty"))
    result = subprocess.run([str(CLI), "--show"], capture_output=True, text=True,
                            timeout=60, env=env)
    assert result.returncode == 0
    assert "no record" in result.stdout


def test_two_cli_runs_at_once_share_the_pool(tmp_path):
    """End-to-end, across processes, which is where the flock actually matters.

    The first child holds its slots until its marker file appears and a second
    claim has been taken, so the two overlap for certain rather than by timing.
    """
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path / "state"))
    gate = tmp_path / "gate"
    started = tmp_path / "started"
    first = subprocess.Popen(
        [str(CLI), "--want", "4", "--cores", "6", "--", "sh", "-c",
         f"touch {started}; while [ ! -f {gate} ]; do sleep 0.05; done"],
        env=env,
    )
    try:
        _await(started, "the first claim never started")
        second = subprocess.run(
            [str(CLI), "--want", "4", "--cores", "6", "--", "sh", "-c",
             "echo $WORKBENCH_TEST_SLOTS_GRANTED"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert second.stdout.strip() == "1"
    finally:
        gate.touch()
        first.wait(timeout=60)


def _await(path: Path, message: str, seconds: float = 30) -> None:
    """Block until *path* appears, failing the test rather than hanging."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(message)
        time.sleep(0.02)


def test_a_record_survives_as_json():
    with claim(want=1, floor=1, cores=18, command="x"):
        text = (job_slots.slots_dir() / "slot-000.lock").read_text()
    assert json.loads(text)["slot"] == 0
