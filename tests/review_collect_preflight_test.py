"""Tests for review.collect — preflight collection and the budget fit that sheds sparse files."""

from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import replace
from pathlib import Path

from conftest import add_self_origin, commit_all, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client
import review.budget
import review.collect
from gh.types import PRContext, PRMetadata
from review.collect import fetch_branch_metadata
from review.types import ReviewJob


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


# ── Sparse-file shedding under budget pressure ───────────────────────────────


def _ceiling_for(repo, monkeypatch, room: int) -> None:
    """Pin the collection budget so exactly ``room`` bytes are left for contents.

    The patch targets `rc` (`review.collect`), not `review.budget`, for the
    reason spelled out in `test_tier1_files_prioritized_over_tier2_when_budget_tight`:
    `collect_preflight_data` reads the name bound into its own module namespace.
    """
    diff_size = len(git.client.out(
        "diff", "origin/main...HEAD", cwd=str(repo),
    ).encode())
    ceiling = diff_size + review.budget.TEMPLATE_OVERHEAD_BYTES + room
    monkeypatch.setattr(review.collect, "collection_budget_bytes", lambda *_: ceiling)


class TestSparseFileShedding:
    def test_a_sparse_file_is_pre_collected_when_the_budget_has_room(self, tmp_path):
        (tmp_path / "big.py").write_text("x = 1\n" * 2000)
        job = _job(tmp_path, [{"path": "big.py", "additions": 2, "deletions": 1}])

        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert "big.py" in data.file_contents
        assert "big.py" not in data.omitted_files


    def test_small_file_always_included(self, tmp_path):
        (tmp_path / "small.py").write_text("x = 1\n")
        job = _job(tmp_path, [{"path": "small.py", "additions": 1, "deletions": 0}])

        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert "small.py" in data.file_contents

    def test_high_density_file_included(self, tmp_path):
        (tmp_path / "refactored.py").write_text("line\n" * 100)
        job = _job(
            tmp_path, [{"path": "refactored.py", "additions": 80, "deletions": 70}],
        )

        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert "refactored.py" in data.file_contents

    # The old gate dropped the sparse file too, by a different route. This case
    # holds the shed *ordering* the change keeps rather than the overflow guard
    # it adds, and fails if the ordering is inverted.
    # passes-at-base: pins the shed ordering, which this change preserves
    def test_the_budget_sheds_the_sparse_file_before_the_dense_one(
        self, tmp_path, monkeypatch,
    ):
        repo = init_repo(tmp_path / "repo")
        (repo / "sparse.py").write_text("x = 1\n" * 2000)
        (repo / "dense.py").write_text("y = 1\n" * 2000)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "sparse.py").write_text("x = 1\n" * 1999 + "x = 2\n")
        (repo / "dense.py").write_text("y = 2\n" * 2000)
        commit_all(repo, "change")

        job = _job(
            tmp_path,
            [
                {"path": "sparse.py", "additions": 1, "deletions": 1},
                {"path": "dense.py", "additions": 2000, "deletions": 2000},
            ],
            wt_path=str(repo),
        )
        # Room for one of the two 12KB files, not both.
        _ceiling_for(repo, monkeypatch, 14_000)
        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert "dense.py" in data.file_contents
        assert "sparse.py" in data.omitted_files

    def test_only_as_many_sparse_files_are_shed_as_the_shortfall_needs(
        self, tmp_path, monkeypatch,
    ):
        repo = init_repo(tmp_path / "repo")
        for name in ("a.py", "b.py", "c.py"):
            (repo / name).write_text(f"# {name}\n" + "x = 1\n" * 2000)
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        for name in ("a.py", "b.py", "c.py"):
            (repo / name).write_text(f"# {name}\n" + "x = 1\n" * 1999 + "x = 2\n")
        commit_all(repo, "change")

        job = _job(
            tmp_path,
            [
                {"path": name, "additions": 1, "deletions": 1}
                for name in ("a.py", "b.py", "c.py")
            ],
            wt_path=str(repo),
        )
        # Three ~12KB sparse files, room for two. One shortfall, one file shed.
        _ceiling_for(repo, monkeypatch, 26_000)
        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert len(data.omitted_files) == 1
        assert len(data.file_contents) == 2


