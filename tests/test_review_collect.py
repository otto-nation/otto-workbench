"""Tests for review_collect — collection, budget fit, and the preflight block."""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import add_self_origin, commit_all, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git_client
import review_budget
import review_collect as rc
from review_collect import fetch_branch_metadata
from review_types import PRContext, PreflightData, PRMetadata, ReviewJob


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
        result = rc.format_preflight_data(data)
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
        result = rc.format_preflight_data(data, file_filter=["foo.go"])
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
        result = rc.format_preflight_data(data, file_filter=["foo.go"])
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
        assert "Commit history" not in rc.format_preflight_data(data)

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
        result = rc.format_preflight_data(data)
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
        assert "Files not pre-collected" not in rc.format_preflight_data(data)

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
        result = rc.format_preflight_data(data, skip_file_contents=True)
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
        result = rc.format_preflight_data(data, max_diff_bytes=500)
        assert "### Diffs not pre-collected" in result
        assert "- big.go" in result


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
    def test_prior_sha_equals_head_sha_returns_empty(self):
        sha = "abc1234def5678901234567890abcdef12345678"
        prior_review = f"<!-- head_sha: {sha} -->\nsome review content"
        job = _delta_job(head_sha=sha, prior_review=prior_review)
        assert rc._collect_delta(job) == ("", "", [], "")

    def test_no_prior_review_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="")
        assert rc._collect_delta(job) == ("", "", [], "")

    def test_prior_review_without_sha_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="no sha marker here")
        assert rc._collect_delta(job) == ("", "", [], "")


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
        delta_diff, _, delta_files, _ = rc._collect_delta(self._job(tmp_path, "self"))
        capsys.readouterr()
        assert "func committed" in delta_diff
        assert "func uncommitted" in delta_diff
        assert "func untracked" in delta_diff
        assert sorted(delta_files) == ["committed.go", "reviewed.go", "untracked.go"]

    def test_pr_mode_delta_stops_at_head(self, tmp_path, capsys):
        delta_diff, _, delta_files, _ = rc._collect_delta(self._job(tmp_path, "pr"))
        capsys.readouterr()
        assert "func committed" in delta_diff
        assert "func uncommitted" not in delta_diff
        assert delta_files == ["committed.go"]


class TestCollectDeltaSurface:
    """The delta is bounded by the PR, not by what the base branch did.

    `prior_sha..HEAD` spans the base as well as the branch, so a rebase onto a
    moved base puts every commit the base gained into the delta. One 107-file
    review reported 4,974 changed files that way, and the file list alone —
    260KB — pushed the synthesis prompt 75% past its budget. It also defeated
    incremental group skipping: with every group's files in the delta set,
    nothing was skipped and the re-review cost a full one.
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
        delta_diff, delta_log, delta_files, _ = rc._collect_delta(job)
        capsys.readouterr()
        assert delta_files == ["mine.go"]
        assert "func mine" in delta_diff
        assert "theirs.go" not in delta_diff
        assert "theirs.go" not in delta_log

    def test_a_job_with_no_surface_keeps_the_whole_range(self, tmp_path, capsys):
        """Branch reviews reach `_collect_delta` before the file list exists."""
        _, _, delta_files, _ = rc._collect_delta(self._job(tmp_path, []))
        capsys.readouterr()
        assert sorted(delta_files) == ["mine.go", "theirs.go"]


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


# ── parse_numstat ───────────────────────────────────────────────────────────


class TestParseNumstat:
    def test_normal_output(self):
        counts = rc.parse_numstat("10\t5\tpkg/handler.go\n3\t1\tpkg/util.go\n")
        assert len(counts.files) == 2
        assert counts.files[0] == {
            "path": "pkg/handler.go", "additions": 10, "deletions": 5,
        }
        assert counts.additions == 13
        assert counts.deletions == 6

    def test_binary_files(self):
        counts = rc.parse_numstat("-\t-\timage.png\n5\t2\tfile.go\n")
        assert len(counts.files) == 2
        assert counts.files[0]["additions"] == 0
        assert counts.files[0]["deletions"] == 0
        assert counts.additions == 5
        assert counts.deletions == 2

    def test_empty_input(self):
        counts = rc.parse_numstat("")
        assert counts.files == []
        assert counts.additions == 0
        assert counts.deletions == 0


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
        numstat = rc.worktree_diff(str(self._repo(tmp_path)), "HEAD", numstat=True)
        paths = sorted(line.split("\t")[-1] for line in numstat.splitlines())
        assert paths == ["new.go", "tracked.go"]
        assert "3\t0\tnew.go" in numstat
