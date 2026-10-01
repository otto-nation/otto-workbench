"""Tests for rebase.conflicts — classification, parsing, git-level resolution."""

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.regenerate
import rebase.conflicts
import rebase.types


# ── parse_resolved_content ───────────────────────────────────────────────

class TestParseResolvedContent:
    def test_success(self):
        stdout = f"{rebase.conflicts.RESOLVE_BEGIN}\nresolved\n{rebase.conflicts.RESOLVE_END}"
        content, reason = rebase.conflicts.parse_resolved_content(stdout)
        assert content == "resolved\n"
        assert reason == ""

    def test_missing_both_markers(self):
        content, reason = rebase.conflicts.parse_resolved_content("just text")
        assert content is None
        assert reason is rebase.types.ParseFailure.MISSING_BOTH_MARKERS

    def test_missing_begin(self):
        content, reason = rebase.conflicts.parse_resolved_content(f"text\n{rebase.conflicts.RESOLVE_END}")
        assert content is None
        assert reason is rebase.types.ParseFailure.MISSING_BEGIN_MARKER

    def test_missing_end(self):
        content, reason = rebase.conflicts.parse_resolved_content(f"{rebase.conflicts.RESOLVE_BEGIN}\ntext")
        assert content is None
        assert reason is rebase.types.ParseFailure.MISSING_END_MARKER

    def test_surviving_conflict_markers(self):
        stdout = f"{rebase.conflicts.RESOLVE_BEGIN}\n<<<<<<< HEAD\n{rebase.conflicts.RESOLVE_END}"
        content, reason = rebase.conflicts.parse_resolved_content(stdout)
        assert content is None
        assert "surviving_conflict_marker" in reason


# ── extract_conflict_blocks ──────────────────────────────────────────────

class TestExtractConflictBlocks:
    def test_single_block(self):
        content = "before\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nafter\n"
        blocks = rebase.conflicts.extract_conflict_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].index == 1
        assert "<<<<<<< HEAD" in blocks[0].conflict

    def test_no_conflicts(self):
        assert rebase.conflicts.extract_conflict_blocks("clean file\n") == []

    # passes-at-base: pins the scan behaviour the rewritten extractor must keep
    def test_an_unclosed_opener_does_not_hide_the_conflicts_below_it(self):
        content = (
            'marker = "<<<<<<< not really"\n'
            "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n"
        )
        blocks = rebase.conflicts.extract_conflict_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].start == 1


class TestContextStopsAtTheNeighbouringConflict:
    """Root cause of the two failures that ended a rebase of this repo.

    Context was taken as a fixed offset from the raw conflicted file, so in any
    file whose hunks sit closer together than the context width — seven hunks
    in one file, which is the ordinary case — block 1's `context_after` held
    blocks 2 and 3 verbatim, markers and all. The prompt then said "output
    everything from <<<<<<< through >>>>>>>" over an envelope containing three
    conflicts, and got back an answer for the envelope or three conflicts
    collapsed into one. Neither was the model's mistake.
    """

    def _packed(self, n: int, gap: int = 2) -> str:
        """*n* conflicts separated by *gap* lines — far inside the context width."""
        between = "".join(f"filler {i}\n" for i in range(gap))
        one = "<<<<<<< HEAD\nours {i}\n=======\ntheirs {i}\n>>>>>>> abc\n"
        return "lead\n" + between.join(one.format(i=i) for i in range(n)) + "tail\n"

    def test_no_block_context_contains_a_conflict_marker(self):
        blocks = rebase.conflicts.extract_conflict_blocks(self._packed(7))
        assert len(blocks) == 7
        for block in blocks:
            assert rebase.conflicts.has_conflict_markers(block.context_before) is None
            assert rebase.conflicts.has_conflict_markers(block.context_after) is None
            assert "=======" not in block.context_before
            assert "=======" not in block.context_after

    def test_the_gap_between_two_conflicts_is_all_the_context_there_is(self):
        blocks = rebase.conflicts.extract_conflict_blocks(self._packed(2, gap=3))
        assert blocks[0].context_after == "filler 0\nfiller 1\nfiller 2\n"
        assert blocks[1].context_before == "filler 0\nfiller 1\nfiller 2\n"

    # passes-at-base: the clamp is a ceiling, so the ordinary case is unchanged by design
    def test_conflicts_further_apart_than_the_window_still_get_full_context(self):
        """The clamp is a ceiling, not a replacement for the context width."""
        blocks = rebase.conflicts.extract_conflict_blocks(
            self._packed(2, gap=100), context_lines=5,
        )
        assert len(blocks[0].context_after.splitlines()) == 5
        assert len(blocks[1].context_before.splitlines()) == 5

    # passes-at-base: the outer bounds were already right; the clamp must not move them
    def test_the_file_edges_still_bound_the_first_and_last(self):
        blocks = rebase.conflicts.extract_conflict_blocks(self._packed(2))
        assert blocks[0].context_before == "lead\n"
        assert blocks[-1].context_after == "tail\n"


