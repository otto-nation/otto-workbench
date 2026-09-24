"""Holding rerere off an AI-resolved rebase, and folding fixup! commits.

Driven against real repositories rather than stubs. Both behaviours are git's
rather than ours — what is under test is that the flags reach git, that git does
what the rebase driver assumes, and that the driver reads the result correctly.
A stub asserting on argv would pass whatever git actually did with it, which is
the half that has broken here before, and the rerere behaviour below is one no
argv assertion could have caught: the defect was in what git does on its own
when the config says nothing.
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


def _rr_cache(repo: Path) -> Path:
    """The repo's rerere cache, wherever git puts it.

    Resolved through git rather than assumed to be `.git/rr-cache`: it lives in
    the *common* directory, so in a worktree it is not under the worktree's own
    git dir at all — which is the property that let one AI resolution reach
    every other worktree of the repo.

    `git_out` hands back raw stdout, so the newline has to come off here. Left
    on, every path built from it is one that cannot exist, and an existence
    check against it answers "no cache" for a repo that has one — which is a
    test that passes whatever the code does.
    """
    raw = git_out(repo, "rev-parse", "--git-path", "rr-cache").strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


def _cached_resolutions(repo: Path) -> list[Path]:
    """Recorded resolutions in the cache — the entries a later rebase replays.

    A resolution is a directory holding a `preimage`; the `postimage` that makes
    it replayable is only written once the conflicted commit lands. Both are
    counted, because a preimage recorded now becomes a replayable resolution at
    the next `--continue` without anything else being decided.
    """
    cache = _rr_cache(repo)
    if not cache.exists():
        return []
    return sorted(p for p in cache.iterdir() if p.is_dir())


class TestRerereIsHeldOff:
    """An AI-resolved rebase records nothing a later rebase could replay.

    The resolution the driver commits has been read by nobody. Recording it as
    the answer for that hunk hands it to every future rebase in the repo —
    including a plain `git rebase` run by hand, which replays from the cache
    with no AI in the loop and no prompt. That is not a hypothetical: a merge
    that duplicated a shell function came back this way after being fixed.
    """

    def test_the_resolver_s_own_output_is_not_recorded(self, tmp_path):
        repo = _diverged(tmp_path)
        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        assert rebase_inspect.detect_conflicts(str(repo)) == ["f.txt"]
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        assert _cached_resolutions(repo) == []

    def test_an_existing_cache_does_not_re_enable_it(self, tmp_path):
        """The reason the config says `false` rather than saying nothing.

        git turns rerere on by itself whenever `rr-cache` exists — documented,
        and the state every worktree on a machine that has ever run this is
        already in. Omitting the key inherits that; only an explicit `false`
        overrides it. This test fails against a config that drops the key.
        """
        repo = _diverged(tmp_path)
        cache = _rr_cache(repo)
        cache = cache if cache.is_absolute() else repo / cache
        cache.mkdir(parents=True, exist_ok=True)

        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        _resolve_and_continue(repo, "a\nMAIN+BRANCH\nc\n")

        assert _cached_resolutions(repo) == []

    def test_a_later_plain_rebase_meets_the_conflict_itself(self, tmp_path):
        """The consequence, end to end: nothing is replayed into a hand rebase.

        The second branch is an identical conflict, so a recorded resolution
        would be applied here with no AI and no prompt. Asserting on the
        conflict rather than on the cache is what makes this about the operator
        rather than about a directory.
        """
        repo = _diverged(tmp_path)
        git_client.run("rebase", "--autosquash", "main", cwd=str(repo),
                       config=lifecycle.REBASE_CONFIG)
        _resolve_and_continue(repo, "a\nAI-WROTE-THIS\nc\n")

        git_in(repo, "checkout", "-q", "-b", "feat2", "main~1")
        _write(repo, "f.txt", "a\nBRANCH\nc\n")
        git_in(repo, "commit", "-q", "-am", "feat: branch edit again")
        git_client.run("rebase", "main", cwd=str(repo))

        assert rebase_inspect.detect_conflicts(str(repo)) == ["f.txt"]
        assert "AI-WROTE-THIS" not in (repo / "f.txt").read_text()


class TestUnattendedEditor:
    """Nothing in the environment can hand an unattended rebase an editor.

    What `unattended_env` does to the variables is `git_client`'s to assert — it
    owns them now, because an AI agent's own git calls are owed the same
    treatment and two copies of the list would drift. What is asserted here is
    that the rebase driver still reaches for it, and that a real `--autosquash`
    replay does not block.
    """

    def test_the_driver_uses_the_client_s_pinned_env(self):
        """Re-exported, not re-implemented — a local copy is a place to drift."""
        assert lifecycle.unattended_env is git_client.unattended_env
        assert lifecycle.UNATTENDED_CONFIG == {"core.editor": git_client.NO_EDITOR}

    def test_git_resolves_to_the_configured_editor_under_the_pinned_env(self, tmp_path):
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
            pinned = git_client.run(
                "var", "GIT_EDITOR", cwd=str(repo),
                config=lifecycle.UNATTENDED_CONFIG,
                env=lifecycle.unattended_env(),
            )

        assert leaked.stdout.strip() == "vim"
        assert pinned.stdout.strip() == git_client.NO_EDITOR

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
