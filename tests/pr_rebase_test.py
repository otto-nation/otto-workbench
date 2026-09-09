"""Tests for pr-rebase helper functions."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from conftest import (
    assert_no_worktree_exit, git_out, init_worktree, make_ctx, run_checked,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from cli import pr_rebase as pr_rebase_cli  # noqa: E402

from gh import client as gh_client  # noqa: E402
from gh import landed as branch_landed  # noqa: E402
from git import topology as git_topology  # noqa: E402
from git import client as git_client  # noqa: E402
from git import land  # noqa: E402
from git import regenerate as regen  # noqa: E402
from core import report as core_report  # noqa: E402
from rebase import inspect as rebase_inspect  # noqa: E402
from rebase import prepush  # noqa: E402
from rebase import types as rebase_types  # noqa: E402
from rebase import conflicts as rebase_conflicts  # noqa: E402
from rebase import resolve_ai as rebase_resolve  # noqa: E402
from rebase import repo_regen  # noqa: E402
from rebase import land as rebase_land  # noqa: E402
from rebase import lifecycle  # noqa: E402
from rebase import refusals  # noqa: E402
from rebase import stash as rebase_stash  # noqa: E402
from rebase import target as rebase_target  # noqa: E402
from config import workbench_config  # noqa: E402
from agent import invoke as agent_invoke  # noqa: E402
from fix import engine as fix_engine  # noqa: E402
from pr import context as pr_context  # noqa: E402
from pr import domains as pr_domains  # noqa: E402
from pr import state as pr_state  # noqa: E402
from git import push  # noqa: E402
from core import timeouts  # noqa: E402
from git.land import CommitStatus  # noqa: E402


def _unconfigured(cmd):
    """A git argv with `git_client`'s `-c key=value` prefixes stripped.

    The client decides `core.quotePath=false` for every path-listing subcommand
    and `core.editor=true` for a `rebase --continue`, so the argv git receives
    no longer starts with the subcommand. A stub keyed on `cmd[:2]` then stops
    firing and answers from its catch-all instead — which reads as the tested
    behaviour changing, or as nothing at all.
    """
    if not cmd or cmd[0] != "git":
        return cmd
    rest = list(cmd[1:])
    while rest[:1] == ["-c"]:
        del rest[:2]
    return ["git", *rest]


# The sha the stubbed owner reports for whatever it landed. Any value does; it
# is here so the tests that read it back are reading one thing.
_LANDED_SHA = "1a2b3c4"

# What `push.resume_command` renders for this script's force-push, which is the
# line `--no-push` prints and a refusal offers.
_RESUME = "git -C '/fake' push --force-with-lease"


def _pushed(sha: str = _LANDED_SHA) -> land.LandResult:
    """The owner's answer when the remote took the force-push."""
    return land.LandResult(CommitStatus.PUSHED, sha=sha)


def _held() -> land.LandResult:
    """The owner's answer with the publishing gate shut — what `--no-push` gets."""
    return land.LandResult(CommitStatus.PUSH_HELD, sha=_LANDED_SHA, resume=_RESUME)


def _refused(error: str = "✗ gofmt: server.go") -> land.LandResult:
    """The owner's answer when a pre-push hook rejected the branch."""
    return land.LandResult(
        CommitStatus.PUSH_FAILED, sha=_LANDED_SHA, error=error, resume=_RESUME,
        push=push.PushResult(
            push.PushStatus.REFUSED, sha=_LANDED_SHA, branch="isaac/feat/x",
            refusal=push.Refusal.HOOK, output=f"{error}\n",
        ),
    )


def _lands(result: land.LandResult):
    """Patch `_land` so the caller under test sees exactly this outcome."""
    return mock.patch.object(rebase_land, "land_rebased", return_value=result)


# The base a run resolved to, threaded into every helper that derives a signal
# from it. Named here rather than repeated as a literal so a test that cares
# which ref reached git can pass its own instead.
_TARGET = "origin/main"

# The non-default base a release-branch PR reports, and the ref resolution
# prefixes it into. Paired here because the tests that assert one against the
# other are asserting exactly that relationship.
_OTHER_BASE = "release/1.2"
_OTHER_TARGET = f"origin/{_OTHER_BASE}"


# ── _detect_rebase_in_progress ──────────────────────────────────────────────


def test_detect_rebase_not_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(tmpdir) is False


def test_detect_rebase_merge_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(tmpdir) is True


def test_detect_rebase_apply_in_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-apply").mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.rebase_in_progress(tmpdir) is True


# ── _detect_conflicts ───────────────────────────────────────────────────────


def test_detect_conflicts_parses_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/a.py\nsrc/b.py\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase_inspect.detect_conflicts("/fake")
    assert result == ["src/a.py", "src/b.py"]


def test_detect_conflicts_empty_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase_inspect.detect_conflicts("/fake")
    assert result == []


# ── _remaining_rebase_commits ───────────────────────────────────────────────


def test_remaining_rebase_commits_no_rebase():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(tmpdir) == 0


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
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(tmpdir) == 3


def test_remaining_rebase_commits_from_apply():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        apply_dir = git_dir / "rebase-apply"
        apply_dir.mkdir()
        (apply_dir / "next").write_text("3\n")
        (apply_dir / "last").write_text("7\n")
        with mock.patch.object(rebase_inspect, "git_dir", return_value=git_dir):
            assert rebase_inspect.remaining_rebase_commits(tmpdir) == 4


# ── ConflictReport ─────────────────────────────────────────────────────────


@mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=2)
@mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc1234", "fix: thing"))
@mock.patch.object(rebase_inspect, "detect_conflicts", return_value=["a.py"])
def test_conflict_report_structure(_m1, _m2, _m3):
    report = rebase_types.ConflictReport.from_repo("/fake")
    assert report.status == "conflicts"
    assert report.files == ["a.py"]
    assert report.rebase_head == "abc1234"
    assert report.rebase_head_subject == "fix: thing"
    assert report.remaining_commits == 2


@mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=0)
@mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("def5678", "feat: other"))
@mock.patch.object(rebase_inspect, "detect_conflicts", return_value=["b.py"])
def test_conflict_report_custom_status(_m1, _m2, _m3):
    report = rebase_types.ConflictReport.from_repo("/fake", status="conflicts_resuming")
    assert report.status == "conflicts_resuming"


# ── _is_binary ─────────────────────────────────────────────────────────────


def test_is_binary_detects_null_bytes(tmp_path):
    binary_file = tmp_path / "file.bin"
    binary_file.write_bytes(b"hello\x00world")
    assert rebase_conflicts.is_binary(binary_file) is True


def test_is_binary_text_file(tmp_path):
    text_file = tmp_path / "file.txt"
    text_file.write_text("hello world\n")
    assert rebase_conflicts.is_binary(text_file) is False


def test_is_binary_missing_file():
    assert rebase_conflicts.is_binary(Path("/nonexistent/file.bin")) is False


# ── _is_generated_file ────────────────────────────────────────────────────


def test_is_generated_file_gitattributes(tmp_path):
    f = tmp_path / "models.go"
    f.write_text("package db\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="models.go: linguist-generated: true\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = rebase_conflicts.is_generated_file("models.go", f, str(tmp_path))
    assert signal is rebase_types.GeneratedSignal.GITATTRIBUTES


def test_is_generated_file_header_do_not_edit(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated by protoc-gen-go. DO NOT EDIT.\npackage v1\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="service.pb.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = rebase_conflicts.is_generated_file("service.pb.go", f, str(tmp_path))
    assert signal is rebase_types.GeneratedSignal.HEADER


def test_is_generated_file_header_at_generated(tmp_path):
    f = tmp_path / "types_pb.ts"
    f.write_text("// @generated by protoc-gen-es v2.12.1\nimport { foo } from 'bar';\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="types_pb.ts: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = rebase_conflicts.is_generated_file("types_pb.ts", f, str(tmp_path))
    assert signal is rebase_types.GeneratedSignal.HEADER


def test_is_generated_file_not_generated(tmp_path):
    f = tmp_path / "handler.go"
    f.write_text("package main\n\nfunc Handle() {}\n")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="handler.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = rebase_conflicts.is_generated_file("handler.go", f, str(tmp_path))
    assert signal is None


def test_is_generated_file_missing_file(tmp_path):
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="missing.go: linguist-generated: unspecified\n",
    )
    with mock.patch("subprocess.run", return_value=fake):
        signal = rebase_conflicts.is_generated_file(
            "missing.go", tmp_path / "missing.go", str(tmp_path),
        )
    assert signal is None


# ── _get_ours_content ──────────────────────────────────────────────────────


def test_get_ours_content_returns_stage2():
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="base version content\n",
    )
    with mock.patch("subprocess.run", return_value=fake_result) as mock_run:
        result = rebase_conflicts.get_ours_content("src/file.py", "/fake")
    assert result == "base version content\n"
    assert _unconfigured(mock_run.call_args[0][0]) == ["git", "show", ":2:src/file.py"]
    assert mock_run.call_args.kwargs["cwd"] == "/fake"


def test_get_ours_content_returns_none_on_failure():
    fake_result = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="not found")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase_conflicts.get_ours_content("new_file.py", "/fake")
    assert result is None


# ── _get_commit_diff ──────────────────────────────────────────────────────


def test_get_commit_diff_returns_diff():
    diff_text = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=diff_text)
    with mock.patch("subprocess.run", return_value=fake_result) as mock_run:
        result = rebase_conflicts.get_commit_diff("file.py", "/fake")
    assert result == diff_text.strip()
    assert _unconfigured(mock_run.call_args[0][0]) == [
        "git", "diff", "REBASE_HEAD^", "REBASE_HEAD", "--", "file.py",
    ]
    assert mock_run.call_args.kwargs["cwd"] == "/fake"


def test_get_commit_diff_returns_none_on_failure():
    fake_result = subprocess.CompletedProcess(args=[], returncode=128, stdout="")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase_conflicts.get_commit_diff("file.py", "/fake")
    assert result is None


def test_get_commit_diff_returns_none_on_empty_output():
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="  \n")
    with mock.patch("subprocess.run", return_value=fake_result):
        result = rebase_conflicts.get_commit_diff("file.py", "/fake")
    assert result is None


# ── _build_resolve_prompt ──────────────────────────────────────────────────


def test_build_resolve_prompt_includes_context():
    prompt = rebase_resolve.build_resolve_prompt(
        "src/auth.py", "<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
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
    prompt = rebase_resolve.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        ours_content="base side content\n",
    )
    assert "--- BASE VERSION (target side before this commit) ---" in prompt
    assert "base side content" in prompt
    assert "--- END BASE VERSION ---" in prompt
    assert "base-side names" in prompt


def test_build_resolve_prompt_includes_commit_diff():
    diff = "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new"
    prompt = rebase_resolve.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        commit_diff=diff,
    )
    assert "--- COMMIT DIFF (what this commit intended to change) ---" in prompt
    assert diff in prompt
    assert "--- END COMMIT DIFF ---" in prompt


def test_build_resolve_prompt_includes_both_contexts():
    prompt = rebase_resolve.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        ours_content="base content\n",
        commit_diff="diff content",
    )
    assert "BASE VERSION" in prompt
    assert "COMMIT DIFF" in prompt
    assert "base-side names" in prompt


def test_build_resolve_prompt_names_the_resolved_ref():
    """The prompt tells the model which branch the commit is being replayed onto."""
    prompt = rebase_resolve.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_OTHER_TARGET,
    )
    assert _OTHER_TARGET in prompt
    assert "origin/main" not in prompt


# ── _parse_resolved_content ───────────────────────────────────────────────


def test_parse_resolved_content_with_markers():
    stdout = "Some preamble\n<<<RESOLVED>>>\nline1\nline2\n<<<END_RESOLVED>>>\nSome epilogue"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content == "line1\nline2\n"
    assert reason == ""


def test_parse_resolved_content_preserves_internal_blank_lines():
    stdout = "<<<RESOLVED>>>\nline1\n\nline3\n<<<END_RESOLVED>>>"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content == "line1\n\nline3\n"
    assert reason == ""


def test_parse_resolved_content_no_markers_returns_none():
    stdout = "resolved content without markers\n"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_rejects_unresolved():
    stdout = "<<<RESOLVED>>>\n<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n<<<END_RESOLVED>>>\n"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content is None
    assert "surviving_conflict_marker" in reason


def test_parse_resolved_content_empty_stdout():
    content, reason = rebase_conflicts.parse_resolved_content("")
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_whitespace_only():
    content, reason = rebase_conflicts.parse_resolved_content("   \n  \n")
    assert content is None
    assert reason == "missing_both_markers"


def test_parse_resolved_content_missing_end_marker():
    stdout = "<<<RESOLVED>>>\npartial content that got truncated..."
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content is None
    assert reason == "missing_end_marker"


