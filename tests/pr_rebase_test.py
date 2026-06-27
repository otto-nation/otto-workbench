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


@mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2)
@mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc1234", "fix: thing"))
@mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["a.py"])
def test_conflict_report_structure(_m1, _m2, _m3):
    report = pr_rebase_cli._conflict_report("/fake")
    assert report["status"] == "conflicts"
    assert report["files"] == ["a.py"]
    assert report["rebase_head"] == "abc1234"
    assert report["rebase_head_subject"] == "fix: thing"
    assert report["remaining_commits"] == 2


@mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0)
@mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("def5678", "feat: other"))
@mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["b.py"])
def test_conflict_report_custom_status(_m1, _m2, _m3):
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


def test_is_binary_detects_null_bytes(tmp_path):
    binary_file = tmp_path / "file.bin"
    binary_file.write_bytes(b"hello\x00world")
    assert pr_rebase_cli._is_binary(binary_file) is True


def test_is_binary_text_file(tmp_path):
    text_file = tmp_path / "file.txt"
    text_file.write_text("hello world\n")
    assert pr_rebase_cli._is_binary(text_file) is False


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


def test_parse_resolved_content_no_markers_returns_none():
    stdout = "resolved content without markers\n"
    result = pr_rebase_cli._parse_resolved_content(stdout)
    assert result is None


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
            calls.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.sum"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["go.sum"]
        cmds = [c[0] for c in calls]
        assert ["git", "checkout", "--theirs", "go.sum"] in cmds
        assert ["git", "add", "go.sum"] in cmds
        # Verify cwd is set on git commands
        for cmd, cwd in calls:
            if cmd[0] == "git":
                assert cwd == tmpdir


def test_resolve_file_conflicts_calls_claude():
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_content = "<<<<<<< HEAD\nold code\n=======\nnew code\n>>>>>>> abc123\n"
        conflict_file.write_text(conflict_content)

        resolved_output = "<<<RESOLVED>>>\nmerged code\n<<<END_RESOLVED>>>\n"

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
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
        # git add must target the worktree
        git_add_calls = [(c, w) for c, w in calls if c == ["git", "add", "main.go"]]
        assert len(git_add_calls) == 1
        assert git_add_calls[0][1] == tmpdir


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


def test_resolve_file_conflicts_claude_exit0_with_conflict_markers():
    """S4: claude returns exit 0 but output still contains conflict markers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        bad_output = "<<<RESOLVED>>>\n<<<<<<< HEAD\nstill broken\n=======\nstill bad\n>>>>>>> abc\n<<<END_RESOLVED>>>\n"

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=bad_output, stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
            )

        assert result is None


def test_resolve_file_conflicts_git_add_failure_returns_none():
    """M1: git add failure must abort, not loop infinitely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        resolved_output = "<<<RESOLVED>>>\nmerged\n<<<END_RESOLVED>>>\n"

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=resolved_output, stderr="",
                )
            if cmd == ["git", "add", "main.go"]:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
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
        # N4: verify go.sum staging after tidy
        tidy_idx = calls.index(["go", "mod", "tidy"])
        post_tidy = calls[tidy_idx + 1:]
        assert ["git", "add", "go.sum"] in post_tidy


# ── _resolve_and_continue ─────────────────────────────────────────────────


def test_resolve_and_continue_no_claude():
    with mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._resolve_and_continue("/fake")
    assert result is None


def test_resolve_and_continue_success():
    """Single-commit resolution: conflicts → resolve → continue → done."""
    conflict_round = [0]

    def fake_detect_conflicts(cwd):
        conflict_round[0] += 1
        if conflict_round[0] == 1:
            return ["file.go"]
        return []

    with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", side_effect=fake_detect_conflicts), \
         mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=["file.go"]), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        result = pr_rebase_cli._resolve_and_continue("/fake")

    assert result == (["file.go"], 1)


def test_resolve_and_continue_resolution_failure():
    """_resolve_file_conflicts returns None → loop aborts."""
    with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["file.go"]), \
         mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=None):
        result = pr_rebase_cli._resolve_and_continue("/fake")

    assert result is None


# ── _fresh ──────────────────────────────────────────────────────────────────


def test_fresh_preflight_allows_untracked_files():
    """Preflight passes when tracked tree is clean; --untracked-files=no flag is used."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    status_cmds = []

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            status_cmds.append(list(cmd))
            # Simulate clean tracked tree — untracked files are ignored by the flag
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, False)

    assert result == 0
    assert len(status_cmds) == 1
    assert "--untracked-files=no" in status_cmds[0]


def test_fresh_delegates_to_resume_on_paused_rebase():
    """_fresh calls _resume when rebase exits non-zero, no conflicts, but rebase is still in progress."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # git rebase fails with non-zero (no conflicts — rebase paused awaiting --continue)
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=[]), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_resume", return_value=0) as mock_resume:
        result = pr_rebase_cli._fresh("/fake", ctx, False)

    assert result == 0
    mock_resume.assert_called_once_with("/fake", ctx, False)
