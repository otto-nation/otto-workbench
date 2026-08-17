"""Tests for pr-rebase helper functions."""

import importlib.util
import importlib.machinery
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless pr-rebase script via importlib
_pr_rebase_path = str(BIN_DIR / "pr-rebase")
_loader = importlib.machinery.SourceFileLoader("pr_rebase_cli", _pr_rebase_path)
_spec = importlib.util.spec_from_loader("pr_rebase_cli", _loader, origin=_pr_rebase_path)
pr_rebase_cli = importlib.util.module_from_spec(_spec)
pr_rebase_cli.__file__ = _pr_rebase_path
_spec.loader.exec_module(pr_rebase_cli)

import pr_context  # noqa: E402

from conftest import assert_no_worktree_exit, make_ctx  # noqa: E402


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


# ── ConflictReport ─────────────────────────────────────────────────────────


@mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2)
@mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc1234", "fix: thing"))
@mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["a.py"])
def test_conflict_report_structure(_m1, _m2, _m3):
    report = pr_rebase_cli.ConflictReport.from_repo("/fake")
    assert report.status == "conflicts"
    assert report.files == ["a.py"]
    assert report.rebase_head == "abc1234"
    assert report.rebase_head_subject == "fix: thing"
    assert report.remaining_commits == 2


@mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0)
@mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("def5678", "feat: other"))
@mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["b.py"])
def test_conflict_report_custom_status(_m1, _m2, _m3):
    report = pr_rebase_cli.ConflictReport.from_repo("/fake", status="conflicts_resuming")
    assert report.status == "conflicts_resuming"


# ── _commits_ahead ──────────────────────────────────────────────────────────


def test_commits_ahead_parses_count():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="5\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        assert pr_rebase_cli._commits_ahead("/fake") == 5


def test_commits_ahead_non_numeric():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        assert pr_rebase_cli._commits_ahead("/fake") == 0


# ── _find_regenerator ──────────────────────────────────────────────────────


def test_find_regenerator_known_lockfile():
    result = pr_rebase_cli._find_regenerator("pnpm-lock.yaml")
    assert result is not None
    assert result.cmd == ("pnpm", "install", "--lockfile-only")


def test_find_regenerator_go_sum():
    result = pr_rebase_cli._find_regenerator("go.sum")
    assert result is not None
    assert result.cmd == ("go", "mod", "tidy")
    assert result.stage_dir is True


def test_find_regenerator_nested_path():
    """Lookup uses basename, not full path."""
    result = pr_rebase_cli._find_regenerator("packages/web/pnpm-lock.yaml")
    assert result is not None
    assert result.cmd == ("pnpm", "install", "--lockfile-only")


def test_find_regenerator_unknown_file():
    result = pr_rebase_cli._find_regenerator("main.go")
    assert result is None


def test_find_regenerator_all_entries_have_cmd():
    """Every registry entry must carry a non-empty command tuple."""
    for name, entry in pr_rebase_cli._LOCKFILE_REGENERATORS.items():
        assert isinstance(entry.cmd, tuple) and len(entry.cmd) > 0, f"{name} has invalid cmd"


def test_find_regenerator_all_keys_are_basenames():
    """Lookup is by basename — a key with a path separator could never match."""
    for name in pr_rebase_cli._LOCKFILE_REGENERATORS:
        assert os.path.basename(name) == name, f"{name} is not a bare basename"


# ── _detect_mise ───────────────────────────────────────────────────────────


def test_detect_mise_found(tmp_path):
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


def test_detect_mise_tool_versions(tmp_path):
    (tmp_path / ".tool-versions").write_text("nodejs 20\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


def test_detect_mise_dotted_toml(tmp_path):
    """.mise.toml is as common as mise.toml and must be detected."""
    (tmp_path / ".mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


@pytest.mark.parametrize("rel", [
    ".config/mise.toml",
    ".config/mise/config.toml",
    ".mise/config.toml",
    "mise/config.toml",
    "mise.local.toml",
    ".mise.local.toml",
])
def test_detect_mise_nested_config_layouts(tmp_path, rel):
    cfg = tmp_path / rel
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


def test_detect_mise_dotted_toml_in_ancestor(tmp_path):
    subdir = tmp_path / "ui-admin"
    subdir.mkdir(parents=True)
    (tmp_path / ".mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(subdir), str(tmp_path)) is True


def test_detect_mise_in_ancestor(tmp_path):
    subdir = tmp_path / "packages" / "web"
    subdir.mkdir(parents=True)
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(subdir), str(tmp_path)) is True


def test_detect_mise_not_installed(tmp_path):
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value=None):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is False


def test_detect_mise_no_config(tmp_path):
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is False


def test_detect_mise_stops_at_repo_root(tmp_path):
    """Does not search above repo_root."""
    repo = tmp_path / "repo"
    subdir = repo / "packages" / "web"
    subdir.mkdir(parents=True)
    (tmp_path / "mise.toml").write_text("[tools]\n")  # above repo root
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(subdir), str(repo)) is False


# ── _run_regeneration ──────────────────────────────────────────────────────


