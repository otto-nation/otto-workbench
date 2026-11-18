"""Tests for rebase.inspect — rebase-state detection and ref reads."""

import sys
from pathlib import Path
from unittest import mock

from conftest import commit_all, git_in, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client
import rebase.inspect


class TestRebaseInProgress:
    """rebase_in_progress detects merge and apply directories."""

    def test_merge_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(str(tmp_path)) is True

    def test_apply_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(str(tmp_path)) is True

    def test_no_rebase(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(str(tmp_path)) is False


class TestRemainingRebaseCommits:
    """remaining_rebase_commits counts from todo or apply."""

    def test_no_rebase(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(str(tmp_path)) == 0

    def test_from_todo(self, tmp_path):
        git_dir = tmp_path / ".git"
        (git_dir / "rebase-merge").mkdir(parents=True)
        (git_dir / "rebase-merge" / "git-rebase-todo").write_text(
            "pick abc123 first\n"
            "pick def456 second\n"
            "# comment\n"
            "pick ghi789 third\n"
        )
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(str(tmp_path)) == 3

    def test_from_apply(self, tmp_path):
        git_dir = tmp_path / ".git"
        apply_dir = git_dir / "rebase-apply"
        apply_dir.mkdir(parents=True)
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(str(tmp_path)) == 4


class TestDetectConflicts:
    """detect_conflicts returns files from git diff."""

    def test_returns_files(self):
        with mock.patch.object(git.client, "lines", return_value=["a.py", "b.py"]):
            assert rebase.inspect.detect_conflicts("/fake") == ["a.py", "b.py"]

    def test_empty(self):
        with mock.patch.object(git.client, "lines", return_value=[]):
            assert rebase.inspect.detect_conflicts("/fake") == []


class TestRefExists:
    """ref_exists checks rev-parse."""

    def test_exists(self):
        with mock.patch.object(git.client, "ok", return_value=True):
            assert rebase.inspect.ref_exists("/fake", "HEAD") is True

    def test_not_exists(self):
        with mock.patch.object(git.client, "ok", return_value=False):
            assert rebase.inspect.ref_exists("/fake", "nonexistent") is False


class TestSharesHistory:
    """shares_history handles missing refs."""

    def test_missing_ref(self):
        with mock.patch.object(rebase.inspect, "ref_exists", return_value=False):
            assert rebase.inspect.shares_history("/fake", target_ref="missing") is True

    def test_shared(self):
        with mock.patch.object(rebase.inspect, "ref_exists", return_value=True), \
             mock.patch.object(git.client, "ok", return_value=True):
            assert rebase.inspect.shares_history("/fake", target_ref="main") is True

    def test_unrelated(self):
        with mock.patch.object(rebase.inspect, "ref_exists", return_value=True), \
             mock.patch.object(git.client, "ok", return_value=False):
            assert rebase.inspect.shares_history("/fake", target_ref="orphan") is False


def _commit_file(repo: Path, name: str, body: str, message: str) -> None:
    (repo / name).write_text(body)
    git_in(repo, "add", name)
    git_in(repo, "commit", "-q", "--no-verify", "-m", message)


def _stage_ours(repo: Path, name: str) -> None:
    git_in(repo, "checkout", "--ours", "--", name)
    git_in(repo, "add", name)


def _last_rewritten_new(repo: Path) -> str:
    merge_dir = rebase.inspect.git_dir(str(repo)) / rebase.inspect.GIT_REBASE_MERGE_DIR
    last = ""
    for line in (merge_dir / "rewritten-list").read_text().splitlines():
        parts = line.split()
        last = parts[1] if len(parts) >= 2 else last
    return last


def test_step_committed_manually_distinguishes_manual_commit_from_empty_patch(tmp_path):
    """A clean tree after `git commit` is not an empty upstream patch."""
    repo = init_repo(tmp_path / "case_a")
    _commit_file(repo, "f.txt", "base\n", "base")
    git_in(repo, "checkout", "-q", "-b", "feat")
    _commit_file(repo, "f.txt", "feat\n", "feat1")
    git_in(repo, "checkout", "-q", "main")
    _commit_file(repo, "f.txt", "main\n", "main")
    git_in(repo, "checkout", "-q", "feat")
    paused = git.client.run("rebase", "main", cwd=str(repo))
    assert not paused.ok
    assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]

    _stage_ours(repo, "f.txt")
    assert rebase.inspect.is_empty_patch(str(repo))
    assert rebase.inspect.step_committed_manually(str(repo)) is False

    _commit_file(repo, "f.txt", "feat\n", "manual step 1")
    merge_dir = rebase.inspect.git_dir(str(repo)) / rebase.inspect.GIT_REBASE_MERGE_DIR
    assert (merge_dir / "stopped-sha").is_file()
    assert rebase.inspect.is_empty_patch(str(repo))
    assert rebase.inspect.step_committed_manually(str(repo)) is True

    repo = init_repo(tmp_path / "case_b")
    (repo / "f.txt").write_text("f-base\n")
    (repo / "g.txt").write_text("g-base\n")
    commit_all(repo, "base")
    git_in(repo, "checkout", "-q", "-b", "feat")
    _commit_file(repo, "f.txt", "f-feat\n", "feat1")
    _commit_file(repo, "g.txt", "g-feat\n", "feat2")
    git_in(repo, "checkout", "-q", "main")
    _commit_file(repo, "g.txt", "g-main\n", "main")
    git_in(repo, "checkout", "-q", "feat")
    paused = git.client.run("rebase", "main", cwd=str(repo))
    assert not paused.ok
    assert rebase.inspect.detect_conflicts(str(repo)) == ["g.txt"]

    head = git.client.out("rev-parse", "HEAD", cwd=str(repo))
    assert head == _last_rewritten_new(repo)
    _stage_ours(repo, "g.txt")
    assert rebase.inspect.is_empty_patch(str(repo))
    assert rebase.inspect.step_committed_manually(str(repo)) is False

    _commit_file(repo, "g.txt", "g-feat\n", "manual step 2")
    assert rebase.inspect.is_empty_patch(str(repo))
    assert rebase.inspect.step_committed_manually(str(repo)) is True
