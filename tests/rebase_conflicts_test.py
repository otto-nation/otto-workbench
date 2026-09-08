"""Tests for rebase.conflicts — classification, parsing, git-level resolution."""

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import regenerate as regen
from rebase import conflicts
from rebase import types as rebase_types


# ── parse_resolved_content ───────────────────────────────────────────────

class TestParseResolvedContent:
    def test_success(self):
        stdout = f"{conflicts.RESOLVE_BEGIN}\nresolved\n{conflicts.RESOLVE_END}"
        content, reason = conflicts.parse_resolved_content(stdout)
        assert content == "resolved\n"
        assert reason == ""

    def test_missing_both_markers(self):
        content, reason = conflicts.parse_resolved_content("just text")
        assert content is None
        assert reason is rebase_types.ParseFailure.MISSING_BOTH_MARKERS

    def test_missing_begin(self):
        content, reason = conflicts.parse_resolved_content(f"text\n{conflicts.RESOLVE_END}")
        assert content is None
        assert reason is rebase_types.ParseFailure.MISSING_BEGIN_MARKER

    def test_missing_end(self):
        content, reason = conflicts.parse_resolved_content(f"{conflicts.RESOLVE_BEGIN}\ntext")
        assert content is None
        assert reason is rebase_types.ParseFailure.MISSING_END_MARKER

    def test_surviving_conflict_markers(self):
        stdout = f"{conflicts.RESOLVE_BEGIN}\n<<<<<<< HEAD\n{conflicts.RESOLVE_END}"
        content, reason = conflicts.parse_resolved_content(stdout)
        assert content is None
        assert "surviving_conflict_marker" in reason


# ── extract_conflict_blocks ──────────────────────────────────────────────

class TestExtractConflictBlocks:
    def test_single_block(self):
        content = "before\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nafter\n"
        blocks = conflicts.extract_conflict_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].index == 1
        assert "<<<<<<< HEAD" in blocks[0].conflict

    def test_no_conflicts(self):
        assert conflicts.extract_conflict_blocks("clean file\n") == []


# ── should_chunk ─────────────────────────────────────────────────────────

class TestShouldChunk:
    def test_small_file_returns_false(self):
        content = "line\n" * 50
        block = rebase_types.ConflictBlock(
            index=1, start=10, end=15, conflict="x", context_before="", context_after="",
        )
        assert conflicts.should_chunk(content, [block]) is False

    def test_large_file_small_conflict(self):
        content = "line\n" * 500
        block = rebase_types.ConflictBlock(
            index=1, start=100, end=105, conflict="x", context_before="", context_after="",
        )
        assert conflicts.should_chunk(content, [block]) is True


# ── classify_conflict ────────────────────────────────────────────────────

class TestClassifyConflict:
    def test_delete_takes_priority(self, tmp_path):
        f = tmp_path / "old.go"
        f.write_text("content")
        with mock.patch.object(
            conflicts, "detect_delete_conflict",
            return_value=rebase_types.DeleteSide.THEIRS_DELETED,
        ):
            plan = conflicts.classify_conflict(
                "old.go", f, str(tmp_path),
                find_regenerator=regen.find_regenerator,
            )
        assert plan.strategy is rebase_types.ConflictStrategy.DELETE

    def test_text_file_ai_merge(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        with mock.patch.object(conflicts, "detect_delete_conflict", return_value=None), \
             mock.patch.object(conflicts, "is_generated_file", return_value=None):
            plan = conflicts.classify_conflict(
                "main.go", f, str(tmp_path),
                find_regenerator=regen.find_regenerator,
            )
        assert plan.strategy is rebase_types.ConflictStrategy.AI_MERGE


# ── splice_resolutions ───────────────────────────────────────────────────

class TestSpliceResolutions:
    def test_replaces_conflict_markers(self):
        content = "before\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nafter\n"
        blocks = conflicts.extract_conflict_blocks(content)
        result = conflicts.splice_resolutions(content, blocks, ["merged\n"])
        assert "merged" in result
        assert "<<<<<<< " not in result


# ── is_binary ────────────────────────────────────────────────────────────

class TestIsBinary:
    def test_binary_file(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\x00\x00")
        assert conflicts.is_binary(f) is True

    def test_text_file(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n")
        assert conflicts.is_binary(f) is False

    def test_missing_file(self):
        assert conflicts.is_binary(Path("/nonexistent/file.bin")) is False