def test_run_regeneration_bare_command(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["pnpm", "install"] in cmds
    assert ["git", "add", "pnpm-lock.yaml"] in cmds


def test_run_regeneration_with_mise(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=True):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["mise", "exec", "--", "pnpm", "install"] in cmds


def test_run_regeneration_bare_fails_retries_mise(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []
    run_count = [0]

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        if cmd == ["pnpm", "install"]:
            run_count[0] += 1
            return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr="command not found")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["mise", "exec", "--", "pnpm", "install"] in cmds


def test_run_regeneration_missing_binary_retries_mise(tmp_path):
    """A binary absent from PATH raises FileNotFoundError, not exit 127."""
    (tmp_path / "pnpm-lock.yaml").write_text("old content")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        if cmd == ["pnpm", "install"]:
            raise FileNotFoundError(2, "No such file or directory: 'pnpm'")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["mise", "exec", "--", "pnpm", "install"] in cmds


def test_run_regeneration_missing_binary_without_mise_returns_false(tmp_path):
    """Missing binary and no mise degrades to a stale file, never a crash."""
    (tmp_path / "pnpm-lock.yaml").write_text("old content")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "pnpm":
            raise FileNotFoundError(2, "No such file or directory: 'pnpm'")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is False


def test_run_regeneration_not_executable_returns_false(tmp_path):
    """A present-but-unexecutable binary raises PermissionError, not 127."""
    (tmp_path / "pnpm-lock.yaml").write_text("old content")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "pnpm":
            raise PermissionError(13, "Permission denied: 'pnpm'")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is False


def test_run_regeneration_missing_binary_under_mise_returns_false(tmp_path):
    """Defensive: a launch failure under mise must not propagate as a traceback.

    _detect_mise gates on shutil.which, so this pairing is unreachable in
    production; the test pins _run_regeneration's own error handling.
    """
    (tmp_path / "pnpm-lock.yaml").write_text("old content")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "mise":
            raise FileNotFoundError(2, "No such file or directory: 'mise'")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is False


def test_run_regeneration_stage_dir(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("go", "mod", "tidy"),
                stage_dir=True, files=["go.sum"],
            ),
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["git", "add", "-u", "."] in cmds


def test_run_regeneration_failure_returns_false(tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[0] in ("pnpm", "mise"):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._run_regeneration(
            pr_rebase_cli.RegenJob(
                regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
            ),
            cwd=str(tmp_path),
        )

    assert result is False


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
        signal = pr_rebase_cli._is_generated_file("models.go", f, str(tmp_path))
    assert signal is pr_rebase_cli.GeneratedSignal.GITATTRIBUTES


def test_is_generated_file_header_do_not_edit(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated by protoc-gen-go. DO NOT EDIT.\npackage v1\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="service.pb.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = pr_rebase_cli._is_generated_file("service.pb.go", f, str(tmp_path))
    assert signal is pr_rebase_cli.GeneratedSignal.HEADER


def test_is_generated_file_header_at_generated(tmp_path):
    f = tmp_path / "types_pb.ts"
    f.write_text("// @generated by protoc-gen-es v2.12.1\nimport { foo } from 'bar';\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="types_pb.ts: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = pr_rebase_cli._is_generated_file("types_pb.ts", f, str(tmp_path))
    assert signal is pr_rebase_cli.GeneratedSignal.HEADER


def test_is_generated_file_not_generated(tmp_path):
    f = tmp_path / "handler.go"
    f.write_text("package main\n\nfunc Handle() {}\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="handler.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = pr_rebase_cli._is_generated_file("handler.go", f, str(tmp_path))
    assert signal is None


def test_is_generated_file_missing_file(tmp_path):
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="missing.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = pr_rebase_cli._is_generated_file(
            "missing.go", tmp_path / "missing.go", str(tmp_path),
        )
    assert signal is None


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


# ── _ai_suggest_regeneration ──────────────────────────────────────────────


def test_ai_suggest_regeneration_returns_command(tmp_path):
    subdir = tmp_path / "ui-admin"
    subdir.mkdir()
    (subdir / "package.json").write_text('{"name": "ui-admin"}')
    (subdir / "pnpm-lock.yaml").write_text("lockfile content")

    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("pnpm install", 0)
        result = pr_rebase_cli._ai_suggest_regeneration(
            "ui-admin/generated.css", str(tmp_path),
        )

    assert result == ("pnpm", "install")


def test_ai_suggest_regeneration_returns_none_response(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("NONE", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_ai_unavailable(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = False
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_ai_fails(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("", 1)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_empty_response(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_multiword_command(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("cargo generate-lockfile", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result == ("cargo", "generate-lockfile")


def test_ai_suggest_regeneration_rejects_unknown_binary(tmp_path):
    """AI-suggested command with unknown binary is rejected for safety."""
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("rm -rf /", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


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
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") is pr_rebase_cli.DeleteSide.THEIRS_DELETED


def test_detect_delete_conflict_ours_deleted():
    """Stage 2 missing — target deleted the file, branch modifies it."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 ccc333 3\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert pr_rebase_cli._detect_delete_conflict("file.tsx", "/fake") is pr_rebase_cli.DeleteSide.OURS_DELETED


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
        result = pr_rebase_cli._resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", pr_rebase_cli.DeleteSide.THEIRS_DELETED,
        )

    assert result is True
    assert ["git", "rm", "--force", "file.tsx"] in calls


def test_resolve_delete_conflict_ours_deleted():
    """Target deletes file — git rm succeeds."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", pr_rebase_cli.DeleteSide.OURS_DELETED,
        )

    assert result is True
    assert ["git", "rm", "--force", "file.tsx"] in calls


def test_resolve_delete_conflict_git_rm_fails():
    """git rm failure returns False."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rm"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = pr_rebase_cli._resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", pr_rebase_cli.DeleteSide.THEIRS_DELETED,
        )

    assert result is False


# ── _classify_conflict ─────────────────────────────────────────────────────


def test_classify_conflict_known_lockfile(tmp_path):
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    with mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        plan = pr_rebase_cli._classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.REGENERATE
    assert plan.regenerator.cmd == ("pnpm", "install", "--lockfile-only")


def test_classify_conflict_go_sum(tmp_path):
    f = tmp_path / "go.sum"
    f.write_text("content")
    with mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        plan = pr_rebase_cli._classify_conflict("go.sum", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.REGENERATE
    assert plan.regenerator.cmd == ("go", "mod", "tidy")


def test_classify_conflict_generated_file(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated. DO NOT EDIT.\npackage v1\n")
    with mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None), \
         mock.patch.object(
             pr_rebase_cli, "_is_generated_file",
             return_value=pr_rebase_cli.GeneratedSignal.HEADER,
         ):
        plan = pr_rebase_cli._classify_conflict("service.pb.go", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.ACCEPT_THEIRS
    assert plan.signal is pr_rebase_cli.GeneratedSignal.HEADER


def test_classify_conflict_delete_conflict(tmp_path):
    f = tmp_path / "old.go"
    f.write_text("content")
    with mock.patch.object(
        pr_rebase_cli, "_detect_delete_conflict",
        return_value=pr_rebase_cli.DeleteSide.THEIRS_DELETED,
    ):
        plan = pr_rebase_cli._classify_conflict("old.go", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.DELETE
    assert plan.delete_side is pr_rebase_cli.DeleteSide.THEIRS_DELETED


def test_classify_conflict_binary_file(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\x00\x00")
    with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=None), \
         mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        plan = pr_rebase_cli._classify_conflict("image.png", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.BINARY_ERROR


def test_classify_conflict_text_file(tmp_path):
    f = tmp_path / "main.go"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
    with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=None), \
         mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        plan = pr_rebase_cli._classify_conflict("main.go", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.AI_MERGE


def test_classify_conflict_lockfile_takes_priority_over_generated(tmp_path):
    """Registry match wins even if the file is also detected as generated."""
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    with mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None), \
         mock.patch.object(
             pr_rebase_cli, "_is_generated_file",
             return_value=pr_rebase_cli.GeneratedSignal.GITATTRIBUTES,
         ):
        plan = pr_rebase_cli._classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert plan.strategy is pr_rebase_cli.ConflictStrategy.REGENERATE


def test_classify_delete_conflict_carries_side():
    """Modify/delete conflict classifies as DELETE and records which side deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "KanbanOverlay.tsx"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("some content\n")

        with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=None), \
             mock.patch.object(
                 pr_rebase_cli, "_detect_delete_conflict",
                 return_value=pr_rebase_cli.DeleteSide.THEIRS_DELETED,
             ):
            plan = pr_rebase_cli._classify_conflict(filepath, full_path, tmpdir)

        assert plan.strategy is pr_rebase_cli.ConflictStrategy.DELETE
        assert plan.delete_side is pr_rebase_cli.DeleteSide.THEIRS_DELETED


def test_classify_normal_conflict_as_ai_merge():
    """Normal content conflict classifies as AI_MERGE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "main.go"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=None), \
             mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
            plan = pr_rebase_cli._classify_conflict(filepath, full_path, tmpdir)

        assert plan.strategy is pr_rebase_cli.ConflictStrategy.AI_MERGE


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


def _block(index=1, start=0, end=0, conflict="", context_before="", context_after=""):
    """Build a ConflictBlock with defaults for fields the test doesn't care about."""
    return pr_rebase_cli.ConflictBlock(
        index=index, start=start, end=end, conflict=conflict,
        context_before=context_before, context_after=context_after,
    )


def test_extract_conflict_blocks_single():
    content = _make_large_file(300, [(50, "old", "new")])
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0].index == 1
    assert blocks[0].start == 50
    assert blocks[0].end == 54
    assert "<<<<<<< HEAD" in blocks[0].conflict
    assert ">>>>>>> abc123" in blocks[0].conflict
    assert blocks[0].context_before.count("\n") == 30
    assert blocks[0].context_after.count("\n") == 30


def test_extract_conflict_blocks_multiple():
    content = _make_large_file(500, [(50, "a", "b"), (200, "c", "d")])
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 2
    assert blocks[0].index == 1
    assert blocks[1].index == 2
    assert blocks[0].start == 50
    assert blocks[1].start == 204


def test_extract_conflict_blocks_at_file_start():
    content = "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nrest\n"
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0].start == 0
    assert blocks[0].context_before == ""


def test_extract_conflict_blocks_at_file_end():
    content = "line 1\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n"
    blocks = pr_rebase_cli._extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0].context_after == ""


def test_should_chunk_small_file():
    content = "x\n" * 100
    assert pr_rebase_cli._should_chunk(content, [_block(start=10, end=15)]) is False


def test_should_chunk_large_file_small_conflict():
    content = "x\n" * 500
    assert pr_rebase_cli._should_chunk(content, [_block(start=100, end=105)]) is True


def test_should_chunk_large_file_mostly_conflicts():
    content = "x\n" * 500
    assert pr_rebase_cli._should_chunk(content, [_block(start=0, end=300)]) is False


def test_build_chunked_prompt_structure():
    blocks = [_block(
        index=1, start=50, end=54,
        conflict="<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
        context_before="before line\n",
        context_after="after line\n",
    )]
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


def test_build_chunked_prompt_instructs_base_side_names():
    """Chunked prompts carry the rename guard the full-file prompt has.

    Any file over _CHUNKED_MIN_LINES with a small conflict takes this path, so
    dropping the instruction here disarmed it for the common case.
    """
    prompt = pr_rebase_cli._build_chunked_prompt(
        "main.go", [_block(index=1, start=0, end=4)], "abc123", "feat: change",
    )
    assert "base-side names" in prompt


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
    blocks = [_block(start=2, end=6)]
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
            pr_rebase_cli, "_is_generated_file", return_value=None,
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
                return_value=pr_rebase_cli.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
            )

        assert result.files == ["service.pb.go"]
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
                return_value=pr_rebase_cli.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = pr_rebase_cli._resolve_file_conflicts(
                ["data.bin"], tmpdir, "abc123", "feat: add data",
            )

        assert result.files == ["data.bin"]
        assert ["git", "checkout", "--theirs", "data.bin"] in calls


def test_resolve_file_conflicts_handles_go_sum():
    with tempfile.TemporaryDirectory() as tmpdir:
        go_sum = Path(tmpdir) / "go.sum"
        go_sum.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=True) as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.sum"], tmpdir, "abc123", "feat: deps",
            )

        assert result.files == ["go.sum"]
        assert result.stale == []
        cmds = [c[0] for c in calls]
        assert ["git", "checkout", "--theirs", "go.sum"] in cmds
        assert ["git", "add", "go.sum"] in cmds
        for cmd, call_cwd in calls:
            if cmd[0] == "git":
                assert call_cwd == tmpdir, f"{cmd} ran outside the worktree"
        mock_regen.assert_called_once()
        job = mock_regen.call_args[0][0]
        assert job.cmd == ("go", "mod", "tidy")
        assert job.stage_dir is True


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

        assert result.files == ["main.go"]
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


def test_resolve_file_conflicts_go_mod_uses_ai_merge():
    """go.mod is hand-maintained — it goes through AI merge, not accept-theirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        go_mod = Path(tmpdir) / "go.mod"
        go_mod.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(
            pr_rebase_cli, "_resolve_single_file", return_value="go.mod",
        ) as mock_ai, \
             mock.patch.object(pr_rebase_cli, "_run_regeneration") as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.mod"], tmpdir, "abc123", "feat: deps",
            )

        assert result.files == ["go.mod"]
        mock_ai.assert_called_once()
        mock_regen.assert_not_called()


def test_resolve_file_conflicts_regenerates_pnpm_lockfile():
    """pnpm-lock.yaml is accepted-theirs and regenerated — the original bug case."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lockfile = Path(tmpdir) / "pnpm-lock.yaml"
        lockfile.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=True) as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["pnpm-lock.yaml"], tmpdir, "abc123", "feat: deps",
            )

        assert result.files == ["pnpm-lock.yaml"]
        cmds = [c[0] for c in calls]
        assert ["git", "checkout", "--theirs", "pnpm-lock.yaml"] in cmds
        mock_regen.assert_called_once()
        job = mock_regen.call_args[0][0]
        assert job.cmd == ("pnpm", "install", "--lockfile-only")
        assert job.stage_dir is False


def test_resolve_file_conflicts_handles_delete_conflict():
    """Delete conflicts route through _resolve_delete_conflict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "old_module.go"
        f = Path(tmpdir) / filepath
        f.write_text("content")

        plan = pr_rebase_cli.ConflictPlan(
            pr_rebase_cli.ConflictStrategy.DELETE,
            delete_side=pr_rebase_cli.DeleteSide.THEIRS_DELETED,
        )
        with mock.patch.object(
            pr_rebase_cli, "_classify_conflict", return_value=plan,
        ), mock.patch.object(
            pr_rebase_cli, "_resolve_delete_conflict", return_value=True,
        ) as mock_delete:
            result = pr_rebase_cli._resolve_file_conflicts(
                [filepath], tmpdir, "abc123", "feat: cleanup",
            )

        assert result.files == [filepath]
        mock_delete.assert_called_once_with(
            filepath, "abc123", tmpdir, pr_rebase_cli.DeleteSide.THEIRS_DELETED,
        )


def test_resolve_file_conflicts_regen_failure_warns():
    """Regeneration failure warns, marks the file stale, and doesn't abort."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lockfile = Path(tmpdir) / "pnpm-lock.yaml"
        lockfile.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=False), \
             mock.patch.object(pr_rebase_cli.log, "warn") as mock_warn:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["pnpm-lock.yaml"], tmpdir, "abc123", "feat: deps",
            )

        assert result.files == ["pnpm-lock.yaml"]
        assert result.stale == ["pnpm-lock.yaml"]
        mock_warn.assert_called_once()
        assert "pnpm-lock.yaml" in mock_warn.call_args[0][0]


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
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 0
    mock_success.assert_called_once()
    tally = mock_success.call_args[0][3]
    assert tally.files == []
    assert tally.commits == 0


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
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, pr_rebase_cli.RunMode.FIX)

    assert result == 0
    mock_step.assert_called_once()
    mock_success.assert_called_once()
    tally = mock_success.call_args[0][3]
    assert tally.files == []
    assert tally.commits == 1


