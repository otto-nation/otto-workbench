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
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content == "line1\nline2\n"
    assert reason == ""


def test_parse_resolved_content_preserves_internal_blank_lines():
    stdout = "<<<RESOLVED>>>\nline1\n\nline3\n<<<END_RESOLVED>>>"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content == "line1\n\nline3\n"
    assert reason == ""


def test_parse_resolved_content_no_markers_returns_none():
    stdout = "resolved content without markers\n"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_rejects_unresolved():
    stdout = "<<<RESOLVED>>>\n<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n<<<END_RESOLVED>>>\n"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content is None
    assert "surviving_conflict_marker" in reason


def test_parse_resolved_content_empty_stdout():
    content, reason = pr_rebase_cli._parse_resolved_content("")
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_whitespace_only():
    content, reason = pr_rebase_cli._parse_resolved_content("   \n  \n")
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_missing_end_marker():
    stdout = "<<<RESOLVED>>>\npartial content that got truncated..."
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content is None
    assert reason == "missing_end_marker"


def test_parse_resolved_content_allows_comment_dividers():
    """Comment dividers with many equals signs should pass (don't start with =======)."""
    stdout = "<<<RESOLVED>>>\n// ========================================\ncode\n<<<END_RESOLVED>>>"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content == "// ========================================\ncode\n"
    assert reason == ""


def test_parse_resolved_content_rejects_bare_equals_line():
    """A bare ======= line (git conflict marker) should be rejected."""
    stdout = "<<<RESOLVED>>>\ncode above\n=======\ncode below\n<<<END_RESOLVED>>>"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content is None
    assert "surviving_conflict_marker" in reason


def test_parse_resolved_content_allows_equals_mid_line():
    """Equals signs mid-line (e.g. in assertions) should pass."""
    stdout = "<<<RESOLVED>>>\nassert x == \"=======\"\ncode\n<<<END_RESOLVED>>>"
    content, reason = pr_rebase_cli._parse_resolved_content(stdout)
    assert content == "assert x == \"=======\"\ncode\n"
    assert reason == ""


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
        # Verify tracked changes staged after tidy (git add -u in go directory)
        tidy_idx = calls.index(["go", "mod", "tidy"])
        post_tidy = calls[tidy_idx + 1:]
        assert ["git", "add", "-u"] in post_tidy


# ── _is_empty_patch ──────────────────────────────────────────────────────


def test_is_empty_patch_both_clean():
    """Empty patch: no staged or unstaged changes."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert pr_rebase_cli._is_empty_patch("/fake") is True


def test_is_empty_patch_staged_changes():
    """Not empty: staged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert pr_rebase_cli._is_empty_patch("/fake") is False


def test_is_empty_patch_unstaged_changes():
    """Not empty: unstaged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" not in cmd and "--quiet" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert pr_rebase_cli._is_empty_patch("/fake") is False


# ── _drive_to_completion ─────────────────────────────────────────────────


def test_drive_to_completion_already_done():
    """Rebase already finished — returns success immediately."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0) as mock_success:
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, False)

    assert result == 0
    mock_success.assert_called_once()
    _, kwargs = mock_success.call_args
    assert kwargs.get("resolved") is None


def test_drive_to_completion_with_conflicts_fix():
    """Conflicts detected with --fix: resolves via AI and continues."""
    ctx = mock.MagicMock()
    rebase_state = [True, False]
    progress_state = [0]

    def fake_in_progress(cwd):
        idx = min(progress_state[0], len(rebase_state) - 1)
        progress_state[0] += 1
        return rebase_state[idx]

    conflict_rounds = [["file.go"]]

    def fake_conflicts(cwd):
        if conflict_rounds[0]:
            return [conflict_rounds[0].pop()]
        return []

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", side_effect=fake_conflicts), \
         mock.patch.object(pr_rebase_cli, "_step_conflicts", return_value=None) as mock_step, \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0) as mock_success:
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, True)

    assert result == 0
    mock_step.assert_called_once()
    mock_success.assert_called_once()
    _, kwargs = mock_success.call_args
    assert kwargs["resolved"] == ([], 1)


def test_drive_to_completion_conflicts_no_fix():
    """Conflicts without --fix: reports and exits 3."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["file.go"]), \
         mock.patch.object(pr_rebase_cli, "_step_conflicts", return_value=3):
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, False)

    assert result == 3


def test_drive_to_completion_empty_commit():
    """Empty commit detected: skips via _step_advance."""
    ctx = mock.MagicMock()
    rebase_state = [True, False]
    call_count = [0]

    def fake_in_progress(cwd):
        idx = min(call_count[0], len(rebase_state) - 1)
        call_count[0] += 1
        return rebase_state[idx]

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=[]), \
         mock.patch.object(pr_rebase_cli, "_step_advance", return_value=None) as mock_advance, \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, False)

    assert result == 0
    mock_advance.assert_called_once()


def test_drive_to_completion_safety_valve():
    """Exceeding max steps aborts the rebase."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_MAX_REBASE_STEPS", 2), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=[]), \
         mock.patch.object(pr_rebase_cli, "_step_advance", return_value=None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, False)

    assert result == 1


# ── _step_conflicts ──────────────────────────────────────────────────────


