"""Tests for the ceiling-scan debt ledger CLI."""

import importlib.machinery
import importlib.util
import time
from pathlib import Path

from conftest import write_marker_file as _write

BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "claude" / "bin"

_spec = importlib.util.spec_from_loader(
    "ceiling_scan",
    importlib.machinery.SourceFileLoader("ceiling_scan", str(BIN_DIR / "ceiling-scan")),
)
ceiling_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ceiling_scan)


def _scan(tmp_path: Path) -> list[dict]:
    return ceiling_scan.scan_directory(tmp_path)


class TestMarkerDetection:
    def test_finds_full_line_marker(self, tmp_path):
        _write(tmp_path, "a.go", "// ceiling: global lock, upgrade if throughput matters")
        markers = _scan(tmp_path)
        assert len(markers) == 1
        assert markers[0]["ceiling"] == "global lock"
        assert markers[0]["trigger"] == "upgrade if throughput matters"

    def test_marker_is_case_insensitive(self, tmp_path):
        _write(tmp_path, "a.sql", "-- Ceiling: full table scan")
        markers = _scan(tmp_path)
        assert len(markers) == 1
        assert markers[0]["trigger"] is None

    def test_trailing_comment_is_not_a_marker(self, tmp_path):
        """A marker opens its own line, so a string literal holding one is inert.

        The repo bans trailing comments outright, and honouring the inline form
        made the scanner report its own test fixtures as debt.
        """
        _write(tmp_path, "a.py", 'x = "# ceiling: not a marker, upgrade if it becomes one"')
        assert _scan(tmp_path) == []


class TestCommentBlock:
    def test_trigger_on_a_continuation_line_is_found(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: the sweep reads every file on each run.",
            "# Fine at this size; upgrade to an index if a scan ever outlasts a keystroke.",
        )
        markers = _scan(tmp_path)
        assert markers[0]["ceiling"] == "the sweep reads every file on each run"
        assert markers[0]["trigger"] == (
            "upgrade to an index if a scan ever outlasts a keystroke"
        )

    def test_block_ends_at_a_blank_comment_line(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: one retry only.",
            "#",
            "# Unrelated paragraph, kept if the retry count changes.",
        )
        assert _scan(tmp_path)[0]["trigger"] is None

    def test_block_ends_at_the_next_marker(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: first, upgrade if one bites",
            "# ceiling: second, upgrade if the other bites",
        )
        markers = _scan(tmp_path)
        assert [m["ceiling"] for m in markers] == ["first", "second"]

    def test_c_block_marker_is_read_to_its_closing_delimiter(self, tmp_path):
        """A `/* ... */` marker is single-line only — its later lines are not continuation."""
        _write(
            tmp_path, "a.c",
            "/* ceiling: one connection, reopen if a second caller appears */",
            " * Unread: the continuation grammar covers #, // and -- only.",
            " */",
        )
        marker = _scan(tmp_path)[0]
        assert marker["ceiling"] == "one connection"
        assert marker["trigger"] == "reopen if a second caller appears"

    def test_block_ends_when_the_indent_changes(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: module-level shortcut.",
            "    # Nested comment, upgrade if it is ever read as continuation.",
        )
        assert _scan(tmp_path)[0]["trigger"] is None


class TestTriggerGrammar:
    def test_explicit_lead_wins_over_a_later_conditional(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: the host is dropped. Upgrade trigger: two same-pathed repos",
            "# on two hosts. Because the host is gone, the fold below is unconditional.",
        )
        assert _scan(tmp_path)[0]["trigger"] == "two same-pathed repos on two hosts"

    def test_explicit_lead_keeps_the_comma_that_follows_its_condition(self, tmp_path):
        _write(
            tmp_path, "a.py",
            "# ceiling: targets leak. Upgrade trigger: if these pile up, add a second signal.",
        )
        assert _scan(tmp_path)[0]["trigger"] == "if these pile up, add a second signal"

    def test_the_first_conditional_clause_wins(self, tmp_path):
        """A marker often explains itself after naming the trigger."""
        _write(
            tmp_path, "a.sh",
            "# ceiling: bypasses the backend; route through it once it takes a flag.",
            "# Until then the raw stream is teed here.",
        )
        assert _scan(tmp_path)[0]["trigger"] == "route through it once it takes a flag"

    def test_an_intention_without_a_condition_is_not_a_trigger(self, tmp_path):
        """An intent to upgrade names no condition, so nothing ever revisits it."""
        _write(tmp_path, "a.py", "# ceiling: the list is static. Upgrade only with a real fix.")
        assert _scan(tmp_path)[0]["trigger"] is None

    def test_a_conditional_inside_the_tradeoff_clause_is_not_a_trigger(self, tmp_path):
        """The tradeoff clause describes what the shortcut does, not when it ends."""
        _write(tmp_path, "a.py", "# ceiling: skip the check when running in CI, no upgrade path")
        marker = _scan(tmp_path)[0]
        assert marker["ceiling"] == "skip the check when running in CI"
        assert marker["trigger"] is None


