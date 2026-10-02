"""Tests for review.collect — diff and file primitives, and the preflight block's format."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.budget
import review.collect
from review.types import PreflightData


# ── scope_diff ──────────────────────────────────────────────────────────────


class TestScopeDiff:
    def test_filter_one_of_two(self):
        diff = (
            "diff --git a/file1.go b/file1.go\n"
            "--- a/file1.go\n+++ b/file1.go\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/file2.go b/file2.go\n"
            "--- a/file2.go\n+++ b/file2.go\n@@ -1 +1 @@\n-old2\n+new2\n"
        )
        result = review.collect.scope_diff(diff, ["file1.go"])
        assert "file1.go" in result
        assert "file2.go" not in result

    def test_filter_two_of_three(self):
        diff = (
            "diff --git a/foo.go b/foo.go\n"
            "--- a/foo.go\n+++ b/foo.go\n@@ -1,3 +1,4 @@\n package main\n+import \"fmt\"\n\n"
            "diff --git a/bar.go b/bar.go\n"
            "--- a/bar.go\n+++ b/bar.go\n@@ -1,3 +1,3 @@\n-package old\n+package bar\n\n"
            "diff --git a/baz.go b/baz.go\n"
            "--- a/baz.go\n+++ b/baz.go\n@@ -1 +1 @@\n-old\n+new\n"
        )
        result = review.collect.scope_diff(diff, ["foo.go", "baz.go"])
        assert "foo.go" in result
        assert "bar.go" not in result
        assert "baz.go" in result

    def test_filter_no_match(self):
        diff = "diff --git a/file1.go b/file1.go\n--- a/file1.go\n+++ b/file1.go\n"
        result = review.collect.scope_diff(diff, ["other.go"])
        assert result == ""

    def test_filter_all_files(self):
        diff = (
            "diff --git a/a.go b/a.go\ncontent a\n"
            "diff --git a/b.go b/b.go\ncontent b\n"
        )
        result = review.collect.scope_diff(diff, ["a.go", "b.go"])
        assert "a.go" in result
        assert "b.go" in result


# ── truncate_diff ───────────────────────────────────────────────────────────


class TestTruncateDiff:
    def test_under_budget(self):
        diff = "diff --git a/f.go b/f.go\nshort\n"
        cut = review.collect.truncate_diff(diff, 10000)
        assert cut.text == diff
        assert cut.omitted == []

    def test_over_budget(self):
        diff = (
            "diff --git a/a.go b/a.go\n" + "+" * 500 + "\n"
            "diff --git a/b.go b/b.go\n" + "+" * 500 + "\n"
        )
        assert review.collect.truncate_diff(diff, 600).omitted

    def test_single_file_over_budget(self):
        diff = "diff --git a/big.go b/big.go\n" + "x" * 5000 + "\n"
        cut = review.collect.truncate_diff(diff, 100)
        assert len(cut.text.encode()) < len(diff.encode())

    def test_empty_diff(self):
        cut = review.collect.truncate_diff("", 1000)
        assert cut.text == ""
        assert cut.omitted == []

    def test_omitted_names_the_files_that_did_not_fit(self):
        """A file dropped without being named reads as a file nothing touched."""
        diff = (
            "diff --git a/small.go b/small.go\n" + "+x\n"
            "diff --git a/big.go b/big.go\n" + "+" * 2000 + "\n"
        )
        assert review.collect.truncate_diff(diff, 500).omitted == ["big.go"]


# ── _read_file_safe ─────────────────────────────────────────────────────────


class TestReadFileSafe:
    def test_normal_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert review.collect._read_file_safe(f) == "hello world"

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x80\x81\x82\xff\xfe")
        assert "binary file" in review.collect._read_file_safe(f)

    def test_file_not_found(self, tmp_path):
        assert review.collect._read_file_safe(tmp_path / "missing.txt") == "<file deleted>"

    def test_large_file_truncation(self, tmp_path):
        f = tmp_path / "large.txt"
        f.write_text("x" * (review.budget.MAX_FILE_BYTES * 2))
        assert "truncated" in review.collect._read_file_safe(f)

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root reads a 0o000 file, so the mode proves nothing",
    )
    def test_permission_denied(self, tmp_path):
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        os.chmod(str(f), 0o000)
        try:
            assert review.collect._read_file_safe(f) == "<permission denied>"
        finally:
            os.chmod(str(f), 0o644)


# ── _file_permissions ───────────────────────────────────────────────────────


class TestFilePermissions:
    def test_returns_octal_for_normal_file(self, tmp_path):
        f = tmp_path / "perm.txt"
        f.write_text("test\n")
        f.chmod(0o644)
        assert review.collect._file_permissions(f) == "0o644"

    def test_returns_question_mark_for_missing_file(self, tmp_path):
        assert review.collect._file_permissions(tmp_path / "nonexistent.txt") == "?"

    def test_returns_executable_mode(self, tmp_path):
        f = tmp_path / "exec.sh"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
        assert review.collect._file_permissions(f) == "0o755"


# ── format_preflight_data ───────────────────────────────────────────────────


class TestFormatPreflightData:
    def test_includes_all_sections(self):
        data = PreflightData(
            diff="--- a/foo.go\n+++ b/foo.go\n@@ -1 +1 @@\n-old\n+new",
            commit_log="abc123 fix bug",
            file_contents={"foo.go": "package main\n", "bar.go": "package bar\n"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="# My Project",
            architecture_md="## Known Constraints",
            review_checklists={"security.md": "# Security checks"},
        )
        result = review.collect.format_preflight_data(data).text
        assert "Pre-collected data" in result
        assert "```diff" in result
        assert "foo.go" in result
        assert "bar.go" in result
        assert "# My Project" in result
        assert "Known Constraints" in result
        assert "Security checks" in result
        assert "abc123 fix bug" in result

    def test_file_filter_scopes_file_contents(self):
        data = PreflightData(
            diff="full diff",
            commit_log="log",
            file_contents={"foo.go": "package main", "bar.go": "package bar"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="",
            architecture_md="",
        )
        result = review.collect.format_preflight_data(data, file_filter=["foo.go"]).text
        assert "package main" in result
        assert "package bar" not in result

    def test_file_filter_scopes_diff(self):
        diff_text = (
            "diff --git a/foo.go b/foo.go\n"
            "--- a/foo.go\n"
            "+++ b/foo.go\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "\n"
            "diff --git a/bar.go b/bar.go\n"
            "--- a/bar.go\n"
            "+++ b/bar.go\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        data = PreflightData(
            diff=diff_text,
            commit_log="log",
            file_contents={"foo.go": "package main", "bar.go": "package bar"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="",
            architecture_md="",
        )
        result = review.collect.format_preflight_data(data, file_filter=["foo.go"]).text
        assert "a/foo.go" in result
        assert "a/bar.go" not in result

    def test_empty_commit_log_omits_section(self):
        data = PreflightData(
            diff="--- a/f.go\n+++ b/f.go",
            commit_log="",
            file_contents={"f.go": "code"},
            file_permissions={"f.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        assert "Commit history" not in review.collect.format_preflight_data(data).text

    def test_omitted_files_listed_in_output(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
            omitted_files=["big.go", "huge.go"],
        )
        result = review.collect.format_preflight_data(data).text
        assert "Files not pre-collected" in result
        assert "- big.go" in result
        assert "- huge.go" in result
        assert "a.go" in result

    def test_an_omitted_file_is_named_with_its_size(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
            omitted_files=["big.go"],
            file_sizes={"big.go": 30_699},
        )
        assert "- big.go (29KB)" in review.collect.format_preflight_data(data).text

    # A recorded size under 1024 bytes still rounds to a truncating `// 1024`
    # as `0KB`, which reads as negligible rather than as the real small size.
    # Fails against `size // 1024` and passes against `max(size // 1024, 1)`.
    def test_an_omitted_file_under_1kb_is_not_rendered_as_0kb(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
            omitted_files=["tiny.go"],
            file_sizes={"tiny.go": 500},
        )
        result = review.collect.format_preflight_data(data).text
        assert "- tiny.go (1KB)" in result

    # The bare-path rendering this replaces also named the file. The case pins
    # that adding sizes did not make the name conditional on having one, and it
    # fails if the absent size is rendered as "(0KB)".
    # passes-at-base: naming the file is behaviour the size suffix preserves
    def test_an_omitted_file_with_no_recorded_size_is_still_named(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
            omitted_files=["big.go"],
        )
        result = review.collect.format_preflight_data(data).text
        assert "- big.go" in result
        assert "0KB" not in result

    def test_the_omitted_section_says_the_diff_is_not_a_substitute(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={},
            file_permissions={},
            claude_md="",
            architecture_md="",
            omitted_files=["big.go"],
        )
        result = review.collect.format_preflight_data(data).text
        assert "not the code around them" in result

    def test_no_omitted_section_when_all_files_included(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        assert "Files not pre-collected" not in review.collect.format_preflight_data(data).text

    def test_skip_file_contents_names_what_it_did_not_inline(self):
        """Dropping the contents cannot also drop the list of them.

        The list is the only place the prompt says which files exist, and the
        environment section sends the agent to read exactly it. Omitting both
        left the agent told its files were pre-collected and shown neither
        them nor their names.
        """
        data = PreflightData(
            diff="--- a/foo.go\n+++ b/foo.go",
            commit_log="abc123 fix bug",
            file_contents={"foo.go": "package main"},
            file_permissions={"foo.go": "0o644"},
            claude_md="# Project",
            architecture_md="",
            omitted_files=["bar.go"],
        )
        dropped_all = review.budget.fit_files(data.file_contents, data.file_permissions, 0)
        result = review.collect.format_preflight_data(data, files=dropped_all).text
        assert "```diff" in result
        assert "abc123 fix bug" in result
        assert "# Project" in result
        assert "package main" not in result
        assert "Changed file contents" not in result
        assert "### Files not pre-collected (read directly)" in result
        assert "- foo.go" in result
        assert "- bar.go" in result

    def test_max_diff_bytes_names_the_diffs_it_dropped(self):
        """The cut has to reach the prompt, or the agent never goes reading."""
        data = PreflightData(
            diff=(
                "diff --git a/small.go b/small.go\n+x\n"
                "diff --git a/big.go b/big.go\n" + "+" * 2000 + "\n"
            ),
            commit_log="",
            file_contents={},
            file_permissions={},
            claude_md="",
            architecture_md="",
        )
        result = review.collect.format_preflight_data(data, max_diff_bytes=500).text
        assert "### Diffs not pre-collected" in result
        assert "- big.go" in result


class TestTheBlockReportsWhatItsDiffCost:
    """The block's own measurement is the only honest one.

    `truncate_diff` drops whole files by tier, so the rendered diff is bounded
    by its allowance but is not derivable from it — a caller that estimates
    the diff from the cap it handed out is off by however much the tier
    ranking declined to spend. The prompt budget read the cap for exactly that
    reason and recorded every healthy render as hundreds of kilobytes under.
    """

    @staticmethod
    def _two_file_diff() -> PreflightData:
        return PreflightData(
            diff=(
                "diff --git a/small.go b/small.go\n+x\n"
                "diff --git a/big.go b/big.go\n" + "+" * 2000 + "\n"
            ),
            commit_log="",
            file_contents={},
            file_permissions={},
            claude_md="",
            architecture_md="",
        )

    def test_an_uncapped_block_is_charged_the_whole_diff(self):
        data = self._two_file_diff()
        block = review.collect.format_preflight_data(data)
        assert block.rendered_diff_bytes == len(data.diff.encode())

    def test_a_truncated_diff_is_charged_what_it_kept_not_what_it_was_allowed(self):
        data = self._two_file_diff()
        block = review.collect.format_preflight_data(data, max_diff_bytes=500)
        kept = "diff --git a/small.go b/small.go\n+x\n"
        assert block.rendered_diff_bytes == len(kept.encode())
        assert block.rendered_diff_bytes < 500

    def test_a_scoped_block_counts_only_the_scoped_diff(self):
        data = self._two_file_diff()
        block = review.collect.format_preflight_data(data, file_filter=["small.go"])
        assert 0 < block.rendered_diff_bytes < len(data.diff.encode())
