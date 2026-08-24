"""Tests for pr_comments_state — the review-thread ledger and its file."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import serde
from pr_comments_state import (
    CommentsState, ThreadRecord, ThreadState, load_state, save_state,
)


def _decided(**overrides) -> ThreadRecord:
    """A thread carrying triage nothing can re-derive."""
    return ThreadRecord(**{
        "state": ThreadState.ADDRESSED,
        "reviewer": "alice",
        "last_seen_reply_id": 1001,
        "file": "handler.go",
        "line": 42,
        "classification": "suggestion",
        "summary": "Fix the handler",
        "decided_at": "2026-06-14T15:00:00Z",
        **overrides,
    })


def _state(**overrides) -> CommentsState:
    """A ledger with the identity fields already filled in.

    Every test below is about the file or the threads in it, not about which PR
    it belongs to — except the round-trip, which names its own values because
    they are what it asserts.
    """
    return CommentsState(
        **{"repo": "owner/repo", "pr_number": 1, "my_login": "me", **overrides})


# ── The file ───────────────────────────────────────────────────────────────


def test_load_state_missing_file(tmp_path):
    assert load_state(tmp_path / "state.json") is None


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = CommentsState(
        repo="otto-nation/maximum", pr_number=142, my_login="isaacg-otto",
        threads={"12345": _decided()},
    )
    save_state(path, state)

    loaded = load_state(path)
    assert loaded.repo == "otto-nation/maximum"
    assert loaded.pr_number == 142
    assert loaded.my_login == "isaacg-otto"
    assert loaded.threads == {"12345": _decided()}


def test_save_stamps_the_run_without_mutating_the_caller(tmp_path):
    """`last_run` is the writer's to set, and the caller's state is not the
    writer's to edit — a frozen record handed to two writes must read the same
    both times."""
    path = tmp_path / "state.json"
    state = _state()
    save_state(path, state)

    assert state.last_run == ""
    assert load_state(path).last_run


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    save_state(path, _state())
    assert path.is_file()


def test_save_never_exposes_a_truncated_file(tmp_path, monkeypatch):
    """Regression: this save was the one copy of the write-and-rename pattern
    that had drifted into a plain `open(path, "w")`, which truncates the target
    before the first byte lands. A failed write left the thread lifecycle state
    half-written — the corruption the read side then has to discard."""
    path = tmp_path / "state.json"
    save_state(path, _state())

    def _explode(obj, fp, **kwargs):
        fp.write('{"partial":')
        raise OSError("disk full")

    monkeypatch.setattr(serde.json, "dump", _explode)
    with pytest.raises(OSError):
        save_state(path, _state(pr_number=2))

    assert load_state(path).pr_number == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_written_shape_is_a_bare_id_to_record_mapping(tmp_path):
    """The ledger is typed in place, so the file a prior run wrote is the file
    this one writes. A wrapper around `threads` would have been a migration."""
    path = tmp_path / "state.json"
    save_state(path, _state(threads={"T_abc": _decided()}))

    raw = json.loads(path.read_text())
    assert set(raw) == {"repo", "pr_number", "my_login", "last_run", "threads"}
    assert raw["threads"]["T_abc"]["classification"] == "suggestion"
    assert raw["threads"]["T_abc"]["state"] == "addressed"


# ── Per-thread recovery ────────────────────────────────────────────────────


def _write(path: Path, threads: dict) -> None:
    """Write a state file's `threads` directly, bypassing `save_state`."""
    document = serde.to_dict(_state(last_run="2026-06-14T15:00:00Z"))
    path.write_text(json.dumps({**document, "threads": threads}))


@pytest.mark.parametrize("corrupt", [
    pytest.param({"state": "half-fixed"}, id="unknown-lifecycle-state"),
    pytest.param("addressed", id="entry-is-not-an-object"),
    pytest.param([], id="entry-is-a-list"),
    pytest.param(None, id="entry-is-null"),
])
def test_one_unreadable_thread_does_not_discard_the_others(tmp_path, corrupt):
    """The reason this file does not use `serde.load_file`'s own recovery.
    Discarding the whole ledger would re-triage every thread on the PR to
    recover from one entry, and triage is the part no API replays."""
    path = tmp_path / "state.json"
    _write(path, {"T_good": serde.to_dict(_decided()), "T_bad": corrupt})

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.threads["T_good"] == _decided()
    assert loaded.threads["T_bad"] == ThreadRecord()


@pytest.mark.parametrize("corrupt,expected", [
    pytest.param({"state": "half-fixed"}, "unreadable thread entry", id="unreadable"),
    pytest.param(None, "thread entry is null", id="null"),
])
def test_an_unreadable_thread_says_so(tmp_path, capsys, corrupt, expected):
    """A run that quietly re-asks about a thread the operator already decided
    is indistinguishable from one that had nothing cached.

    A null needs its own line because `serde.from_dict` reads one as "every
    field omitted" and would rebuild the blank record without a word."""
    path = tmp_path / "state.json"
    _write(path, {"T_bad": corrupt})

    load_state(path)
    assert expected in capsys.readouterr().err


def test_a_thread_missing_optional_fields_keeps_what_it_has(tmp_path):
    """An entry written before a field existed is not corruption — every field
    has a default, so the older shape loads as itself."""
    path = tmp_path / "state.json"
    _write(path, {"T_abc": {"state": "verified", "reviewer": "alice"}})

    assert load_state(path).threads["T_abc"] == ThreadRecord(
        state=ThreadState.VERIFIED, reviewer="alice")


def test_a_file_that_is_not_json_is_still_discarded_whole(tmp_path, capsys):
    """Per-entry recovery reaches entries, not the document holding them."""
    path = tmp_path / "state.json"
    path.write_text('{"threads": {"T_abc"')

    assert load_state(path) is None
    assert "unreadable" in capsys.readouterr().err