class TestTheSeparatorIsNotEvidenceOnItsOwn:
    """`=======` alone on a line is also a Markdown setext H1 underline.

    Treating it as a surviving conflict marker rejected correct resolutions of
    `.md` conflicts whose own content happened to underline a seven-character
    heading.
    """

    def test_a_setext_underline_is_not_a_conflict_marker(self):
        assert rebase.conflicts.has_conflict_markers("Heading\n=======\n\nbody\n") is None

    def test_a_markdown_resolution_with_a_setext_heading_parses(self):
        body = "Release\n=======\n\nNotes.\n"
        stdout = f"{rebase.conflicts.RESOLVE_BEGIN}\n{body}{rebase.conflicts.RESOLVE_END}"
        content, reason = rebase.conflicts.parse_resolved_content(stdout)
        assert reason == ""
        assert content == body

    # passes-at-base: what dropping the separator must not cost, so it has to hold both sides
    def test_a_real_surviving_conflict_is_still_caught_by_its_opener(self):
        text = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> abc\n"
        assert rebase.conflicts.has_conflict_markers(text) == "<<<<<<< "

    def test_a_surviving_closer_alone_is_still_caught(self):
        """A resolution that dropped the opener but kept the rest."""
        assert rebase.conflicts.has_conflict_markers("ours\n=======\ntheirs\n>>>>>>> abc\n") == ">>>>>>> "

    def test_the_diff3_base_marker_is_caught(self):
        assert rebase.conflicts.has_conflict_markers("||||||| merged common ancestors\n") == "||||||| "


# ── should_chunk ─────────────────────────────────────────────────────────

class TestShouldChunk:
    def test_small_file_returns_false(self):
        content = "line\n" * 50
        block = rebase.types.ConflictBlock(
            index=1, start=10, end=15, conflict="x", context_before="", context_after="",
        )
        assert rebase.conflicts.should_chunk(content, [block]) is False

    def test_large_file_small_conflict(self):
        content = "line\n" * 500
        block = rebase.types.ConflictBlock(
            index=1, start=100, end=105, conflict="x", context_before="", context_after="",
        )
        assert rebase.conflicts.should_chunk(content, [block]) is True


# ── classify_conflict ────────────────────────────────────────────────────

