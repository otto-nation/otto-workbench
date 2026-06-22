"""Tests for pr-rebase helper functions."""

import importlib.util
import importlib.machinery
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless pr-rebase script via importlib
_pr_rebase_path = str(BIN_DIR / "pr-rebase")
_loader = importlib.machinery.SourceFileLoader("pr_rebase_cli", _pr_rebase_path)
_spec = importlib.util.spec_from_loader("pr_rebase_cli", _loader, origin=_pr_rebase_path)
pr_rebase_cli = importlib.util.module_from_spec(_spec)
pr_rebase_cli.__file__ = _pr_rebase_path
_spec.loader.exec_module(pr_rebase_cli)


# ── _detect_rebase_in_progress ──────────────────────────────────────────────


def _make_git_repo(tmpdir: str) -> str:
    """Create a minimal git repo and return its path."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"}
    subprocess.run(["git", "init", tmpdir], capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "-c", "commit.gpgSign=false", "commit", "--allow-empty", "-m", "init"],
        capture_output=True, cwd=tmpdir, check=True, env=env,
    )
    return tmpdir


def test_detect_rebase_not_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is False


def test_detect_rebase_merge_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        (Path(tmpdir) / ".git" / "rebase-merge").mkdir()
        assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is True


def test_detect_rebase_apply_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        (Path(tmpdir) / ".git" / "rebase-apply").mkdir()
        assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is True


# ── _detect_conflicts ───────────────────────────────────────────────────────


def test_detect_conflicts_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        assert pr_rebase_cli._detect_conflicts(tmpdir) == []


def test_detect_conflicts_parses_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/a.py\nsrc/b.py\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = pr_rebase_cli._detect_conflicts("/fake")
    assert result == ["src/a.py", "src/b.py"]


def test_detect_conflicts_empty_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = pr_rebase_cli._detect_conflicts("/fake")
    assert result == []


# ── _remaining_rebase_commits ───────────────────────────────────────────────


def test_remaining_rebase_commits_no_rebase():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        assert pr_rebase_cli._remaining_rebase_commits(tmpdir) == 0


def test_remaining_rebase_commits_from_todo():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        rebase_dir = Path(tmpdir) / ".git" / "rebase-merge"
        rebase_dir.mkdir()
        todo = rebase_dir / "git-rebase-todo"
        todo.write_text(
            "pick abc123 first commit\n"
            "pick def456 second commit\n"
            "# this is a comment\n"
            "fixup ghi789 squash me\n"
        )
        assert pr_rebase_cli._remaining_rebase_commits(tmpdir) == 3


def test_remaining_rebase_commits_from_apply():
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_git_repo(tmpdir)
        apply_dir = Path(tmpdir) / ".git" / "rebase-apply"
        apply_dir.mkdir()
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        assert pr_rebase_cli._remaining_rebase_commits(tmpdir) == 4


# ── _conflict_report ───────────────────────────────────────────────────────


def test_conflict_report_structure():
    with mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["a.py"]):
        with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc1234", "fix: thing")):
            with mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2):
                report = pr_rebase_cli._conflict_report("/fake")
    assert report["status"] == "conflicts"
    assert report["files"] == ["a.py"]
    assert report["rebase_head"] == "abc1234"
    assert report["rebase_head_subject"] == "fix: thing"
    assert report["remaining_commits"] == 2


def test_conflict_report_custom_status():
    with mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["b.py"]):
        with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("def5678", "feat: other")):
            with mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0):
                report = pr_rebase_cli._conflict_report("/fake", status="conflicts_resuming")
    assert report["status"] == "conflicts_resuming"


# ── _commits_ahead ──────────────────────────────────────────────────────────


def test_commits_ahead_parses_count():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="5\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        assert pr_rebase_cli._commits_ahead("/fake") == 5


def test_commits_ahead_non_numeric():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        assert pr_rebase_cli._commits_ahead("/fake") == 0
