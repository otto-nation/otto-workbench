"""`git diff --numstat` output as per-file and total counts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import numstat  # noqa: E402


def test_parse_numstat_totals_the_files_it_read():
    counts = numstat.parse_numstat("10\t5\tpkg/handler.go\n3\t1\tpkg/util.go\n")

    assert counts.additions == 13
    assert counts.deletions == 6
    assert [f["path"] for f in counts.files] == ["pkg/handler.go", "pkg/util.go"]


def test_parse_numstat_keeps_a_binary_file_at_zero():
    counts = numstat.parse_numstat("-\t-\timage.png\n5\t2\tfile.go\n")

    assert counts.files[0] == {"path": "image.png", "additions": 0, "deletions": 0}


def test_parse_numstat_reads_empty_output_as_nothing_changed():
    counts = numstat.parse_numstat("")

    assert counts.files == []
    assert counts.additions == 0


class TestParseNumstat:
    def test_normal_output(self):
        counts = numstat.parse_numstat("10\t5\tpkg/handler.go\n3\t1\tpkg/util.go\n")
        assert len(counts.files) == 2
        assert counts.files[0] == {
            "path": "pkg/handler.go", "additions": 10, "deletions": 5,
        }
        assert counts.additions == 13
        assert counts.deletions == 6

    def test_binary_files(self):
        counts = numstat.parse_numstat("-\t-\timage.png\n5\t2\tfile.go\n")
        assert len(counts.files) == 2
        assert counts.files[0]["additions"] == 0
        assert counts.files[0]["deletions"] == 0
        assert counts.additions == 5
        assert counts.deletions == 2

    def test_empty_input(self):
        counts = numstat.parse_numstat("")
        assert counts.files == []
        assert counts.additions == 0
        assert counts.deletions == 0