class TestPermanentMarkers:
    def test_permanent_marker_is_not_counted_as_untriggered(self, tmp_path):
        _write(tmp_path, "a.py", "# ceiling-permanent: the prefix is truncated, and stays so.")
        marker = _scan(tmp_path)[0]
        assert marker["permanent"] is True
        assert ceiling_scan.is_untriggered(marker) is False

    def test_permanent_marker_reports_no_trigger_read_from_its_prose(self, tmp_path):
        """Permanence is the claim; a condition inferred from the rest would contradict it."""
        _write(tmp_path, "a.py", "# ceiling-permanent: accepted, and if it bites we live with it.")
        assert _scan(tmp_path)[0]["trigger"] is None

    def test_counts_separate_permanent_from_untriggered(self, tmp_path):
        _write(tmp_path, "a.py", "# ceiling-permanent: accepted for good.")
        _write(tmp_path, "b.py", "# ceiling: a shortcut with nothing said about ending it.")
        _write(tmp_path, "c.py", "# ceiling: another, upgrade if it bites")
        assert ceiling_scan.marker_counts(_scan(tmp_path)) == {
            "total": 3, "no_trigger": 1, "permanent": 1,
        }


class TestSkippedFiles:
    def test_binary_file_is_skipped(self, tmp_path):
        # A compiled artifact with no extension: SKIP_EXTENSIONS cannot catch it,
        # so the NUL sniff has to.
        (tmp_path / "server").write_bytes(b"\x7fELF\x00\x00\x00\x00// ceiling: not real\n")
        assert _scan(tmp_path) == []

    def test_nul_after_sniff_window_still_scanned(self, tmp_path):
        padding = b"# padding\n" * (ceiling_scan.BINARY_SNIFF_BYTES // 10 + 1)
        (tmp_path / "a.py").write_bytes(padding + b"# ceiling: real marker\n\x00")
        assert len(_scan(tmp_path)) == 1

    def test_oversized_file_is_skipped(self, tmp_path):
        big = tmp_path / "dump.xml"
        big.write_text("<x/>\n" * (ceiling_scan.MAX_FILE_BYTES // 5 + 1) + "# ceiling: nope\n")
        assert big.stat().st_size > ceiling_scan.MAX_FILE_BYTES
        assert _scan(big.parent) == []

    def test_minified_line_is_skipped(self, tmp_path):
        line = "// ceiling: generated, " + "a" * ceiling_scan.MAX_LINE_LEN + "\n"
        (tmp_path / "bundle.ts").write_text(line)
        assert _scan(tmp_path) == []


class TestPathologicalInput:
    def test_long_whitespace_line_does_not_backtrack(self, tmp_path):
        # A single generated line used to cost minutes of regex backtracking.
        # The candidate gate must reject it in linear time.
        (tmp_path / "gen.ts").write_text(("x  y " * 40_000) + "\n")
        start = time.perf_counter()
        assert _scan(tmp_path) == []
        assert time.perf_counter() - start < 2.0


class TestLedgerOutput:
    def test_ledger_written_when_markers_exist(self, tmp_path):
        (tmp_path / "a.go").write_text("// ceiling: stub, replace with real client\n")
        out = tmp_path / ".claude" / "ceiling-debt.md"
        ceiling_scan.main(["--output", str(out), str(tmp_path)])
        assert "stub" in out.read_text()

    def test_stale_ledger_removed_when_markers_gone(self, tmp_path):
        out = tmp_path / ".claude" / "ceiling-debt.md"
        out.parent.mkdir()
        out.write_text("# Ceiling Debt Ledger\n\nstale content\n")
        ceiling_scan.main(["--output", str(out), str(tmp_path)])
        assert not out.exists()

    def test_no_ledger_created_for_clean_repo(self, tmp_path):
        (tmp_path / "a.go").write_text("package main\n")
        out = tmp_path / ".claude" / "ceiling-debt.md"
        ceiling_scan.main(["--output", str(out), str(tmp_path)])
        assert not out.exists()

    def test_ledger_distinguishes_permanent_from_untriggered(self, tmp_path):
        _write(tmp_path, "a.py", "# ceiling-permanent: accepted for good.")
        _write(tmp_path, "b.py", "# ceiling: a shortcut with nothing said about ending it.")
        ledger = ceiling_scan.format_ledger(_scan(tmp_path))
        assert "1 with no trigger, 1 permanent" in ledger
        assert "accepted for good — **permanent**" in ledger
        assert "ending it — **no-trigger**" in ledger