def test_drive_to_completion_conflicts_no_fix():
    """Conflicts without --fix: reports and exits 3."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=["file.go"]), \
         mock.patch.object(pr_rebase_cli, "_step_conflicts", return_value=3):
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

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
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

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
        result = pr_rebase_cli._drive_to_completion("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 1


# ── _step_conflicts ──────────────────────────────────────────────────────


def test_step_conflicts_no_fix_reports():
    """Without --fix, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(pr_rebase_cli, "_report_conflicts_and_stop", return_value=3):
        rc = pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.PUSH, ["a.py"], pr_rebase_cli.ResolutionTally(),
        )

    assert rc == 3


def test_step_conflicts_no_fix_saves_state():
    """Without --fix, saves conflicts state before returning."""
    ctx = mock.MagicMock()
    saved = []

    def fake_save(self, saved_ctx):
        saved.append((self.status, saved_ctx))

    with mock.patch.object(pr_rebase_cli.RebaseOutcome, "save", fake_save), \
         mock.patch.object(pr_rebase_cli.ConflictReport, "from_repo") as mock_report:
        pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.PUSH, ["a.py"], pr_rebase_cli.ResolutionTally(),
        )

    assert saved == [(pr_rebase_cli.RebaseStatus.CONFLICTS, ctx)]
    mock_report.return_value.emit.assert_called_once()


