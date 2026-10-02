"""Tests for rebase.resolve_ai.resolve_file_conflicts across conflict kinds."""

import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.regenerate  # noqa: E402
import rebase.types  # noqa: E402
import rebase.conflicts  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import rebase.repo_regen  # noqa: E402
import core.log

from pr_rebase_support import _unconfigured, _TARGET


# ── _resolve_file_conflicts ───────────────────────────────────────────────


def test_resolve_file_conflicts_skips_binary():
    with tempfile.TemporaryDirectory() as tmpdir:
        binary_file = Path(tmpdir) / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        with mock.patch.object(
            rebase.conflicts, "is_generated_file", return_value=None,
        ):
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["image.png"], tmpdir, "abc123", "feat: add image",
                target_ref=_TARGET,
            )
        assert not result.ok
        assert result.failed == ["image.png"]
        assert result.files == []


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
                rebase.conflicts, "is_generated_file",
                return_value=rebase.types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase.resolve_ai.resolve_file_conflicts(
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
                rebase.conflicts, "is_generated_file",
                return_value=rebase.types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase.resolve_ai.resolve_file_conflicts(
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
             mock.patch.object(git.regenerate, "run_regeneration", return_value=True) as mock_regen:
            result = rebase.resolve_ai.resolve_file_conflicts(
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
            if _unconfigured(cmd)[:3] == ["git", "cat-file", "blob"] and ":2:" in cmd[-1]:
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
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
            )

        assert result.files == ["main.go"]
        assert conflict_file.read_text() == "merged code\n"
        # git add must target the worktree
        git_add_calls = [(c, w) for c, w in calls if c == ["git", "add", "main.go"]]
        assert len(git_add_calls) == 1
        assert git_add_calls[0][1] == tmpdir
        # Verify context-fetching git calls were made: each conflict stage read
        # once, which is both the prompt's target side and the survival check's
        # three versions.
        stage_reads = sorted(
            _unconfigured(c)[-1] for c, _ in calls
            if _unconfigured(c)[:3] == ["git", "cat-file", "blob"]
        )
        assert stage_reads == [":1:main.go", ":2:main.go", ":3:main.go"]
        diff_calls = [c for c, _ in calls if "REBASE_HEAD^" in str(c)]
        assert len(diff_calls) == 1


def _fake_run_with_context(extra_handler=None):
    """Return a fake subprocess.run that handles context-fetching git calls."""
    def fake_run(cmd, **kwargs):
        unconfigured = _unconfigured(cmd)
        if unconfigured[:3] == ["git", "cat-file", "blob"]:
            arg = cmd[-1]
            # Every stage is given distinct, present content by default so a
            # test built on this helper that exercises survival/loss behavior
            # (`judge_answer`, `stage_texts`) gets real base/replayed text
            # rather than the generic fallback's "empty file present", which
            # a real missing stage would never produce (git exits non-zero).
            if ":1:" in arg:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="base\n", stderr="")
            if ":2:" in arg:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="target\n", stderr="")
            if ":3:" in arg:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="replayed\n", stderr="")
        if unconfigured[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
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
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
            )

        assert not result.ok
        assert result.failed == ["main.go"]


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
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
            )

        assert not result.ok
        assert result.failed == ["main.go"]


