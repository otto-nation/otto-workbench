"""Tests for review_collect — collection, budget fit, and the preflight block."""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import (
    add_self_origin, commit_all, git_out, init_repo, run_checked,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult
from git import client as git_client
from review import budget as review_budget
from review import collect as rc
from gh.types import PRContext, PRMetadata
from review.collect import fetch_branch_metadata
from review.types import PreflightData, ReviewJob


def _job(tmp_path: Path, files: list[dict], **overrides) -> ReviewJob:
    """A review job over *files*, rooted at *tmp_path* unless overridden."""
    pr = PRMetadata(
        title="t", body="", head="feat", base="main", head_sha="abc123",
        additions=0, deletions=0, changed_files=len(files), files=files,
    )
    job = ReviewJob(
        repo="org/repo", pr_number="1", pr=pr, ctx=PRContext(),
        wt_path=str(tmp_path), review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )
    return replace(job, **overrides) if overrides else job


# ── scope_diff ──────────────────────────────────────────────────────────────


class TestScopeDiff:
    def test_filter_one_of_two(self):
        diff = (
            "diff --git a/file1.go b/file1.go\n"
            "--- a/file1.go\n+++ b/file1.go\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/file2.go b/file2.go\n"
            "--- a/file2.go\n+++ b/file2.go\n@@ -1 +1 @@\n-old2\n+new2\n"
        )
        result = rc.scope_diff(diff, ["file1.go"])
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
        result = rc.scope_diff(diff, ["foo.go", "baz.go"])
        assert "foo.go" in result
        assert "bar.go" not in result
        assert "baz.go" in result

    def test_filter_no_match(self):
        diff = "diff --git a/file1.go b/file1.go\n--- a/file1.go\n+++ b/file1.go\n"
        result = rc.scope_diff(diff, ["other.go"])
        assert result == ""

    def test_filter_all_files(self):
        diff = (
            "diff --git a/a.go b/a.go\ncontent a\n"
            "diff --git a/b.go b/b.go\ncontent b\n"
        )
        result = rc.scope_diff(diff, ["a.go", "b.go"])
        assert "a.go" in result
        assert "b.go" in result


# ── truncate_diff ───────────────────────────────────────────────────────────


class TestTruncateDiff:
    def test_under_budget(self):
        diff = "diff --git a/f.go b/f.go\nshort\n"
        cut = rc.truncate_diff(diff, 10000)
        assert cut.text == diff
        assert cut.omitted == []

    def test_over_budget(self):
        diff = (
            "diff --git a/a.go b/a.go\n" + "+" * 500 + "\n"
            "diff --git a/b.go b/b.go\n" + "+" * 500 + "\n"
        )
        assert rc.truncate_diff(diff, 600).omitted

    def test_single_file_over_budget(self):
        diff = "diff --git a/big.go b/big.go\n" + "x" * 5000 + "\n"
        cut = rc.truncate_diff(diff, 100)
        assert len(cut.text.encode()) < len(diff.encode())

    def test_empty_diff(self):
        cut = rc.truncate_diff("", 1000)
        assert cut.text == ""
        assert cut.omitted == []

    def test_omitted_names_the_files_that_did_not_fit(self):
        """A file dropped without being named reads as a file nothing touched."""
        diff = (
            "diff --git a/small.go b/small.go\n" + "+x\n"
            "diff --git a/big.go b/big.go\n" + "+" * 2000 + "\n"
        )
        assert rc.truncate_diff(diff, 500).omitted == ["big.go"]


# ── _read_file_safe ─────────────────────────────────────────────────────────


class TestReadFileSafe:
    def test_normal_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert rc._read_file_safe(f) == "hello world"

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x80\x81\x82\xff\xfe")
        assert "binary file" in rc._read_file_safe(f)

    def test_file_not_found(self, tmp_path):
        assert rc._read_file_safe(tmp_path / "missing.txt") == "<file deleted>"

    def test_large_file_truncation(self, tmp_path):
        f = tmp_path / "large.txt"
        f.write_text("x" * (review_budget.MAX_FILE_BYTES * 2))
        assert "truncated" in rc._read_file_safe(f)

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root reads a 0o000 file, so the mode proves nothing",
    )
    def test_permission_denied(self, tmp_path):
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        os.chmod(str(f), 0o000)
        try:
            assert rc._read_file_safe(f) == "<permission denied>"
        finally:
            os.chmod(str(f), 0o644)