# ── collect_preflight_data (git repo tests) ─────────────────────────────────


class TestCollectPreflightData:
    def test_a_pr_against_an_unpushed_base_still_sees_its_commits(self, tmp_path):
        """The PR path reads its diff and commit log through the same resolver.
        A base with no remote-tracking ref makes both ranges name a ref that
        does not exist — `git` exits non-zero and `git.client.out` reports that
        as empty, so the review is handed no diff and no log at all."""
        origin = tmp_path / "origin.git"
        git_out(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        git_out(repo, "remote", "add", "origin", str(origin))
        git_out(repo, "push", "-q", "origin", "main")
        git_out(repo, "checkout", "-b", "parent", "-q")
        (repo / "parent.go").write_text("package main\n")
        commit_all(repo, "add parent")
        git_out(repo, "checkout", "-b", "child", "-q")
        (repo / "child.go").write_text("package main\nfunc child() {}\n")
        commit_all(repo, "the child commit")

        job = _job(
            tmp_path, [{"path": "child.go", "additions": 2, "deletions": 0}],
            wt_path=str(repo),
            pr=replace(_job(tmp_path, []).pr, base="parent"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)

        assert "func child" in data.diff
        assert "package main\n+func parent" not in data.diff
        assert "the child commit" in data.commit_log
        assert "add parent" not in data.commit_log

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
            data = review.collect.collect_preflight_data(job)
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
            data = review.collect.collect_preflight_data(job)
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
            data = review.collect.collect_preflight_data(job)
        assert data is not None
        assert "main.go" in data.file_contents
        assert "main.go" in data.file_permissions
        assert data.file_permissions["main.go"] != "?"
        assert "# Project" in data.instructions_md
        assert "## Known Constraints" in data.architecture_md
        assert "security.md" in data.review_checklists
        assert len(data.diff) > 0
        assert len(data.commit_log) > 0
        assert data.omitted_files == []

    def test_agents_md_is_read_over_a_legacy_claude_md_and_named_in_the_prompt(self, tmp_path):
        # The order belongs to core.project_context; what this pins is that the
        # preflight reads through it, and that the prompt names the file the
        # repo actually uses rather than always saying CLAUDE.md.
        repo = tmp_path / "repo"
        init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        (repo / "AGENTS.md").write_text("# From AGENTS\n")
        (repo / "CLAUDE.md").write_text("# From CLAUDE\n")
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
            data = review.collect.collect_preflight_data(job)
        assert data is not None
        assert data.instructions_md == "# From AGENTS\n"
        assert data.instructions_path == "AGENTS.md"
        assert "#### AGENTS.md" in review.collect.build_project_context(data)

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
            data = review.collect.collect_preflight_data(job)
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
            data = review.collect.collect_preflight_data(job)
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
            data = review.collect.collect_preflight_data(job)
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
            data = review.collect.collect_preflight_data(job)
        assert "func committed" in data.diff
        assert "func uncommitted" not in data.diff
        assert "func untracked" not in data.diff

    def test_sparse_large_file_kept_when_the_budget_has_room(self, tmp_path):
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
            data = review.collect.collect_preflight_data(job)
        assert "big.py" in data.file_contents
        assert "big.py" not in data.omitted_files

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
        # The patch targets `rc` (`review.collect`), not `review.budget`, because
        # `collect_preflight_data` reads the name `review.collect` bound into its
        # own module namespace when it imported it — patching `review.budget`
        # would rebind a name `review.collect` already copied, which the code
        # under test would never see. `TEMPLATE_OVERHEAD_BYTES` on the same line
        # is a plain read rather than a patch, so it names its owner directly.
        diff_size = len(git.client.out(
            "diff", "origin/main...HEAD", cwd=str(repo),
        ).encode())
        ceiling = diff_size + review.budget.TEMPLATE_OVERHEAD_BYTES + 1000
        monkeypatch.setattr(review.collect, "collection_budget_bytes", lambda *_: ceiling)
        with contextlib.redirect_stdout(io.StringIO()):
            data = review.collect.collect_preflight_data(job)
        assert "CLAUDE.md" in data.file_contents
        assert "util.go" in data.omitted_files