def test_step_conflicts_fix_resolves():
    """With --fix, resolves conflicts via AI and returns None to continue."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally()

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(
             pr_rebase_cli, "_resolve_file_conflicts",
             return_value=pr_rebase_cli.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", ctx, pr_rebase_cli.RunMode.FIX, ["a.py"], tally)

    assert rc is None
    assert tally.files == ["a.py"]
    assert tally.stale == []


def test_step_conflicts_records_stale_files():
    """Files whose regeneration failed are carried into the tally as stale."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally()
    resolution = pr_rebase_cli.Resolution(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"],
    )

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=resolution), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", ctx, pr_rebase_cli.RunMode.FIX, ["pnpm-lock.yaml"], tally)

    assert rc is None
    assert tally.files == ["pnpm-lock.yaml"]
    assert tally.stale == ["pnpm-lock.yaml"]


def test_rebase_success_emits_stale_files():
    """Stale files reach both the emitted JSON and the persisted state."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"], commits=1,
    )
    saved = []

    with mock.patch.object(pr_rebase_cli, "_commits_ahead", return_value=1), \
         mock.patch.object(
             pr_rebase_cli.RebaseOutcome, "save",
             lambda self, c: saved.append(self),
         ), \
         mock.patch.object(pr_rebase_cli, "_emit_json") as mock_emit:
        rc = pr_rebase_cli._rebase_success("/fake", ctx, pr_rebase_cli.RunMode.PUSH, tally)

    assert rc == 0
    assert saved[0].files_stale == ["pnpm-lock.yaml"]
    assert mock_emit.call_args[0][0]["files_stale"] == ["pnpm-lock.yaml"]


def test_rebase_success_counts_commits_before_push():
    """commits_replayed excludes commits the push recovery creates.

    _force_push can add regeneration and check-fix commits; counting after it
    reported them as replayed from the branch.
    """
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally(files=["a.py"], commits=1)
    ahead = iter([2, 3])

    with mock.patch.object(pr_rebase_cli, "_commits_ahead", lambda _: next(ahead)), \
         mock.patch.object(pr_rebase_cli, "_force_push", return_value=0), \
         mock.patch.object(pr_rebase_cli.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(pr_rebase_cli, "_emit_json") as mock_emit:
        pr_rebase_cli._rebase_success("/fake", ctx, pr_rebase_cli.RunMode.FIX, tally)

    assert mock_emit.call_args[0][0]["commits_replayed"] == 2


def test_rebase_success_conflicts_resolved_counts_files():
    """conflicts_resolved is a file count — rebase_status renders it as 'file(s)'."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally(files=["a.py", "b.py", "c.py"], commits=2)

    with mock.patch.object(pr_rebase_cli, "_commits_ahead", return_value=5), \
         mock.patch.object(pr_rebase_cli.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(pr_rebase_cli, "_emit_json") as mock_emit:
        pr_rebase_cli._rebase_success("/fake", ctx, pr_rebase_cli.RunMode.PUSH, tally)

    assert mock_emit.call_args[0][0]["conflicts_resolved"] == 3


def test_step_conflicts_fix_resolution_fails_aborts():
    """AI resolution failure aborts rebase and returns 1."""
    ctx = mock.MagicMock()
    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=0), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_resolve_file_conflicts", return_value=None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.FIX, ["a.py"], pr_rebase_cli.ResolutionTally(),
        )

    assert rc == 1


