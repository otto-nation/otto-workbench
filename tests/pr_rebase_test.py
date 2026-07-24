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


# ── _is_generated_file ────────────────────────────────────────────────────


def test_is_generated_file_gitattributes(tmp_path):
    f = tmp_path / "models.go"
    f.write_text("package db\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="models.go: linguist-generated: true\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        result, method = pr_rebase_cli._is_generated_file("models.go", f, str(tmp_path))
    assert result is True
    assert method == "gitattributes"


def test_is_generated_file_header_do_not_edit(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated by protoc-gen-go. DO NOT EDIT.\npackage v1\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="service.pb.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        result, method = pr_rebase_cli._is_generated_file("service.pb.go", f, str(tmp_path))
    assert result is True
    assert method == "header"


def test_is_generated_file_header_at_generated(tmp_path):
    f = tmp_path / "types_pb.ts"
    f.write_text("// @generated by protoc-gen-es v2.12.1\nimport { foo } from 'bar';\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="types_pb.ts: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        result, method = pr_rebase_cli._is_generated_file("types_pb.ts", f, str(tmp_path))
    assert result is True
    assert method == "header"


def test_is_generated_file_not_generated(tmp_path):
    f = tmp_path / "handler.go"
    f.write_text("package main\n\nfunc Handle() {}\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="handler.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        result, method = pr_rebase_cli._is_generated_file("handler.go", f, str(tmp_path))
    assert result is False
    assert method == ""


def test_is_generated_file_missing_file(tmp_path):
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="missing.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        result, method = pr_rebase_cli._is_generated_file(
            "missing.go", tmp_path / "missing.go", str(tmp_path),
        )
    assert result is False


# ── _get_ours_content ──────────────────────────────────────────────────────


def test_get_ours_content_returns_stage2():
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="base version content\n",
    )
    with mock.patch("subprocess.run", return_value=fake_result) as mock_run:
        result = pr_rebase_cli._get_ours_content("src/file.py", "/fake")
    assert result == "base version content\n"
    mock_run.assert_called_once_with(
        ["git", "show", ":2:src/file.py"],
        capture_output=True, text=True, cwd="/fake",
    )


def test_get_ours_content_returns_none_on_failure():
    fake_result = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="not found")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = pr_rebase_cli._get_ours_content("new_file.py", "/fake")
    assert result is None


# ── _get_commit_diff ──────────────────────────────────────────────────────


def test_get_commit_diff_returns_diff():
    diff_text = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_text)
    with mock.patch("subprocess.run", return_value=fake_result) as mock_run:
        result = pr_rebase_cli._get_commit_diff("file.py", "/fake")
    assert result == diff_text.strip()
    mock_run.assert_called_once_with(
        ["git", "diff", "REBASE_HEAD^", "REBASE_HEAD", "--", "file.py"],
        capture_output=True, text=True, cwd="/fake",
    )


def test_get_commit_diff_returns_none_on_failure():
    fake_result = subprocess.CompletedProcess(args=[], returncode=128, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = pr_rebase_cli._get_commit_diff("file.py", "/fake")
    assert result is None


def test_get_commit_diff_returns_none_on_empty_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="  \n")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = pr_rebase_cli._get_commit_diff("file.py", "/fake")
    assert result is None


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
    assert "BASE VERSION" not in prompt
    assert "COMMIT DIFF" not in prompt


def test_build_resolve_prompt_includes_ours_content():
    prompt = pr_rebase_cli._build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh",
        ours_content="base side content\n",
    )
    assert "--- BASE VERSION (target side before this commit) ---" in prompt
    assert "base side content" in prompt
    assert "--- END BASE VERSION ---" in prompt
    assert "base-side names" in prompt


def test_build_resolve_prompt_includes_commit_diff():
    diff = "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new"
    prompt = pr_rebase_cli._build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh",
        commit_diff=diff,
    )
    assert "--- COMMIT DIFF (what this commit intended to change) ---" in prompt
    assert diff in prompt
    assert "--- END COMMIT DIFF ---" in prompt


def test_build_resolve_prompt_includes_both_contexts():
    prompt = pr_rebase_cli._build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh",
        ours_content="base content\n",
        commit_diff="diff content",
    )
    assert "BASE VERSION" in prompt
    assert "COMMIT DIFF" in prompt
    assert "base-side names" in prompt


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


# ── _detect_delete_conflict ───────────────────────────────────────────────


def test_detect_delete_conflict_normal_conflict():
    """Both stages present — normal content conflict, returns None."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 bbb222 2\tfile.tsx\n"
        "100644 ccc333 3\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") is None


def test_detect_delete_conflict_theirs_deleted():
    """Stage 3 missing — branch commit deletes the file."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 bbb222 2\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") == "theirs_deleted"


