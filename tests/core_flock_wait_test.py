"""Timed exclusive flock: deadline, release, and a parent that cannot be created."""

import fcntl
import os
import threading

import core.flock_wait

_JOIN = 30.0


def test_acquire_returns_false_after_deadline_while_another_fd_holds(tmp_path):
    path = tmp_path / "lock"
    path.touch()
    holder = os.open(path, os.O_RDWR)
    waiter = os.open(path, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        assert core.flock_wait.acquire(waiter, 0.2) is False
    finally:
        os.close(holder)
        os.close(waiter)


def test_acquire_returns_true_after_release(tmp_path):
    path = tmp_path / "lock"
    path.touch()
    holder = os.open(path, os.O_RDWR)
    waiter = os.open(path, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    result: list[bool] = []

    def wait():
        result.append(core.flock_wait.acquire(waiter, _JOIN))

    thread = threading.Thread(target=wait)
    thread.start()
    thread.join(0.2)
    assert thread.is_alive()
    fcntl.flock(holder, fcntl.LOCK_UN)
    thread.join(_JOIN)
    os.close(waiter)
    os.close(holder)
    assert result == [True]


def test_held_yields_false_when_parent_cannot_be_created(tmp_path):
    parent = tmp_path / "not-a-dir"
    parent.write_text("file")
    path = parent / "child.lock"
    with core.flock_wait.held(path, wait_s=1.0) as locked:
        assert locked is False
