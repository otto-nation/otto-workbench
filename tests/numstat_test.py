"""`git diff --numstat` output as per-file and total counts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git.numstat  # noqa: E402


class TestParseNumstat:
    def test_normal_output(self):
        counts = git.numstat.parse_numstat("10\t5\tpkg/handler.go\n3\t1\tpkg/util.go\n")
        assert len(counts.files) == 2
        assert counts.files[0] == {
            "path": "pkg/handler.go", "additions": 10, "deletions": 5,
        }
        assert counts.additions == 13
        assert counts.deletions == 6

    def test_binary_files(self):
        counts = git.numstat.parse_numstat("-\t-\timage.png\n5\t2\tfile.go\n")
        assert len(counts.files) == 2
        assert counts.files[0]["additions"] == 0
        assert counts.files[0]["deletions"] == 0
        assert counts.additions == 5
        assert counts.deletions == 2

    def test_empty_input(self):
        counts = git.numstat.parse_numstat("")
        assert counts.files == []
        assert counts.additions == 0
        assert counts.deletions == 0


class TestWeightedLines:
    """Review effort, in added-line equivalents.

    Exact values rather than inequalities: a bound is satisfied by any weight
    below one, so it would pass for a weight of zero — which would make a
    deletion free and exempt a branch of pure removals from review entirely.
    """

    def test_an_addition_counts_in_full(self):
        assert git.numstat.weighted_lines(100, 0) == 100

    def test_a_deletion_counts_for_a_quarter(self):
        assert git.numstat.weighted_lines(0, 100) == 25

    def test_a_deletion_is_not_free(self):
        # Review of a deletion is what it broke, which is real work even for
        # one line. A weight that rounded this to nothing would let a branch
        # of pure removals size as an empty diff.
        assert git.numstat.weighted_lines(0, 4) > 0

    def test_the_two_are_summed(self):
        assert git.numstat.weighted_lines(50, 40) == 60

    def test_an_empty_diff_is_zero(self):
        assert git.numstat.weighted_lines(0, 0) == 0
