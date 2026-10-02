"""Tests for rebase.inspect: rebase state, conflicts, empty patches and status lines."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from conftest import init_worktree

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import core.timeouts  # noqa: E402


# ── _detect_rebase_in_progress ──────────────────────────────────────────────


def test_detect_rebase_not_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(tmpdir) is False


def test_detect_rebase_merge_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(tmpdir) is True


def test_detect_rebase_apply_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.rebase_in_progress(tmpdir) is True


# ── _detect_conflicts ───────────────────────────────────────────────────────


def test_detect_conflicts_parses_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/a.py\nsrc/b.py\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase.inspect.detect_conflicts("/fake")
    assert result == ["src/a.py", "src/b.py"]


def test_detect_conflicts_empty_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase.inspect.detect_conflicts("/fake")
    assert result == []


# ── _remaining_rebase_commits ───────────────────────────────────────────────


def test_remaining_rebase_commits_no_rebase():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(tmpdir) == 0


def test_remaining_rebase_commits_from_todo():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        rebase_dir = git_dir / "rebase-merge"
        rebase_dir.mkdir()
        (rebase_dir / "git-rebase-todo").write_text(
            "pick abc123 first commit\n"
            "pick def456 second commit\n"
            "# this is a comment\n"
            "fixup ghi789 squash me\n"
        )
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(tmpdir) == 3


def test_remaining_rebase_commits_from_apply():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        apply_dir = git_dir / "rebase-apply"
        apply_dir.mkdir()
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        with mock.patch.object(rebase.inspect, "git_dir", return_value=git_dir):
            assert rebase.inspect.remaining_rebase_commits(tmpdir) == 4


# ── ConflictReport ─────────────────────────────────────────────────────────


@mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=2)
@mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc1234", "fix: thing"))
@mock.patch.object(rebase.inspect, "detect_conflicts", return_value=["a.py"])
def test_conflict_report_structure(_m1, _m2, _m3):
    report = rebase.types.ConflictReport.from_repo("/fake")
    assert report.status == "conflicts"
    assert report.files == ["a.py"]
    assert report.rebase_head == "abc1234"
    assert report.rebase_head_subject == "fix: thing"
    assert report.remaining_commits == 2


@mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=0)
@mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("def5678", "feat: other"))
@mock.patch.object(rebase.inspect, "detect_conflicts", return_value=["b.py"])
def test_conflict_report_custom_status(_m1, _m2, _m3):
    report = rebase.types.ConflictReport.from_repo("/fake", status="conflicts_resuming")
    assert report.status == "conflicts_resuming"


# ── _is_empty_patch ──────────────────────────────────────────────────────


def test_is_empty_patch_both_clean():
    """Empty patch: no staged or unstaged changes."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.inspect.is_empty_patch("/fake") is True


def test_is_empty_patch_staged_changes():
    """Not empty: staged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.inspect.is_empty_patch("/fake") is False


def test_is_empty_patch_unstaged_changes():
    """Not empty: unstaged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" not in cmd and "--quiet" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.inspect.is_empty_patch("/fake") is False


# ── _status_lines ──────────────────────────────────────────────────────────


def test_status_lines_reads_a_clean_worktree_as_empty(tmp_path):
    """Half the contract: clean is an empty list, and an empty list is not None."""
    init_worktree(tmp_path)
    assert rebase.inspect.status_lines(str(tmp_path)) == []


def test_status_lines_cannot_read_a_path_that_is_not_a_repo(tmp_path):
    """The other half: a read that failed is None, not a tree with nothing in it."""
    assert rebase.inspect.status_lines(str(tmp_path)) is None


def test_status_lines_cannot_read_a_worktree_with_a_broken_index(tmp_path):
    init_worktree(tmp_path)
    (tmp_path / ".git" / "index").write_bytes(b"garbage")
    assert rebase.inspect.status_lines(str(tmp_path)) is None


def test_status_lines_folds_a_timeout_into_the_same_answer():
    """`subprocess.run(timeout=)` raises rather than returning non-zero.

    Uncaught, the exception aborts the command from inside a read whose whole
    job is to decide whether the rebase is safe to start.
    """
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, core.timeouts.LOCAL)

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.inspect.status_lines("/fake") is None
