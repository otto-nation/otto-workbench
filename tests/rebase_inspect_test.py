"""Tests for rebase.inspect — rebase-state detection and ref reads."""

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import client as git_client
from rebase import inspect as rebase_inspect


class TestRebaseInProgress:
    """rebase_in_progress detects merge and apply directories."""

    def test_merge_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(str(tmp_path)) is True

    def test_apply_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(str(tmp_path)) is True

    def test_no_rebase(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(str(tmp_path)) is False


class TestRemainingRebaseCommits:
    """remaining_rebase_commits counts from todo or apply."""

    def test_no_rebase(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(str(tmp_path)) == 0

    def test_from_todo(self, tmp_path):
        git_dir = tmp_path / ".git"
        (git_dir / "rebase-merge").mkdir(parents=True)
        (git_dir / "rebase-merge" / "git-rebase-todo").write_text(
            "pick abc123 first\n"
            "pick def456 second\n"
            "# comment\n"
            "pick ghi789 third\n"
        )
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(str(tmp_path)) == 3

    def test_from_apply(self, tmp_path):
        git_dir = tmp_path / ".git"
        apply_dir = git_dir / "rebase-apply"
        apply_dir.mkdir(parents=True)
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(str(tmp_path)) == 4


class TestDetectConflicts:
    """detect_conflicts returns files from git diff."""

    def test_returns_files(self):
        with mock.patch.object(git_client, "lines", return_value=["a.py", "b.py"]):
            assert rebase_inspect.detect_conflicts("/fake") == ["a.py", "b.py"]

    def test_empty(self):
        with mock.patch.object(git_client, "lines", return_value=[]):
            assert rebase_inspect.detect_conflicts("/fake") == []


class TestRefExists:
    """ref_exists checks rev-parse."""

    def test_exists(self):
        with mock.patch.object(git_client, "ok", return_value=True):
            assert rebase_inspect.ref_exists("/fake", "HEAD") is True

    def test_not_exists(self):
        with mock.patch.object(git_client, "ok", return_value=False):
            assert rebase_inspect.ref_exists("/fake", "nonexistent") is False


class TestSharesHistory:
    """shares_history handles missing refs."""

    def test_missing_ref(self):
        with mock.patch.object(rebase_inspect, "ref_exists", return_value=False):
            assert rebase_inspect.shares_history("/fake", target_ref="missing") is True

    def test_shared(self):
        with mock.patch.object(rebase_inspect, "ref_exists", return_value=True), \
             mock.patch.object(git_client, "ok", return_value=True):
            assert rebase_inspect.shares_history("/fake", target_ref="main") is True

    def test_unrelated(self):
        with mock.patch.object(rebase_inspect, "ref_exists", return_value=True), \
             mock.patch.object(git_client, "ok", return_value=False):
            assert rebase_inspect.shares_history("/fake", target_ref="orphan") is False