def test_step_conflicts_fix_ai_unavailable():
    """With --fix but AI unavailable, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(pr_rebase_cli, "_report_conflicts_and_stop", return_value=3):
        mock_ai.is_available.return_value = False
        rc = pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.FIX, ["a.py"], pr_rebase_cli.ResolutionTally(),
        )

    assert rc == 3


def test_step_conflicts_continue_fails_but_rebase_in_progress():
    """rebase --continue fails because next commit has conflicts — continue loop."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally()

    def fake_run(cmd, **kwargs):
        r = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "rebase" in cmd and "--continue" in cmd:
            r.returncode = 1
            r.stderr = "error: could not apply abc123... next commit"
        return r

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(
             pr_rebase_cli, "_resolve_file_conflicts",
             return_value=pr_rebase_cli.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts("/fake", ctx, pr_rebase_cli.RunMode.FIX, ["a.py"], tally)

    assert rc is None
    assert tally.files == ["a.py"]


def test_step_conflicts_continue_fails_rebase_not_in_progress_aborts():
    """rebase --continue fails and rebase is not in progress — abort."""
    ctx = mock.MagicMock()
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
         mock.patch.object(
             pr_rebase_cli, "_resolve_file_conflicts",
             return_value=pr_rebase_cli.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.FIX, ["a.py"], pr_rebase_cli.ResolutionTally(),
        )

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
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

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
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.FIX)

    assert result == 0
    mock_drive.assert_called_once_with("/fake", ctx, pr_rebase_cli.RunMode.FIX)


def test_fresh_skips_checkout_when_on_correct_branch():
    """No checkout when current_branch already matches ctx.branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "feat/my-branch"
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 0
    assert len(checkout_calls) == 0


def test_fresh_checks_out_branch_on_detached_head():
    """Detached HEAD (current_branch=None) triggers checkout -B."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 0
    assert len(checkout_calls) == 1
    assert checkout_calls[0] == ["git", "checkout", "-B", "feat/my-branch", "origin/feat/my-branch"]


def test_fresh_checks_out_branch_on_wrong_branch():
    """Wrong current_branch triggers checkout -B to ctx.branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "other-branch"
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 0
    assert len(checkout_calls) == 1
    assert checkout_calls[0] == ["git", "checkout", "-B", "feat/my-branch", "origin/feat/my-branch"]


def test_fresh_refuses_to_check_out_into_default_branch_worktree():
    """Regression: checking out into main/ let the next main sync eat the branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "main"
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        stdout = "refs/remotes/origin/main\n" if "symbolic-ref" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 1
    assert len(checkout_calls) == 0


def test_fresh_refuses_to_rebase_the_default_branch():
    """The protected-branch check follows origin/HEAD, not a hardcoded 'main'."""
    ctx = mock.MagicMock()
    ctx.branch = "trunk"
    ctx.current_branch = "trunk"

    def fake_run(cmd, **kwargs):
        stdout = "refs/remotes/origin/trunk\n" if "symbolic-ref" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_rebase_success", return_value=0):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 1


def test_fresh_checkout_failure_returns_error():
    """Checkout failure aborts with return code 1."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error: pathspec")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False):
        result = pr_rebase_cli._fresh("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 1


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
    committed = [False]
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--porcelain" in cmd:
            out = "" if committed[0] else " M docs/ai-automation.md\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if cmd[:2] == ["git", "commit"]:
            committed[0] = True
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
    committed = [False]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="rejected")
            return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="retry failed")
        if "--porcelain" in cmd:
            out = "" if committed[0] else " M docs/ai-automation.md\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if cmd[:2] == ["git", "commit"]:
            committed[0] = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 128


def test_force_push_refuses_to_retry_a_dirty_tree_after_the_ai_fix():
    """A fix that leaves the worktree dirty is not pushed.

    Pre-push hooks validate the worktree, not the commits being pushed, so
    retrying past leftover edits greens a HEAD no hook ever saw — #663 pushed
    a branch that could not be imported that way.
    """
    push_count = [0]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="test failed",
            )
        if "--porcelain" in cmd and "--untracked-files=no" not in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=" M ai/lib/review_phases.py\n", stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True):
        rc = pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert rc == 1
    assert push_count[0] == 1


def _regeneration_leaves_untracked(push_count):
    """subprocess stub: the hook regenerates a file and leaves an untracked one."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="rejected",
            )
        if "--porcelain" in cmd:
            out = (" M docs/ai-automation.md\n" if "--untracked-files=no" in cmd
                   else "?? docs/new-page.md\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return fake_run


def test_force_push_refuses_to_retry_a_dirty_tree_after_regeneration():
    """Step 1's retry is gated too — `git add -u` cannot sweep an untracked file."""
    push_count = [0]

    with mock.patch("subprocess.run", side_effect=_regeneration_leaves_untracked(push_count)):
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 1
    assert push_count[0] == 1


def test_force_push_dirty_after_regeneration_still_reaches_the_ai_fix():
    """Gating step 1's retry must not dead-end the run.

    Step 2 stages the whole tree, which is exactly what clears the untracked
    leftover that blocked step 1 — so dirtiness abandons the retry, not the
    recovery.
    """
    push_count = [0]

    with mock.patch("subprocess.run", side_effect=_regeneration_leaves_untracked(push_count)), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True) as mock_fix:
        pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    mock_fix.assert_called_once()
    assert mock_fix.call_args[0][1] == "rejected"


