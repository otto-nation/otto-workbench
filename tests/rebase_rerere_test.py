"""Reuse of recorded conflict resolutions, and folding fixup! commits.

Driven against real repositories rather than stubs. Both behaviours are git's
rather than ours — what is under test is that the flags reach git, that git does
what the rebase driver assumes, and that the driver reads the result correctly.
A stub asserting on argv would pass whatever git actually did with it, which is
the half that has broken here before.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import client as git_client  # noqa: E402
from rebase import inspect as rebase_inspect  # noqa: E402
from rebase import lifecycle  # noqa: E402
from rebase import types as rebase_types  # noqa: E402


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body)


def _diverged(tmp_path, *, subject: str = "feat: branch edit") -> Path:
    """A repo whose `feat` branch conflicts with `main` in one file."""
    repo = init_repo(tmp_path / "repo")
    _write(repo, "f.txt", "a\nb\nc\n")
    git_in(repo, "add", "f.txt")
    git_in(repo, "commit", "-q", "-m", "base")

    git_in(repo, "checkout", "-q", "-b", "feat")
    _write(repo, "f.txt", "a\nBRANCH\nc\n")
    git_in(repo, "commit", "-q", "-am", subject)

    git_in(repo, "checkout", "-q", "main")
    _write(repo, "f.txt", "a\nMAIN\nc\n")
    git_in(repo, "commit", "-q", "-am", "main: base edit")
    git_in(repo, "checkout", "-q", "feat")
    return repo


def _resolve_and_continue(repo: Path, body: str) -> None:
    """Resolve the halted conflict the way the AI resolver does, then continue.

    Whole-file write, `git add`, `rebase --continue` — the same three steps as
    `resolve_ai.resolve_full_file` followed by `lifecycle.rebase_continue`, so
    what gets recorded in the rerere cache is what a real run would record.
    """
    _write(repo, "f.txt", body)
    git_in(repo, "add", "f.txt")
    lifecycle.rebase_continue(str(repo))


class TestRerereRecordsAndReplays:
    """The cache is written by our own resolution path and read back later."""

    def test_replays_a_resolution_recorded_by_the_resolver(self, tmp_path):
        """The second encounter of a conflict costs no resolver call.

        This is the whole point of the feature: the file is staged, so
        `detect_conflicts` no longer reports it and the driver never routes it
        to the AI.
        """
        repo = _diverged(tmp_path)
        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        assert rebase_inspect.detect_conflicts(str(repo)) == ["f.txt"]
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        # The same branch cut again from the same base: an identical conflict.
        git_in(repo, "checkout", "-q", "-b", "feat2", "main~1")
        _write(repo, "f.txt", "a\nBRANCH\nc\n")
        git_in(repo, "commit", "-q", "-am", "feat: branch edit again")
        result = git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                                config=lifecycle.REBASE_CONFIG)

        assert rebase_inspect.rerere_replayed(result.combined_output) == ["f.txt"]
        # Staged, not merely rewritten in the worktree. Without autoUpdate the
        # file is still unmerged here and the resolver pays for it again.
        assert rebase_inspect.detect_conflicts(str(repo)) == []
        assert (repo / "f.txt").read_text() == "a\nMAIN+BRANCH\nc\n"

    def test_records_nothing_without_the_config(self, tmp_path):
        """Confirms the replay above is the config's doing and not git's default.

        A guard against the test passing because the machine running it has
        rerere on globally, which would make the assertion above vacuous.
        """
        repo = _diverged(tmp_path)
        git_client.run("rebase", "main", cwd=str(repo))
        _write(repo, "f.txt", "a\nMAIN+BRANCH\nc\n")
        git_in(repo, "add", "f.txt")
        git_client.run("rebase", "--continue", cwd=str(repo),
                       config={"core.editor": "true"})

        git_in(repo, "checkout", "-q", "-b", "feat2", "main~1")
        _write(repo, "f.txt", "a\nBRANCH\nc\n")
        git_in(repo, "commit", "-q", "-am", "feat: branch edit again")
        result = git_client.run("rebase", "main", cwd=str(repo))

        assert rebase_inspect.rerere_replayed(result.combined_output) == []
        assert rebase_inspect.detect_conflicts(str(repo)) == ["f.txt"]


class TestRerereReplayedParsing:
    """Reading git's replay lines out of its output."""

    def test_reads_the_autoupdate_wording(self):
        out = "Rebasing (1/2)\nStaged 'src/a.py' using previous resolution.\n"
        assert rebase_inspect.rerere_replayed(out) == ["src/a.py"]

    def test_reads_the_wording_without_autoupdate(self):
        """A worktree whose own config enables rerere but not auto-staging."""
        out = "Resolved 'src/a.py' using previous resolution.\n"
        assert rebase_inspect.rerere_replayed(out) == ["src/a.py"]

    def test_reads_several_in_order(self):
        out = (
            "Staged 'b.py' using previous resolution.\n"
            "Staged 'a.py' using previous resolution.\n"
        )
        assert rebase_inspect.rerere_replayed(out) == ["b.py", "a.py"]

    def test_ignores_unrelated_output(self):
        out = (
            "Auto-merging f\n"
            "CONFLICT (content): Merge conflict in f\n"
            "hint: Resolve all conflicts manually\n"
        )
        assert rebase_inspect.rerere_replayed(out) == []

    def test_ignores_a_line_it_cannot_parse(self):
        """Presentation output — an unparsed line yields nothing, not a guess."""
        assert rebase_inspect.rerere_replayed("Staged '' using previous resolution.") == []
        assert rebase_inspect.rerere_replayed("Staged 'f' using some other thing.") == []


