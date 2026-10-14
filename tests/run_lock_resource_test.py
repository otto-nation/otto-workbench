"""Tests for the marker-less Resource lock kind."""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.run_lock  # noqa: E402
from core.run_lock import LockBusy, LockReentry, Resource  # noqa: E402
from run_lock_support import held_elsewhere, live_pid  # noqa: E402


_CHILD = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import core.run_lock\n"
    "lock = core.run_lock.Resource(Path(sys.argv[2]), 'batch run x')\n"
    "try:\n"
    "    with core.run_lock.hold_resource(lock, command='child', started='t'):\n"
    "        raise SystemExit(11)\n"
    "except core.run_lock.LockBusy as exc:\n"
    "    raise SystemExit(0 if exc.kind == 'resource' else 12)\n"
)


def test_a_resource_appears_in_no_marker(tmp_path):
    lock_file = tmp_path / "run.lock"
    lock = Resource(lock_file, "batch run x")
    with core.run_lock.hold_resource(lock, command="pr batch run", started="t"):
        assert core.run_lock.LOCK_ENV not in os.environ
        assert core.run_lock.TREE_LOCK_ENV not in os.environ
        assert core.run_lock.GLOBAL_LOCK_ENV not in os.environ
        assert core.run_lock.holds(lock_file)
    assert not core.run_lock.holds(lock_file)


def test_a_child_cannot_pass_through_a_resource(tmp_path):
    """A resource has no marker, so even a child that inherits LOCK_ENV is refused."""
    lock_file = tmp_path / "run.lock"
    lock = Resource(lock_file, "batch run x")
    with core.run_lock.hold_resource(lock, command="parent", started="t"):
        env = os.environ.copy()
        env[core.run_lock.LOCK_ENV] = str(lock_file)
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, str(LIB_DIR), str(lock_file)],
            env=env, capture_output=True, text=True,
        )
    assert proc.returncode == 0, proc.stderr


def test_fields_reach_the_record(tmp_path):
    lock_file = tmp_path / "run" / "run.lock"
    lock = Resource(lock_file, "batch run x")
    with core.run_lock.hold_resource(
            lock, command="pr batch run", started="t0",
            fields={"role": "driver", "run": "id"}, create=True):
        record = core.run_lock.read_holder(lock_file)
    assert record["role"] == "driver"
    assert record["run"] == "id"
    assert record["command"] == "pr batch run"
    assert record["started"] == "t0"
    assert record["pid"] == os.getpid()
    assert record["released"] is None


def test_create_false_refuses_a_missing_parent(tmp_path):
    parent = tmp_path / "gone"
    lock_file = parent / "run.lock"
    lock = Resource(lock_file, "batch run x")
    with pytest.raises(FileNotFoundError):
        with core.run_lock.hold_resource(lock, command="x", started="t", create=False):
            pytest.fail("a missing parent must not be created")
    assert not parent.exists()


def test_holds_reads_the_registry_only(tmp_path):
    lock_file = tmp_path / "run.lock"
    with live_pid() as pid, \
         held_elsewhere(lock_file, {
             "pid": pid, "command": "other", "started": "t", "released": None,
         }):
        assert not core.run_lock.holds(lock_file)
        assert core.run_lock.is_held(tmp_path)
    lock = Resource(lock_file, "batch run x")
    with core.run_lock.hold_resource(lock, command="x", started="t"):
        assert core.run_lock.holds(lock_file)
        assert core.run_lock.holds(Path(str(lock_file)))
    assert not core.run_lock.holds(lock_file)


def test_a_refused_resource_names_the_subject_and_kind(tmp_path):
    lock_file = tmp_path / "run.lock"
    lock = Resource(lock_file, "batch run abc")
    with live_pid() as pid, \
         held_elsewhere(lock_file, {
             "pid": pid, "command": "pr batch run", "started": "t0", "released": None,
         }), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold_resource(lock, command="pr batch resume", started="t"):
            pytest.fail("a held resource must refuse the block")
    assert exc.value.kind == "resource"
    assert "owns this batch run abc: pr batch run" in str(exc.value)
    assert f"pid {pid}" in str(exc.value)


def test_a_resource_refuses_reentry_in_the_same_process(tmp_path):
    """Two holds of one run in one process would be two Run objects; the second must raise."""
    lock = Resource(tmp_path / "run.lock", "batch run x")
    with core.run_lock.hold_resource(lock, command="outer", started="t"), \
         pytest.raises(LockReentry, match="batch run x is already held by this process"):
        with core.run_lock.hold_resource(lock, command="inner", started="t"):
            pytest.fail("a resource must not nest")


def test_a_refused_reentry_leaves_the_first_hold_in_place(tmp_path):
    lock_file = tmp_path / "run.lock"
    lock = Resource(lock_file, "batch run x")
    with core.run_lock.hold_resource(lock, command="outer", started="t"):
        with pytest.raises(LockReentry):
            core.run_lock.hold_resource(lock, command="inner", started="t").__enter__()
        assert core.run_lock.holds(lock_file)
        assert core.run_lock.read_holder(lock_file)["command"] == "outer"
        assert core.run_lock._HELD[str(lock_file)].depth == 1
    assert not core.run_lock.holds(lock_file)
    assert core.run_lock.read_holder(lock_file)["released"] is not None
