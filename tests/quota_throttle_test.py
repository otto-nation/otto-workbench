"""Tests for the machine-wide quota backoff.

The throttle's whole claim is that a 429 in one process holds every other
process back, so most of these assert against a second instance that does not
share the first's object — and one asserts across a subprocess, which is
where the flock actually matters.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from core.quota_throttle import QuotaThrottle, throttle_path


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    """Point the state root at a scratch dir so tests never touch the real one."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    yield


def _record():
    return json.loads(throttle_path().read_text())


def _write_record(**fields):
    throttle_path().write_text(json.dumps(fields))


def _await(path: Path, message: str, seconds: float = 5) -> None:
    """Block until *path* appears, failing the test rather than hanging."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(message)
        time.sleep(0.02)


def test_report_exhausted_writes_resume_at_as_an_epoch():
    before = time.time()
    QuotaThrottle().report_exhausted("sonnet")
    after = time.time()
    resume_at = _record()["resume_at"]
    # Wall-clock epoch, not a monotonic-ish ~30.
    assert resume_at > 1_000_000_000
    assert before + 30 - 1 <= resume_at <= after + 30 + 1


def test_a_second_instance_sees_the_firsts_resume_at(monkeypatch):
    QuotaThrottle().report_exhausted("sonnet")
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    QuotaThrottle().wait_if_needed()
    assert slept
    assert slept[0] == pytest.approx(30, abs=2)


def _sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    return slept


def test_a_429_on_one_provider_holds_back_only_that_provider(monkeypatch):
    """A quota is one provider's fact: grok throttling must not stall Claude agents."""
    QuotaThrottle().report_exhausted("grok-4.6", "xai")
    slept = _sleeps(monkeypatch)

    QuotaThrottle().wait_if_needed("google-vertex-claude")
    QuotaThrottle().wait_if_needed()
    assert slept == []

    QuotaThrottle().wait_if_needed("xai")
    assert slept[0] == pytest.approx(30, abs=2)


def test_the_default_providers_backoff_does_not_hold_back_a_named_one(monkeypatch):
    QuotaThrottle().report_exhausted("claude-sonnet-5")
    slept = _sleeps(monkeypatch)
    QuotaThrottle().wait_if_needed("xai")
    assert slept == []
    QuotaThrottle().wait_if_needed()
    assert slept


def test_each_provider_climbs_its_own_ladder():
    assert QuotaThrottle().report_exhausted("grok-4.6", "xai") == 30
    assert QuotaThrottle().report_exhausted("grok-4.6", "xai") == 60
    assert QuotaThrottle().report_exhausted("claude-sonnet-5", "google-vertex-claude") == 30
    assert json.loads(throttle_path("xai").read_text())["backoff"] == 120
    assert json.loads(throttle_path("google-vertex-claude").read_text())["backoff"] == 60


def test_one_instance_keeps_a_ladder_per_provider():
    """A review shares one throttle across providers; the in-memory ladder must split too."""
    throttle = QuotaThrottle()
    assert throttle.report_exhausted("grok-4.6", "xai") == 30
    assert throttle.report_exhausted("claude-sonnet-5") == 30
    assert throttle.resume_at("xai") > 0
    assert throttle.resume_at("google-vertex-claude") == 0


def test_the_record_names_the_provider_it_throttles():
    QuotaThrottle().report_exhausted("grok-4.6", "xai")
    assert json.loads(throttle_path("xai").read_text())["provider"] == "xai"


def test_a_provider_cannot_name_a_file_outside_the_state_dir(tmp_path):
    path = throttle_path("../../etc/x")
    assert path.parent == tmp_path
    assert "/" not in path.name


def test_colliding_provider_ids_get_distinct_files(tmp_path):
    assert throttle_path("a/b") != throttle_path("a_b")
    assert len(throttle_path("x" * 500).name) < 100


def test_a_second_exhaustion_doubles():
    assert QuotaThrottle().report_exhausted("sonnet") == 30
    assert QuotaThrottle().report_exhausted("sonnet") == 60
    assert _record()["backoff"] == 120


def test_concurrent_writers_do_not_both_write_the_initial_backoff():
    """Two writers must serialise the doubling, not both store the first step.

    Without a blocking flock the second writer skips the disk update on
    contention and the file is left at backoff 60 — the collision this module
    exists to prevent.
    """
    waits = []

    def worker():
        waits.append(QuotaThrottle().report_exhausted("sonnet"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    path = throttle_path()
    raw = path.read_text() if path.exists() else ""
    data = json.loads(raw) if raw.strip() else {}
    assert data.get("backoff") == 120
    assert sorted(waits) == [30, 60]


def test_wait_if_needed_does_not_block_on_a_held_flock(tmp_path):
    path = throttle_path()
    path.write_text("{}")
    started = tmp_path / "started"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time\n"
         "from pathlib import Path\n"
         "handle = open(sys.argv[1], 'a+')\n"
         "fcntl.flock(handle, fcntl.LOCK_EX)\n"
         "Path(sys.argv[2]).touch()\n"
         "time.sleep(2)\n",
         str(path), str(started)],
    )
    try:
        _await(started, "the child never took LOCK_EX")
        t0 = time.monotonic()
        QuotaThrottle().wait_if_needed()
        assert time.monotonic() - t0 < 1
    finally:
        child.wait(timeout=10)


def test_an_unwritable_state_dir_still_backs_off_in_memory(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.write_text("not-a-directory")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(locked))
    throttle = QuotaThrottle()
    wait = throttle.report_exhausted("sonnet")
    assert wait == 30
    assert throttle.resume_at() > 0


def test_malformed_json_is_fail_open(monkeypatch):
    throttle_path().write_text("{")
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    QuotaThrottle().wait_if_needed()
    assert slept == []


def test_a_far_future_resume_at_is_clamped_to_max_backoff(monkeypatch):
    now = time.time()
    _write_record(
        resume_at=now + 99_999, backoff=120, updated_at=now, pid=1, model="sonnet",
    )
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    QuotaThrottle().wait_if_needed()
    assert slept == [120]


def test_the_idle_ladder_resets_after_max_backoff():
    now = time.time()
    _write_record(
        resume_at=now, backoff=120, updated_at=now - 121, pid=1, model="sonnet",
    )
    assert QuotaThrottle().report_exhausted("sonnet") == 30


def test_a_retry_within_max_backoff_does_not_reset():
    now = time.time()
    _write_record(
        resume_at=now + 30, backoff=60, updated_at=now - 30, pid=1, model="sonnet",
    )
    assert QuotaThrottle().report_exhausted("sonnet") == 60


def test_a_subprocess_exhaustion_holds_the_parent_back(tmp_path, monkeypatch):
    """End-to-end, across processes, which is where the flock actually matters."""
    env = dict(os.environ, WORKBENCH_STATE_DIR=str(tmp_path))
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\n" % str(LIB_DIR)
         + "from core.quota_throttle import QuotaThrottle\n"
         "QuotaThrottle().report_exhausted('sonnet')\n"],
        check=True, timeout=60, env=env,
    )
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    QuotaThrottle().wait_if_needed()
    assert slept
    assert slept[0] == pytest.approx(30, abs=2)