def test_parse_resolved_content_allows_comment_dividers():
    """Comment dividers with many equals signs should pass (don't start with =======)."""
    stdout = "<<<RESOLVED>>>\n// ========================================\ncode\n<<<END_RESOLVED>>>"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content == "// ========================================\ncode\n"
    assert reason == ""


def test_parse_resolved_content_rejects_bare_equals_line():
    """A bare ======= line (git conflict marker) should be rejected."""
    stdout = "<<<RESOLVED>>>\ncode above\n=======\ncode below\n<<<END_RESOLVED>>>"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content is None
    assert "surviving_conflict_marker" in reason


def test_parse_resolved_content_allows_equals_mid_line():
    """Equals signs mid-line (e.g. in assertions) should pass."""
    stdout = "<<<RESOLVED>>>\nassert x == \"=======\"\ncode\n<<<END_RESOLVED>>>"
    content, reason = rebase_conflicts.parse_resolved_content(stdout)
    assert content == "assert x == \"=======\"\ncode\n"
    assert reason == ""


# ── Repo-level regeneration ───────────────────────────────────────────────


@contextlib.contextmanager
def _repo_declaring(commands, *, root, mise_task=False):
    """Stand in for the two sources ``repo_regen.repo_regenerators`` consults."""
    repo_regen.clear_caches()
    config = workbench_config.WorkbenchConfig(
        rebase=workbench_config.RebaseConfig(regenerate=list(commands)),
    )
    try:
        with mock.patch.object(workbench_config, "load_config_or_default",
                               return_value=config), \
             mock.patch.object(repo_regen.git_client, "out", return_value=str(root)), \
             mock.patch.object(repo_regen, "mise_has_task", return_value=mise_task):
            yield
    finally:
        repo_regen.clear_caches()


class TestLedgerAttribution:
    """A rebase-assist call bills to the PR the run is rebasing.

    The helpers making these calls are several frames below the resolved
    context, so they read the subject off the run's trail. A call that reaches
    the ledger with neither repo nor PR cannot be attributed afterwards.
    """

    @staticmethod
    def _trail_for(**context):
        trail = mock.MagicMock()
        trail.context = dict(context)
        return trail

    @staticmethod
    def _resolving(tmp_path, trail, recorded):
        """Drive one rebase-assist prompt and capture what it billed to."""
        with mock.patch.object(rebase_conflicts, "get_ours_content", return_value=""), \
             mock.patch.object(rebase_conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent_invoke.ai_backend, "prompt",
                               side_effect=lambda *a, **kw: (
                                   recorded.update(kw) or ("", 1))):
            rebase_resolve.resolve_full_file(
                "a.py", tmp_path / "a.py", "<<<<<<< ours\n", "1a2b3c4d",
                "subject", str(tmp_path), target_ref="origin/main", trail=trail,
            )

    def test_the_runs_repo_and_pr_reach_the_ledger(self, tmp_path):
        recorded = {}
        self._resolving(
            tmp_path, self._trail_for(repo="org/repo", pr=7, branch="feat/x"),
            recorded,
        )

        assert (recorded["repo"], recorded["pr"]) == ("org/repo", "7")

    def test_a_branch_with_no_pr_bills_to_the_repo_alone(self, tmp_path):
        recorded = {}
        self._resolving(
            tmp_path, self._trail_for(repo="org/repo", pr=None, branch="feat/x"),
            recorded,
        )

        assert (recorded["repo"], recorded["pr"]) == ("org/repo", None)


class TestFailureRecording:
    def test_an_unparseable_resolution_hands_over_the_whole_answer(self):
        """The old record kept 500 characters of a tail and no way to the rest."""
        fake_trail = mock.MagicMock()
        answer = mock.Mock(exit_code=0, text="the model explained itself at length")
        with mock.patch.object(rebase_conflicts, "get_ours_content", return_value=""), \
             mock.patch.object(rebase_conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent_invoke, "run_prompt",
                               return_value=answer):
            resolved = rebase_resolve.resolve_full_file(
                "a.py", Path("/tmp/a.py"), "<<<<<<< ours\n", "1a2b3c4d", "subject",
                "/tmp/wt", target_ref="origin/main", trail=fake_trail,
            )

        assert resolved is None
        kwargs = fake_trail.failure.call_args.kwargs
        assert kwargs["output"] == answer.text
        assert "stdout_tail" not in kwargs["data"]
        assert "stdout_len" not in kwargs["data"]

    def test_an_unparseable_chunked_resolution_hands_over_the_whole_answer(self):
        """Same guard as the full-file path, exercised through the chunked one."""
        fake_trail = mock.MagicMock()
        answer = mock.Mock(exit_code=0, text="the model explained itself at length")
        block = rebase_types.ConflictBlock(
            index=1, start=0, end=0, conflict="<<<<<<< ours\n",
            context_before="", context_after="",
        )
        with mock.patch.object(rebase_conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent_invoke, "run_prompt",
                               return_value=answer):
            resolved = rebase_resolve.resolve_chunked(
                "a.py", Path("/tmp/a.py"), "<<<<<<< ours\n", [block], "1a2b3c4d",
                "subject", "/tmp/wt", target_ref="origin/main", trail=fake_trail,
            )

        assert resolved is None
        kwargs = fake_trail.failure.call_args.kwargs
        assert kwargs["output"] == answer.text
        assert kwargs["data"] == {
            "filepath": "a.py",
            "reason": f"{rebase_types.ParseFailure.MISSING_BLOCK_MARKERS}_1",
        }


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
        assert rebase_conflicts.detect_delete_conflict("file.tsx", "/fake") is None


def test_detect_delete_conflict_theirs_deleted():
    """Stage 3 missing — branch commit deletes the file."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 bbb222 2\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert rebase_conflicts.detect_delete_conflict("file.tsx", "/fake") is rebase_types.DeleteSide.THEIRS_DELETED


def test_detect_delete_conflict_ours_deleted():
    """Stage 2 missing — target deleted the file, branch modifies it."""
    stdout = (
        "100644 aaa111 1\tfile.tsx\n"
        "100644 ccc333 3\tfile.tsx\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
    with mock.patch("subprocess.run", return_value=fake):
        assert rebase_conflicts.detect_delete_conflict("file.tsx", "/fake") is rebase_types.DeleteSide.OURS_DELETED


def test_detect_delete_conflict_empty_output():
    """No unmerged entries — returns None."""
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("subprocess.run", return_value=fake):
        assert rebase_conflicts.detect_delete_conflict("file.tsx", "/fake") is None


def test_detect_delete_conflict_git_failure():
    """Git command fails — returns None."""
    fake = subprocess.CompletedProcess(args=[], returncode=128, stdout="")
    with mock.patch("subprocess.run", return_value=fake):
        assert rebase_conflicts.detect_delete_conflict("file.tsx", "/fake") is None


# ── _resolve_delete_conflict ──────────────────────────────────────────────


def test_resolve_delete_conflict_theirs_deleted():
    """Branch deletes file — git rm succeeds."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = rebase_conflicts.resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", rebase_types.DeleteSide.THEIRS_DELETED,
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
        result = rebase_conflicts.resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", rebase_types.DeleteSide.OURS_DELETED,
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
        result = rebase_conflicts.resolve_delete_conflict(
            "file.tsx", "abc123", "/fake", rebase_types.DeleteSide.THEIRS_DELETED,
        )

    assert result is False


# ── _classify_conflict ─────────────────────────────────────────────────────


def test_classify_conflict_known_lockfile(tmp_path):
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    with mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None):
        plan = rebase_conflicts.classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.REGENERATE
    assert plan.regenerator.cmd == ("pnpm", "install", "--lockfile-only")