# ── _file_permissions ───────────────────────────────────────────────────────


class TestFilePermissions:
    def test_returns_octal_for_normal_file(self, tmp_path):
        f = tmp_path / "perm.txt"
        f.write_text("test\n")
        f.chmod(0o644)
        assert rc._file_permissions(f) == "0o644"

    def test_returns_question_mark_for_missing_file(self, tmp_path):
        assert rc._file_permissions(tmp_path / "nonexistent.txt") == "?"

    def test_returns_executable_mode(self, tmp_path):
        f = tmp_path / "exec.sh"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
        assert rc._file_permissions(f) == "0o755"


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
        result = rc.format_preflight_data(data).text
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
        result = rc.format_preflight_data(data, file_filter=["foo.go"]).text
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
        result = rc.format_preflight_data(data, file_filter=["foo.go"]).text
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
        assert "Commit history" not in rc.format_preflight_data(data).text

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
        result = rc.format_preflight_data(data).text
        assert "Files not pre-collected" in result
        assert "- big.go" in result
        assert "- huge.go" in result
        assert "a.go" in result

    def test_no_omitted_section_when_all_files_included(self):
        data = PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        assert "Files not pre-collected" not in rc.format_preflight_data(data).text

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
        dropped_all = review_budget.fit_files(data.file_contents, data.file_permissions, 0)
        result = rc.format_preflight_data(data, files=dropped_all).text
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
        result = rc.format_preflight_data(data, max_diff_bytes=500).text
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
        block = rc.format_preflight_data(data)
        assert block.rendered_diff_bytes == len(data.diff.encode())

    def test_a_truncated_diff_is_charged_what_it_kept_not_what_it_was_allowed(self):
        data = self._two_file_diff()
        block = rc.format_preflight_data(data, max_diff_bytes=500)
        kept = "diff --git a/small.go b/small.go\n+x\n"
        assert block.rendered_diff_bytes == len(kept.encode())
        assert block.rendered_diff_bytes < 500

    def test_a_scoped_block_counts_only_the_scoped_diff(self):
        data = self._two_file_diff()
        block = rc.format_preflight_data(data, file_filter=["small.go"])
        assert 0 < block.rendered_diff_bytes < len(data.diff.encode())


# ── Density-based file content skipping ─────────────────────────────────────


class TestDensitySkipping:
    def test_large_file_small_diff_omitted(self, tmp_path):
        (tmp_path / "big.py").write_text("x = 1\n" * 2000)
        job = _job(tmp_path, [{"path": "big.py", "additions": 2, "deletions": 1}])

        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)

        assert "big.py" not in data.file_contents
        assert "big.py" in data.omitted_files

    def test_small_file_always_included(self, tmp_path):
        (tmp_path / "small.py").write_text("x = 1\n")
        job = _job(tmp_path, [{"path": "small.py", "additions": 1, "deletions": 0}])

        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)

        assert "small.py" in data.file_contents

    def test_high_density_file_included(self, tmp_path):
        (tmp_path / "refactored.py").write_text("line\n" * 100)
        job = _job(
            tmp_path, [{"path": "refactored.py", "additions": 80, "deletions": 70}],
        )

        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)

        assert "refactored.py" in data.file_contents


# ── collect_preflight_data (git repo tests) ─────────────────────────────────


