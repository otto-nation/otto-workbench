"""Tests for push_status — detect_unpushed and render_status."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import push_status


def _make_result(returncode: int, stdout: str) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


# ── detect_unpushed ───────────────────────────────────────────────────────────


class TestDetectUnpushed:
    @patch("push_status.subprocess.run")
    def test_up_to_date(self, mock_run):
        mock_run.return_value = _make_result(0, "0\n")
        result = push_status.detect_unpushed(Path("/repo"), "main")
        assert result == 0

    @patch("push_status.subprocess.run")
    def test_ahead_commits(self, mock_run):
        mock_run.return_value = _make_result(0, "3\n")
        result = push_status.detect_unpushed(Path("/repo"), "feat/branch")
        assert result == 3

    @patch("push_status.subprocess.run")
    def test_counts_commits_ahead_of_remote(self, mock_run):
        """Range direction matters — an inverted range counts the wrong side."""
        mock_run.return_value = _make_result(0, "3\n")
        push_status.detect_unpushed(Path("/repo"), "feat/branch")
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "git", "rev-list", "--count", "origin/feat/branch..HEAD",
        ]
        assert kwargs["cwd"] == "/repo"

    @patch("push_status.subprocess.run")
    def test_nonzero_returncode_returns_none(self, mock_run):
        """Branch never pushed — git rev-list exits non-zero."""
        mock_run.return_value = _make_result(128, "fatal: unknown revision\n")
        result = push_status.detect_unpushed(Path("/repo"), "untracked-branch")
        assert result is None

    @patch("push_status.subprocess.run")
    def test_non_digit_output_returns_none(self, mock_run):
        mock_run.return_value = _make_result(0, "not-a-number\n")
        result = push_status.detect_unpushed(Path("/repo"), "main")
        assert result is None


# ── render_status ─────────────────────────────────────────────────────────────


class TestRenderStatus:
    def test_not_pushed(self):
        result = push_status.render_status(Path("/repo"), "main", ahead=None)
        assert result == ["**Push**: branch not pushed to remote"]

    def test_up_to_date(self):
        result = push_status.render_status(Path("/repo"), "main", ahead=0)
        assert result == ["**Push**: up to date"]

    def test_ahead_commits(self):
        result = push_status.render_status(Path("/repo"), "main", ahead=4)
        assert result == ["**Push**: 4 commit(s) not pushed"]

    @patch("push_status.detect_unpushed")
    def test_calls_detect_when_ahead_not_provided(self, mock_detect):
        mock_detect.return_value = 0
        push_status.render_status(Path("/repo"), "main")
        mock_detect.assert_called_once_with(Path("/repo"), "main")

    @patch("push_status.detect_unpushed")
    def test_does_not_call_detect_when_ahead_provided(self, mock_detect):
        push_status.render_status(Path("/repo"), "main", ahead=2)
        mock_detect.assert_not_called()