def test_classify_conflict_go_sum(tmp_path):
    f = tmp_path / "go.sum"
    f.write_text("content")
    with mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None):
        plan = rebase_conflicts.classify_conflict("go.sum", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.REGENERATE
    assert plan.regenerator.cmd == ("go", "mod", "tidy")


def test_classify_conflict_generated_file(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated. DO NOT EDIT.\npackage v1\n")
    with mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None), \
         mock.patch.object(
             rebase_conflicts, "is_generated_file",
             return_value=rebase_types.GeneratedSignal.HEADER,
         ):
        plan = rebase_conflicts.classify_conflict("service.pb.go", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.ACCEPT_THEIRS
    assert plan.signal is rebase_types.GeneratedSignal.HEADER


def test_classify_conflict_delete_conflict(tmp_path):
    f = tmp_path / "old.go"
    f.write_text("content")
    with mock.patch.object(
        rebase_conflicts, "detect_delete_conflict",
        return_value=rebase_types.DeleteSide.THEIRS_DELETED,
    ):
        plan = rebase_conflicts.classify_conflict("old.go", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.DELETE
    assert plan.delete_side is rebase_types.DeleteSide.THEIRS_DELETED


def test_classify_conflict_binary_file(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\x00\x00")
    with mock.patch.object(rebase_conflicts, "is_generated_file", return_value=None), \
         mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None):
        plan = rebase_conflicts.classify_conflict("image.png", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.BINARY_ERROR


def test_classify_conflict_text_file(tmp_path):
    f = tmp_path / "main.go"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
    with mock.patch.object(rebase_conflicts, "is_generated_file", return_value=None), \
         mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None):
        plan = rebase_conflicts.classify_conflict("main.go", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.AI_MERGE


def test_classify_conflict_lockfile_takes_priority_over_generated(tmp_path):
    """Registry match wins even if the file is also detected as generated."""
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    with mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None), \
         mock.patch.object(
             rebase_conflicts, "is_generated_file",
             return_value=rebase_types.GeneratedSignal.GITATTRIBUTES,
         ):
        plan = rebase_conflicts.classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert plan.strategy is rebase_types.ConflictStrategy.REGENERATE


def test_classify_delete_conflict_carries_side():
    """Modify/delete conflict classifies as DELETE and records which side deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "KanbanOverlay.tsx"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("some content\n")

        with mock.patch.object(rebase_conflicts, "is_generated_file", return_value=None), \
             mock.patch.object(
                 rebase_conflicts, "detect_delete_conflict",
                 return_value=rebase_types.DeleteSide.THEIRS_DELETED,
             ):
            plan = rebase_conflicts.classify_conflict(filepath, full_path, tmpdir)

        assert plan.strategy is rebase_types.ConflictStrategy.DELETE
        assert plan.delete_side is rebase_types.DeleteSide.THEIRS_DELETED


def test_classify_normal_conflict_as_ai_merge():
    """Normal content conflict classifies as AI_MERGE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "main.go"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(rebase_conflicts, "is_generated_file", return_value=None), \
             mock.patch.object(rebase_conflicts, "detect_delete_conflict", return_value=None):
            plan = rebase_conflicts.classify_conflict(filepath, full_path, tmpdir)

        assert plan.strategy is rebase_types.ConflictStrategy.AI_MERGE


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
    return rebase_types.ConflictBlock(
        index=index, start=start, end=end, conflict=conflict,
        context_before=context_before, context_after=context_after,
    )


def test_extract_conflict_blocks_single():
    content = _make_large_file(300, [(50, "old", "new")])
    blocks = rebase_conflicts.extract_conflict_blocks(content)
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
    blocks = rebase_conflicts.extract_conflict_blocks(content)
    assert len(blocks) == 2
    assert blocks[0].index == 1
    assert blocks[1].index == 2
    assert blocks[0].start == 50
    assert blocks[1].start == 204


def test_extract_conflict_blocks_at_file_start():
    content = "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nrest\n"
    blocks = rebase_conflicts.extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0].start == 0
    assert blocks[0].context_before == ""


def test_extract_conflict_blocks_at_file_end():
    content = "line 1\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n"
    blocks = rebase_conflicts.extract_conflict_blocks(content)
    assert len(blocks) == 1
    assert blocks[0].context_after == ""


def test_should_chunk_small_file():
    content = "x\n" * 100
    assert rebase_conflicts.should_chunk(content, [_block(start=10, end=15)]) is False


def test_should_chunk_large_file_small_conflict():
    content = "x\n" * 500
    assert rebase_conflicts.should_chunk(content, [_block(start=100, end=105)]) is True


def test_should_chunk_large_file_mostly_conflicts():
    content = "x\n" * 500
    assert rebase_conflicts.should_chunk(content, [_block(start=0, end=300)]) is False


def test_build_chunked_prompt_structure():
    blocks = [_block(
        index=1, start=50, end=54,
        conflict="<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
        context_before="before line\n",
        context_after="after line\n",
    )]
    prompt = rebase_resolve.build_chunked_prompt(
        "main.go", blocks, "abc123", "feat: change",
        commit_diff="diff content", target_ref=_TARGET,
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
    prompt = rebase_resolve.build_chunked_prompt(
        "main.go", [_block(index=1, start=0, end=4)], "abc123", "feat: change",
        target_ref=_TARGET,
    )
    assert "base-side names" in prompt


def test_build_chunked_prompt_names_the_resolved_ref():
    prompt = rebase_resolve.build_chunked_prompt(
        "main.go", [_block(index=1, start=0, end=4)], "abc123", "feat: change",
        target_ref=_OTHER_TARGET,
    )
    assert _OTHER_TARGET in prompt
    assert "origin/main" not in prompt


def test_parse_chunked_resolutions_single():
    stdout = "<<<RESOLVED>>>_1\nresolved line\n<<<END_RESOLVED>>>_1\n"
    result, reason = rebase_conflicts.parse_chunked_resolutions(stdout, 1)
    assert reason == ""
    assert result == ["resolved line\n"]


def test_parse_chunked_resolutions_multiple():
    stdout = (
        "<<<RESOLVED>>>_1\nfirst\n<<<END_RESOLVED>>>_1\n"
        "<<<RESOLVED>>>_2\nsecond\n<<<END_RESOLVED>>>_2\n"
    )
    result, reason = rebase_conflicts.parse_chunked_resolutions(stdout, 2)
    assert reason == ""
    assert result == ["first\n", "second\n"]


def test_parse_chunked_resolutions_missing_marker():
    stdout = "<<<RESOLVED>>>_1\nfirst\n<<<END_RESOLVED>>>_1\n"
    result, reason = rebase_conflicts.parse_chunked_resolutions(stdout, 2)
    assert result is None
    assert "block_2" in reason


def test_parse_chunked_resolutions_surviving_markers():
    stdout = "<<<RESOLVED>>>_1\n<<<<<<< HEAD\nstill broken\n<<<END_RESOLVED>>>_1\n"
    result, reason = rebase_conflicts.parse_chunked_resolutions(stdout, 1)
    assert result is None
    assert "surviving_conflict_marker" in reason


def test_splice_resolutions_single():
    content = "line 0\nline 1\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\nline 7\n"
    blocks = [_block(start=2, end=6)]
    resolutions = ["merged\n"]
    result = rebase_conflicts.splice_resolutions(content, blocks, resolutions)
    assert result == "line 0\nline 1\nmerged\nline 7\n"


def test_splice_resolutions_multiple():
    lines = [f"line {i}\n" for i in range(20)]
    lines[5:6] = ["<<<<<<< HEAD\na\n=======\nb\n>>>>>>> abc\n"]
    lines[14:15] = ["<<<<<<< HEAD\nc\n=======\nd\n>>>>>>> abc\n"]
    content = "".join(lines)
    blocks = rebase_conflicts.extract_conflict_blocks(content)
    resolutions = ["merged_1\n", "merged_2\n"]
    result = rebase_conflicts.splice_resolutions(content, blocks, resolutions)
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
        result = rebase_resolve.resolve_single_file(
            "big.go", f, "abc123", "feat: update", str(tmp_path),
            target_ref=_TARGET,
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
        result = rebase_resolve.resolve_single_file(
            "small.go", f, "abc123", "feat: update", str(tmp_path),
            target_ref=_TARGET,
        )

    assert result == "small.go"
    assert "merged code" in f.read_text()


# ── _resolve_file_conflicts ───────────────────────────────────────────────


def test_resolve_file_conflicts_skips_binary():
    with tempfile.TemporaryDirectory() as tmpdir:
        binary_file = Path(tmpdir) / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        with mock.patch.object(
            rebase_conflicts, "is_generated_file", return_value=None,
        ):
            result = rebase_resolve.resolve_file_conflicts(
                ["image.png"], tmpdir, "abc123", "feat: add image",
                target_ref=_TARGET,
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
                rebase_conflicts, "is_generated_file",
                return_value=rebase_types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase_resolve.resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
                target_ref=_TARGET,
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
                rebase_conflicts, "is_generated_file",
                return_value=rebase_types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase_resolve.resolve_file_conflicts(
                ["data.bin"], tmpdir, "abc123", "feat: add data",
                target_ref=_TARGET,
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
             mock.patch.object(regen, "run_regeneration", return_value=True) as mock_regen:
            result = rebase_resolve.resolve_file_conflicts(
                ["go.sum"], tmpdir, "abc123", "feat: deps",
                target_ref=_TARGET,
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
            if _unconfigured(cmd)[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="diff output\n", stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = rebase_resolve.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
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
        if _unconfigured(cmd)[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
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
            result = rebase_resolve.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
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
            result = rebase_resolve.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
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
            result = rebase_resolve.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
            )

        assert result is None


def test_resolve_file_conflicts_go_mod_uses_ai_merge():
    """go.mod is hand-maintained — it goes through AI merge, not accept-theirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        go_mod = Path(tmpdir) / "go.mod"
        go_mod.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(
            rebase_resolve, "resolve_single_file", return_value="go.mod",
        ) as mock_ai, \
             mock.patch.object(regen, "run_regeneration") as mock_regen:
            result = rebase_resolve.resolve_file_conflicts(
                ["go.mod"], tmpdir, "abc123", "feat: deps",
                target_ref=_TARGET,
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
             mock.patch.object(regen, "run_regeneration", return_value=True) as mock_regen:
            result = rebase_resolve.resolve_file_conflicts(
                ["pnpm-lock.yaml"], tmpdir, "abc123", "feat: deps",
                target_ref=_TARGET,
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

        plan = rebase_types.ConflictPlan(
            rebase_types.ConflictStrategy.DELETE,
            delete_side=rebase_types.DeleteSide.THEIRS_DELETED,
        )
        with mock.patch.object(
            rebase_conflicts, "classify_conflict", return_value=plan,
        ), mock.patch.object(
            rebase_conflicts, "resolve_delete_conflict", return_value=True,
        ) as mock_delete:
            result = rebase_resolve.resolve_file_conflicts(
                [filepath], tmpdir, "abc123", "feat: cleanup",
                target_ref=_TARGET,
            )

        assert result.files == [filepath]
        mock_delete.assert_called_once_with(
            filepath, "abc123", tmpdir, rebase_types.DeleteSide.THEIRS_DELETED,
            trail=None,
        )


def test_resolve_file_conflicts_regen_failure_warns():
    """Regeneration failure warns, marks the file stale, and doesn't abort."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lockfile = Path(tmpdir) / "pnpm-lock.yaml"
        lockfile.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "run_regeneration", return_value=False), \
             mock.patch.object(prepush.log, "warn") as mock_warn:
            result = rebase_resolve.resolve_file_conflicts(
                ["pnpm-lock.yaml"], tmpdir, "abc123", "feat: deps",
                target_ref=_TARGET,
            )

        assert result.files == ["pnpm-lock.yaml"]
        assert result.stale == ["pnpm-lock.yaml"]
        mock_warn.assert_called_once()
        assert "pnpm-lock.yaml" in mock_warn.call_args[0][0]


def test_resolve_file_conflicts_generated_without_regenerator_is_stale():
    """A generated file nothing can rebuild is reported stale, not resolved.

    Taking theirs stages an artifact generated before the base moved, so a repo
    that declares no rebuild leaves it as wrong as a rebuild that failed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        gen_file = Path(tmpdir) / "service.pb.go"
        gen_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                rebase_conflicts, "is_generated_file",
                return_value=rebase_types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch.object(repo_regen, "repo_regenerators", return_value=()),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase_resolve.resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
                target_ref=_TARGET,
            )

        assert result.files == ["service.pb.go"]
        assert result.stale == ["service.pb.go"]


def test_resolve_file_conflicts_generated_with_regenerator_is_not_stale():
    """A rebuilt generated file is resolved outright — nothing left to redo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gen_file = Path(tmpdir) / "service.pb.go"
        gen_file.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
        regenerator = rebase_types.Regenerator(("mise", "run", "generate"))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                rebase_conflicts, "is_generated_file",
                return_value=rebase_types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch.object(repo_regen, "repo_regenerators", return_value=(regenerator,)),
            mock.patch.object(regen, "run_regeneration", return_value=True) as mock_regen,
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase_resolve.resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
                target_ref=_TARGET,
            )

        assert result.files == ["service.pb.go"]
        assert result.stale == []
        assert mock_regen.call_args[0][0].cmd == regenerator.cmd


# ── _is_empty_patch ──────────────────────────────────────────────────────


def test_is_empty_patch_both_clean():
    """Empty patch: no staged or unstaged changes."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_inspect.is_empty_patch("/fake") is True


def test_is_empty_patch_staged_changes():
    """Not empty: staged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_inspect.is_empty_patch("/fake") is False


def test_is_empty_patch_unstaged_changes():
    """Not empty: unstaged changes exist."""
    def fake_run(cmd, **kwargs):
        if "--cached" not in cmd and "--quiet" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_inspect.is_empty_patch("/fake") is False


# ── _drive_to_completion ─────────────────────────────────────────────────


def test_drive_to_completion_already_done():
    """Rebase already finished — returns success immediately."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0) as mock_success:
        result = lifecycle.drive_to_completion(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

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

    with mock.patch.object(rebase_inspect, "rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(rebase_inspect, "detect_conflicts", side_effect=fake_conflicts), \
         mock.patch.object(lifecycle, "step_conflicts", return_value=None) as mock_step, \
         mock.patch.object(lifecycle, "rebase_success", return_value=0) as mock_success:
        result = lifecycle.drive_to_completion(
            "/fake", ctx, rebase_types.RunMode.FIX, target_ref=_TARGET,
        )

    assert result == 0
    mock_step.assert_called_once()
    mock_success.assert_called_once()
    tally = mock_success.call_args[0][3]
    assert tally.files == []
    assert tally.commits == 1


def test_drive_to_completion_conflicts_no_fix():
    """Conflicts without --fix: reports and exits 3."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase_inspect, "detect_conflicts", return_value=["file.go"]), \
         mock.patch.object(lifecycle, "step_conflicts", return_value=3):
        result = lifecycle.drive_to_completion(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

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

    with mock.patch.object(rebase_inspect, "rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(rebase_inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(lifecycle, "step_advance", return_value=None) as mock_advance, \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.drive_to_completion(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    mock_advance.assert_called_once()


def test_drive_to_completion_safety_valve():
    """Exceeding max steps aborts the rebase."""
    ctx = mock.MagicMock()

    with mock.patch.object(lifecycle, "MAX_REBASE_STEPS", 2), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase_inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(lifecycle, "step_advance", return_value=None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        result = lifecycle.drive_to_completion(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1


# ── _step_conflicts ──────────────────────────────────────────────────────


def test_step_conflicts_no_fix_reports():
    """Without --fix, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(lifecycle, "_report_conflicts_and_stop", return_value=3):
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.PUSH, ["a.py"],
            rebase_types.ResolutionTally(), target_ref=_TARGET,
        )

    assert rc == 3


def test_step_conflicts_no_fix_saves_state():
    """Without --fix, saves conflicts state before returning."""
    ctx = mock.MagicMock()
    saved = []

    def fake_save(self, saved_ctx):
        saved.append((self.status, saved_ctx))

    with mock.patch.object(rebase_types.RebaseOutcome, "save", fake_save), \
         mock.patch.object(rebase_types.ConflictReport, "from_repo") as mock_report:
        lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.PUSH, ["a.py"],
            rebase_types.ResolutionTally(), target_ref=_TARGET,
        )

    assert saved == [(pr_domains.RebaseStatus.CONFLICTS, ctx)]
    mock_report.return_value.emit.assert_called_once()


def test_step_conflicts_fix_resolves():
    """With --fix, resolves conflicts via AI and returns None to continue."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally()

    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts",
             return_value=rebase_types.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["a.py"], tally, target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["a.py"]
    assert tally.stale == []


def test_step_conflicts_records_stale_files():
    """Files whose regeneration failed are carried into the tally as stale."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally()
    resolution = rebase_types.Resolution(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"],
    )

    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=0), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts", return_value=resolution), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["pnpm-lock.yaml"], tally,
            target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["pnpm-lock.yaml"]
    assert tally.stale == ["pnpm-lock.yaml"]


def _run_step_over_budget(*, force=False, already=None, conflicts=None):
    """Run one conflicted step with a tally already near the file budget."""
    over = rebase_types.CONFLICT_FILE_BUDGET + 1
    tally = rebase_types.ResolutionTally(
        files=already if already is not None else [f"f{i}.py" for i in range(over)],
    )
    with mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(refusals, "refuse_over_budget", return_value=4) as refuse, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts",
             return_value=rebase_types.Resolution(files=["late.py"]),
         ) as resolve, \
         mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc", "s")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=1), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", mock.MagicMock(), rebase_types.RunMode.FIX,
            conflicts if conflicts is not None else ["late.py"], tally,
            target_ref=_TARGET, force=force,
        )
    return rc, refuse, resolve


def test_step_conflicts_refuses_past_the_file_budget():
    """A rebase conflicting across too many files stops instead of resolving.

    Resolving dozens of files unattended is how a rebase rewrites a file the
    branch never touched — the spread is the signal that the branch and its
    base have diverged past what automatic resolution should attempt.
    """
    rc, refuse, resolve = _run_step_over_budget()

    assert rc == 4
    resolve.assert_not_called()
    assert refuse.call_args[0][2] == rebase_types.CONFLICT_FILE_BUDGET + 2


def test_step_conflicts_counts_distinct_files_not_conflicts():
    """A file conflicting in several replayed commits counts once."""
    repeated = ["same.py"] * (rebase_types.CONFLICT_FILE_BUDGET + 5)
    rc, refuse, resolve = _run_step_over_budget(already=repeated, conflicts=["same.py"])

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_budget_is_waived_by_force():
    rc, refuse, resolve = _run_step_over_budget(force=True)

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_under_the_budget_resolves():
    rc, refuse, resolve = _run_step_over_budget(already=["a.py"], conflicts=["b.py"])

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_rebase_success_emits_stale_files():
    """Stale files reach both the emitted JSON and the persisted state."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"], commits=1,
    )
    saved = []

    with mock.patch.object(git_client, "commits_ahead", return_value=1), \
         mock.patch.object(
             rebase_types.RebaseOutcome, "save",
             lambda self, c: saved.append(self),
         ), \
         mock.patch.object(core_report, "emit_json") as mock_emit:
        rc = lifecycle.rebase_success(
            "/fake", ctx, rebase_types.RunMode.PUSH, tally, target_ref=_TARGET,
        )

    assert rc == 0
    assert saved[0].files_stale == ["pnpm-lock.yaml"]
    assert mock_emit.call_args[0][0]["files_stale"] == ["pnpm-lock.yaml"]


def test_rebase_success_counts_commits_before_push():
    """commits_replayed excludes commits the push recovery creates.

    The landing can add regeneration and check-fix commits; counting after it
    reported them as replayed from the branch.
    """
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally(files=["a.py"], commits=1)
    ahead = iter([2, 3])

    with mock.patch.object(git_client, "commits_ahead", lambda _, **kw: next(ahead)), \
         _lands(_pushed()), \
         mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core_report, "emit_json") as mock_emit:
        lifecycle.rebase_success(
            "/fake", ctx, rebase_types.RunMode.FIX, tally, target_ref=_TARGET,
        )

    assert mock_emit.call_args[0][0]["commits_replayed"] == 2


def test_rebase_success_conflicts_resolved_counts_files():
    """conflicts_resolved is a file count — rebase_status renders it as 'file(s)'."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally(files=["a.py", "b.py", "c.py"], commits=2)

    with mock.patch.object(git_client, "commits_ahead", return_value=5), \
         mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core_report, "emit_json") as mock_emit:
        lifecycle.rebase_success(
            "/fake", ctx, rebase_types.RunMode.PUSH, tally, target_ref=_TARGET,
        )

    assert mock_emit.call_args[0][0]["conflicts_resolved"] == 3


def test_step_conflicts_fix_resolution_fails_aborts():
    """AI resolution failure aborts rebase and returns 1."""
    ctx = mock.MagicMock()
    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=0), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts", return_value=None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["a.py"],
            rebase_types.ResolutionTally(), target_ref=_TARGET,
        )

    assert rc == 1


def test_step_conflicts_fix_ai_unavailable():
    """With --fix but AI unavailable, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(lifecycle, "_report_conflicts_and_stop", return_value=3):
        mock_ai.is_available.return_value = False
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["a.py"],
            rebase_types.ResolutionTally(), target_ref=_TARGET,
        )

    assert rc == 3


def test_step_conflicts_continue_fails_but_rebase_in_progress():
    """rebase --continue fails because next commit has conflicts — continue loop."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally()

    def fake_run(cmd, **kwargs):
        r = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "rebase" in cmd and "--continue" in cmd:
            r.returncode = 1
            r.stderr = "error: could not apply abc123... next commit"
        return r

    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts",
             return_value=rebase_types.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["a.py"], tally, target_ref=_TARGET,
        )

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

    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=0), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts",
             return_value=rebase_types.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX, ["a.py"],
            rebase_types.ResolutionTally(), target_ref=_TARGET,
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

    with mock.patch.object(rebase_inspect, "is_empty_patch", return_value=True), \
         mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = lifecycle.step_advance("/fake")

    assert rc is None
    assert skip_called


def test_step_advance_continue_succeeds():
    """Non-empty patch with successful --continue returns None."""
    with mock.patch.object(rebase_inspect, "is_empty_patch", return_value=False), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        rc = lifecycle.step_advance("/fake")

    assert rc is None


def test_step_advance_continue_fails_but_rebase_in_progress():
    """--continue fails because next commit has conflicts — continue loop."""
    def fake_run(cmd, **kwargs):
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="could not apply")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(rebase_inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = lifecycle.step_advance("/fake")

    assert rc is None


def test_step_advance_continue_fails_aborts():
    """--continue failure with rebase not in progress aborts and returns 1."""
    abort_called = []

    def fake_run(cmd, **kwargs):
        if "--abort" in cmd:
            abort_called.append(True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="stuck state")

    with mock.patch.object(rebase_inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = lifecycle.step_advance("/fake")

    assert rc == 1
    assert abort_called


def test_step_advance_continue_failure_records_the_whole_output():
    """The abort path recorded a warn carrying a bare `stderr` key before this.

    Its sibling `_step_conflicts` ends identically and already routes through
    `Trail.failure`, so the whole of what git said reaches an artifact.
    """
    stderr = "".join(f"detail line {n}\n" for n in range(200))
    fake_trail = mock.MagicMock()

    def fake_run(cmd, **kwargs):
        if "--abort" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=stderr)

    with mock.patch.object(rebase_inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = lifecycle.step_advance("/fake", trail=fake_trail)

    assert rc == 1
    fake_trail.warn.assert_not_called()
    fake_trail.failure.assert_called_once()
    kwargs = fake_trail.failure.call_args.kwargs
    assert kwargs["output"].count("detail line") == 200
    assert "detail line 0\n" in kwargs["output"]
    assert kwargs["data"] == {"exit_code": 1}


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
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

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
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(lifecycle, "drive_to_completion", return_value=0) as mock_drive:
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.FIX, target_ref=_TARGET,
        )

    assert result == 0
    mock_drive.assert_called_once_with(
        "/fake", ctx, rebase_types.RunMode.FIX, target_ref=_TARGET, force=False,
        trail=None,
    )


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
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(checkout_calls) == 0


def _fake_run_without_local_branch(checkout_calls):
    """A git stub for a worktree that does not have ctx.branch locally yet.

    `rev-parse --verify` is the question _checkout_target_branch asks first, and
    a blanket success would answer "the branch is already here" — which is a
    different path with a different checkout.
    """
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return fake_run


def test_fresh_checks_out_branch_on_detached_head():
    """Detached HEAD (current_branch=None) triggers checkout -B."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None
    checkout_calls = []

    with mock.patch("subprocess.run", side_effect=_fake_run_without_local_branch(checkout_calls)), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(checkout_calls) == 1
    assert checkout_calls[0] == ["git", "checkout", "-B", "feat/my-branch", "origin/feat/my-branch"]


def test_fresh_checks_out_branch_on_wrong_branch():
    """Wrong current_branch triggers checkout -B to ctx.branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "other-branch"
    checkout_calls = []

    with mock.patch("subprocess.run", side_effect=_fake_run_without_local_branch(checkout_calls)), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

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
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

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
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1


# Whether the local ref exists picks between two different checkouts, so a
# failure has to propagate from both — one blanket stub would only ever prove
# whichever path its rev-parse answer happened to select.
@pytest.mark.parametrize("local_ref_exists", [False, True])
def test_fresh_checkout_failure_returns_error(local_ref_exists):
    """Checkout failure aborts with return code 1, whichever checkout ran."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error: pathspec")
        if cmd[:3] == ["git", "rev-parse", "--verify"] and not local_ref_exists:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False):
        result = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1


# ── _checkout_target_branch ─────────────────────────────────────────────────
#
# Against a real repo rather than a subprocess stub: the bug these cover is
# `checkout -B` resetting the branch ref, and only git itself decides where
# a ref lands. A stub asserting on the argv would have passed throughout.

_CHECKOUT_BRANCH = "feat/checkout"


def _git(repo, *args):
    """The shared git runner, stripped — see conftest.run_checked."""
    return git_out(repo, *args).strip()


def _commit(repo, name, message):
    """Add a one-file commit and return its sha."""
    (Path(repo) / name).write_text(f"{name}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo_on_main(tmp_path):
    """A repo with one commit on main, checked out there."""
    repo = init_worktree(tmp_path / "repo")
    _git(repo, "config", "user.email", "rebase-test@example.com")
    _git(repo, "config", "user.name", "Rebase Test")
    _commit(repo, "base.txt", "base")
    return repo


def _checkout_ctx():
    ctx = mock.MagicMock()
    ctx.branch = _CHECKOUT_BRANCH
    return ctx


def test_checkout_target_branch_keeps_unpushed_commits(tmp_path):
    """A commit that was never pushed survives the checkout."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    pushed = _commit(repo, "pushed.txt", "pushed work")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", pushed)
    unpushed = _commit(repo, "unpushed.txt", "unpushed work")
    _git(repo, "checkout", "-q", "main")

    rc = rebase_target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == unpushed
    assert _git(repo, "rev-parse", _CHECKOUT_BRANCH) == unpushed
    assert "unpushed work" in _git(repo, "log", "--oneline")


def test_checkout_target_branch_fast_forwards_when_behind(tmp_path):
    """A local ref with nothing of its own still takes the remote's newer tip."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    local = _commit(repo, "one.txt", "one")
    remote = _commit(repo, "two.txt", "two")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "reset", "-q", "--hard", local)
    _git(repo, "checkout", "-q", "main")

    rc = rebase_target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == remote


def test_checkout_target_branch_refuses_when_diverged(tmp_path):
    """Neither side can be dropped, so the run stops instead of picking one."""
    repo = _repo_on_main(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    local = _commit(repo, "local.txt", "local only")
    _git(repo, "checkout", "-q", "-b", "remote-side", base)
    remote = _commit(repo, "remote.txt", "remote only")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-qD", "remote-side")

    rc = rebase_target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 1
    assert _git(repo, "rev-parse", _CHECKOUT_BRANCH) == local
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_checkout_target_branch_names_the_commits_it_will_not_discard(tmp_path, capsys):
    """The refusal has to be actionable, and truncation has to admit itself."""
    repo = _repo_on_main(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    for i in range(rebase_types.UNPUSHED_SUBJECT_LIMIT + 2):
        _commit(repo, f"local{i}.txt", f"local work {i}")
    _git(repo, "checkout", "-q", "-b", "remote-side", base)
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}",
         _commit(repo, "remote.txt", "remote only"))
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-qD", "remote-side")

    assert rebase_target.checkout_target_branch(str(repo), _checkout_ctx()) == 1

    err = capsys.readouterr().err
    assert "local work 11" in err
    assert "... and 2 more" in err


def test_checkout_target_branch_creates_from_origin_when_absent(tmp_path):
    """No local ref means nothing to preserve — -B is how the branch arrives."""
    repo = _repo_on_main(tmp_path)
    remote = _commit(repo, "remote.txt", "remote work")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "reset", "-q", "--hard", "HEAD~1")

    rc = rebase_target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == remote


def test_checkout_target_branch_uses_local_when_remote_ref_is_gone(tmp_path):
    """--prune drops origin/<branch>; every commit on it is then unpushed."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    tip = _commit(repo, "only.txt", "only local")
    _git(repo, "checkout", "-q", "main")

    rc = rebase_target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == tip


# ── already-landed preflight ────────────────────────────────────────────────


_LANDED_BRANCH = "feat/landed"
_LANDED_PR = 726
_LANDED_URL = "https://x/pull/726"


def _completed(cmd, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr="")


def _gh_response(payload: str, returncode: int = 0):
    """Patch the transport so the gh call answers with *payload*.

    Stubbed under `gh_client` rather than at it, so the argv the client builds
    and the tier it picks are both still observable from the call.
    """
    return mock.patch(
        "core.proc.subprocess.run",
        return_value=_completed(["gh"], returncode=returncode, stdout=payload),
    )


def _landed_ctx(**overrides):
    """A context for the branch every preflight test asks about."""
    defaults = dict(branch=_LANDED_BRANCH, pr_number=_LANDED_PR)
    defaults.update(overrides)
    return make_ctx(**defaults)


# ── target ref resolution ───────────────────────────────────────────────────


def test_pr_base_branch_reads_the_base_github_reports():
    ctx = _landed_ctx(repo="owner/repo")

    with _gh_response(f'{{"baseRefName": "{_OTHER_BASE}"}}') as mock_try:
        assert rebase_target.pr_base_branch("/fake", ctx) == _OTHER_BASE

    cmd = mock_try.call_args[0][0]
    assert cmd[:4] == ["gh", "pr", "view", str(_LANDED_PR)]
    assert cmd[4:6] == ["--repo", "owner/repo"]


def test_pr_base_branch_omits_repo_when_the_context_has_none():
    """gh infers the repo from the remote — an empty --repo value would not."""
    ctx = _landed_ctx(repo="")

    with _gh_response(f'{{"baseRefName": "{_OTHER_BASE}"}}') as mock_try:
        assert rebase_target.pr_base_branch("/fake", ctx) == _OTHER_BASE

    assert "--repo" not in mock_try.call_args[0][0]


def test_pr_base_branch_stays_quiet_without_a_pr_number():
    """Probing by branch name would spend a round trip to learn nothing."""
    with mock.patch("core.proc.subprocess.run") as mock_gh:
        assert rebase_target.pr_base_branch("/fake", _landed_ctx(pr_number=None)) is None

    mock_gh.assert_not_called()


@pytest.mark.parametrize("payload,returncode", [
    ("not json at all", 0),
    ("{}", 0),
    ('{"baseRefName": ""}', 0),
    ("no such pull request", 1),
])
def test_pr_base_branch_degrades_when_gh_cannot_answer(payload, returncode):
    with _gh_response(payload, returncode=returncode):
        assert rebase_target.pr_base_branch("/fake", _landed_ctx()) is None


def test_pr_base_branch_survives_gh_being_absent():
    with mock.patch("core.proc.subprocess.run", side_effect=FileNotFoundError):
        assert rebase_target.pr_base_branch("/fake", _landed_ctx()) is None


def _resolve_target(onto=None, *, pr_base=None, default_branch="main"):
    """Resolve the target ref with both probes forced."""
    with mock.patch.object(rebase_target, "pr_base_branch", return_value=pr_base), \
         mock.patch.object(git_topology, "default_branch",
                           return_value=default_branch):
        return rebase_target.resolve_target_ref("/fake", _landed_ctx(), onto)


def test_resolve_target_ref_prefers_the_onto_flag():
    """The flag is taken verbatim — it may name a remote the probes never see."""
    assert _resolve_target(
        "upstream/trunk", pr_base=_OTHER_BASE, default_branch="master",
    ) == "upstream/trunk"


def test_resolve_target_ref_prefers_the_pr_base_over_the_default_branch():
    assert _resolve_target(pr_base=_OTHER_BASE, default_branch="main") == _OTHER_TARGET


def test_resolve_target_ref_falls_back_to_the_default_branch():
    """Regression: a repo on master was rebased onto a ref it does not have."""
    assert _resolve_target(default_branch="master") == "origin/master"


def test_resolve_target_ref_never_asks_the_default_branch_when_a_pr_answers():
    with mock.patch.object(rebase_target, "pr_base_branch", return_value=_OTHER_BASE), \
         mock.patch.object(git_topology, "default_branch") as mock_default:
        rebase_target.resolve_target_ref("/fake", _landed_ctx(), None)

    mock_default.assert_not_called()


def _run_tracker_check(merged=None, ctx=None):
    """Run the tracker half of the preflight with gh's answer forced."""
    with mock.patch.object(branch_landed, "merged_pr", return_value=merged):
        return refusals.tracker_landed_check("/fake", ctx or _landed_ctx())


def _run_git_check(*, ahead=3, empty_diff=False, upstream=False, ctx=None):
    """Run the git half of the preflight with each signal forced."""
    with mock.patch.object(git_client, "commits_ahead", return_value=ahead), \
         mock.patch.object(branch_landed, "diff_is_empty", return_value=empty_diff), \
         mock.patch.object(branch_landed, "all_commits_upstream", return_value=upstream):
        return refusals.git_landed_check(
            "/fake", ctx or _landed_ctx(), target_ref=_TARGET,
        )


def test_every_landed_signal_has_a_refusal_of_its_own():
    """`branch_landed` owns the names and this script re-exports them.

    A signal added to the lib and not mapped here would reach `_as_refusal` and
    build a report whose `signal` no `RefusalSignal` matches — the skill's table
    would document a value the script cannot emit under any name it knows.
    """
    refusals = {member.value for member in rebase_types.RefusalSignal}

    assert {signal.value for signal in branch_landed.LandedSignal} <= refusals


def test_tracker_check_reports_a_merged_pr():
    report = _run_tracker_check(
        branch_landed.MergedPR(number=_LANDED_PR, url=_LANDED_URL),
    )

    assert report.signal == rebase_types.RefusalSignal.PR_MERGED.value
    assert report.pr_number == _LANDED_PR
    assert report.detail == f"PR #{_LANDED_PR} is merged ({_LANDED_URL})"


def test_tracker_check_leaves_commits_ahead_unmeasured():
    """It runs before the checkout, so HEAD is another branch — null, not wrong."""
    report = _run_tracker_check(branch_landed.MergedPR(number=_LANDED_PR))

    assert report.commits_ahead is None


def test_tracker_check_omits_the_link_when_gh_reports_no_url():
    """The detail sentence is documented in SKILL.md — no empty parentheses."""
    report = _run_tracker_check(branch_landed.MergedPR(number=_LANDED_PR))

    assert report.detail == f"PR #{_LANDED_PR} is merged"


def test_tracker_check_never_reads_head():
    """The whole split rests on this: it must be safe before the checkout.

    Every HEAD-dependent signal is on the git side, so reaching for one here
    would reintroduce the ordering bug the split fixes.
    """
    with mock.patch.object(branch_landed, "merged_pr",
                           return_value=branch_landed.MergedPR(number=_LANDED_PR)), \
         mock.patch.object(git_client, "commits_ahead") as ahead, \
         mock.patch.object(branch_landed, "diff_is_empty") as diff, \
         mock.patch.object(branch_landed, "all_commits_upstream") as cherry:
        refusals.tracker_landed_check("/fake", _landed_ctx())

    ahead.assert_not_called()
    diff.assert_not_called()
    cherry.assert_not_called()


def test_tracker_check_passes_when_github_has_no_answer():
    assert _run_tracker_check(None) is None


def test_git_check_never_asks_the_tracker():
    """The order is this script's, not the lib's ladder.

    `branch_landed.check` would fall through to gh here, and the round trip has
    already been spent before the checkout — where it is the only probe that can
    still answer for a branch `fetch --prune` just dropped.
    """
    with mock.patch.object(branch_landed, "merged_pr") as gh:
        _run_git_check()

    gh.assert_not_called()


def test_git_check_catches_a_squash_merge_by_empty_diff():
    """The squash-merge case: the commits are unreachable, the tree matches."""
    report = _run_git_check(empty_diff=True)

    assert report.signal == rebase_types.RefusalSignal.EMPTY_DIFF.value
    assert report.pr_number is None
    assert report.commits_ahead == 3


def test_git_check_catches_a_rebase_merge_by_patch_id():
    report = _run_git_check(upstream=True)

    assert report.signal == rebase_types.RefusalSignal.COMMITS_UPSTREAM.value
    assert report.commits_ahead == 3


def test_git_check_passes_an_unlanded_branch():
    assert _run_git_check() is None


def test_git_check_ignores_a_branch_with_no_commits_of_its_own():
    """Regression: both git signals read as landed for a freshly cut branch.

    An empty diff and an empty `git cherry` are vacuously true there, so
    without the guard every new worktree would be refused before its first
    rebase.
    """
    assert _run_git_check(ahead=0, empty_diff=True, upstream=True) is None


def _run_unrelated_check(*, merge_base_rc, rev_parse_rc=0):
    """Run the unrelated-history check with git's merge-base answer stubbed."""
    def fake_run(cmd, **kwargs):
        rc = 0
        if cmd[:2] == ["git", "merge-base"]:
            rc = merge_base_rc
        elif cmd[:2] == ["git", "rev-parse"]:
            rc = rev_parse_rc
        return subprocess.CompletedProcess(
            args=cmd, returncode=rc, stdout="", stderr="",
        )

    with mock.patch("subprocess.run", side_effect=fake_run):
        return refusals.unrelated_history_check(
            "/fake", _landed_ctx(), target_ref=_TARGET,
        )


def test_unrelated_check_refuses_a_branch_with_no_merge_base():
    """A branch cut from a different root would replay its whole history."""
    report = _run_unrelated_check(merge_base_rc=1)

    assert report.signal == rebase_types.RefusalSignal.NO_MERGE_BASE.value
    assert report.status == pr_domains.RebaseStatus.UNRELATED_HISTORY.value
    assert _TARGET in report.detail
    # No merge base means no meaningful "ahead of" count to report.
    assert report.commits_ahead is None


def test_unrelated_check_passes_a_connected_branch():
    assert _run_unrelated_check(merge_base_rc=0) is None


def test_unrelated_check_passes_a_ref_that_does_not_resolve():
    """A typo'd `--onto` fails merge-base too, and is not unrelated history.

    Refusing it would send the operator after a root they do not have; git's own
    error for the missing ref is the honest report.
    """
    assert _run_unrelated_check(merge_base_rc=1, rev_parse_rc=1) is None


def test_refuse_over_budget_aborts_before_refusing(capsys):
    """The refusal restores the branch — it does not leave a rebase in progress."""
    ctx = _landed_ctx()
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None):
        rc = refusals.refuse_over_budget("/fake", ctx, 35, target_ref=_TARGET)

    assert rc == 4
    assert ["git", "rebase", "--abort"] in commands
    payload = json.loads(capsys.readouterr().out)
    assert payload["signal"] == rebase_types.RefusalSignal.CONFLICTS_OVER_BUDGET.value
    assert payload["status"] == pr_domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value
    assert payload["override"] == "--force"
    assert "35" in payload["detail"]


def test_refuse_renders_the_hint_for_every_refusal_status():
    """Every status a refusal can carry has an explanation to print.

    `_refuse` indexes the hint table by status, so a status added without a row
    raises rather than printing nothing — this pins that they stay in step.
    """
    statuses = {
        pr_domains.RebaseStatus.ALREADY_LANDED.value,
        pr_domains.RebaseStatus.UNRELATED_HISTORY.value,
        pr_domains.RebaseStatus.CONFLICTS_OVER_BUDGET.value,
    }
    assert set(refusals.REFUSAL_HINTS) == statuses


def test_refuse_landed_emits_the_exit_4_payload(capsys):
    ctx = _landed_ctx()
    report = rebase_types.RefusalReport(
        branch=_LANDED_BRANCH, signal="pr_merged",
        detail=f"PR #{_LANDED_PR} is merged", commits_ahead=18, pr_number=_LANDED_PR,
    )

    with mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None):
        rc = refusals.refuse(ctx, report, target_ref=_TARGET)

    captured = capsys.readouterr()
    assert rc == 4
    assert json.loads(captured.out) == {
        "branch": _LANDED_BRANCH, "signal": "pr_merged",
        "detail": f"PR #{_LANDED_PR} is merged", "commits_ahead": 18,
        "pr_number": _LANDED_PR, "status": "already_landed", "override": "--force",
    }
    assert f"Refusing to rebase {_LANDED_BRANCH}" in captured.err
    assert "--force" in captured.err


def test_refuse_landed_keeps_every_documented_key_when_unmeasured(capsys):
    """SKILL.md documents the key set — the tracker path nulls, never drops."""
    report = rebase_types.RefusalReport(
        branch=_LANDED_BRANCH, signal="pr_merged",
        detail=f"PR #{_LANDED_PR} is merged", pr_number=_LANDED_PR,
    )

    with mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None):
        refusals.refuse(_landed_ctx(), report, target_ref=_TARGET)

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "branch", "signal", "detail", "commits_ahead",
        "pr_number", "status", "override",
    }
    assert payload["commits_ahead"] is None