class TestCollectPreflightData:
    def test_oversized_file_in_diff_but_omitted_from_contents(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "big.txt").write_text("x" * 600_000)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "big.txt").write_text("y" * 600_000)
        commit_all(repo, "change")

        job = _job(
            tmp_path, [{"path": "big.txt", "additions": 1, "deletions": 0}],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert data is not None
        assert len(data.diff) > 0
        assert data.omitted_files == ["big.txt"]
        assert data.file_contents == {}

    def test_large_diff_includes_diff_but_omits_some_files(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        for i in range(1, 6):
            content = "".join(
                f"original_line_content_padding_{j}\n" for j in range(10_000)
            )
            (repo / f"file{i}.go").write_text(content)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        for i in range(1, 6):
            content = "".join(
                f"modified_line_content_padding_{j}\n" for j in range(10_000)
            )
            (repo / f"file{i}.go").write_text(content)
        commit_all(repo, "change")

        job = _job(
            tmp_path,
            [
                {"path": f"file{i}.go", "additions": 10_000, "deletions": 10_000}
                for i in range(1, 6)
            ],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert data is not None
        assert len(data.diff) > 0
        assert len(data.omitted_files) > 0
        assert len(data.file_contents) + len(data.omitted_files) == 5

    def test_success_path_collects_all_data(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".claude" / "review").mkdir(parents=True)
        init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        (repo / "CLAUDE.md").write_text("# Project\n")
        (repo / ".claude" / "architecture.md").write_text("## Known Constraints\n")
        (repo / ".claude" / "review" / "security.md").write_text("# Security\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")
        commit_all(repo, "add hello")

        job = _job(
            tmp_path, [{"path": "main.go", "additions": 1, "deletions": 0}],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert data is not None
        assert "main.go" in data.file_contents
        assert "main.go" in data.file_permissions
        assert data.file_permissions["main.go"] != "?"
        assert "# Project" in data.claude_md
        assert "## Known Constraints" in data.architecture_md
        assert "security.md" in data.review_checklists
        assert len(data.diff) > 0
        assert len(data.commit_log) > 0
        assert data.omitted_files == []

    def test_handles_deleted_files_in_pr(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "removed.txt").write_text("old content\n")
        (repo / "kept.txt").write_text("keep\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "removed.txt").unlink()
        (repo / "kept.txt").write_text("updated\n")
        commit_all(repo, "remove file")

        job = _job(
            tmp_path,
            [
                {"path": "removed.txt", "additions": 0, "deletions": 1},
                {"path": "kept.txt", "additions": 1, "deletions": 0},
            ],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert data.file_contents["removed.txt"] == "<file deleted>"
        assert "updated" in data.file_contents["kept.txt"]
        assert len(data.diff) > 0

    def test_captures_uncommitted_diff_when_no_commits_on_branch(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")

        job = _job(
            tmp_path, [], wt_path=str(repo), pr_number="",
            pr=fetch_branch_metadata(str(repo)), mode="self",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert "func hello" in data.diff
        assert "main.go" in data.file_contents

    @staticmethod
    def _repo_with_worktree_changes(tmp_path) -> Path:
        """Branch with one commit, one uncommitted edit and one untracked file."""
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        (repo / "helper.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "main.go").write_text("package main\nfunc committed() {}\n")
        commit_all(repo, "add committed")
        (repo / "helper.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "extra.go").write_text("package main\nfunc untracked() {}\n")
        return repo

    def test_self_mode_diff_spans_the_whole_worktree(self, tmp_path):
        repo = self._repo_with_worktree_changes(tmp_path)

        job = _job(
            tmp_path, [], wt_path=str(repo), pr_number="",
            pr=fetch_branch_metadata(str(repo)), mode="self",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert "func committed" in data.diff
        assert "func uncommitted" in data.diff
        assert "func untracked" in data.diff
        assert set(data.file_contents) == {"main.go", "helper.go", "extra.go"}

    def test_pr_mode_diff_stops_at_head(self, tmp_path):
        repo = self._repo_with_worktree_changes(tmp_path)

        job = _job(
            tmp_path, [{"path": "main.go", "additions": 1, "deletions": 0}],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert "func committed" in data.diff
        assert "func uncommitted" not in data.diff
        assert "func untracked" not in data.diff

    def test_low_density_large_file_omitted_from_contents(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "big.py").write_text("x = 1\n" * 2000)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        with open(str(repo / "big.py"), "a") as f:
            f.write("new_line_1\n")
            f.write("new_line_2\n")
        commit_all(repo, "small change")

        job = _job(
            tmp_path, [{"path": "big.py", "additions": 2, "deletions": 0}],
            wt_path=str(repo),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert "big.py" not in data.file_contents
        assert "big.py" in data.omitted_files

    def test_tier1_files_prioritized_over_tier2_when_budget_tight(
        self, tmp_path, monkeypatch,
    ):
        repo = init_repo(tmp_path / "repo")
        (repo / "CLAUDE.md").write_text("# Rules\n" * 10)
        (repo / "util.go").write_text("package main\n" + "func f() {}\n" * 3000)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "CLAUDE.md").write_text("# Updated rules\n" * 10)
        (repo / "util.go").write_text("package main\n" + "func g() {}\n" * 3000)
        commit_all(repo, "change")

        job = _job(
            tmp_path,
            [
                {"path": "util.go", "additions": 3000, "deletions": 3000},
                {"path": "CLAUDE.md", "additions": 10, "deletions": 10},
            ],
            wt_path=str(repo),
        )
        # Set budget so diff fits but only ~1000 bytes remain for file contents.
        #
        # The patch targets `rc` (`review_collect`), not `review_budget`, because
        # `collect_preflight_data` reads the name `review_collect` bound into its
        # own module namespace when it imported it — patching `review_budget`
        # would rebind a name `review_collect` already copied, which the code
        # under test would never see. `TEMPLATE_OVERHEAD_BYTES` on the same line
        # is a plain read rather than a patch, so it names its owner directly.
        diff_size = len(git_client.out(
            "diff", "origin/main...HEAD", cwd=str(repo),
        ).encode())
        monkeypatch.setattr(
            rc, "MAX_PROMPT_BYTES",
            diff_size + review_budget.TEMPLATE_OVERHEAD_BYTES + 1000,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = rc.collect_preflight_data(job)
        assert "CLAUDE.md" in data.file_contents
        assert "util.go" in data.omitted_files


# ── _collect_delta ──────────────────────────────────────────────────────────


def _delta_job(head_sha: str, prior_review: str = "") -> ReviewJob:
    pr = PRMetadata(
        title="test",
        body="",
        head="feature",
        base="main",
        head_sha=head_sha,
        additions=10,
        deletions=5,
        changed_files=1,
        files=[],
    )
    return ReviewJob(
        repo="owner/repo",
        pr_number="1",
        pr=pr,
        ctx=PRContext(),
        wt_path="/tmp/fake",
        review_file="/tmp/review.md",
        session_log="/tmp/session.log",
        prior_review=prior_review,
    )


class TestCollectDeltaSameSha:
    """The reasons a run reviews the whole PR instead of a delta.

    Each returns the default scope, whose empty `prior_sha` is what
    `_is_incremental` reads as "not a re-review". None of them is a *proven*
    empty delta: the run did not measure one and skipping work on the strength
    of it would skip the full review these ask for.
    """

    def test_prior_sha_equals_head_sha_returns_empty(self):
        sha = "abc1234def5678901234567890abcdef12345678"
        prior_review = f"<!-- head_sha: {sha} -->\nsome review content"
        job = _delta_job(head_sha=sha, prior_review=prior_review)
        assert rc._collect_delta(job) == rc.DeltaScope()

    def test_no_prior_review_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="")
        assert rc._collect_delta(job) == rc.DeltaScope()

    def test_prior_review_without_sha_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="no sha marker here")
        assert rc._collect_delta(job) == rc.DeltaScope()

    def test_a_full_review_is_never_reported_as_proven_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="")
        assert rc._collect_delta(job).proven_empty is False


class TestCollectDeltaMode:
    """The delta surface follows the same rule as the full diff: self mode
    reaches into the working tree, PR mode stops at HEAD."""

    @staticmethod
    def _repo_with_prior_commit(tmp_path: Path) -> tuple[Path, str]:
        repo = init_repo(tmp_path / "repo")
        (repo / "reviewed.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "committed.go").write_text("package main\nfunc committed() {}\n")
        commit_all(repo, "since review")
        (repo / "reviewed.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "untracked.go").write_text("package main\nfunc untracked() {}\n")
        return repo, prior_sha

    def _job(self, tmp_path: Path, mode: str) -> ReviewJob:
        repo, prior_sha = self._repo_with_prior_commit(tmp_path)
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        return replace(job, wt_path=str(repo), mode=mode)

    def test_self_mode_delta_includes_worktree_changes(self, tmp_path, capsys):
        delta = rc._collect_delta(self._job(tmp_path, "self"))
        capsys.readouterr()
        assert "func committed" in delta.diff
        assert "func uncommitted" in delta.diff
        assert "func untracked" in delta.diff
        assert sorted(delta.files) == ["committed.go", "reviewed.go", "untracked.go"]

    def test_pr_mode_delta_stops_at_head(self, tmp_path, capsys):
        delta = rc._collect_delta(self._job(tmp_path, "pr"))
        capsys.readouterr()
        assert "func committed" in delta.diff
        assert "func uncommitted" not in delta.diff
        assert delta.files == ["committed.go"]

    def test_self_mode_is_never_proven_empty(self, tmp_path, capsys):
        """A working tree has no commits to attribute, so nothing is proven.

        Self-review's surface reaches past HEAD deliberately. There is no
        ancestry walk that could establish the author changed nothing, so the
        flag that lets a caller skip work stays off even when the delta is
        genuinely empty — which is what this builds: a worktree restored to the
        prior review's commit, with the head SHA the only thing that differs.
        """
        repo = init_repo(tmp_path / "repo")
        (repo / "reviewed.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        add_self_origin(repo)
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        job = replace(
            _delta_job(
                head_sha="a-later-commit-this-worktree-does-not-hold",
                prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
            ),
            wt_path=str(repo), mode="self",
        )

        delta = rc._collect_delta(job)
        capsys.readouterr()

        assert delta.files == []
        assert delta.proven_empty is False


class TestCollectDeltaSurface:
    """The delta diff is bounded by the PR, not by what the base branch did.

    `prior_sha..HEAD` spans the base as well as the branch, so a rebase onto a
    moved base puts every commit the base gained into the delta. One 107-file
    review reported 4,974 changed files that way, and the file list alone —
    260KB — pushed the synthesis prompt 75% past its budget. It also defeated
    incremental group skipping: with every group's files in the delta set,
    nothing was skipped and the re-review cost a full one.

    These repos have no `origin`, so they are also what the ancestry walk falls
    back to when it cannot resolve a base ref to exclude — the whole range,
    path-scoped, over-reporting rather than reporting nothing. What the walk
    does when it *can* resolve one is `TestCollectDeltaAncestry`'s.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> tuple[Path, str]:
        repo = init_repo(tmp_path / "repo")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "mine.go").write_text("package main\nfunc mine() {}\n")
        (repo / "theirs.go").write_text("package main\nfunc theirs() {}\n")
        commit_all(repo, "mine plus a rebased base commit")
        return repo, prior_sha

    def _job(self, tmp_path: Path, files: list[dict]) -> ReviewJob:
        repo, prior_sha = self._repo(tmp_path)
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        return replace(
            job, wt_path=str(repo), pr=replace(job.pr, files=files),
        )

    def test_files_outside_the_pr_are_not_in_the_delta(self, tmp_path, capsys):
        job = self._job(tmp_path, [{"path": "mine.go", "additions": 1, "deletions": 0}])
        delta = rc._collect_delta(job)
        capsys.readouterr()
        assert delta.files == ["mine.go"]
        assert "func mine" in delta.diff
        assert "theirs.go" not in delta.diff
        assert "theirs.go" not in delta.commit_log

    def test_a_job_with_no_surface_keeps_the_whole_range(self, tmp_path, capsys):
        """Branch reviews reach `_collect_delta` before the file list exists."""
        delta = rc._collect_delta(self._job(tmp_path, []))
        capsys.readouterr()
        assert sorted(delta.files) == ["mine.go", "theirs.go"]

    def test_an_unresolvable_base_ref_is_not_a_proven_empty_delta(
        self, tmp_path, capsys,
    ):
        """The guard that keeps a missing ref from reading as "nothing changed".

        `git log ... --not origin/main` against a repo without that ref exits
        128, which reports as empty output. Believing it would skip a review of
        real work and advance the marker past it.
        """
        job = self._job(tmp_path, [{"path": "mine.go", "additions": 1, "deletions": 0}])
        delta = rc._collect_delta(job)
        capsys.readouterr()
        assert delta.files == ["mine.go"]
        assert delta.proven_empty is False


class TestCollectDeltaAncestry:
    """The delta's file list is the author's commits, not everything in range.

    Path-scoping bounds the delta by what is reviewable; it cannot bound it by
    who wrote it. A base commit touching a file the PR also touches is inside
    the surface, so it survives that filter and reads as author work — the case
    the 4,974-file incident could not have been prevented by scoping alone.
    Excluding the base by ancestry is what closes it, and what makes a merge of
    main cost nothing while a merge of a sub-branch still costs a review.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> tuple[Path, str]:
        """A branch and a base that both touch `shared.go`, ready to merge.

        Returns the repo and the SHA the prior review was written against — the
        branch's own tip, before the base is merged into it.
        """
        repo = init_repo(tmp_path / "repo")
        (repo / "shared.go").write_text("package main\n")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)

        git_out(repo, "checkout", "-q", "-b", "feat")
        (repo / "mine.go").write_text("package main\nfunc reviewed() {}\n")
        commit_all(repo, "work the prior review saw")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()

        git_out(repo, "checkout", "-q", "main")
        (repo / "shared.go").write_text("package main\nfunc fromBase() {}\n")
        commit_all(repo, "base work on a file the PR also touches")
        git_out(repo, "fetch", "-q", "origin", "main")
        git_out(repo, "checkout", "-q", "feat")
        return repo, prior_sha

    @staticmethod
    def _merge_expecting_conflict(repo: Path, ref: str) -> None:
        """Merge `ref`, which is expected to stop with a conflict.

        Not `git_out`: a conflicting merge exits non-zero, which that helper
        reports as a failed test rather than as the state being set up here.
        `run_checked` still runs it, so a process killed by machine contention
        is reported as contention rather than passing for the wrong reason.
        """
        result = run_checked(
            ["git", "-C", str(repo), "merge", ref, "-m", "Merge main"],
            check=False,
        )
        assert result.returncode != 0, "expected the merge to conflict"

    @staticmethod
    def _job(repo: Path, prior_sha: str, files: list[str]) -> ReviewJob:
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        surface = [{"path": p, "additions": 1, "deletions": 0} for p in files]
        return replace(
            job, wt_path=str(repo), pr=replace(job.pr, files=surface),
        )

    def _merged(self, tmp_path: Path, files: list[str]) -> ReviewJob:
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        return self._job(repo, prior_sha, files)

    def test_a_merge_of_main_alone_leaves_the_delta_empty(self, tmp_path, capsys):
        delta = rc._collect_delta(self._merged(tmp_path, ["mine.go", "shared.go"]))
        capsys.readouterr()
        assert delta.files == []
        assert delta.lines == 0

    def test_base_work_on_a_file_the_pr_also_touches_is_excluded(
        self, tmp_path, capsys,
    ):
        """The case path-scoping cannot close: `shared.go` is in the surface."""
        job = self._merged(tmp_path, ["mine.go", "shared.go"])
        delta = rc._collect_delta(job)
        capsys.readouterr()
        assert "shared.go" not in delta.files

    def test_a_merge_of_main_is_a_proven_empty_delta(self, tmp_path, capsys):
        delta = rc._collect_delta(self._merged(tmp_path, ["mine.go", "shared.go"]))
        capsys.readouterr()
        assert delta.proven_empty is True

    def test_author_work_on_top_of_a_merge_is_kept(self, tmp_path, capsys):
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        (repo / "mine.go").write_text("package main\nfunc afterTheMerge() {}\n")
        commit_all(repo, "more author work")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.files == ["mine.go"]
        assert delta.lines > 0
        assert delta.proven_empty is False

    def test_a_merge_of_a_sub_branch_keeps_the_topics_own_commits(
        self, tmp_path, capsys,
    ):
        """Ancestry distinguishes the two merges a path filter cannot.

        A sub-branch's commits are the topic's own work and have to be
        reviewed; main's are everyone's and must not be. Both arrive as a merge
        commit on the branch.
        """
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "checkout", "-q", "-b", "sub")
        (repo / "sub.go").write_text("package main\nfunc fromSubBranch() {}\n")
        commit_all(repo, "sub-branch work")
        git_out(repo, "checkout", "-q", "feat")
        git_out(repo, "merge", "-q", "sub", "-m", "Merge sub")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "sub.go"]))
        capsys.readouterr()

        assert delta.files == ["sub.go"]
        assert delta.proven_empty is False

    def test_a_conflict_resolution_counts_as_author_work(self, tmp_path, capsys):
        """Work living only in a merge commit, which `--no-merges` cannot see.

        Resolving a conflict is hand-written code, on the file the author was
        most likely to get wrong. Dropping every merge commit would report this
        re-review as having nothing to do.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "shared.go").write_text("package main\nfunc fromBranch() {}\n")
        commit_all(repo, "branch edits the same file the base did")
        self._merge_expecting_conflict(repo, "main")
        (repo / "shared.go").write_text(
            "package main\nfunc fromBranch() {}\nfunc fromBase() {}\n"
        )
        git_out(repo, "add", "shared.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert "shared.go" in delta.files
        assert delta.proven_empty is False

    def test_an_edit_made_during_a_clean_merge_counts_as_author_work(
        self, tmp_path, capsys,
    ):
        repo, prior_sha = self._repo(tmp_path)
        # `--no-commit` stops before the merge commit so the tree can be edited
        # while merging; it exits zero because this merge does not conflict.
        git_out(repo, "merge", "-q", "--no-commit", "--no-ff", "main")
        (repo / "mine.go").write_text("package main\nfunc snuckIn() {}\n")
        git_out(repo, "add", "mine.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert "mine.go" in delta.files
        assert delta.proven_empty is False

    def test_the_delta_reports_the_lines_the_author_changed(self, tmp_path, capsys):
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        (repo / "mine.go").write_text("package main\n" + "func f() {}\n" * 5)
        commit_all(repo, "five more lines")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.lines == 6

    def test_a_file_both_walks_report_is_named_once(self, tmp_path, capsys):
        """A commit and a later conflict resolution can touch the same file.

        Each consumer counts what it is handed — the prompt says how many files
        changed, the line total sums them — so a path arriving from both walks
        would be reported twice.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "shared.go").write_text("package main\nfunc fromBranch() {}\n")
        commit_all(repo, "branch edits the same file the base did")
        self._merge_expecting_conflict(repo, "main")
        (repo / "shared.go").write_text(
            "package main\nfunc fromBranch() {}\nfunc fromBase() {}\n"
        )
        git_out(repo, "add", "shared.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.files.count("shared.go") == 1

    def test_a_walk_that_failed_is_not_an_empty_delta(self, tmp_path, capsys):
        """The guard M1 asked for: git failing must not read as "nothing changed".

        `git_client.out` reports a non-zero exit and a timeout alike as no
        output, which is exactly what an author who changed nothing produces.
        Believing it would carry every group forward and skip a real review.
        """
        job = self._merged(tmp_path, ["mine.go", "shared.go"])
        # The delta walk fails; the diff and log the fallback reads still work.
        real_run = rc.git_client.run

        def _fail_the_walk(*args, **kwargs):
            if args and args[0] in {"log", "rev-list", "show"} and "--numstat" in args:
                return CmdResult(returncode=128, stderr="fatal: bad revision")
            return real_run(*args, **kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(rc.git_client, "run", _fail_the_walk)
            delta = rc._collect_delta(job)
        capsys.readouterr()

        assert delta.proven_empty is False
        assert delta.files, "a failed walk falls back to the whole range"
        # The log describes the same range the file list does, rather than the
        # ancestry-scoped one the walk was going to use.
        assert "Merge main" in delta.commit_log

    def test_a_non_ascii_path_is_named_as_git_stores_it(self, tmp_path, capsys):
        """`core.quotePath` is not applied to `log` by the client's own default.

        An escaped name matches no group's file list, so the group holding the
        file would be skipped as unchanged.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "caf\u00e9.go").write_text("package main\n")
        commit_all(repo, "a path git would escape")

        delta = rc._collect_delta(self._job(repo, prior_sha, ["caf\u00e9.go"]))
        capsys.readouterr()

        assert "caf\u00e9.go" in delta.files


# ── fetch_branch_metadata ─────────────────────────────────────────────


class TestFetchBranchMetadata:
    def test_includes_uncommitted_changes_when_no_commits_on_branch(
        self, tmp_path,
    ):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        git_out(repo, "add", ".")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "init")
        add_self_origin(repo)
        # Stay on main but modify a file without committing
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")

        pr = fetch_branch_metadata(str(repo))
        assert pr.changed_files == 1
        assert pr.files[0]["path"] == "main.go"

    def test_includes_staged_changes_when_no_commits_on_branch(
        self, tmp_path,
    ):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        git_out(repo, "add", ".")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "init")
        add_self_origin(repo)
        # Stage changes without committing
        (repo / "main.go").write_text("package main\nfunc staged() {}\n")
        git_out(repo, "add", "main.go")

        pr = fetch_branch_metadata(str(repo))
        assert pr.changed_files == 1

    def test_committed_uncommitted_and_untracked_changes_all_appear(
        self, tmp_path,
    ):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        (repo / "helper.go").write_text("package main\n")
        (repo / ".gitignore").write_text("secret.txt\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "main.go").write_text("package main\nfunc committed() {}\n")
        commit_all(repo, "add committed")
        (repo / "helper.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "extra.go").write_text("package main\nfunc untracked() {}\n")
        (repo / "secret.txt").write_text("ignored\n")

        pr = fetch_branch_metadata(str(repo))
        paths = sorted(f["path"] for f in pr.files)
        assert paths == ["extra.go", "helper.go", "main.go"]
        assert pr.changed_files == 3
        assert "secret.txt" not in paths

    def test_untracked_files_are_counted_as_whole_file_additions(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        (repo / "new.go").write_text("one\ntwo\nthree\n")

        pr = fetch_branch_metadata(str(repo))
        assert pr.files == [{"path": "new.go", "additions": 3, "deletions": 0}]
        assert pr.additions == 3
        assert pr.deletions == 0

    def test_commits_on_base_are_not_reported_as_branch_changes(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        commit_all(repo, "add feat")
        # Move main forward behind the branch's back, so the branch is stale
        git_out(repo, "checkout", "main", "-q")
        (repo / "other.go").write_text("package main\nfunc other() {}\n")
        commit_all(repo, "add other")
        git_out(repo, "fetch", "-q", "origin", "main")
        git_out(repo, "checkout", "feat", "-q")

        pr = fetch_branch_metadata(str(repo))
        paths = [f["path"] for f in pr.files]
        assert paths == ["feat.go"]

    def test_the_base_ref_is_fetched_before_the_range_is_built(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        # Origin is added but never fetched, so origin/main does not resolve and
        # the fork point would collapse to HEAD — hiding every commit.
        git_out(repo, "remote", "add", "origin", str(repo))
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        commit_all(repo, "add feat")

        pr = fetch_branch_metadata(str(repo))
        assert [f["path"] for f in pr.files] == ["feat.go"]

    def test_base_argument_selects_the_diff_range(self, tmp_path):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        git_out(repo, "checkout", "-b", "develop", "-q")
        (repo / "dev.go").write_text("package main\nfunc dev() {}\n")
        commit_all(repo, "add dev")
        add_self_origin(repo)
        git_out(repo, "fetch", "-q", "origin", "develop")
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        commit_all(repo, "add feat")

        pr = fetch_branch_metadata(str(repo), "develop")
        assert [f["path"] for f in pr.files] == ["feat.go"]
        assert pr.base == "develop"

    def test_an_omitted_base_resolves_the_trunk_instead_of_assuming_main(
        self, tmp_path,
    ):
        """A `master` repository is diffed against origin/master.

        This is the no-PR self-review path, where nothing upstream names a base.
        The signature used to default to the literal "main", so every range here
        was against a ref the repository does not have.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        git_out(repo, "init", "-b", "master", "-q")
        git_out(repo, "config", "user.email", "test@test.com")
        git_out(repo, "config", "user.name", "Test")
        git_out(repo, "config", "commit.gpgsign", "false")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        git_out(repo, "remote", "add", "origin", str(repo))
        git_out(repo, "fetch", "-q", "origin", "master")
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        commit_all(repo, "add feat")

        pr = fetch_branch_metadata(str(repo))
        assert pr.base == "master"
        assert [f["path"] for f in pr.files] == ["feat.go"]


# ── worktree_diff ───────────────────────────────────────────────────────────


class TestWorktreeDiff:
    """One reader for the tracked, untracked and numstat halves of a range.

    `fetch_branch_metadata` used to assemble the numstat form itself, so the
    file list a self-review reported and the diff it sent came off two
    separately-spelled ranges that could disagree.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> Path:
        repo = init_repo(tmp_path / "repo")
        (repo / "tracked.go").write_text("package main\n")
        commit_all(repo, "init")
        (repo / "tracked.go").write_text("package main\nfunc edited() {}\n")
        (repo / "new.go").write_text("one\ntwo\nthree\n")
        return repo

    def test_patch_form_covers_tracked_and_untracked(self, tmp_path):
        diff = rc.worktree_diff(str(self._repo(tmp_path)), "HEAD")
        assert "func edited" in diff
        assert "three" in diff

    def test_numstat_form_names_the_same_files(self, tmp_path):
        numstat = rc.worktree_diff(str(self._repo(tmp_path)), "HEAD", counts_only=True)
        paths = sorted(line.split("\t")[-1] for line in numstat.splitlines())
        assert paths == ["new.go", "tracked.go"]
        assert "3\t0\tnew.go" in numstat