def test_detect_delete_conflict_ours_deleted():
    """Stage 2 missing — target deleted the file, branch modifies it."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 ccc333 3\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") == "ours_deleted"


def test_detect_delete_conflict_empty_output():
    """No unmerged entries — returns None."""
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") is None


def test_detect_delete_conflict_git_failure():
    """Git command fails — returns None."""
    fake = subprocess.CompletedProcess(args=[], returncode=128, stdout="")
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") is None


# ── _resolve_delete_conflict ──────────────────────────────────────────────


def test_resolve_delete_conflict_theirs_deleted():
    """Branch deletes file — git rm succeeds."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_delete_conflict("file.tsx", "abc123", "/fake", "theirs_deleted")

    assert result is True
    assert ["git", "rm", "--force", "file.tsx"] in calls


def test_resolve_delete_conflict_ours_deleted():
    """Target deletes file — git rm succeeds."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_delete_conflict("file.tsx", "abc123", "/fake", "ours_deleted")

    assert result is True
    assert ["git", "rm", "--force", "file.tsx"] in calls


def test_resolve_delete_conflict_git_rm_fails():
    """git rm failure returns False."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rm"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_delete_conflict("file.tsx", "abc123", "/fake", "theirs_deleted")

    assert result is False


# ── _resolve_one (delete conflicts) ──────────────────────────────────────


def test_resolve_one_delete_conflict_skips_ai():
    """Modify/delete conflict is resolved via git rm without calling AI."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "KanbanOverlay.tsx"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("some content without conflict markers\n")
        go_dirs: set[Path] = set()

        with mock.patch.object(
            pr_rebase_cli, "_detect_delete_conflict", return_value="theirs_deleted",
        ), mock.patch.object(
            pr_rebase_cli, "_resolve_delete_conflict", return_value=True,
        ) as mock_delete, mock.patch.object(
            pr_rebase_cli, "_resolve_single_file",
        ) as mock_ai:
            result = pr_rebase_cli._resolve_one(
                filepath, full_path, "e3e2fdf", "refactor(web): remove components",
                tmpdir, go_dirs,
            )

        assert result is True
        mock_delete.assert_called_once_with(filepath, "e3e2fdf", tmpdir, "theirs_deleted")
        mock_ai.assert_not_called()


def test_resolve_one_normal_conflict_falls_through_to_ai():
    """Normal content conflict (no delete) falls through to AI resolution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "main.go"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        go_dirs: set[Path] = set()

        with mock.patch.object(
            pr_rebase_cli, "_detect_delete_conflict", return_value=None,
        ), mock.patch.object(
            pr_rebase_cli, "_resolve_single_file", return_value=filepath,
        ) as mock_ai:
            result = pr_rebase_cli._resolve_one(
                filepath, full_path, "abc123", "feat: change",
                tmpdir, go_dirs,
            )

        assert result is True
        mock_ai.assert_called_once()


# ── Chunked conflict resolution ──────────────────────────────────────────


def _make_large_file(num_lines, conflicts):
    """Build a file with num_lines of filler and conflict blocks at given positions.

    conflicts: list of (line_index, ours_text, theirs_text)
    """
    lines = [f"line {i}\n" for i in range(num_lines)]
    offset = 0
    for pos, ours, theirs in conflicts:
        block = [
            "<<<<<<< HEAD\n",
            f"{ours}\n",
            "=======\n",
            f"{theirs}\n",
            ">>>>>>> abc123\n",
        ]
        lines[pos + offset:pos + offset + 1] = block
        offset += len(block) - 1
    return "".join(lines)


def test_extract_conflict_blocks_single():
    content = _make_large_file(300, [(50, "old", "new")])
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0]["index"] == 1
    assert blocks[0]["start"] == 50
    assert blocks[0]["end"] == 54
    assert "<<<<<<< HEAD" in blocks[0]["conflict"]
    assert ">>>>>>> abc123" in blocks[0]["conflict"]
    assert blocks[0]["context_before"].count("\n") == 30
    assert blocks[0]["context_after"].count("\n") == 30


def test_extract_conflict_blocks_multiple():
    content = _make_large_file(500, [(50, "a", "b"), (200, "c", "d")])
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 2
    assert blocks[0]["index"] == 1
    assert blocks[1]["index"] == 2
    assert blocks[0]["start"] == 50
    assert blocks[1]["start"] == 204


def test_extract_conflict_blocks_at_file_start():
    content = "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nrest\n"
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0]["start"] == 0
    assert blocks[0]["context_before"] == ""