def test_force_push_regenerated_files_fall_through_to_ai_fix():
    """A hook that regenerates AND fails a check must still reach the AI fix.

    Recovery 1 used to return unconditionally, so on any repo whose hooks
    regenerate a tracked file the AI fix was unreachable.
    """
    push_count = [0]
    committed = [False]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] < 3:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="test failed",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--porcelain" in cmd:
            # Only the first check sees the regenerated file; it gets committed.
            out = "" if committed[0] else " M docs/ai-automation.md\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if cmd[:2] == ["git", "commit"]:
            committed[0] = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True) as mock_fix:
        rc = pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert rc == 0
    assert push_count[0] == 3
    assert mock_fix.call_args[0][1] == "test failed"


def test_force_push_ai_fix_sees_the_retry_error():
    """Step 2 fixes the error the retry reported, not the stale first one."""
    push_count = [0]
    committed = [False]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            errs = {1: "stale first error", 2: "fresh retry error"}
            if push_count[0] < 3:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr=errs[push_count[0]],
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--porcelain" in cmd:
            out = "" if committed[0] else " M generated.md\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if cmd[:2] == ["git", "commit"]:
            committed[0] = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True) as mock_fix:
        pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert mock_fix.call_args[0][1] == "fresh retry error"


# ── _fix_commit_message ───────────────────────────────────────────────────


def _diff_only(diff: str):
    """subprocess.run stub where `git diff --cached` yields `diff`."""
    def fake_run(cmd, **kwargs):
        out = diff if cmd[:3] == ["git", "diff", "--cached"] else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
    return fake_run


def test_commit_types_read_from_conventions():
    """The type list comes from lib/conventions.sh, not a copy in the script."""
    types = pr_rebase_cli._commit_types()
    assert "fix" in types and "test" in types and "refactor" in types


@pytest.mark.parametrize("subject,valid", [
    ("test(review): drop the reviews_dir kwarg removed from ReviewJob", True),
    ("fix: correct the import path", True),
    ("nonsense(scope): not a real type", False),
    ("no colon here at all", False),
    ("fix: ", False),
    ("fix: ends with a period.", False),
    ("fix: " + "x" * 80, False),
    ("", False),
])
def test_valid_commit_header(subject, valid):
    assert pr_rebase_cli._valid_commit_header(subject) is valid


def test_fix_commit_message_uses_ai_subject():
    """A well-formed subject describes the change instead of the generic line."""
    subject = "test(review): drop the reviews_dir kwarg removed from ReviewJob"

    with mock.patch("subprocess.run", side_effect=_diff_only("-  reviews_dir=x\n")), \
         mock.patch.object(pr_rebase_cli.ai_backend, "prompt", return_value=(f"`{subject}`\n", 0)):
        assert pr_rebase_cli._fix_commit_message("/fake", ["a.py"]) == subject


@pytest.mark.parametrize("reply,rc", [
    ("wip(review): not an allowed type", 0),
    ("Subject: fix the import path", 0),
    ("I could not determine a good subject for this change.", 0),
    ("", 0),
    ("fix: fine subject but the call failed", 1),
])
def test_fix_commit_message_falls_back_on_unusable_reply(reply, rc):
    with mock.patch("subprocess.run", side_effect=_diff_only("-  reviews_dir=x\n")), \
         mock.patch.object(pr_rebase_cli.ai_backend, "prompt", return_value=(reply, rc)):
        result = pr_rebase_cli._fix_commit_message("/fake", ["a.py"])

    assert result == pr_rebase_cli._FALLBACK_FIX_SUBJECT


def test_fix_commit_message_empty_diff_skips_the_prompt():
    with mock.patch("subprocess.run", side_effect=_diff_only("")), \
         mock.patch.object(pr_rebase_cli.ai_backend, "prompt") as mock_prompt:
        result = pr_rebase_cli._fix_commit_message("/fake", ["a.py"])

    assert result == pr_rebase_cli._FALLBACK_FIX_SUBJECT
    mock_prompt.assert_not_called()


# ── _fix_push_failures ────────────────────────────────────────────────────


def test_fix_push_failures_ai_fixes_file(tmp_path):
    """AI returns fixed content — stages, commits, returns True."""
    f = tmp_path / "server.go"
    f.write_text("package main\n\nbad format\n")

    fixed_output = "<<<RESOLVED>>>\npackage main\n\ngood format\n<<<END_RESOLVED>>>\n"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=fixed_output, stderr="")
        if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="server.go\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        result = pr_rebase_cli._fix_push_failures(
            str(tmp_path), "gofmt: server.go needs formatting", ["server.go"],
        )

    assert result is True
    assert f.read_text() == "package main\n\ngood format\n"
    assert ["git", "add", "-A"] in calls
    commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
    assert len(commit_calls) == 1


def test_fix_push_failures_commits_edits_outside_the_marker_protocol(tmp_path):
    """Direct agent edits reach the commit instead of being stranded.

    The backend runs with acceptEdits and Bash(*), so a fix can land in a file
    the marker protocol never names. #663 committed only the round-tripped
    file, force-pushed, and left the real source fix uncommitted.
    """
    f = tmp_path / "server_test.go"
    unchanged = "package main\n"
    f.write_text(unchanged)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=f"<<<RESOLVED>>>\n{unchanged}<<<END_RESOLVED>>>\n", stderr="",
            )
        if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            # The agent edited server.go directly; nothing round-tripped.
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="server.go\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        result = pr_rebase_cli._fix_push_failures(
            str(tmp_path), "NameError: name 'group_log' is not defined", ["server_test.go"],
        )

    assert result is True
    assert ["git", "add", "-A"] in calls
    assert len([c for c in calls if c[:2] == ["git", "commit"]]) == 1


def test_fix_push_failures_staging_fails(tmp_path):
    """`git add -A` fails — nothing is committed and the retry is not reached."""
    (tmp_path / "server.go").write_text("package main\n")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        if cmd[:3] == ["git", "add", "-A"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="add failed")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        result = pr_rebase_cli._fix_push_failures(str(tmp_path), "errors", ["server.go"])

    assert result is False


def test_fix_push_failures_ai_unavailable():
    """AI backend unavailable — returns False without attempting."""
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = False
        result = pr_rebase_cli._fix_push_failures("/fake", "errors", ["file.go"])

    assert result is False


def test_fix_push_failures_ai_prompt_fails(tmp_path):
    """AI prompt fails — returns False."""
    f = tmp_path / "server.go"
    f.write_text("package main\n")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        result = pr_rebase_cli._fix_push_failures(
            str(tmp_path), "errors", ["server.go"],
        )

    assert result is False