def test_refuse_landed_records_the_status_for_the_dashboard():
    ctx = _landed_ctx()
    report = rebase_types.RefusalReport(
        branch=_LANDED_BRANCH, signal="empty_diff", detail="no diff", commits_ahead=2,
    )

    with mock.patch.object(core_report, "emit_json"):
        refusals.refuse(ctx, report, target_ref=_OTHER_TARGET)

    state = pr_state.load_state(ctx.target_dir)
    assert state.rebase.status == pr_domains.RebaseStatus.ALREADY_LANDED.value
    assert state.rebase.target_base == _OTHER_TARGET


def _run_fresh(*, tracker=None, git=None, unrelated=None, force=False,
               current_branch=_LANDED_BRANCH, target_ref=_TARGET):
    """Run _fresh with every half of the preflight forced.

    Returns (exit code, commands run, checkout-seen-by-each-half), the last of
    which is what pins the ordering: the tracker probe must run before the
    checkout and the git signals after it.
    """
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = current_branch
    commands, saw_checkout = [], {}

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        return _completed(cmd)

    def record(half, report):
        saw_checkout[half] = any(c[:2] == ["git", "checkout"] for c in commands)
        return report

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(refusals, "tracker_landed_check",
                           side_effect=lambda *_, **kw: record("tracker", tracker)), \
         mock.patch.object(refusals, "git_landed_check",
                           side_effect=lambda *_, **kw: record("git", git)), \
         mock.patch.object(refusals, "unrelated_history_check",
                           side_effect=lambda *_, **kw: record("unrelated", unrelated)), \
         mock.patch.object(refusals, "refuse", return_value=4), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        rc = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, force=force, target_ref=target_ref,
        )

    return rc, commands, saw_checkout


