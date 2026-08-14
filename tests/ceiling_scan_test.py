"""Tests for the ceiling-scan debt ledger CLI."""

import importlib.machinery
import importlib.util
import time
from pathlib import Path

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
        (tmp_path / "a.go").write_text("// ceiling: global lock, upgrade if throughput matters\n")
        markers = _scan(tmp_path)
        assert len(markers) == 1
        assert markers[0]["ceiling"] == "global lock"
        assert markers[0]["trigger"] == "upgrade if throughput matters"

    def test_finds_inline_marker(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1  # ceiling: single retry, revisit on flakes\n")
        markers = _scan(tmp_path)
        assert len(markers) == 1
        assert markers[0]["line"] == 1
        assert markers[0]["trigger"] == "revisit on flakes"

    def test_marker_is_case_insensitive(self, tmp_path):
        (tmp_path / "a.sql").write_text("-- Ceiling: full table scan\n")
        markers = _scan(tmp_path)
        assert len(markers) == 1
        assert markers[0]["trigger"] is None


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
        line = "a" * (ceiling_scan.MAX_LINE_LEN + 1) + "  // ceiling: generated\n"
        (tmp_path / "bundle.ts").write_text(line)
        assert _scan(tmp_path) == []


class TestPathologicalInput:
    def test_long_whitespace_line_does_not_backtrack(self, tmp_path):
        # CEILING_INLINE_RE is quadratic in line length. Before the candidate gate
        # this single file took minutes; the gate must reject it in linear time.
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