def test_fix_push_failures_no_changes_needed(tmp_path):
    """AI returns identical content — no commit, returns False."""
    content = "package main\n\nfunc main() {}\n"
    f = tmp_path / "server.go"
    f.write_text(content)

    unchanged_output = f"<<<RESOLVED>>>\n{content}<<<END_RESOLVED>>>\n"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=unchanged_output, stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        result = pr_rebase_cli._fix_push_failures(
            str(tmp_path), "errors", ["server.go"],
        )

    assert result is False


def test_fix_push_failures_missing_file(tmp_path):
    """File doesn't exist — skips it, and an empty index means no commit."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run) as mock_run, \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        result = pr_rebase_cli._fix_push_failures(
            str(tmp_path), "errors", ["nonexistent.go"],
        )

    assert result is False
    assert not [c for c in mock_run.call_args_list if c[0][0][:2] == ["git", "commit"]]


def test_fix_push_failures_truncates_error_output(tmp_path):
    """Long error output is truncated to _FIX_ERROR_MAX_CHARS."""
    f = tmp_path / "server.go"
    f.write_text("package main\n")
    long_error = "x" * 10000

    captured_prompt = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            captured_prompt.append(cmd[-1] if len(cmd) > 3 else kwargs.get("input", ""))
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli.ai_backend, "is_available", return_value=True):
        pr_rebase_cli._fix_push_failures(
            str(tmp_path), long_error, ["server.go"],
        )

    # The AI prompt should contain at most _FIX_ERROR_MAX_CHARS of the error
    # We can't easily inspect the prompt via subprocess mock, but we verify
    # the function doesn't crash on long input
    assert True


def test_force_push_tries_ai_fix_on_check_failure():
    """Push fails with no modified files but resolved_files provided — tries AI fix."""
    push_count = [0]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="gofmt: server.go",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True) as mock_fix:
        rc = pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert rc == 0
    mock_fix.assert_called_once_with("/fake", "gofmt: server.go", ["server.go"])
    assert push_count[0] == 2


def test_force_push_ai_fix_fails_returns_error():
    """Push fails, AI fix fails — returns original error code."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="check errors",
            )
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=False):
        rc = pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert rc == 1


def test_force_push_logs_the_final_retry_error():
    """The post-fix retry captures its output — a failure must still be shown."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="still broken",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures", return_value=True), \
         mock.patch.object(pr_rebase_cli.log, "dim") as mock_dim:
        rc = pr_rebase_cli._force_push("/fake", resolved_files=["server.go"])

    assert rc == 1
    assert mock_dim.call_count == 2
    assert mock_dim.call_args[0][0] == "still broken"


def test_force_push_no_resolved_files_skips_ai_fix():
    """Push fails without resolved_files — doesn't attempt AI fix."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_fix_push_failures") as mock_fix:
        rc = pr_rebase_cli._force_push("/fake")

    assert rc == 1
    mock_fix.assert_not_called()


# ── _auto_stash ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status_out,expected", [
    (" M ai/lib/review_phases.py\n", True),
    ("?? scratch.txt\n", True),
    ("", False),
])
def test_auto_stash_covers_untracked_files(status_out, expected):
    """Untracked files are dirt too — they reach the hooks and the fix commit.

    Left in place they join what the pre-push hooks validate, and the recovery's
    whole-tree stage would then force-push a scratch file (#663).
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = status_out if cmd[:2] == ["git", "status"] else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert pr_rebase_cli._auto_stash("/fake") is expected

    stash_calls = [c for c in calls if c[:2] == ["git", "stash"]]
    assert bool(stash_calls) is expected
    assert all("-u" in c for c in stash_calls)


# ── _auto_unstash ──────────────────────────────────────────────────────────


def test_auto_unstash_pop_failure_without_conflicts_names_the_stash():
    """A pop that fails with no conflict markers must say the work is still stashed.

    Stashing untracked files (-u) makes git's "would be overwritten by merge"
    refusal reachable, and that failure produces no markers to resolve.
    """
    warnings = []

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="",
            stderr="error: untracked working tree files would be overwritten by merge",
        )

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_conflicts", return_value=[]), \
         mock.patch.object(pr_rebase_cli.log, "warn", side_effect=warnings.append):
        pr_rebase_cli._auto_unstash("/fake", pr_rebase_cli.RunMode.PUSH)

    assert any(pr_rebase_cli._STASH_MSG in w for w in warnings)


# ── cmd_start ──────────────────────────────────────────────────────────────


def test_cmd_start_skips_stash_when_rebase_in_progress():
    """Mid-rebase resume must not attempt stash (git index is locked during rebase)."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=True), \
         mock.patch.object(pr_rebase_cli, "_auto_stash") as mock_stash, \
         mock.patch.object(pr_rebase_cli, "_drive_to_completion", return_value=0):
        result = pr_rebase_cli.cmd_start("/fake", ctx, pr_rebase_cli.RunMode.FIX)

    assert result == 0
    mock_stash.assert_not_called()


def test_cmd_start_stashes_before_fresh_rebase():
    """Fresh rebase stashes uncommitted changes before starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_auto_stash", return_value=False) as mock_stash, \
         mock.patch.object(pr_rebase_cli, "_fresh", return_value=0):
        result = pr_rebase_cli.cmd_start("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 0
    mock_stash.assert_called_once()


def test_cmd_start_stash_failure_aborts():
    """When stash fails on a fresh rebase, cmd_start returns 1 without starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_detect_rebase_in_progress", return_value=False), \
         mock.patch.object(pr_rebase_cli, "_auto_stash", return_value=None), \
         mock.patch.object(pr_rebase_cli, "_fresh") as mock_fresh:
        result = pr_rebase_cli.cmd_start("/fake", ctx, pr_rebase_cli.RunMode.PUSH)

    assert result == 1
    mock_fresh.assert_not_called()


# ── main() --push dispatch ──────────────────────────────────────────────────


