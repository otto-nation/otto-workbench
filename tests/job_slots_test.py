"""Tests for the machine-wide test-parallelism slot pool.

The pool's whole claim is that a grant reflects what other runs are *holding*
rather than what the load average has got round to reporting, so most of these
assert against a second claim taken while the first is still open.
"""

import json
import os
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

CLI = REPO_ROOT / "bin" / "local" / "claim-job-slots"


@pytest.fixture(autouse=True)
def _pool_in_tmp(tmp_path, monkeypatch):
    """Point the state root at a scratch dir and drop any inherited marker.

    The state root has to move or a test would claim slots out of the pool the
    developer's own suite run is holding — and then report a grant that says
    more about the machine than about the code.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv(LOCK_ENV, raising=False)
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
        os.environ.pop(LOCK_ENV, None)  # a fresh run, not our own pass-through
        with claim(want=4, floor=1, cores=6) as second:
            assert second == 1


def test_slots_are_released_when_the_claim_exits():
    with claim(want=3, floor=1, cores=18):
        pass
    os.environ.pop(LOCK_ENV, None)
    with claim(want=3, floor=1, cores=18) as granted:
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


def test_the_grant_never_exceeds_what_was_asked_for():
    with claim(want=2, floor=1, cores=18) as granted:
        assert granted == 2


def test_a_nested_claim_passes_through_with_the_parents_grant():
    """run-tests re-execs itself under two wrappers; the inner must not re-claim.

    A second claim from inside the first would compete with slots this process
    already holds, and would report a smaller number than the run is using.
    """
    with claim(want=5, floor=1, cores=18) as outer:
        with claim(want=5, floor=1, cores=18) as inner:
            assert inner == outer == 5


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
    (job_slots.slots_dir() / "slot-99.lock").write_text("{not json")
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


def test_the_cli_returns_the_childs_exit_status():
    """The status is the whole point of a wrapper the gate runs."""
    assert _cli("--want", "1", "--cores", "18", "--", "sh", "-c", "exit 7").returncode == 7


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
        text = (job_slots.slots_dir() / "slot-00.lock").read_text()
    assert json.loads(text)["slot"] == 0