def _landed_report(signal="pr_merged"):
    return rebase_types.RefusalReport(
        branch=_LANDED_BRANCH, signal=signal,
        detail=f"PR #{_LANDED_PR} is merged", pr_number=_LANDED_PR,
    )


def test_fresh_refuses_a_landed_branch_before_touching_the_remote():
    """Regression: rebasing a merged branch force-pushed the deleted remote back."""
    rc, commands, _ = _run_fresh(tracker=_landed_report())

    assert rc == 4
    assert not any(cmd[:2] == ["git", "rebase"] for cmd in commands)
    assert not any(cmd[:2] == ["git", "push"] for cmd in commands)


def test_fresh_asks_the_tracker_before_the_checkout():
    """--prune has just deleted origin/<branch>; the checkout starts from it."""
    rc, commands, saw_checkout = _run_fresh(
        tracker=_landed_report(), current_branch="other",
    )

    assert rc == 4
    assert saw_checkout["tracker"] is False
    assert not any(cmd[:2] == ["git", "checkout"] for cmd in commands)


def test_fresh_probes_the_tracker_even_when_already_on_the_branch():
    """The common case takes the same path — no checkout, same probe."""
    rc, commands, saw_checkout = _run_fresh()

    assert rc == 0
    assert saw_checkout["tracker"] is False
    assert not any(cmd[:2] == ["git", "checkout"] for cmd in commands)