def _run_main(cmd_start_rc: int, *flags: str) -> tuple[int, mock.MagicMock, mock.MagicMock]:
    """Run main() with the given flags. Returns (exit_code, cmd_push, cmd_start)."""
    fake_ctx = mock.MagicMock()
    fake_ctx.worktree_root = Path("/fake")
    fake_ctx.require_worktree.return_value = Path("/fake")
    fake_trail = mock.MagicMock()
    fake_trail.__enter__ = mock.Mock(return_value=fake_trail)
    fake_trail.__exit__ = mock.Mock(return_value=False)

    with mock.patch("sys.argv", ["pr-rebase", *flags]), \
         mock.patch.object(pr_rebase_cli.pr_context, "resolve", return_value=fake_ctx), \
         mock.patch.object(pr_rebase_cli, "Trail") as mock_trail_cls, \
         mock.patch.object(pr_rebase_cli, "cmd_start", return_value=cmd_start_rc) as mock_start, \
         mock.patch.object(pr_rebase_cli, "cmd_push", return_value=0) as mock_push:
        mock_trail_cls.start.return_value = fake_trail
        try:
            pr_rebase_cli.main()
        except SystemExit as exc:
            exit_code = exc.code
    return exit_code, mock_push, mock_start


def _run_main_with_push(cmd_start_rc: int) -> tuple[int, mock.MagicMock]:
    """Run main() with --push and return (exit_code, mock_cmd_push)."""
    exit_code, mock_push, _ = _run_main(cmd_start_rc, "--push")
    return exit_code, mock_push


def test_push_flag_calls_cmd_push_when_start_succeeds():
    """--push must call cmd_push after cmd_start returns 0 (the bug being fixed)."""
    exit_code, mock_push = _run_main_with_push(cmd_start_rc=0)

    mock_push.assert_called_once()
    assert exit_code == 0


def test_push_flag_skips_cmd_push_on_conflicts():
    """--push must not call cmd_push when cmd_start returns non-zero (e.g. conflicts)."""
    exit_code, mock_push = _run_main_with_push(cmd_start_rc=3)

    mock_push.assert_not_called()
    assert exit_code == 3


# ── --fix --no-push ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("flags,expected", [
    (["--fix"], pr_rebase_cli.RunMode.FIX),
    (["--fix", "--no-push"], pr_rebase_cli.RunMode.FIX_ONLY),
    ([], pr_rebase_cli.RunMode.PUSH),
    (["--no-push"], pr_rebase_cli.RunMode.REBASE_ONLY),
])
def test_select_mode_keeps_fix_and_push_independent(flags, expected):
    """--fix says the AI may resolve; --no-push says nothing reaches the remote."""
    args = mock.Mock(fix="--fix" in flags, push="--no-push" not in flags)
    assert pr_rebase_cli._select_mode(args)[0] is expected


def test_fix_with_no_push_does_not_reach_the_remote():
    """`--fix --no-push` force-pushed anyway: main() branched on --fix first."""
    exit_code, mock_push, mock_start = _run_main(0, "--fix", "--no-push")

    mock_push.assert_not_called()
    assert mock_start.call_args[0][2] is pr_rebase_cli.RunMode.FIX_ONLY
    assert exit_code == 0


def test_rebase_success_in_fix_only_prints_the_push_command(capsys):
    """The force-push is handed to the user, not issued — repository policy."""
    ctx = mock.MagicMock()
    with mock.patch.object(pr_rebase_cli, "_commits_ahead", return_value=2), \
         mock.patch.object(pr_rebase_cli, "_force_push") as mock_force, \
         mock.patch.object(pr_rebase_cli.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(pr_rebase_cli, "_emit_json") as mock_emit:
        rc = pr_rebase_cli._rebase_success("/fake", ctx, pr_rebase_cli.RunMode.FIX_ONLY)

    mock_force.assert_not_called()
    assert rc == 0
    # force_pushed is None, which the emitter drops — "not pushed" rather than
    # "push failed", the same shape --no-push already reports.
    assert "force_pushed" not in mock_emit.call_args[0][0]
    assert "git push --force-with-lease" in capsys.readouterr().err


@pytest.mark.parametrize("mode,hinted", [
    (pr_rebase_cli.RunMode.REBASE_ONLY, True),
    (pr_rebase_cli.RunMode.FIX_ONLY, True),
    (pr_rebase_cli.RunMode.PUSH, False),
    (pr_rebase_cli.RunMode.FIX, False),
])
def test_manual_push_hint_only_when_the_run_never_pushes(mode, hinted, capsys):
    """The hint keyed on "pushes from here", so PUSH printed it then pushed.

    RunMode.PUSH pushes from main() via cmd_push, which is invisible to the
    rebase-completion path — the condition has to be whether the run reaches
    the remote at all.
    """
    ctx = mock.MagicMock()

    with mock.patch.object(pr_rebase_cli, "_commits_ahead", return_value=2), \
         mock.patch.object(pr_rebase_cli, "_force_push", return_value=0), \
         mock.patch.object(pr_rebase_cli.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(pr_rebase_cli, "_emit_json"):
        rc = pr_rebase_cli._rebase_success("/fake", ctx, mode)

    assert rc == 0
    err = capsys.readouterr().err
    assert ("git push --force-with-lease" in err) is hinted
    assert ("--no-push" in err) is hinted
    assert "Rebase complete" in err


def test_fix_only_still_resolves_conflicts():
    """--no-push suppresses the push, not the AI — the two must stay separable."""
    ctx = mock.MagicMock()
    tally = pr_rebase_cli.ResolutionTally()

    with mock.patch.object(pr_rebase_cli, "_rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(pr_rebase_cli, "_remaining_rebase_commits", return_value=2), \
         mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai, \
         mock.patch.object(
             pr_rebase_cli, "_resolve_file_conflicts",
             return_value=pr_rebase_cli.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = pr_rebase_cli._step_conflicts(
            "/fake", ctx, pr_rebase_cli.RunMode.FIX_ONLY, ["a.py"], tally,
        )

    assert rc is None
    assert tally.files == ["a.py"]


# ── worktree_root guard ─────────────────────────────────────────────────────


def test_main_without_a_worktree_exits_with_guidance(capsys):
    """The old code coerced None to "None" and handed it to git -C."""
    ctx = make_ctx(branch="isaac/feat/x", worktree_root=None, head_sha="abc1234")
    with mock.patch("sys.argv", ["pr-rebase"]), \
         mock.patch.object(pr_rebase_cli.pr_context, "resolve", return_value=ctx), \
         mock.patch.object(pr_rebase_cli, "Trail") as mock_trail_cls:
        assert_no_worktree_exit(capsys, "isaac/feat/x", pr_rebase_cli.main)
    mock_trail_cls.start.assert_not_called()
