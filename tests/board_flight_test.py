"""Single-flight: a concurrent `pr board` serves the leader instead of rebuilding."""

import fcntl
import json
import os
import threading

import board.flight
import core.workbench_paths
from board.model import Phase

_JOIN = 30.0


def _req(**overrides) -> board.flight.Request:
    fields = dict(
        phase=Phase.LISTING,
        checkouts=("/x",),
        prs=None,
        offline=False,
        if_changed=None,
    )
    fields.update(overrides)
    return board.flight.Request(**fields)


def test_waiter_serves_the_leaders_answer_without_building():
    entered = threading.Event()
    release = threading.Event()
    req = _req()
    leader_doc = {"answer": "A"}
    waiter_calls: list[int] = []
    leader_result: list[dict] = []
    waiter_result: list[dict] = []

    def leader_build():
        entered.set()
        assert release.wait(_JOIN)
        return leader_doc

    def waiter_build():
        waiter_calls.append(1)
        return {"answer": "B"}

    leader = threading.Thread(
        target=lambda: leader_result.append(board.flight.serve(req, leader_build)),
    )
    leader.start()
    assert entered.wait(_JOIN)

    waiter = threading.Thread(
        target=lambda: waiter_result.append(board.flight.serve(req, waiter_build)),
    )
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive()
    release.set()
    waiter.join(_JOIN)
    leader.join(_JOIN)
    assert waiter_result == [leader_doc]
    assert waiter_calls == []


def test_a_memo_older_than_the_wait_is_rebuilt():
    req = _req()
    board.flight.serve(req, lambda: {"v": "old"})
    path = board.flight.memo_path(req)
    data = json.loads(path.read_text())
    data["at"] = 0
    path.write_text(json.dumps(data))
    calls: list[int] = []

    def build():
        calls.append(1)
        return {"v": "new"}

    assert board.flight.serve(req, build) == {"v": "new"}
    assert calls == [1]


def test_a_different_if_changed_recomputes_after_waiting():
    entered = threading.Event()
    release = threading.Event()
    leader_req = _req(if_changed="old")
    waiter_req = _req(if_changed="new")
    waiter_doc = {"answer": "B"}
    waiter_calls: list[int] = []
    waiter_result: list[dict] = []

    def leader_build():
        entered.set()
        assert release.wait(_JOIN)
        return {"answer": "A"}

    def waiter_build():
        waiter_calls.append(1)
        return waiter_doc

    leader = threading.Thread(
        target=lambda: board.flight.serve(leader_req, leader_build),
    )
    leader.start()
    assert entered.wait(_JOIN)
    waiter = threading.Thread(
        target=lambda: waiter_result.append(
            board.flight.serve(waiter_req, waiter_build),
        ),
    )
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive()
    release.set()
    waiter.join(_JOIN)
    leader.join(_JOIN)
    assert waiter_result == [waiter_doc]
    assert waiter_calls == [1]


def test_a_wait_that_runs_out_builds_unlocked(monkeypatch):
    req = _req()
    path = board.flight.lock_path(req)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    calls: list[int] = []

    def build():
        calls.append(1)
        return {"ok": True}

    monkeypatch.setattr(board.flight, "_WAIT_S", 0.2)
    try:
        assert board.flight.serve(req, build) == {"ok": True}
    finally:
        os.close(fd)
    assert calls == [1]


def test_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    abs_path = str(repo)
    slashed = abs_path + "/"
    relative = os.path.relpath(repo)
    a = _req(checkouts=(abs_path,))
    b = _req(checkouts=(slashed,), prs=(1, 2), if_changed="stamp")
    c = _req(checkouts=(relative,), if_changed="other")
    key = board.flight.lock_key(a)
    assert key == board.flight.lock_key(b) == board.flight.lock_key(c)
    state = core.workbench_paths.state_dir()
    cache = core.workbench_paths.cache_dir("board")
    assert board.flight.lock_path(a) == state / "board-flight" / f"{key}.lock"
    assert board.flight.memo_path(a) == cache / "flight" / f"{key}.json"