class TestClassifyConflict:
    def test_delete_takes_priority(self, tmp_path):
        f = tmp_path / "old.go"
        f.write_text("content")
        with mock.patch.object(
            rebase.conflicts, "detect_delete_conflict",
            return_value=rebase.types.DeleteSide.THEIRS_DELETED,
        ):
            plan = rebase.conflicts.classify_conflict(
                "old.go", f, str(tmp_path),
            )
        assert plan.strategy is rebase.types.ConflictStrategy.DELETE

    def test_text_file_ai_merge(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        with mock.patch.object(rebase.conflicts, "detect_delete_conflict", return_value=None), \
             mock.patch.object(rebase.conflicts, "is_generated_file", return_value=None):
            plan = rebase.conflicts.classify_conflict(
                "main.go", f, str(tmp_path),
            )
        assert plan.strategy is rebase.types.ConflictStrategy.AI_MERGE


# ── splice_resolutions ───────────────────────────────────────────────────

class TestSpliceResolutions:
    def test_replaces_conflict_markers(self):
        content = "before\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nafter\n"
        blocks = rebase.conflicts.extract_conflict_blocks(content)
        result = rebase.conflicts.splice_resolutions(content, blocks, ["merged\n"])
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
    return rebase.types.ConflictBlock(
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
        assert rebase.conflicts.echoed_context_lines("    return a + b\n", _ctx_block()) == 0

    def test_a_trailing_blank_line_is_not_an_echo(self):
        """One shared line at the boundary is ordinary, so it stays allowed."""
        n = rebase.conflicts.echoed_context_lines("    return a + b\n\n", _ctx_block())
        assert n <= rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_shared_closing_brace_is_not_an_echo(self):
        block = _ctx_block(after="}\n\nint other(void) {\n")
        n = rebase.conflicts.echoed_context_lines("    x();\n}\n", block)
        assert n <= rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_run_of_blank_lines_is_not_an_echo(self):
        """Filler both sides produce independently, not content repeated back.

        Counting raw matched lines rejected this: a resolution ending in two
        blank lines where the context also opens with two scores a two-line
        overlap while nothing was echoed at all.
        """
        block = _ctx_block(after="\n\n# next\ndef g():\n")
        n = rebase.conflicts.echoed_context_lines("    return x\n\n\n", block)
        assert n <= rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_stacked_closing_braces_are_not_an_echo(self):
        """The C-like and Go shape: several bare closers in a row.

        Three of them overlap exactly where a nested block ends and the next
        declaration begins, which is ordinary code rather than a model
        repeating its context.
        """
        block = _ctx_block(after="}\n}\n}\nint main(void) {\n")
        n = rebase.conflicts.echoed_context_lines("    x();\n}\n}\n}\n", block)
        assert n <= rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_one_substantive_line_is_an_echo(self):
        """The budget is zero once filler is discounted.

        A line that says something, sitting outside the region being replaced
        and reproduced anyway, has no innocent explanation — so it does not need
        a second line beside it to be read as an echo.
        """
        block = _ctx_block(after="\n# next thing\ndef other():\n")
        n = rebase.conflicts.echoed_context_lines(
            "    return a + b\n\n# next thing\n", block,
        )
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_two_echoed_lines_are_caught(self):
        n = rebase.conflicts.echoed_context_lines(
            "    return a + b\n\n# next thing\n", _ctx_block(),
        )
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_the_whole_trailing_context_is_caught(self):
        n = rebase.conflicts.echoed_context_lines("    return a + b\n" + _AFTER, _ctx_block())
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_the_leading_context_is_caught_too(self):
        """A model can echo the context it was shown on either side."""
        n = rebase.conflicts.echoed_context_lines(
            _BEFORE + "    return a + b\n", _ctx_block(),
        )
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_an_echo_on_both_sides_at_once_is_caught(self):
        """Why `max` of the two sides is enough, rather than their sum.

        A resolution can echo the context at its head and its tail in the same
        answer. `max` reports the larger of the two rather than the total, which
        would matter if a single echoed line were tolerated — with the budget at
        zero, either side alone already fails, so the two can never combine into
        a verdict neither reaches.
        """
        block = rebase.types.ConflictBlock(
            index=1, start=0, end=4, conflict=_CONFLICT,
            context_before="import os\nCONST = 1\n",
            context_after="def tail():\n    pass\n",
        )
        n = rebase.conflicts.echoed_context_lines(
            "CONST = 1\n    merged\ndef tail():\n", block,
        )
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_a_line_the_conflict_owns_is_not_an_echo(self):
        """Ending on a line the conflict contained is the resolution's job.

        Counting it would reject a correct merge whenever the following context
        happens to open with a line the conflict also held.
        """
        block = _ctx_block(after="    return a\n    cleanup()\n")
        assert rebase.conflicts.echoed_context_lines("    return a\n", block) == 0


class TestTrimEchoedContext:
    """The repair the measurement was already computing and throwing away."""

    def test_a_clean_resolution_is_returned_untouched(self):
        trim = rebase.conflicts.trim_echoed_context("    return a + b\n", _ctx_block())
        assert trim.ok
        assert trim.trimmed == 0
        assert trim.text == "    return a + b\n"

    def test_the_echoed_tail_is_removed(self):
        trim = rebase.conflicts.trim_echoed_context(
            "    return a + b\n" + _AFTER, _ctx_block(),
        )
        assert trim.ok
        assert trim.text == "    return a + b\n"

    def test_the_echoed_head_is_removed(self):
        trim = rebase.conflicts.trim_echoed_context(
            _BEFORE + "    return a + b\n", _ctx_block(),
        )
        assert trim.ok
        assert trim.text == "    return a + b\n"

    def test_an_echo_on_both_sides_at_once_is_removed(self):
        block = rebase.types.ConflictBlock(
            index=1, start=0, end=4, conflict=_CONFLICT,
            context_before="import os\nCONST = 1\n",
            context_after="def tail():\n    pass\n",
        )
        trim = rebase.conflicts.trim_echoed_context(
            "CONST = 1\n    merged\ndef tail():\n", block,
        )
        assert trim.ok
        assert trim.text == "    merged\n"

    def test_a_coincidental_boundary_line_is_left_alone(self):
        """Trimming is as conservative as the measurement that triggers it.

        A resolution ending in the same bare closer the context opens with is
        two different braces, not a repeat — removing it would break the code
        exactly the way the duplicate does.
        """
        block = _ctx_block(after="}\n\nint other(void) {\n")
        trim = rebase.conflicts.trim_echoed_context("    x();\n}\n", block)
        assert trim.trimmed == 0
        assert trim.text == "    x();\n}\n"

    def test_the_whole_echoed_run_goes_not_just_its_substantive_lines(self):
        """Half a duplicate is still a duplicate.

        The run here is a blank line then a comment. Only the comment is
        evidence of an echo, but both are in the file already, so leaving the
        blank behind would splice an extra one in.
        """
        block = _ctx_block(after="\n# next thing\ndef other():\n")
        trim = rebase.conflicts.trim_echoed_context(
            "    return a + b\n\n# next thing\n", block,
        )
        assert trim.text == "    return a + b\n"

    def test_a_resolution_that_is_nothing_but_echo_is_not_repaired(self):
        """There is no resolution under the echo to recover."""
        trim = rebase.conflicts.trim_echoed_context(_AFTER, _ctx_block())
        assert not trim.ok


class TestParseChunkedRepairsEchoedContext:
    """The repair reached through the parser the resolver actually calls."""

    def _stdout(self, body: str, n: int = 1) -> str:
        return (
            f"{rebase.conflicts.RESOLVE_BEGIN}_{n}\n{body}"
            f"{rebase.conflicts.RESOLVE_END}_{n}\n"
        )

    def test_it_repairs_a_resolution_that_echoed_its_context(self):
        """The echo was measured exactly and then thrown away; now it is fixed."""
        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._stdout("    return a + b\n" + _AFTER), [_ctx_block()],
        )
        assert parsed.ok
        assert parsed.repaired == 1
        assert parsed.resolutions == ["    return a + b\n"]

    def test_a_repaired_resolution_splices_without_duplicating(self):
        """The damage the repair exists to prevent, asserted on the output."""
        content = (
            "def head():\n    pass\n"
            "<<<<<<< HEAD\n    return a\n=======\n    return b\n>>>>>>> abc\n"
            "\n# next thing\ndef other():\n    pass\n"
        )
        block = rebase.conflicts.extract_conflict_blocks(content)[0]
        echoed = "    return a + b\n" + block.context_after

        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._stdout(echoed), [block],
        )
        assert parsed.ok
        spliced = rebase.conflicts.splice_resolutions(content, [block], parsed.resolutions)
        assert spliced.count("def other():") == 1
        assert spliced.count("# next thing") == 1

    def test_it_accepts_the_same_resolution_without_the_echo(self):
        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._stdout("    return a + b\n"), [_ctx_block()],
        )
        assert parsed.reason == ""
        assert parsed.repaired == 0
        assert parsed.resolutions == ["    return a + b\n"]

    def test_it_counts_only_the_blocks_it_repaired(self):
        blocks = [
            rebase.types.ConflictBlock(
                index=1, start=0, end=4, conflict=_CONFLICT,
                context_before="", context_after="",
            ),
            _ctx_block(),
        ]
        stdout = (
            self._stdout("fine\n", 1)
            + self._stdout("    return a + b\n" + _AFTER, 2)
        )
        parsed = rebase.conflicts.parse_chunked_resolutions(stdout, blocks)
        assert parsed.ok
        assert parsed.repaired == 1
        assert parsed.resolutions == ["fine\n", "    return a + b\n"]

    def test_a_wholly_echoed_block_fails_and_names_itself(self):
        """The one echo trimming cannot repair goes back to the model."""
        blocks = [
            rebase.types.ConflictBlock(
                index=1, start=0, end=4, conflict=_CONFLICT,
                context_before="", context_after="",
            ),
            _ctx_block(),
        ]
        stdout = self._stdout("fine\n", 1) + self._stdout(_AFTER, 2)
        parsed = rebase.conflicts.parse_chunked_resolutions(stdout, blocks)
        assert not parsed.ok
        assert rebase.types.ParseFailure.WHOLLY_ECHOED in parsed.reason
        assert "block_2" in parsed.reason


