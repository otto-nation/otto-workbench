"""Tests for pr-rebase helper functions."""

import importlib.util
import importlib.machinery
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


def test_detect_rebase_not_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
            assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is False


def test_detect_rebase_merge_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
            assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is True


def test_detect_rebase_apply_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
            assert pr_rebase_cli._detect_rebase_in_progress(tmpdir) is True


# ── _detect_conflicts ───────────────────────────────────────────────────────


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
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
            assert pr_rebase_cli._remaining_rebase_commits(tmpdir) == 0


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
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
            assert pr_rebase_cli._remaining_rebase_commits(tmpdir) == 3


def test_remaining_rebase_commits_from_apply():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        apply_dir = git_dir / "rebase-apply"
        apply_dir.mkdir()
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        with mock.patch.object(pr_rebase_cli, "_git_dir", return_value=git_dir):
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


# ── _is_binary ─────────────────────────────────────────────────────────────


def test_is_binary_detects_null_bytes():
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"hello\x00world")
        f.flush()
        assert pr_rebase_cli._is_binary(Path(f.name)) is True


def test_is_binary_text_file():
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("hello world\n")
        f.flush()
        assert pr_rebase_cli._is_binary(Path(f.name)) is False


def test_is_binary_missing_file():
    assert pr_rebase_cli._is_binary(Path("/nonexistent/file.bin")) is False


# ── _build_resolve_prompt ──────────────────────────────────────────────────


def test_build_resolve_prompt_includes_context():
    prompt = pr_rebase_cli._build_resolve_prompt(
        "src/auth.py", "<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n",
        "abc123", "fix: auth refresh",
    )
    assert "src/auth.py" in prompt
    assert "abc123" in prompt
    assert "fix: auth refresh" in prompt
    assert "<<<RESOLVED>>>" in prompt
    assert "<<<END_RESOLVED>>>" in prompt
    assert "<<<<<<< HEAD" in prompt


# ── _parse_resolved_content ───────────────────────────────────────────────


def test_parse_resolved_content_with_markers():
    stdout = "Some preamble\n<<<RESOLVED>>>\nline1\nline2\n<<<END_RESOLVED>>>\nSome epilogue"
    result = pr_rebase_cli._parse_resolved_content(stdout)
    assert result == "line1\nline2\n"


def test_parse_resolved_content_preserves_internal_blank_lines():
    stdout = "<<<RESOLVED>>>\nline1\n\nline3\n<<<END_RESOLVED>>>"
    result = pr_rebase_cli._parse_resolved_content(stdout)
    assert result == "line1\n\nline3\n"


def test_parse_resolved_content_fallback_no_markers():
    stdout = "resolved content without markers\n"
    result = pr_rebase_cli._parse_resolved_content(stdout)
    assert result == "resolved content without markers\n"


def test_parse_resolved_content_rejects_unresolved():
    stdout = "<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n"
    result = pr_rebase_cli._parse_resolved_content(stdout)
    assert result is None


def test_parse_resolved_content_empty_stdout():
    result = pr_rebase_cli._parse_resolved_content("")
    assert result is None


def test_parse_resolved_content_whitespace_only():
    result = pr_rebase_cli._parse_resolved_content("   \n  \n")
    assert result is None


# ── _resolve_file_conflicts ───────────────────────────────────────────────


def test_resolve_file_conflicts_skips_binary():
    with tempfile.TemporaryDirectory() as tmpdir:
        binary_file = Path(tmpdir) / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        result = pr_rebase_cli._resolve_file_conflicts(
            ["image.png"], tmpdir, "abc123", "feat: add image",
        )
        assert result is None


def test_resolve_file_conflicts_handles_go_sum():
    with tempfile.TemporaryDirectory() as tmpdir:
        go_sum = Path(tmpdir) / "go.sum"
        go_sum.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.sum"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["go.sum"]
        assert ["git", "checkout", "--theirs", "go.sum"] in calls
        assert ["git", "add", "go.sum"] in calls


def test_resolve_file_conflicts_calls_claude():
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_content = "<<<<<<< HEAD\nold code\n=======\nnew code\n>>>>>>> abc123\n"
        conflict_file.write_text(conflict_content)

        resolved_output = "<<<RESOLVED>>>\nmerged code\n<<<END_RESOLVED>>>\n"

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout=resolved_output, stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
            )

        assert result == ["main.go"]
        assert conflict_file.read_text() == "merged code\n"


def test_resolve_file_conflicts_claude_failure_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="error",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
            )

        assert result is None


def test_resolve_file_conflicts_go_mod_triggers_tidy():
    with tempfile.TemporaryDirectory() as tmpdir:
        go_mod = Path(tmpdir) / "go.mod"
        go_mod.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        go_sum = Path(tmpdir) / "go.sum"
        go_sum.write_text("existing sum")

        resolved_output = "<<<RESOLVED>>>\nmodule example.com\n<<<END_RESOLVED>>>\n"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout=resolved_output, stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.mod"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["go.mod"]
        assert ["go", "mod", "tidy"] in calls


# ── _resolve_and_continue ─────────────────────────────────────────────────


def test_resolve_and_continue_no_claude():
    with mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._resolve_and_continue("/fake")
    assert result is None
