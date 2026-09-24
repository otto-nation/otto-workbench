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
            )
        assert plan.strategy is rebase_types.ConflictStrategy.DELETE

    def test_text_file_ai_merge(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        with mock.patch.object(conflicts, "detect_delete_conflict", return_value=None), \
             mock.patch.object(conflicts, "is_generated_file", return_value=None):
            plan = conflicts.classify_conflict(
                "main.go", f, str(tmp_path),
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


_CONFLICT = (
    "<<<<<<< HEAD\n"
    "    return a\n"
    "=======\n"
    "    return b\n"
    ">>>>>>> abc\n"
)
_AFTER = "\n# next thing\ndef other():\n    pass\n"
_BEFORE = "def f():\n    setup()\n"


def _ctx_block(before: str = _BEFORE, after: str = _AFTER):
    return rebase_types.ConflictBlock(
        index=1, start=0, end=4, conflict=_CONFLICT,
        context_before=before, context_after=after,
    )


class TestEchoedContextLines:
    """The chunked prompt's one instruction that nothing else enforces.

    A resolution that repeats the context it was shown parses, holds no
    conflict markers and splices cleanly — and then every echoed line exists
    twice, because `splice_resolutions` replaces only the marker region while
    the real context still follows it in the file.
    """

    def test_a_clean_resolution_echoes_nothing(self):
        assert conflicts.echoed_context_lines("    return a + b\n", _ctx_block()) == 0

    def test_a_trailing_blank_line_is_not_an_echo(self):
        """One shared line at the boundary is ordinary, so it stays allowed."""
        n = conflicts.echoed_context_lines("    return a + b\n\n", _ctx_block())
        assert n <= conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_shared_closing_brace_is_not_an_echo(self):
        block = _ctx_block(after="}\n\nint other(void) {\n")
        n = conflicts.echoed_context_lines("    x();\n}\n", block)
        assert n <= conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_run_of_blank_lines_is_not_an_echo(self):
        """Filler both sides produce independently, not content repeated back.

        Counting raw matched lines rejected this: a resolution ending in two
        blank lines where the context also opens with two scores a two-line
        overlap while nothing was echoed at all.
        """
        block = _ctx_block(after="\n\n# next\ndef g():\n")
        n = conflicts.echoed_context_lines("    return x\n\n\n", block)
        assert n <= conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_stacked_closing_braces_are_not_an_echo(self):
        """The C-like and Go shape: several bare closers in a row.

        Three of them overlap exactly where a nested block ends and the next
        declaration begins, which is ordinary code rather than a model
        repeating its context.
        """
        block = _ctx_block(after="}\n}\n}\nint main(void) {\n")
        n = conflicts.echoed_context_lines("    x();\n}\n}\n}\n", block)
        assert n <= conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_one_substantive_line_is_an_echo(self):
        """The budget is zero once filler is discounted.

        A line that says something, sitting outside the region being replaced
        and reproduced anyway, has no innocent explanation — so it does not need
        a second line beside it to be read as an echo.
        """
        block = _ctx_block(after="\n# next thing\ndef other():\n")
        n = conflicts.echoed_context_lines(
            "    return a + b\n\n# next thing\n", block,
        )
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_two_echoed_lines_are_caught(self):
        n = conflicts.echoed_context_lines(
            "    return a + b\n\n# next thing\n", _ctx_block(),
        )
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_the_whole_trailing_context_is_caught(self):
        n = conflicts.echoed_context_lines("    return a + b\n" + _AFTER, _ctx_block())
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_the_leading_context_is_caught_too(self):
        """A model can echo the context it was shown on either side."""
        n = conflicts.echoed_context_lines(
            _BEFORE + "    return a + b\n", _ctx_block(),
        )
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_an_echo_on_both_sides_at_once_is_caught(self):
        """Why `max` of the two sides is enough, rather than their sum.

        A resolution can echo the context at its head and its tail in the same
        answer. `max` reports the larger of the two rather than the total, which
        would matter if a single echoed line were tolerated — with the budget at
        zero, either side alone already fails, so the two can never combine into
        a verdict neither reaches.
        """
        block = rebase_types.ConflictBlock(
            index=1, start=0, end=4, conflict=_CONFLICT,
            context_before="import os\nCONST = 1\n",
            context_after="def tail():\n    pass\n",
        )
        n = conflicts.echoed_context_lines(
            "CONST = 1\n    merged\ndef tail():\n", block,
        )
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_line_the_conflict_owns_is_not_an_echo(self):
        """Ending on a line the conflict contained is the resolution's job.

        Counting it would reject a correct merge whenever the following context
        happens to open with a line the conflict also held.
        """
        block = _ctx_block(after="    return a\n    cleanup()\n")
        assert conflicts.echoed_context_lines("    return a\n", block) == 0


class TestParseChunkedRejectsEchoedContext:
    """The guard reached through the parser the resolver actually calls."""

    def _stdout(self, body: str, n: int = 1) -> str:
        return (
            f"{conflicts.RESOLVE_BEGIN}_{n}\n{body}"
            f"{conflicts.RESOLVE_END}_{n}\n"
        )

    def test_rejects_a_resolution_that_echoed_its_context(self):
        result, reason = conflicts.parse_chunked_resolutions(
            self._stdout("    return a + b\n" + _AFTER), [_ctx_block()],
        )
        assert result is None
        assert rebase_types.ParseFailure.ECHOED_CONTEXT in reason

    def test_accepts_the_same_resolution_without_the_echo(self):
        result, reason = conflicts.parse_chunked_resolutions(
            self._stdout("    return a + b\n"), [_ctx_block()],
        )
        assert reason == ""
        assert result == ["    return a + b\n"]

    def test_names_the_block_that_echoed(self):
        """Which block failed, so a retry's diagnosis is not a guess."""
        blocks = [
            rebase_types.ConflictBlock(
                index=1, start=0, end=4, conflict=_CONFLICT,
                context_before="", context_after="",
            ),
            _ctx_block(),
        ]
        stdout = (
            self._stdout("fine\n", 1)
            + self._stdout("    return a + b\n" + _AFTER, 2)
        )
        result, reason = conflicts.parse_chunked_resolutions(stdout, blocks)
        assert result is None
        assert "block_2" in reason


class TestTheDuplicatedFunctionRegression:
    """The shape of the rebase that landed a shell function defined twice.

    Reconstructed as a whole file so the damage is asserted where it was seen:
    not in the parser's return value, but in what `splice_resolutions` writes
    out. Both halves matter — that the guard rejects it, and that letting it
    through really does duplicate the function — because a guard asserted only
    against itself would pass with the splice bug fixed the other way.
    """

    HELPER = (
        "report_missing_xdist() {\n"
        '  warn "pytest has no xdist plugin" >&2\n'
        "}\n"
    )

    def _file(self) -> str:
        return (
            "run_pytest() {\n"
            "  local jobs_flag=()\n"
            "<<<<<<< HEAD\n"
            '    warn "no xdist" >&2\n'
            "=======\n"
            "    report_missing_xdist\n"
            ">>>>>>> abc123\n"
            "}\n"
            "\n"
            + self.HELPER
        )

    def test_the_echoing_resolution_is_rejected(self):
        content = self._file()
        block = conflicts.extract_conflict_blocks(content)[0]
        echoed = "    report_missing_xdist\n" + block.context_after

        n = conflicts.echoed_context_lines(echoed, block)
        assert n > conflicts.MAX_ECHOED_CONTEXT_LINES

    # passes-at-base: asserts the unchanged splice behaviour the guard exists to keep unreached
    def test_splicing_it_would_have_duplicated_the_function(self):
        """What the guard prevents, stated as the damage rather than a count."""
        content = self._file()
        block = conflicts.extract_conflict_blocks(content)[0]
        echoed = "    report_missing_xdist\n" + block.context_after

        spliced = conflicts.splice_resolutions(content, [block], [echoed])
        assert spliced.count("report_missing_xdist() {") == 2

        clean = conflicts.splice_resolutions(
            content, [block], ["    report_missing_xdist\n"],
        )
        assert clean.count("report_missing_xdist() {") == 1


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