def test_extract_conflict_blocks_at_file_end():
    content = "line 1\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n"
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0]["context_after"] == ""


def test_should_chunk_small_file():
    content = "x\n" * 100
    blocks = [{"start": 10, "end": 15}]
    assert pr_rebase_cli._should_chunk(content, blocks) is False


def test_should_chunk_large_file_small_conflict():
    content = "x\n" * 500
    blocks = [{"start": 100, "end": 105}]
    assert pr_rebase_cli._should_chunk(content, blocks) is True


def test_should_chunk_large_file_mostly_conflicts():
    content = "x\n" * 500
    blocks = [{"start": 0, "end": 300}]
    assert pr_rebase_cli._should_chunk(content, blocks) is False


def test_build_chunked_prompt_structure():
    blocks = [{
        "index": 1,
        "start": 50, "end": 54,
        "conflict": "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
        "context_before": "before line\n",
        "context_after": "after line\n",
    }]
    prompt = pr_rebase_cli._build_chunked_prompt(
        "main.go", blocks, "abc123", "feat: change",
        commit_diff="diff content",
    )
    assert "--- CONFLICT 1 ---" in prompt
    assert "--- END CONFLICT 1 ---" in prompt
    assert "before line" in prompt
    assert "after line" in prompt
    assert "<<<RESOLVED>>>_1" in prompt
    assert "<<<END_RESOLVED>>>_1" in prompt
    assert "diff content" in prompt
    assert "BASE VERSION" not in prompt


def test_parse_chunked_resolutions_single():
    stdout = "<<<RESOLVED>>>_1\nresolved line\n<<<END_RESOLVED>>>_1\n"
    result, reason = pr_rebase_cli._parse_chunked_resolutions(stdout, 1)
    assert reason == ""
    assert result == ["resolved line\n"]


def test_parse_chunked_resolutions_multiple():
    stdout = (
        "<<<RESOLVED>>>_1\nfirst\n<<<END_RESOLVED>>>_1\n"
        "<<<RESOLVED>>>_2\nsecond\n<<<END_RESOLVED>>>_2\n"
    )
    result, reason = pr_rebase_cli._parse_chunked_resolutions(stdout, 2)
    assert reason == ""
    assert result == ["first\n", "second\n"]


def test_parse_chunked_resolutions_missing_marker():
    stdout = "<<<RESOLVED>>>_1\nfirst\n<<<END_RESOLVED>>>_1\n"
    result, reason = pr_rebase_cli._parse_chunked_resolutions(stdout, 2)
    assert result is None
    assert "block_2" in reason


def test_parse_chunked_resolutions_surviving_markers():
    stdout = "<<<RESOLVED>>>_1\n<<<<<<< HEAD\nstill broken\n<<<END_RESOLVED>>>_1\n"
    result, reason = pr_rebase_cli._parse_chunked_resolutions(stdout, 1)
    assert result is None
    assert "surviving_conflict_marker" in reason


def test_splice_resolutions_single():
    content = "line 0\nline 1\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nline 7\n"
    blocks = [{"start": 2, "end": 6}]
    resolutions = ["merged\n"]
    result = pr_rebase_cli._splice_resolutions(content, blocks, resolutions)
    assert result == "line 0\nline 1\nmerged\nline 7\n"


def test_splice_resolutions_multiple():
    lines = [f"line {i}\n" for i in range(20)]
    lines[5:6] = ["<<<<<<< HEAD\na\n=======\nb\n>>>>>>> abc\n"]
    lines[14:15] = ["<<<<<<< HEAD\nc\n=======\nd\n>>>>>>> abc\n"]
    content = "".join(lines)
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    resolutions = ["merged_1\n", "merged_2\n"]
    result = pr_rebase_cli._splice_resolutions(content, blocks, resolutions)
    assert "<<<<<<< " not in result
    assert "merged_1" in result
    assert "merged_2" in result


def test_resolve_single_file_uses_chunked_for_large_file(tmp_path):
    content = _make_large_file(500, [(100, "old", "new")])
    f = tmp_path / "big.go"
    f.write_text(content)

    resolved_output = "<<<RESOLVED>>>_1\nmerged\n<<<END_RESOLVED>>>_1\n"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=resolved_output, stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_single_file(
            "big.go", f, "abc123", "feat: update", str(tmp_path),
        )

    assert result == "big.go"
    written = f.read_text()
    assert "merged" in written
    assert "<<<<<<< " not in written


