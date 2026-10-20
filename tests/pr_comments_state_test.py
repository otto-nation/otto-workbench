"""Tests for pr.comments_state — the review-thread ledger and its file."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.serde
import pr.comments
from pr.comments_state import (
    CommentsState, ThreadRecord, ThreadState, load_state, save_state,
)


def _recorded(**overrides) -> ThreadRecord:
    """A thread as a prior run recorded it."""
    return ThreadRecord(**{
        "state": ThreadState.ADDRESSED,
        "reviewer": "alice",
        "file": "handler.go",
        "line": 42,
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


def test_the_ledger_sits_under_the_run_target(tmp_path):
    """Not in the worktree. A target repo that does not gitignore ``ignore/``
    tracked this file, and it is scratch state rather than anything the repo
    should hold."""
    target = tmp_path / "state" / "pr" / "acme-widget-abc-main"

    path = pr.comments.threads_state_path(target)

    assert path == target / "pr-comments" / "state.json"
    assert "ignore" not in path.parts


def test_the_ledger_sits_beside_the_rest_of_the_pass(tmp_path):
    """One directory holds everything the comments pass produced, so the join
    has one owner and a sweep of the target reclaims the ledger with the
    artifacts rather than leaving it behind."""
    target = tmp_path / "target"

    assert pr.comments.threads_state_path(target).parent == \
        pr.comments.artifacts_dir(target)


def test_two_branches_in_one_worktree_get_separate_ledgers(tmp_path):
    """What keying on the target buys over keying on the worktree: the old path
    gave one file to whichever branch the worktree currently held."""
    first = pr.comments.threads_state_path(tmp_path / "acme-widget-main")
    second = pr.comments.threads_state_path(tmp_path / "acme-widget-feature")

    assert first != second


def test_load_state_missing_file(tmp_path):
    assert load_state(tmp_path / "state.json") is None


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = CommentsState(
        repo="otto-nation/maximum", pr_number=142, my_login="isaacg-otto",
        threads={"12345": _recorded()},
    )
    save_state(path, state)

    loaded = load_state(path)
    assert loaded.repo == "otto-nation/maximum"
    assert loaded.pr_number == 142
    assert loaded.my_login == "isaacg-otto"
    assert loaded.threads == {"12345": _recorded()}


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

    monkeypatch.setattr(core.serde.json, "dump", _explode)
    with pytest.raises(OSError):
        save_state(path, _state(pr_number=2))

    assert load_state(path).pr_number == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_written_shape_is_a_bare_id_to_record_mapping(tmp_path):
    """The ledger is typed in place, so the file a prior run wrote is the file
    this one writes. A wrapper around `threads` would have been a migration."""
    path = tmp_path / "state.json"
    save_state(path, _state(threads={"T_abc": _recorded()}))

    raw = json.loads(path.read_text())
    assert set(raw) == {"repo", "pr_number", "my_login", "last_run", "threads"}
    assert raw["threads"]["T_abc"]["reviewer"] == "alice"
    assert raw["threads"]["T_abc"]["state"] == "addressed"


# ── Recovery ───────────────────────────────────────────────────────────────


def _write(path: Path, threads: dict) -> None:
    """Write a state file's `threads` directly, bypassing `save_state`."""
    document = core.serde.to_dict(_state(last_run="2026-06-14T15:00:00Z"))
    path.write_text(json.dumps({**document, "threads": threads}))


def test_an_unreadable_thread_discards_the_ledger(tmp_path, capsys):
    """A cache: every field is re-fetched next run, so one bad entry costs the
    file rather than a per-entry recovery path."""
    path = tmp_path / "state.json"
    _write(path, {"T_good": core.serde.to_dict(_recorded()),
                  "T_bad": {"state": "half-fixed"}})

    assert load_state(path) is None
    assert "unreadable" in capsys.readouterr().err


@pytest.mark.parametrize("corrupt", [
    pytest.param("addressed", id="entry-is-not-an-object"),
    pytest.param([], id="entry-is-a-list"),
])
def test_a_malformed_thread_map_loads_empty(tmp_path, corrupt):
    """The shared reader drops a map holding a non-object entry. Nothing in it
    is irreplaceable, so the next run rebuilds every record from GitHub."""
    path = tmp_path / "state.json"
    _write(path, {"T_good": core.serde.to_dict(_recorded()), "T_bad": corrupt})

    assert load_state(path).threads == {}


def test_a_ledger_written_with_the_old_triage_fields_still_loads(tmp_path):
    """A file from before the fields were dropped is not corruption: the keys
    nothing reads any more are ignored."""
    path = tmp_path / "state.json"
    _write(path, {"T_abc": {
        **core.serde.to_dict(_recorded()), "classification": "suggestion",
        "summary": "Fix the handler", "decided_at": "2026-06-14T15:00:00Z",
        "last_seen_reply_id": 1001,
    }})

    assert load_state(path).threads["T_abc"] == _recorded()


def test_a_thread_missing_optional_fields_keeps_what_it_has(tmp_path):
    """An entry written before a field existed is not corruption — every field
    has a default, so the older shape loads as itself."""
    path = tmp_path / "state.json"
    _write(path, {"T_abc": {"state": "verified", "reviewer": "alice"}})

    assert load_state(path).threads["T_abc"] == ThreadRecord(
        state=ThreadState.VERIFIED, reviewer="alice")


def test_a_file_that_is_not_json_is_discarded(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text('{"threads": {"T_abc"')

    assert load_state(path) is None
    assert "unreadable" in capsys.readouterr().err
