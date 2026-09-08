"""Tests for rebase.types — enums, dataclasses, and report payloads."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from rebase import types as rebase_types


class TestRunMode:
    """RunMode properties resolve correctly."""

    def test_fix_resolves_conflicts(self):
        assert rebase_types.RunMode.FIX.resolves_conflicts is True

    def test_fix_reaches_remote(self):
        assert rebase_types.RunMode.FIX.reaches_remote is True

    def test_fix_only_resolves_conflicts(self):
        assert rebase_types.RunMode.FIX_ONLY.resolves_conflicts is True

    def test_fix_only_does_not_reach_remote(self):
        assert rebase_types.RunMode.FIX_ONLY.reaches_remote is False

    def test_push_does_not_resolve_conflicts(self):
        assert rebase_types.RunMode.PUSH.resolves_conflicts is False

    def test_push_reaches_remote(self):
        assert rebase_types.RunMode.PUSH.reaches_remote is True

    def test_rebase_only_does_not_resolve_conflicts(self):
        assert rebase_types.RunMode.REBASE_ONLY.resolves_conflicts is False

    def test_rebase_only_does_not_reach_remote(self):
        assert rebase_types.RunMode.REBASE_ONLY.reaches_remote is False


class TestResolutionTally:
    """ResolutionTally.absorb accumulates files."""

    def test_absorb(self):
        tally = rebase_types.ResolutionTally()
        tally.absorb(rebase_types.Resolution(files=["a.py"], stale=["b.py"]))
        tally.absorb(rebase_types.Resolution(files=["c.py"]))
        assert tally.files == ["a.py", "c.py"]
        assert tally.stale == ["b.py"]


class TestRefDivergence:
    """RefDivergence properties read correctly."""

    def test_diverged(self):
        div = rebase_types.RefDivergence(ahead=1, behind=2, comparable=True)
        assert div.diverged is True

    def test_not_diverged_when_only_ahead(self):
        div = rebase_types.RefDivergence(ahead=3, behind=0, comparable=True)
        assert div.diverged is False

    def test_local_only_work(self):
        div = rebase_types.RefDivergence(ahead=1, behind=0, comparable=True)
        assert div.local_only_work is True

    def test_not_comparable(self):
        div = rebase_types.RefDivergence(ahead=1, behind=1, comparable=False)
        assert div.diverged is False
        assert div.local_only_work is False


class TestConflictBlock:
    """ConflictBlock.line_count is computed."""

    def test_line_count(self):
        block = rebase_types.ConflictBlock(
            index=0, start=10, end=20, conflict="...", context_before="", context_after="",
        )
        assert block.line_count == 11