def test_step_conflicts_no_fix_reports():
    """Without --fix, reports conflicts and returns 3."""
    with mock.patch.object(pr_rebase_cli, "_conflict_report", return_value={"status": "conflicts"}):
        rc = pr_rebase_cli._step_conflicts("/fake", False, ["a.py"], [])

    assert rc == 3


def test_step_conflicts_fix_resolves():
    """With --fix, resolves conflicts via AI and returns None to continue."""
    all_resolved = []

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=["a.py"]), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", True, ["a.py"], all_resolved)

    assert rc is None
    assert all_resolved == ["a.py"]


def test_step_conflicts_fix_resolution_fails_aborts():
    """AI resolution failure aborts rebase and returns 1."""
    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", True, ["a.py"], [])

    assert rc == 1


def test_step_conflicts_fix_ai_unavailable():
    """With --fix but AI unavailable, reports conflicts and returns 3."""
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_conflict_report", return_value={"status": "conflicts"}):
        mock_ai.is_available.return_value = False
        rc = pr_rebase_cli._step_conflicts("/fake", True, ["a.py"], [])

    assert rc == 3


def test_step_conflicts_continue_fails_but_rebase_in_progress():
    """rebase --continue fails because next commit has conflicts — continue loop."""
    all_resolved = []

    def fake_run(cmd, **kwargs):
        r = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "rebase" in cmd and "--continue" in cmd:
            r.returncode = 1
            r.stderr = "error: could not apply abc123... next commit"
        return r

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=["a.py"]), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", True, ["a.py"], all_resolved)

    assert rc is None
    assert all_resolved == ["a.py"]


def test_step_conflicts_continue_fails_rebase_not_in_progress_aborts():
    """rebase --continue fails and rebase is not in progress — abort."""
    abort_called = []

    def fake_run(cmd, **kwargs):
        if "--abort" in cmd:
            abort_called.append(True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="fatal: error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=["a.py"]), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", True, ["a.py"], [])

    assert rc == 1
    assert abort_called


# ── _step_advance ────────────────────────────────────────────────────────


def test_step_advance_empty_patch_skips():
    """Empty patch triggers git rebase --skip."""
    skip_called = []

    def fake_run(cmd, **kwargs):
        if "--skip" in cmd:
            skip_called.append(True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(pr_rebase_cli, "_is_empty_patch", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._step_advance("/fake")

    assert rc is None
    assert skip_called


def test_step_advance_continue_succeeds():
    """Non-empty patch with successful --continue returns None."""
    with mock.patch.object(pr_rebase_cli, "_is_empty_patch", return_value=False), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        rc = pr_rebase_cli._step_advance("/fake")

    assert rc is None


def test_step_advance_continue_fails_but_rebase_in_progress():
    """--continue fails because next commit has conflicts — continue loop."""
    def fake_run(cmd, **kwargs):
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="could not apply")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(pr_rebase_cli, "_is_empty_patch", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._step_advance("/fake")

    assert rc is None


def test_step_advance_continue_fails_aborts():
    """--continue failure with rebase not in progress aborts and returns 1."""
    abort_called = []

    def fake_run(cmd, **kwargs):
        if "--abort" in cmd:
            abort_called.append(True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="stuck state")

    with mock.patch.object(pr_rebase_cli, "_is_empty_patch", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._step_advance("/fake")

    assert rc == 1
    assert abort_called


# ── _fresh ──────────────────────────────────────────────────────────────────


def test_fresh_no_dirty_check():
    """_fresh no longer checks for uncommitted changes (handled by cmd_start auto-stash)."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    status_cmds = []

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            status_cmds.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, False)

    assert result == 0
    assert len(status_cmds) == 0


def test_fresh_delegates_to_drive_on_paused_rebase():
    """_fresh calls _drive_to_completion when rebase is in progress after initial start."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_drive_to_completion", return_value=0) as mock_drive:
        result = pr_rebase_cli._fresh("/fake", ctx, True)

    assert result == 0
    mock_drive.assert_called_once_with("/fake", ctx, True)


# ── _force_push ────────────────────────────────────────────────────────────


def test_force_push_succeeds_first_try():
    """Push succeeds on first attempt — returns 0."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert pr_rebase_cli._force_push("/fake") == 0


def test_force_push_fails_no_modified_files():
    """Push fails with no modified files — returns failure code without retry."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="rejected")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 1
    push_calls = [c for c in calls if c[:2] == ["git", "push"]]
    assert len(push_calls) == 1


def test_force_push_retries_after_regenerated_files():
    """Push fails due to regenerated files — commits and retries successfully."""
    push_count = [0]
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=" M docs/ai-automation.md\n", stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 0
    push_calls = [c for c in calls if c[:2] == ["git", "push"]]
    assert len(push_calls) == 2
    assert ["git", "add", "-u"] in calls
    commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
    assert len(commit_calls) == 1


def test_force_push_commit_fails_returns_original_error():
    """Push fails, commit of regenerated files fails — returns original push error."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=" M docs/ai-automation.md\n", stderr="",
            )
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="commit error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 1


def test_force_push_retry_also_fails():
    """Push fails, retry after commit also fails — returns retry's error code."""
    push_count = [0]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="rejected")
            return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="retry failed")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=" M docs/ai-automation.md\n", stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 128
