"""Tests for pr_comments library."""

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_comments import load_state, save_state, empty_state


def test_empty_state_has_required_fields():
    state = empty_state("otto-nation/maximum", 142, "isaacg-otto")
    assert state["repo"] == "otto-nation/maximum"
    assert state["pr_number"] == 142
    assert state["my_login"] == "isaacg-otto"
    assert state["threads"] == {}
    assert "last_run" in state


def test_load_state_missing_file():
    state = load_state(Path("/nonexistent/state.json"))
    assert state is None


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        state = empty_state("otto-nation/maximum", 142, "isaacg-otto")
        state["threads"]["12345"] = {
            "state": "new",
            "classification": None,
            "reviewer": "alice",
            "file": "handler.go",
            "line": 42,
            "summary": None,
            "decided_at": None,
            "last_seen_reply_id": None,
        }
        save_state(path, state)
        loaded = load_state(path)
        assert loaded == state


def test_save_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "dir" / "state.json"
        state = empty_state("repo", 1, "user")
        save_state(path, state)
        assert path.exists()