def test_resolve_file_conflicts_keeps_going_past_one_unresolvable_file():
    """One bad answer must not cost the files that resolved fine.

    This returned None at the first failure, and the caller turned that into
    `git rebase --abort` — so one unparseable resolution threw away every file
    the run had already resolved and every commit it had already replayed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ("good.go", "bad.go", "also_good.go"):
            (Path(tmpdir) / name).write_text(
                "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
            )

        def handler(cmd, **kwargs):
            if cmd[:3] != ["claude", "-p", "--bare"]:
                return None
            # The prompt reaches the CLI on stdin, not in argv.
            if "bad.go" in kwargs.get("input", ""):
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="no markers here", stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="<<<RESOLVED>>>\nmerged\n<<<END_RESOLVED>>>\n", stderr="",
            )

        with mock.patch("subprocess.run", side_effect=_fake_run_with_context(handler)):
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["good.go", "bad.go", "also_good.go"], tmpdir,
                "abc123", "feat: refactor", target_ref=_TARGET,
            )

        assert result.failed == ["bad.go"]
        assert result.files == ["good.go", "also_good.go"]
        # The file *after* the failure was resolved on disk, not merely listed.
        # That is the difference between carrying on and reporting late, and
        # it is the half a count of names cannot show.
        assert (Path(tmpdir) / "also_good.go").read_text() == "merged\n"
        assert "<<<<<<<" in (Path(tmpdir) / "bad.go").read_text()


def test_resolve_file_conflicts_opens_a_span_per_conflicted_file():
    """The trail claim the skill makes, made true.

    SKILL.md tells an operator to read "a timed pair of events per conflicted
    file" to tell a progressing run from a wedged one. There was one span in
    the whole flow, and the common path emitted only a completion event — so
    the file being worked on right now was invisible, which is exactly the
    question that section claims to answer.
    """
    trail = mock.MagicMock()
    trail.span.return_value = contextlib.nullcontext()

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ("a.go", "b.go"):
            (Path(tmpdir) / name).write_text(
                "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
            )

        def handler(cmd, **kwargs):
            if cmd[:3] != ["claude", "-p", "--bare"]:
                return None
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="<<<RESOLVED>>>\nmerged\n<<<END_RESOLVED>>>\n", stderr="",
            )

        with mock.patch("subprocess.run", side_effect=_fake_run_with_context(handler)):
            rebase.resolve_ai.resolve_file_conflicts(
                ["a.go", "b.go"], tmpdir, "abc123", "feat: x",
                target_ref=_TARGET, trail=trail,
            )

    opened = [c[0][0] for c in trail.span.call_args_list]
    assert opened == ["resolve_file:a.go", "resolve_file:b.go"]


def test_resolve_file_conflicts_git_add_failure_is_reported_per_file():
    """M1: git add failure is reported per file, not by aborting the run."""
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
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["main.go"], tmpdir, "abc123", "feat: refactor",
                target_ref=_TARGET,
            )

        assert not result.ok
        assert result.failed == ["main.go"]


def test_resolve_file_conflicts_go_mod_uses_ai_merge():
    """go.mod is hand-maintained — it goes through AI merge, not accept-theirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        go_mod = Path(tmpdir) / "go.mod"
        go_mod.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(
            rebase.resolve_ai, "resolve_single_file", return_value="go.mod",
        ) as mock_ai, \
             mock.patch.object(git.regenerate, "run_regeneration") as mock_regen:
            result = rebase.resolve_ai.resolve_file_conflicts(
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
             mock.patch.object(git.regenerate, "run_regeneration", return_value=True) as mock_regen:
            result = rebase.resolve_ai.resolve_file_conflicts(
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

        plan = rebase.types.ConflictPlan(
            rebase.types.ConflictStrategy.DELETE,
            delete_side=rebase.types.DeleteSide.THEIRS_DELETED,
        )
        with mock.patch.object(
            rebase.conflicts, "classify_conflict", return_value=plan,
        ), mock.patch.object(
            rebase.conflicts, "resolve_delete_conflict", return_value=True,
        ) as mock_delete:
            result = rebase.resolve_ai.resolve_file_conflicts(
                [filepath], tmpdir, "abc123", "feat: cleanup",
                target_ref=_TARGET,
            )

        assert result.files == [filepath]
        mock_delete.assert_called_once_with(
            filepath, "abc123", tmpdir, rebase.types.DeleteSide.THEIRS_DELETED,
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
             mock.patch.object(git.regenerate, "run_regeneration", return_value=False), \
             mock.patch.object(core.log, "warn") as mock_warn:
            result = rebase.resolve_ai.resolve_file_conflicts(
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
                rebase.conflicts, "is_generated_file",
                return_value=rebase.types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch.object(rebase.repo_regen, "repo_regenerators", return_value=()),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase.resolve_ai.resolve_file_conflicts(
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
        regenerator = rebase.types.Regenerator(("mise", "run", "generate"))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                rebase.conflicts, "is_generated_file",
                return_value=rebase.types.GeneratedSignal.GITATTRIBUTES,
            ),
            mock.patch.object(rebase.repo_regen, "repo_regenerators", return_value=(regenerator,)),
            mock.patch.object(git.regenerate, "run_regeneration", return_value=True) as mock_regen,
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["service.pb.go"], tmpdir, "abc123", "feat: add proto",
                target_ref=_TARGET,
            )

        assert result.files == ["service.pb.go"]
        assert result.stale == []
        assert mock_regen.call_args[0][0].cmd == regenerator.cmd