def test_fresh_runs_the_git_signals_after_the_checkout():
    """They compare HEAD, so they mean nothing until the branch is checked out."""
    rc, _, saw_checkout = _run_fresh(
        git=_landed_report("empty_diff"), current_branch="other",
    )

    assert rc == 4
    assert saw_checkout["git"] is True


def test_fresh_refuses_an_unrelated_branch_before_rebasing():
    """The rebase would replay the branch's whole history onto a foreign root."""
    report = rebase_types.RefusalReport(
        branch=_LANDED_BRANCH, signal=rebase_types.RefusalSignal.NO_MERGE_BASE.value,
        detail=f"no commit in common with {_TARGET}",
        status=pr_domains.RebaseStatus.UNRELATED_HISTORY.value,
    )
    rc, commands, saw_checkout = _run_fresh(unrelated=report, current_branch="other")

    assert rc == 4
    # It compares HEAD against the ref, so it means nothing before the checkout.
    assert saw_checkout["unrelated"] is True
    assert not any(cmd[:2] == ["git", "rebase"] for cmd in commands)


def test_fresh_asks_about_unrelated_history_before_the_git_landed_signals():
    """The landed signals compare against a ref an unrelated branch cannot answer for."""
    rc, _, saw_checkout = _run_fresh(
        unrelated=rebase_types.RefusalReport(
            branch=_LANDED_BRANCH,
            signal=rebase_types.RefusalSignal.NO_MERGE_BASE.value,
            detail="no commit in common",
            status=pr_domains.RebaseStatus.UNRELATED_HISTORY.value,
        ),
    )

    assert rc == 4
    assert "git" not in saw_checkout


def test_fresh_skips_every_half_of_the_preflight_under_force():
    """--force must not spend a gh round trip only to ignore the answer."""
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = _LANDED_BRANCH

    with mock.patch("subprocess.run", side_effect=lambda cmd, **kw: _completed(cmd)), \
         mock.patch.object(refusals, "tracker_landed_check") as mock_tracker, \
         mock.patch.object(refusals, "git_landed_check") as mock_git, \
         mock.patch.object(refusals, "unrelated_history_check") as mock_unrelated, \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, force=True, target_ref=_TARGET,
        )

    mock_tracker.assert_not_called()
    mock_git.assert_not_called()
    mock_unrelated.assert_not_called()


def test_fresh_rebases_a_landed_branch_under_force():
    rc, commands, _ = _run_fresh(tracker=_landed_report(), force=True)

    assert rc == 0
    assert ["git", "rebase", _TARGET] in commands


def test_fresh_rebases_onto_the_resolved_ref():
    """A repo whose trunk is not main must not be replayed onto origin/main.

    The ref reaching `git rebase` is the one the run resolved, so a repo on
    master, a release branch, or a stack parent replays onto its own base.
    """
    rc, commands, _ = _run_fresh(target_ref="origin/master")

    assert rc == 0
    assert ["git", "rebase", "origin/master"] in commands
    assert not any(cmd[:2] == ["git", "rebase"] and cmd[2] == _TARGET
                   for cmd in commands)


def test_fresh_prunes_on_fetch():
    """Without --prune the stale remote-tracking ref satisfies --force-with-lease."""
    _, commands, _ = _run_fresh()

    assert ["git", "fetch", "--prune", "origin"] in commands