class TestTallyReplays:
    """Replayed files are counted apart from resolved ones."""

    def test_records_without_touching_resolved_files(self):
        tally = rebase_types.ResolutionTally()
        tally.record_replays(["a.py"])

        assert tally.replayed == ["a.py"]
        # `conflicts_resolved` counts `files`, and a replay is not a resolution
        # this run performed.
        assert tally.files == []

    def test_deduplicates_across_steps(self):
        """One file replayed in three commits is one file that cost nothing."""
        tally = rebase_types.ResolutionTally()
        tally.record_replays(["a.py", "b.py"])
        tally.record_replays(["a.py"])

        assert tally.replayed == ["a.py", "b.py"]

    def test_note_replays_logs_each_file_once(self):
        """The trail gets one event per newly replayed file, not per step."""
        tally = rebase_types.ResolutionTally()
        trail = mock.MagicMock()
        result = mock.MagicMock(
            combined_output="Staged 'a.py' using previous resolution.\n")

        lifecycle.note_replays(result, tally, trail=trail)
        lifecycle.note_replays(result, tally, trail=trail)

        assert tally.replayed == ["a.py"]
        assert trail.info.call_count == 1


class TestUnattendedEditor:
    """Nothing in the environment can hand an unattended rebase an editor."""

    def test_clears_every_editor_override(self):
        env = dict(
            GIT_EDITOR="vim", GIT_SEQUENCE_EDITOR="vim",
            VISUAL="vim", EDITOR="vim", PATH="/usr/bin",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            result = lifecycle.unattended_env()

        assert "GIT_EDITOR" not in result
        assert "GIT_SEQUENCE_EDITOR" not in result
        assert "VISUAL" not in result
        assert "EDITOR" not in result
        # Everything else is passed through — a git that cannot find its own
        # binaries is not an improvement on one that opens an editor.
        assert result["PATH"] == "/usr/bin"

    def test_git_resolves_to_the_configured_editor_under_the_scrubbed_env(self, tmp_path):
        """The precedence this defends against, asserted against git itself.

        `GIT_EDITOR` outranks `core.editor`, so the config alone leaves an
        operator's `export GIT_EDITOR=vim` in charge of an unattended rebase.
        """
        repo = init_repo(tmp_path / "repo")
        with mock.patch.dict(os.environ, {"GIT_EDITOR": "vim"}):
            leaked = git_client.run(
                "var", "GIT_EDITOR", cwd=str(repo),
                config=lifecycle.UNATTENDED_CONFIG,
            )
            scrubbed = git_client.run(
                "var", "GIT_EDITOR", cwd=str(repo),
                config=lifecycle.UNATTENDED_CONFIG,
                env=lifecycle.unattended_env(),
            )

        assert leaked.stdout.strip() == "vim"
        assert scrubbed.stdout.strip() == "true"

    def test_a_squash_does_not_block_when_the_operator_prefers_an_editor(self, tmp_path):
        """The regression: this hung indefinitely rather than failing.

        A `squash!` asks for a combined message mid-replay. With an inherited
        `GIT_EDITOR` naming a real editor, git opened it on a pipe with no
        terminal and blocked — and `rebase` is unbounded, so nothing ended it.

        Run as a bounded subprocess rather than in-process: the bug's signature
        is a hang, and a test that reproduces it under a regression would stall
        the suite instead of failing it. `pytest-timeout` is not installed here,
        so the bound has to be one the test enforces itself.

        The branch deliberately does *not* conflict. A conflict halts the replay
        before the squash step is ever reached, so the editor is never opened
        and the test passes whatever the environment holds — which is exactly
        how an earlier version of this test passed against the unfixed code.
        """
        repo = init_repo(tmp_path / "repo")
        _write(repo, "f.txt", "a\nb\nc\n")
        git_in(repo, "add", "f.txt")
        git_in(repo, "commit", "-q", "-m", "base")

        git_in(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "x.txt", "x\n")
        git_in(repo, "add", "x.txt")
        git_in(repo, "commit", "-q", "-m", "feat: thing")
        _write(repo, "g.txt", "extra\n")
        git_in(repo, "add", "g.txt")
        git_in(repo, "commit", "-q", "-m", "squash! feat: thing")

        git_in(repo, "checkout", "-q", "main")
        _write(repo, "y.txt", "y\n")
        git_in(repo, "add", "y.txt")
        git_in(repo, "commit", "-q", "-m", "main: unrelated")
        git_in(repo, "checkout", "-q", "feat")

        # `sleep` rather than a real editor: `proc.run` closes the child's
        # stdin, so `cat` would read EOF and return at once, and `vi` only hung
        # because it opens /dev/tty directly — which is machine-dependent. A
        # sleep blocks on nothing at all, so it reproduces the shape of the bug
        # (git waiting on an editor that never returns) the same way everywhere.
        # Longer than the bound below, so the bound is what ends it.
        driver = textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, {str(LIB_DIR)!r})
            from rebase import lifecycle
            from git import client as git_client
            git_client.run(
                "rebase", "--autosquash", "main",
                cwd={str(repo)!r}, config=lifecycle.REBASE_CONFIG,
                env=lifecycle.unattended_env(),
            )
        """)
        try:
            done = subprocess.run(
                [sys.executable, "-c", driver],
                env=os.environ | {"GIT_EDITOR": "sleep 300"},
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                "the rebase blocked on an editor — an inherited GIT_EDITOR is "
                "reaching git again"
            )

        assert done.returncode == 0, done.stderr
        assert not rebase_inspect.rebase_in_progress(str(repo))
        subjects = git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
        assert [s for s in subjects if s] == ["feat: thing"]


class TestAutosquash:
    """fixup!/squash! commits are folded rather than replayed."""

    def _with_fixup(self, tmp_path, marker: str) -> Path:
        repo = _diverged(tmp_path, subject="feat: thing")
        _write(repo, "g.txt", "extra\n")
        git_in(repo, "add", "g.txt")
        git_in(repo, "commit", "-q", "-m", f"{marker}! feat: thing")
        return repo

    def test_folds_a_fixup_commit(self, tmp_path):
        repo = self._with_fixup(tmp_path, "fixup")
        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        subjects = git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
        assert [s for s in subjects if s] == ["feat: thing"]
        assert (repo / "g.txt").exists()

    def test_folds_a_squash_commit_without_halting_on_the_editor(self, tmp_path):
        """`squash!` asks for a combined message during the initial replay.

        `fixup!` never does, so `core.editor=true` on the fresh rebase is load
        bearing only here: without it the run stops with "there was a problem
        with the editor" and leaves a rebase in progress.
        """
        repo = self._with_fixup(tmp_path, "squash")
        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        assert not rebase_inspect.rebase_in_progress(str(repo))
        subjects = git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
        assert [s for s in subjects if s] == ["feat: thing"]

    def test_leaves_an_ordinary_branch_alone(self, tmp_path):
        """No markers, no folding — every commit is still replayed."""
        repo = _diverged(tmp_path)
        _write(repo, "g.txt", "extra\n")
        git_in(repo, "add", "g.txt")
        git_in(repo, "commit", "-q", "-m", "feat: second thing")

        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        subjects = git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
        assert [s for s in subjects if s] == [
            "feat: second thing", "feat: branch edit",
        ]
