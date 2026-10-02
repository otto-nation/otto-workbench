"""Tests for review.collect — branch metadata for a self-review and the worktree diff."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import add_self_origin, commit_all, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.collect
from review.collect import fetch_branch_metadata


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

    def test_an_unpushed_stack_parent_anchors_on_the_local_branch(self, tmp_path):
        """A stack whose parent exists only locally. `origin/<parent>` resolves
        to nothing, and without the local fallback every range collapses: the
        fork point degrades to HEAD and the review covers no commits at all —
        silently, since an empty diff reads as a branch that changed nothing.

        Origin is a real second repository rather than `add_self_origin`: with
        the repo as its own remote, `fetch_base` creates `origin/parent` out of
        the local branch and the case under test cannot arise.
        """
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
        (repo / "child.go").write_text("package main\n")
        commit_all(repo, "add child")

        pr = fetch_branch_metadata(str(repo), "parent")

        assert [f["path"] for f in pr.files] == ["child.go"]

    def test_a_pushed_base_is_measured_against_the_remote_not_the_local_ref(
        self, tmp_path,
    ):
        """The two name different commits whenever the local branch is behind,
        and the review must not depend on a fetch it does not control.

        Built so the answer is observable rather than cosmetic: the local
        `main` is left an ancestor of HEAD — so the narrowed local fallback
        would accept it — while `origin/main` sits further back. Anchoring on
        the local ref would drop `moved.go` from the review.
        """
        origin = tmp_path / "origin.git"
        git_out(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        git_out(repo, "remote", "add", "origin", str(origin))
        git_out(repo, "push", "-q", "origin", "main")
        # main moves on locally, and is never pushed
        (repo / "moved.go").write_text("package main\n")
        commit_all(repo, "move main")
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\n")
        commit_all(repo, "add feat")

        assert review.collect.base_ref(str(repo), "main") == "origin/main"

        pr = fetch_branch_metadata(str(repo), "main")
        assert [f["path"] for f in pr.files] == ["feat.go", "moved.go"]

    def test_the_commit_log_spans_the_same_range_as_the_diff(self, tmp_path):
        """The log and the file list are read separately and must agree. An
        unpushed stack parent is where they come apart: a log range spelled
        `origin/<parent>` against a ref that does not exist exits non-zero and
        reports nothing, beside a file list that found the commits."""
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
        (repo / "child.go").write_text("package main\n")
        commit_all(repo, "the child commit")

        pr = fetch_branch_metadata(str(repo), "parent")

        # The title comes off the log range: empty output falls back to the
        # branch name, so the subject proves the log saw the same commits.
        assert pr.title == "the child commit"

    def test_a_local_ref_at_head_is_not_a_base(self, tmp_path):
        """An unfetched clone sitting on its own base branch. Anchoring there
        makes every range empty — the diff spans two names for one commit, and
        the delta's ancestry walk excludes the commits it is asking about."""
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")

        assert review.collect.base_ref(str(repo), "main") == ""

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
        diff = review.collect.worktree_diff(str(self._repo(tmp_path)), "HEAD")
        assert "func edited" in diff
        assert "three" in diff

    def test_numstat_form_names_the_same_files(self, tmp_path):
        numstat = review.collect.worktree_diff(str(self._repo(tmp_path)), "HEAD", counts_only=True)
        paths = sorted(line.split("\t")[-1] for line in numstat.splitlines())
        assert paths == ["new.go", "tracked.go"]
        assert "3\t0\tnew.go" in numstat