def test_fresh_falls_back_to_the_git_signals_when_the_tracker_is_unreachable():
    """A gh that cannot answer must not disarm the preflight."""
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = _LANDED_BRANCH
    seen = []

    with mock.patch("subprocess.run", side_effect=lambda cmd, **kw: _completed(cmd)), \
         mock.patch.object(regen, "try_run", return_value=None), \
         mock.patch.object(git_client, "commits_ahead", return_value=2), \
         mock.patch.object(branch_landed, "diff_is_empty", return_value=True), \
         mock.patch.object(refusals, "refuse",
                           side_effect=lambda c, r, **kw: (seen.append(r), 4)[1]), \
         mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(lifecycle, "rebase_success", return_value=0):
        rc = lifecycle.fresh(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert rc == 4
    assert seen[0].signal == rebase_types.RefusalSignal.EMPTY_DIFF.value


def _merged_and_deleted_remote(tmp_path) -> Path:
    """A clone whose feature branch merged and whose remote branch is gone.

    Built against real git rather than a stubbed subprocess because the bug is
    in the interaction between `fetch --prune` and the checkout that follows:
    prune drops origin/<branch>, and the checkout starts from that exact ref.
    The worktree is left on an unrelated branch so _fresh has to check out.
    """
    origin = tmp_path / "origin"
    run_checked(["git", "init", "--bare", "-b", "main", str(origin)])
    work = tmp_path / "work"
    run_checked(["git", "clone", str(origin), str(work)])

    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "base.txt").write_text("base\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "main")

    _git(work, "checkout", "-b", _LANDED_BRANCH)
    (work / "feature.txt").write_text("feature\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "feature")
    _git(work, "push", "-u", "origin", _LANDED_BRANCH)

    # The merge, as GitHub leaves it: the remote branch is deleted and the
    # worktree is sitting on something else by the time anyone rebases.
    _git(work, "checkout", "-b", "other", "main")
    _git(work, "push", "origin", "--delete", _LANDED_BRANCH)
    return work


def test_fresh_refuses_a_merged_branch_whose_remote_was_pruned(tmp_path, capsys):
    """Regression: prune deleted origin/<branch>, so the checkout failed first.

    The refusal never reached the exact input it exists for — a merged branch —
    because `git checkout -B <branch> origin/<branch>` errored out with a
    generic message and exit 1 before any signal was consulted.
    """
    work = _merged_and_deleted_remote(tmp_path)
    ctx = _landed_ctx(
        worktree_root=work, current_branch="other",
        target_dir=tmp_path / "target",
    )

    with mock.patch.object(branch_landed, "merged_pr",
                           return_value=branch_landed.MergedPR(number=_LANDED_PR)):
        rc = lifecycle.fresh(
            str(work), ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    captured = capsys.readouterr()
    assert rc == 4
    assert json.loads(captured.out)["signal"] == rebase_types.RefusalSignal.PR_MERGED.value
    assert "Cannot checkout" not in captured.err
    # The prune really happened, so the old order really would have failed here.
    refs = run_checked(["git", "-C", str(work), "branch", "-r"])
    assert f"origin/{_LANDED_BRANCH}" not in refs.stdout


def _tool_schema(capsys) -> dict:
    """The --tool-schema document, which parse_args prints before resolving."""
    with mock.patch("sys.argv", ["pr-rebase", "--tool-schema"]), pytest.raises(SystemExit):
        pr_rebase_cli.main()
    return json.loads(capsys.readouterr().out)


def test_landed_exit_code_is_published_as_a_reportable_outcome(capsys):
    """The MCP layer renders any code outside ok_exit_codes as a tool error."""
    assert rebase_types.REFUSAL_EXIT in _tool_schema(capsys)["ok_exit_codes"]


def test_force_is_published_in_the_input_schema(capsys):
    """The skill can only offer the override if the schema names the flag."""
    assert "force" in _tool_schema(capsys)["input_schema"]["properties"]


def test_main_threads_force_into_cmd_start():
    exit_code, _, mock_start = _run_main(0, "--force")

    assert exit_code == 0
    assert mock_start.call_args.kwargs["force"] is True


def test_main_leaves_the_preflight_armed_by_default():
    _, _, mock_start = _run_main(0)

    assert mock_start.call_args.kwargs["force"] is False


def test_main_rebases_a_master_repo_onto_origin_master():
    """A repo whose default branch is not main must not be sent to origin/main."""
    _, _, mock_start = _run_main(0, default_branch="master")

    assert mock_start.call_args.kwargs["target_ref"] == "origin/master"


def test_main_rebases_onto_the_pr_base_rather_than_the_default_branch():
    """A stacked or release-branch PR replays onto its own base.

    Rebasing it onto the trunk would replay the parent's commits too and then
    force-push the result — silently, with no error to notice.
    """
    _, _, mock_start = _run_main(0, pr_base=_OTHER_BASE, default_branch="main")

    assert mock_start.call_args.kwargs["target_ref"] == _OTHER_TARGET


def test_main_lets_onto_override_every_probe():
    _, _, mock_start = _run_main(
        0, "--onto", "upstream/trunk", pr_base=_OTHER_BASE, default_branch="master",
    )

    assert mock_start.call_args.kwargs["target_ref"] == "upstream/trunk"


def test_main_threads_one_ref_into_both_commands():
    """cmd_push records the base, so a second resolution could disagree."""
    _, mock_push, mock_start = _run_main(0, "--push", default_branch="master")

    assert mock_start.call_args.kwargs["target_ref"] == "origin/master"
    assert mock_push.call_args.kwargs["target_ref"] == "origin/master"


def test_main_resolves_the_target_ref_once():
    """Each resolution can hit the GitHub API, and both answers must agree."""
    with mock.patch.object(rebase_target, "resolve_target_ref",
                           return_value=_TARGET) as resolve:
        _run_main(0)

    resolve.assert_called_once()


def test_main_aborts_without_asking_the_network():
    """Abort is the escape hatch for a hung rebase — it cannot need a round trip."""
    with mock.patch.object(rebase_types, "recorded_target_base",
                           return_value="origin/release/1.2"), \
         mock.patch.object(rebase_target, "resolve_target_ref") as resolve, \
         mock.patch.object(pr_rebase_cli, "cmd_abort", return_value=0) as abort:
        _run_main(0, "--abort")

    resolve.assert_not_called()
    assert abort.call_args.kwargs["target_ref"] == "origin/release/1.2"


# ── _land ──────────────────────────────────────────────────────────────────


def _owner_reports(result: land.LandResult):
    """Patch the land owner, whose own behaviour is `tests/land_test.py`'s subject.

    What is left here is the composition: which flags this script asks the owner
    for, and which of the owner's answers is worth handing to the AI fix.
    """
    return mock.patch.object(prepush.land, "land_head", return_value=result)


def test_land_asks_the_owner_for_a_force_push_with_the_regen_recovery():
    """The rebase replayed the branch, so every push here rewrites the remote.

    `gated=True` whatever the mode: this is the one entry point where pushing is
    the command rather than an accident, and `main` opens the publishing gate
    for the modes that reach the remote. The gate, not this argument, is what
    `--no-push` shuts.
    """
    landed = _pushed()

    with _owner_reports(landed) as owner:
        assert rebase_land.land_rebased("/fake") is landed

    assert owner.call_args[0][0] == "/fake"
    kwargs = owner.call_args.kwargs
    assert kwargs["gated"] is True
    assert kwargs["args"] == ("--force-with-lease",)
    assert kwargs["regen"] == rebase_types.REGEN_MESSAGE


def test_a_landed_push_never_reaches_the_ai_fix():
    with _owner_reports(_pushed()), \
         mock.patch.object(prepush, "fix_push_failures") as mock_fix:
        assert rebase_land.land_rebased(
            "/fake", resolved_files=["server.go"]).ok

    mock_fix.assert_not_called()


def test_a_refusal_hands_the_hook_output_to_the_ai_fix():
    """The second recovery: `land` committed what the hook regenerated and the
    checks still failed, so the output is a complaint an agent can act on."""
    repaired = _pushed(sha="9f8e7d6")

    with _owner_reports(_refused("gofmt: server.go")), \
         mock.patch.object(prepush, "fix_push_failures",
                           return_value=repaired) as mock_fix:
        assert rebase_land.land_rebased(
            "/fake", resolved_files=["server.go"]) is repaired

    mock_fix.assert_called_once_with(
        "/fake", "gofmt: server.go", ["server.go"], trail=None)


def test_a_fix_that_produced_nothing_leaves_the_refusal_standing():
    """No backend, or an agent that changed nothing — the push is still the answer."""
    refusal = _refused()

    with _owner_reports(refusal), \
         mock.patch.object(prepush, "fix_push_failures", return_value=None):
        assert rebase_land.land_rebased(
            "/fake", resolved_files=["server.go"]) is refusal


@pytest.mark.parametrize("result", [
    land.LandResult(CommitStatus.PUSH_HELD, sha=_LANDED_SHA, resume=_RESUME,
                    push=push.PushResult(push.PushStatus.HELD, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
    land.LandResult(CommitStatus.PUSH_LOST, sha=_LANDED_SHA,
                    push=push.PushResult(push.PushStatus.LOST, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
    land.LandResult(CommitStatus.PUSH_UNVERIFIED, sha=_LANDED_SHA,
                    push=push.PushResult(push.PushStatus.UNVERIFIED, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
])
def test_only_a_refusal_reaches_the_ai_fix(result):
    """A held, lost, or unverified push says nothing is wrong with the worktree.

    Handing one to the fix pass asks an agent to rewrite code that passed every
    check — and under `--no-push` it would do that on every run.
    """
    with _owner_reports(result), \
         mock.patch.object(prepush, "fix_push_failures") as mock_fix:
        assert rebase_land.land_rebased(
            "/fake", resolved_files=["server.go"]) is result

    mock_fix.assert_not_called()


def test_a_refusal_with_no_resolved_files_skips_the_ai_fix():
    """Nothing the AI resolved means nothing it has standing to repair."""
    with _owner_reports(_refused()), \
         mock.patch.object(prepush, "fix_push_failures") as mock_fix:
        assert not rebase_land.land_rebased("/fake").ok

    mock_fix.assert_not_called()


def test_a_refusal_that_said_nothing_skips_the_ai_fix():
    """An empty complaint is not a prompt — the agent would be guessing."""
    with _owner_reports(_refused(error="")), \
         mock.patch.object(prepush, "fix_push_failures") as mock_fix:
        rebase_land.land_rebased("/fake", resolved_files=["server.go"])

    mock_fix.assert_not_called()


# ── the pre-push fix pass ─────────────────────────────────────────────────


def _answering(adapter_box, *, tick="fixed", reason=""):
    """A `run_fix` stub that answers the checklist the engine wrote.

    The engine rewrites the tracking file immediately before each invocation,
    so an answer written any earlier is thrown away before an agent would see
    it. `adapter_box` is a one-element list the caller fills in once the
    adapter exists, since the stub is installed before the pass constructs one.
    """
    def run_fix(_phase, prompt, **_kwargs):
        _prompts.append(prompt)
        tracking = adapter_box[0].tracking_path
        box = f"- [ ] {tick}"
        answer = f"- [x] {tick}" + (f" — {reason}" if reason else "")
        tracking.write_text("\n".join(
            answer if line.startswith(box) else line
            for line in tracking.read_text().splitlines()
        ) + "\n")
        return agent_invoke.FixResult(0, None)
    return run_fix


_prompts: list[str] = []


@contextlib.contextmanager
def _fix_pass(*, tick="fixed", reason="", landed=None, available=True):
    """Drive the pass with a stubbed agent and a stubbed landing owner.

    `land` is stubbed rather than run: what a landing does belongs to
    `tests/land_test.py`, and what this pass asks for is the assertion here.
    """
    _prompts.clear()
    box: list = [None]
    real_init = prepush.PrePushFixAdapter.__init__

    def capture(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        box[0] = self

    result = landed or land.LandResult(land.CommitStatus.PUSHED, "abc1234")
    with mock.patch.object(prepush.PrePushFixAdapter, "__init__", capture), \
         mock.patch.object(prepush.ai_backend, "is_available", return_value=available), \
         mock.patch.object(fix_engine.git_client, "head_sha", return_value="9999999"), \
         mock.patch.object(fix_engine.land, "land", return_value=result) as owner, \
         mock.patch.object(fix_engine.agent_invoke, "run_fix",
                           side_effect=_answering(box, tick=tick, reason=reason)):
        yield owner, box


def test_the_pass_force_pushes_the_branch_it_repaired(tmp_path):
    """A replayed branch's push is non-fast-forward; a plain one is rejected."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _):
        result = prepush.fix_push_failures(
            str(tmp_path), "gofmt: server.go needs formatting", ["server.go"],
        )

    assert result.sha == "abc1234"
    kwargs = owner.call_args.kwargs
    assert kwargs["gated"] is True
    assert kwargs["args"] == ("--force-with-lease",)


def test_the_pass_commits_the_whole_tree(tmp_path):
    """Direct agent edits reach the commit instead of being stranded.

    The backend runs with acceptEdits and Bash(*), so a repair can land in a
    file the pass never named — committing a narrower set force-pushes without
    the real source fix.
    """
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _):
        prepush.fix_push_failures(str(tmp_path), "vet: server.go", ["server.go"])

    assert owner.call_args.kwargs["paths"] is None


def test_the_pass_accounts_for_an_agent_that_committed_its_own_work(tmp_path):
    """Otherwise the pass finds nothing to stage and reports a real fix as nothing."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _):
        prepush.fix_push_failures(str(tmp_path), "vet: server.go", ["server.go"])

    assert owner.call_args.kwargs["recover_from"] == "9999999"


def test_the_commit_body_carries_each_file_s_verdict(tmp_path):
    """Squash-merged with COMMIT_MESSAGES, so this lands verbatim on main."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(tick="needs a person", reason="the resolution dropped a branch") as (owner, _):
        prepush.fix_push_failures(str(tmp_path), "vet: server.go", ["server.go"])

    message = owner.call_args.kwargs["message"]
    assert message.startswith(prepush.FIX_SUBJECT)
    assert "0 fixed, 1 unresolved" in message
    assert "- server.go — the resolution dropped a branch" in message


def test_the_check_output_reaches_the_agent(tmp_path):
    """The output is the oracle — a pass that withholds it asks for a guess."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass():
        prepush.fix_push_failures(
            str(tmp_path), "gofmt: server.go needs formatting", ["server.go"],
        )

    assert "gofmt: server.go needs formatting" in _prompts[0]


def test_the_pass_bills_to_the_run_s_repo_and_pr(tmp_path):
    """A rebase-assist call attributes to the PR the run is rebasing."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {"repo": "org/repo", "pr": 7, "branch": "feat/x"}

    with _fix_pass() as (_, box):
        prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], trail=trail,
        )

    assert (box[0].repo, box[0].pr, box[0].branch) == ("org/repo", "7", "feat/x")


def test_a_branch_with_no_pr_bills_to_the_repo_alone(tmp_path):
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {"repo": "org/repo", "pr": None, "branch": "feat/x"}

    with _fix_pass() as (_, box):
        prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], trail=trail,
        )

    assert (box[0].repo, box[0].pr) == ("org/repo", "")


def test_the_tracking_file_sits_beside_the_rebase_s_other_leavings(tmp_path):
    adapter = prepush.PrePushFixAdapter(str(tmp_path), ["a.py"], "output")
    artifacts = tmp_path / "ignore" / "pr-rebase"

    assert adapter.tracking_path == artifacts / "fix-tracking.md"
    assert adapter.session_log == artifacts / "fix-session.jsonl"


def test_the_pass_records_what_it_did_on_the_trail(tmp_path):
    """The trail is the only durable record — no ctx here means no FixRecord."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass():
        prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], trail=trail,
        )

    entries = {c.args[1]: c.kwargs.get("data", {}) for c in trail.info.call_args_list}
    assert "committed check-failure fixes" in entries
    assert entries["committed check-failure fixes"]["files"] == ["server.go"]
    assert entries["committed check-failure fixes"]["sha"] == "abc1234"


def test_a_pass_that_committed_nothing_is_not_reported_as_a_commit(tmp_path):
    """Otherwise the trail carries a fix no SHA stands behind."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass(landed=land.LandResult(land.CommitStatus.NO_CHANGES)):
        prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], trail=trail,
        )

    entries = [c.args[1] for c in trail.info.call_args_list]
    assert "pre-push fix pass committed nothing" in entries
    assert "committed check-failure fixes" not in entries


def test_no_backend_attempts_nothing(tmp_path):
    """The engine invokes unconditionally, so the guard stays outside it."""
    (tmp_path / "file.go").write_text("package main\n")

    with _fix_pass(available=False) as (owner, _), \
         mock.patch.object(rebase_conflicts, "is_generated_file", return_value=None):
        result = prepush.fix_push_failures(str(tmp_path), "errors", ["file.go"])

    assert result is None
    owner.assert_not_called()


# ── generated files are rebuilt, never edited ─────────────────────────────


def test_a_generated_file_is_rebuilt_instead_of_prompted(tmp_path):
    """The bug this guard exists for: a generated file must never reach the AI.

    Hand-editing a protobuf descriptor or a hash manifest cannot produce the
    generator's output, so the drift check that refused the push refuses it
    again — after spending the agent's budget per artifact.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    regenerated = []

    with _fix_pass() as (owner, box), \
         mock.patch.object(regen, "run_regeneration",
                           side_effect=lambda job, **kw: regenerated.append(job.cmd) or True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        prepush.fix_push_failures(str(tmp_path), "drift: models.go", ["models.go"])

    assert box[0] is None, "a generated file was handed to the fix pass"
    assert regenerated == [("mise", "run", "generate")]


def test_a_rebuild_with_nothing_else_to_fix_is_still_landed(tmp_path):
    """The engine no-ops on an empty item set, but the rebuild is the repair.

    Withholding it because no editable file was named leaves the branch
    unpushable for the same drift the hook rejected.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")

    with _fix_pass(), \
         mock.patch.object(regen, "run_regeneration", return_value=True), \
         mock.patch.object(prepush.land, "land",
                           return_value=land.LandResult(land.CommitStatus.PUSHED, "reb1234")) as owner, \
         _repo_declaring(["mise run generate"], root=tmp_path):
        result = prepush.fix_push_failures(
            str(tmp_path), "drift: models.go", ["models.go"],
        )

    assert result.sha == "reb1234"
    kwargs = owner.call_args.kwargs
    assert kwargs["args"] == ("--force-with-lease",)
    assert kwargs["gated"] is True
    assert kwargs["message"] == prepush.REGEN_MESSAGE


def test_a_rebuild_alongside_editable_work_is_swept_into_the_agent_s_commit(tmp_path):
    """One commit, not two: a hook validates the worktree, not the commits under it.

    Splitting the rebuild out would push a HEAD the green run never saw.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _), \
         mock.patch.object(regen, "run_regeneration", return_value=True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        prepush.fix_push_failures(
            str(tmp_path), "drift: models.go, vet: server.go",
            ["models.go", "server.go"],
        )

    # `prepush.land` and `fix_engine.land` are one module, so the count is what
    # distinguishes one commit from two — not which name the owner was reached
    # through.
    assert owner.call_count == 1
    assert owner.call_args.kwargs["message"].startswith(prepush.FIX_SUBJECT)
    assert owner.call_args.kwargs["paths"] is None


def test_the_agent_is_asked_only_about_the_hand_written_files(tmp_path):
    """The guard is scoped: a normal file alongside a generated one still gets fixed."""
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    (tmp_path / "handler.go").write_text("package main\n")

    with _fix_pass() as (_, box), \
         mock.patch.object(regen, "run_regeneration", return_value=True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        prepush.fix_push_failures(
            str(tmp_path), "drift: models.go, vet: handler.go",
            ["models.go", "handler.go"],
        )

    assert [i.id for i in box[0].items()] == ["handler.go"]


def test_a_generated_file_with_no_regenerator_is_left_alone(tmp_path):
    """Unable to rebuild is still not a licence to hand-edit."""
    (tmp_path / "x.gen").write_text("// @generated\n")

    with _fix_pass() as (owner, box), \
         _repo_declaring([], root=tmp_path, mise_task=False):
        result = prepush.fix_push_failures(str(tmp_path), "drift: x.gen", ["x.gen"])

    assert box[0] is None
    assert result is None
    owner.assert_not_called()


def test_a_rebuild_that_did_not_commit_is_recorded(tmp_path):
    """A regeneration that lands nothing is the quiet failure worth a trail entry."""
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass(), \
         mock.patch.object(regen, "run_regeneration", return_value=True), \
         mock.patch.object(prepush.land, "land",
                           return_value=land.LandResult(land.CommitStatus.NO_CHANGES)), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        prepush.fix_push_failures(
            str(tmp_path), "drift: models.go", ["models.go"], trail=trail,
        )

    assert "regenerated files did not commit" in [
        c.args[1] for c in trail.error.call_args_list
    ]


# ── which files the pass is asked about ───────────────────────────────────


def _targets(tmp_path, error_output, resolved_files):
    """The files the pass would hand an agent, without running one."""
    box: list = [None]
    real_init = prepush.PrePushFixAdapter.__init__

    def capture(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        box[0] = self

    for name in dict.fromkeys(resolved_files):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n")

    with mock.patch.object(prepush.PrePushFixAdapter, "__init__", capture), \
         mock.patch.object(prepush.ai_backend, "is_available", return_value=True), \
         mock.patch.object(fix_engine, "run", return_value=fix_engine.FixRun()):
        prepush.fix_push_failures(tmp_path.as_posix(), error_output, resolved_files)

    return box[0].editable if box[0] else []


def test_only_the_files_the_check_names_are_handed_over(tmp_path):
    """A rebase resolves dozens of files; the pass asks about the named ones.

    Handing over every resolved file spends budget per entry and gives an agent
    with edit access a file the check never complained about.
    """
    resolved = ["bin/otto-workbench", "docs/libraries.md", "ai/lib/prompt.py"]
    error = "✗ bin/otto-workbench: nesting exceeds 2 levels\n      line 1304: depth 3\n"

    assert _targets(tmp_path, error, resolved) == ["bin/otto-workbench"]


def test_a_file_resolved_in_several_commits_is_handed_over_once(tmp_path):
    resolved = ["ai/lib/review_issue.py"] * 4
    error = "ai/lib/review_issue.py:12: undefined name"

    assert _targets(tmp_path, error, resolved) == ["ai/lib/review_issue.py"]


def test_a_basename_only_report_still_scopes_to_that_file(tmp_path):
    resolved = ["pkg/server.go", "pkg/client.go"]
    error = "gofmt: server.go needs formatting"

    assert _targets(tmp_path, error, resolved) == ["pkg/server.go"]


def test_check_output_naming_no_file_leaves_every_file_a_suspect(tmp_path):
    resolved = ["pkg/server.go", "pkg/client.go"]

    assert _targets(tmp_path, "build failed: exit status 2", resolved) == resolved


def test_the_unscoped_fallback_is_recorded(tmp_path):
    """The expensive unscoped pass is visible in the trail, not just in spend."""
    (tmp_path / "server.go").write_text("stub\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass():
        prepush.fix_push_failures(
            str(tmp_path), "build failed", ["server.go"], trail=trail,
        )

    assert "check output named no resolved file" in [
        c.args[1] for c in trail.info.call_args_list
    ]


def test_the_error_output_handed_over_is_truncated(tmp_path):
    """A check can print megabytes; the prompt carries a bounded slice of it."""
    (tmp_path / "server.go").write_text("package main\n")
    long_error = "server.go " + "x" * 10000

    with _fix_pass() as (_, box):
        prepush.fix_push_failures(str(tmp_path), long_error, ["server.go"])

    assert len(box[0].check_output) == prepush.FIX_ERROR_MAX_CHARS


# ── _status_lines ──────────────────────────────────────────────────────────


def test_status_lines_reads_a_clean_worktree_as_empty(tmp_path):
    """Half the contract: clean is an empty list, and an empty list is not None."""
    init_worktree(tmp_path)
    assert rebase_inspect.status_lines(str(tmp_path)) == []


def test_status_lines_cannot_read_a_path_that_is_not_a_repo(tmp_path):
    """The other half: a read that failed is None, not a tree with nothing in it."""
    assert rebase_inspect.status_lines(str(tmp_path)) is None


def test_status_lines_cannot_read_a_worktree_with_a_broken_index(tmp_path):
    init_worktree(tmp_path)
    (tmp_path / ".git" / "index").write_bytes(b"garbage")
    assert rebase_inspect.status_lines(str(tmp_path)) is None


def test_status_lines_folds_a_timeout_into_the_same_answer():
    """`subprocess.run(timeout=)` raises rather than returning non-zero.

    Uncaught, the exception aborts the command from inside a read whose whole
    job is to decide whether the rebase is safe to start.
    """
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeouts.LOCAL)

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_inspect.status_lines("/fake") is None


# ── _auto_stash ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status_out,expected", [
    (" M ai/lib/review_phases.py\n", True),
    ("?? scratch.txt\n", True),
    ("", False),
])
def test_auto_stash_covers_untracked_files(status_out, expected):
    """Untracked files are dirt too — they reach the hooks and the fix commit.

    Left in place they join what the pre-push hooks validate, and the recovery's
    whole-tree stage would then force-push a scratch file.
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = status_out if _unconfigured(cmd)[:2] == ["git", "status"] else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_stash.auto_stash("/fake") is expected

    stash_calls = [c for c in calls if c[:2] == ["git", "stash"]]
    assert bool(stash_calls) is expected
    assert all("-u" in c for c in stash_calls)


def test_auto_stash_refuses_when_the_worktree_cannot_be_read(tmp_path):
    """A failed status must not read as a clean tree and rebase over the work.

    git refusing a rebase on a dirty tree is a backstop that happens to catch
    this; `_auto_stash` is the guard, and its answer has to be honest whether
    or not something downstream would notice.
    """
    assert rebase_stash.auto_stash(str(tmp_path)) is None


def test_auto_stash_does_not_stash_a_tree_it_could_not_read():
    """Refusing means refusing before the stash, not stashing blind."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=128, stdout="", stderr="fatal: index file corrupt",
        )

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase_stash.auto_stash("/fake") is None

    assert not [c for c in calls if c[:2] == ["git", "stash"]]


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
         mock.patch.object(rebase_inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(prepush.log, "warn", side_effect=warnings.append):
        rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)

    assert any(rebase_stash.STASH_MSG in w for w in warnings)


# ── cmd_start ──────────────────────────────────────────────────────────────


def test_cmd_start_skips_stash_when_rebase_in_progress():
    """Mid-rebase resume must not attempt stash (git index is locked during rebase)."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase_stash, "auto_stash") as mock_stash, \
         mock.patch.object(lifecycle, "drive_to_completion", return_value=0):
        result = pr_rebase_cli.cmd_start(
            "/fake", ctx, rebase_types.RunMode.FIX, target_ref=_TARGET,
        )

    assert result == 0
    mock_stash.assert_not_called()


def test_cmd_start_stashes_before_fresh_rebase():
    """Fresh rebase stashes uncommitted changes before starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase_stash, "auto_stash", return_value=False) as mock_stash, \
         mock.patch.object(lifecycle, "fresh", return_value=0):
        result = pr_rebase_cli.cmd_start(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    mock_stash.assert_called_once()


def test_cmd_start_stash_failure_aborts():
    """When stash fails on a fresh rebase, cmd_start returns 1 without starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase_inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase_stash, "auto_stash", return_value=None), \
         mock.patch.object(lifecycle, "fresh") as mock_fresh:
        result = pr_rebase_cli.cmd_start(
            "/fake", ctx, rebase_types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1
    mock_fresh.assert_not_called()


# ── main() --push dispatch ──────────────────────────────────────────────────


def _run_main(cmd_start_rc: int, *flags: str,
              pr_base=None, default_branch="main",
              ) -> tuple[int, mock.MagicMock, mock.MagicMock]:
    """Run main() with the given flags. Returns (exit_code, cmd_push, cmd_start).

    The target-ref resolution runs for real off the two probes it consults, so
    a test can move the repo's trunk or the PR's base and watch what main()
    hands the commands.
    """
    fake_ctx = mock.MagicMock()
    fake_ctx.worktree_root = Path("/fake")
    fake_ctx.require_worktree.return_value = Path("/fake")
    fake_trail = mock.MagicMock()
    fake_trail.__enter__ = mock.Mock(return_value=fake_trail)
    fake_trail.__exit__ = mock.Mock(return_value=False)

    with mock.patch.object(pr_rebase_cli.pr_context, "resolve", return_value=fake_ctx), \
         mock.patch.object(rebase_target, "pr_base_branch", return_value=pr_base), \
         mock.patch.object(git_topology, "default_branch",
                           return_value=default_branch), \
         mock.patch.object(pr_rebase_cli, "Trail") as mock_trail_cls, \
         mock.patch.object(pr_rebase_cli, "cmd_start", return_value=cmd_start_rc) as mock_start, \
         mock.patch.object(pr_rebase_cli, "cmd_push", return_value=0) as mock_push:
        mock_trail_cls.start.return_value = fake_trail
        exit_code = pr_rebase_cli.main([*flags])
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
    (["--fix"], rebase_types.RunMode.FIX),
    (["--fix", "--no-push"], rebase_types.RunMode.FIX_ONLY),
    ([], rebase_types.RunMode.PUSH),
    (["--no-push"], rebase_types.RunMode.REBASE_ONLY),
])
def test_select_mode_keeps_fix_and_push_independent(flags, expected):
    """--fix says the AI may resolve; --no-push says nothing reaches the remote."""
    args = mock.Mock(fix="--fix" in flags, push="--no-push" not in flags)
    assert pr_rebase_cli._select_mode(args)[0] is expected


def test_fix_with_no_push_does_not_reach_the_remote():
    """`--fix --no-push` force-pushed anyway: main() branched on --fix first."""
    exit_code, mock_push, mock_start = _run_main(0, "--fix", "--no-push")

    mock_push.assert_not_called()
    assert mock_start.call_args[0][2] is rebase_types.RunMode.FIX_ONLY
    assert exit_code == 0


def test_rebase_success_in_fix_only_prints_the_push_command(capsys):
    """The force-push is handed to the user, not issued — repository policy.

    The landing still happens: `--no-push` shuts the publishing gate rather than
    skipping the call, so the command the user is handed is the one the owner
    drafted against the real worktree rather than a string composed here.
    """
    ctx = mock.MagicMock()
    with mock.patch.object(git_client, "commits_ahead", return_value=2), \
         _lands(_held()), \
         mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core_report, "emit_json") as mock_emit:
        rc = lifecycle.rebase_success(
            "/fake", ctx, rebase_types.RunMode.FIX_ONLY, target_ref=_TARGET,
        )

    assert rc == 0
    # force_pushed is None, which the emitter drops — "not pushed" rather than
    # "push failed", the same shape --no-push already reports.
    assert "force_pushed" not in mock_emit.call_args[0][0]
    assert _RESUME in capsys.readouterr().err


@pytest.mark.parametrize("mode,hinted", [
    (rebase_types.RunMode.REBASE_ONLY, True),
    (rebase_types.RunMode.FIX_ONLY, True),
    (rebase_types.RunMode.PUSH, False),
    (rebase_types.RunMode.FIX, False),
])
def test_manual_push_hint_only_when_the_run_never_pushes(mode, hinted, capsys):
    """The hint keyed on "pushes from here", so PUSH printed it then pushed.

    RunMode.PUSH pushes from main() via cmd_push, which is invisible to the
    rebase-completion path — the condition has to be whether the run reaches
    the remote at all.
    """
    ctx = mock.MagicMock()
    landed = _held() if hinted else _pushed()

    with mock.patch.object(git_client, "commits_ahead", return_value=2), \
         _lands(landed), \
         mock.patch.object(rebase_types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core_report, "emit_json"):
        rc = lifecycle.rebase_success("/fake", ctx, mode, target_ref=_TARGET)

    assert rc == 0
    err = capsys.readouterr().err
    assert (_RESUME in err) is hinted
    assert ("--no-push" in err) is hinted
    assert "Rebase complete" in err


def test_fix_only_still_resolves_conflicts():
    """--no-push suppresses the push, not the AI — the two must stay separable."""
    ctx = mock.MagicMock()
    tally = rebase_types.ResolutionTally()

    with mock.patch.object(rebase_inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase_inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(lifecycle, "ai_backend") as mock_ai, \
         mock.patch.object(rebase_resolve, "resolve_file_conflicts",
             return_value=rebase_types.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = lifecycle.step_conflicts(
            "/fake", ctx, rebase_types.RunMode.FIX_ONLY, ["a.py"], tally,
            target_ref=_TARGET,
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