class TestBlockMarkersAreMatchedOnBoundaries:
    """`<<<RESOLVED>>>_1` is a prefix of `<<<RESOLVED>>>_11`.

    A plain substring search reads block 11's answer into block 1 in any file
    with ten or more conflicts, which is exactly the size of file the chunked
    path is chosen for.
    """

    def _blocks(self, n: int) -> list:
        return [
            rebase.types.ConflictBlock(
                index=i + 1, start=0, end=1,
                conflict="<<<<<<< HEAD\n>>>>>>> abc\n",
                context_before="", context_after="",
            )
            for i in range(n)
        ]

    def _answer(self, order: list[int]) -> str:
        return "".join(
            f"{rebase.conflicts.RESOLVE_BEGIN}_{i}\nR{i}\n"
            f"{rebase.conflicts.RESOLVE_END}_{i}\n"
            for i in order
        )

    def test_block_one_is_not_read_from_block_eleven(self):
        """Out of order, because in order the bug hides behind `find`.

        A substring search returns the *earliest* match, so an answer emitted
        1..11 finds block 1's own marker first and the defect is invisible.
        Models do not reliably emit in order, and when block 11 comes first its
        resolution is what block 1 gets — or the span runs backwards and the
        block is reported missing.
        """
        blocks = self._blocks(11)
        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._answer([11, *range(1, 11)]), blocks,
        )
        assert parsed.ok
        assert parsed.resolutions[0] == "R1\n"
        assert parsed.resolutions[10] == "R11\n"

    def test_an_in_order_answer_still_parses(self):
        """The back-compat half: the common answer shape is unaffected."""
        blocks = self._blocks(11)
        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._answer(list(range(1, 12))), blocks,
        )
        assert parsed.ok
        assert parsed.resolutions[0] == "R1\n"
        assert parsed.resolutions[10] == "R11\n"

    def test_a_missing_block_one_is_not_satisfied_by_block_eleven(self):
        """The substring match did not only misread — it hid a real absence."""
        blocks = self._blocks(11)
        parsed = rebase.conflicts.parse_chunked_resolutions(
            self._answer(list(range(2, 12))), blocks,
        )
        assert not parsed.ok
        assert parsed.reason.endswith("_1")


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

    # passes-at-base: the pre-existing detection, kept as the premise the new repair acts on
    def test_the_echoing_resolution_is_caught(self):
        content = self._file()
        block = rebase.conflicts.extract_conflict_blocks(content)[0]
        echoed = "    report_missing_xdist\n" + block.context_after

        n = rebase.conflicts.echoed_context_lines(echoed, block)
        assert n > rebase.conflicts.MAX_ECHOED_CONTEXT_LINES

    def test_the_echo_is_trimmed_back_to_the_resolution(self):
        """Caught is no longer the end of it — the repair is exact."""
        content = self._file()
        block = rebase.conflicts.extract_conflict_blocks(content)[0]
        echoed = "    report_missing_xdist\n" + block.context_after

        trim = rebase.conflicts.trim_echoed_context(echoed, block)
        assert trim.ok
        spliced = rebase.conflicts.splice_resolutions(content, [block], [trim.text])
        assert spliced.count("report_missing_xdist() {") == 1

    # passes-at-base: asserts the unchanged splice behaviour the guard exists to keep unreached
    def test_splicing_it_would_have_duplicated_the_function(self):
        """What the guard prevents, stated as the damage rather than a count."""
        content = self._file()
        block = rebase.conflicts.extract_conflict_blocks(content)[0]
        echoed = "    report_missing_xdist\n" + block.context_after

        spliced = rebase.conflicts.splice_resolutions(content, [block], [echoed])
        assert spliced.count("report_missing_xdist() {") == 2

        clean = rebase.conflicts.splice_resolutions(
            content, [block], ["    report_missing_xdist\n"],
        )
        assert clean.count("report_missing_xdist() {") == 1


# ── is_binary ────────────────────────────────────────────────────────────

class TestIsBinary:
    def test_binary_file(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\x00\x00")
        assert rebase.conflicts.is_binary(f) is True

    def test_text_file(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n")
        assert rebase.conflicts.is_binary(f) is False

    def test_missing_file(self):
        assert rebase.conflicts.is_binary(Path("/nonexistent/file.bin")) is False
