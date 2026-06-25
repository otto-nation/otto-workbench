"""Tests for otto-log query CLI."""

import json
import sys
import tempfile
from pathlib import Path
import importlib.machinery
import importlib.util

BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "claude" / "bin"
LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(BIN_DIR))

from trail import TRAIL_FILENAME, Trail

_spec = importlib.util.spec_from_loader(
    "otto_log",
    importlib.machinery.SourceFileLoader("otto_log", str(BIN_DIR / "otto-log")),
)
otto_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(otto_log)


def _make_trail(d: str, script: str, events: list[tuple[str, str]]) -> str:
    """Write a trail with the given action/detail pairs, return invocation ID."""
    trail = Trail.start(script=script, artifact_dir=d, context={"repo": "org/repo", "pr": 42})
    for action, detail in events:
        trail.info(action, detail)
    trail.finish()
    return trail.invocation


class TestTrailDiscovery:
    def test_discover_worktree_trail(self):
        with tempfile.TemporaryDirectory() as d:
            wb_dir = Path(d) / ".workbench"
            wb_dir.mkdir()
            _make_trail(str(wb_dir), "ci-check", [("fetch", "fetched")])
            trails = otto_log.discover_trails(worktree_root=d)
            assert len(trails) >= 1
            assert any(str(wb_dir / TRAIL_FILENAME) in str(t) for t in trails)

    def test_discover_review_trails(self):
        with tempfile.TemporaryDirectory() as d:
            review_dir = Path(d) / "reviews" / "repo-42"
            review_dir.mkdir(parents=True)
            _make_trail(str(review_dir), "claude-review", [("review", "reviewed")])
            trails = otto_log.discover_trails(reviews_dir=str(Path(d) / "reviews"))
            assert len(trails) >= 1


class TestQueryFiltering:
    def test_filter_by_script(self):
        with tempfile.TemporaryDirectory() as d:
            _make_trail(d, "ci-check", [("a", "first")])
            _make_trail(d, "pr-rebase", [("b", "second")])
            events = otto_log.load_events([str(Path(d) / TRAIL_FILENAME)])
            filtered = otto_log.filter_events(events, script="ci-check")
            assert all(e["script"] == "ci-check" for e in filtered)

    def test_filter_by_level(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.info("ok", "fine")
            trail.error("bad", "broken")
            trail.finish()
            events = otto_log.load_events([str(Path(d) / TRAIL_FILENAME)])
            filtered = otto_log.filter_events(events, level="error")
            assert all(e["level"] == "error" for e in filtered)

    def test_filter_by_invocation(self):
        with tempfile.TemporaryDirectory() as d:
            inv1 = _make_trail(d, "test", [("a", "first")])
            _make_trail(d, "test", [("b", "second")])
            events = otto_log.load_events([str(Path(d) / TRAIL_FILENAME)])
            filtered = otto_log.filter_events(events, invocation=inv1)
            assert all(e["invocation"] == inv1 for e in filtered)