def test_resolve_single_file_uses_full_for_small_file(tmp_path):
    content = "<<<<<<< HEAD\nold code\n=======\nnew code\n>>>>>>> abc123\n"
    f = tmp_path / "small.go"
    f.write_text(content)

    resolved_output = "<<<RESOLVED>>>\nmerged code\n<<<END_RESOLVED>>>\n"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=resolved_output, stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_single_file(
            "small.go", f, "abc123", "feat: update", str(tmp_path),
        )

    assert result == "small.go"
    assert "merged code" in f.read_text()


# ── _resolve_file_conflicts ───────────────────────────────────────────────


def test_resolve_file_conflicts_skips_binary():
    with tempfile.TemporaryDirectory() as tmpdir:
        binary_file = Path(tmpdir) / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        with mock.patch.object(
            pr_rebase_cli, "_is_generated_file", return_value=(False, ""),
        ):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["image.png"], tmpdir, "abc123", "feat: add image",
            )
        assert result is None


def test_resolve_file_conflicts_accepts_theirs_for_generated():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen_file = Path(tmpdir) / "service.pb.go"
        gen_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                pr_rebase_cli, "_is_generated_file",
                return_value=(True, "gitattributes"),
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
            )

        assert result == ["service.pb.go"]
        assert ["git", "checkout", "--theirs", "service.pb.go"] in calls
        assert ["git", "add", "service.pb.go"] in calls


def test_resolve_file_conflicts_generated_before_binary():
    """Generated binary files should be accepted, not rejected as binary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gen_binary = Path(tmpdir) / "data.bin"
        gen_binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                pr_rebase_cli, "_is_generated_file",
                return_value=(True, "gitattributes"),
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["data.bin"], tmpdir, "abc123", "feat: add data",
            )

        assert result == ["data.bin"]
        assert ["git", "checkout", "--theirs", "data.bin"] in calls


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
            if cmd[:2] == ["git", "show"] and ":2:" in cmd[2]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="base version\n", stderr="",
                )
            if cmd[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="diff output\n", stderr="",
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
        # Verify context-fetching git calls were made
        ours_calls = [c for c, _ in calls if c[:2] == ["git", "show"] and ":2:" in str(c)]
        assert len(ours_calls) == 1
        diff_calls = [c for c, _ in calls if "REBASE_HEAD^" in str(c)]
        assert len(diff_calls) == 1


def _fake_run_with_context(extra_handler=None):
    """Return a fake subprocess.run that handles context-fetching git calls."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "show"] and len(cmd) > 2 and ":2:" in cmd[2]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="base\n", stderr="")
        if cmd[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="diff\n", stderr="")
        if extra_handler:
            result = extra_handler(cmd, **kwargs)
            if result is not None:
                return result
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return fake_run


def test_resolve_file_conflicts_claude_failure_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "main.go"
        conflict_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        def handler(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="error",
                )

        with mock.patch("subprocess.run", side_effect=_fake_run_with_context(handler)):
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

        def handler(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=bad_output, stderr="",
                )

        with mock.patch("subprocess.run", side_effect=_fake_run_with_context(handler)):
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

        def handler(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=resolved_output, stderr="",
                )
            if cmd == ["git", "add", "main.go"]:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=_fake_run_with_context(handler)):
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
            if cmd[:2] == ["git", "show"] and len(cmd) > 2 and ":2:" in cmd[2]:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="base\n", stderr="")
            if cmd[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="diff\n", stderr="")
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


# ── cmd_start ──────────────────────────────────────────────────────────────


def test_cmd_start_skips_stash_when_rebase_in_progress():
    """Mid-rebase resume must not attempt stash (git index is locked during rebase)."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_auto_stash") as mock_stash, \
         mock.patch.object(pr_rebase_cli, "_drive_to_completion", return_value=0):
        result = pr_rebase_cli.cmd_start("/fake", ctx, True)

    assert result == 0
    mock_stash.assert_not_called()


def test_cmd_start_stashes_before_fresh_rebase():
    """Fresh rebase stashes uncommitted changes before starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_auto_stash", return_value=False) as mock_stash, \
         mock.patch.object(pr_rebase_cli, "_fresh", return_value=0):
        result = pr_rebase_cli.cmd_start("/fake", ctx, False)

    assert result == 0
    mock_stash.assert_called_once()


def test_cmd_start_stash_failure_aborts():
    """When stash fails on a fresh rebase, cmd_start returns 1 without starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_auto_stash", return_value=None), \
         mock.patch.object(pr_rebase_cli, "_fresh") as mock_fresh:
        result = pr_rebase_cli.cmd_start("/fake", ctx, False)

    assert result == 1
    mock_fresh.assert_not_called()
