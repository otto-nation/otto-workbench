"""Tests for the Python side of the workbench roots.

Each rung of the chain is exercised in isolation here. That the shell, zsh, and
Python resolvers agree on the *same* rung is asserted separately, in
tests/workbench_roots.bats. The fourth root — per-worktree state, which has no
shell twin — is covered at the bottom of this file.
"""

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
MCP_SERVER = REPO_ROOT / "ai" / "claude" / "mcps" / "server.py"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths  # noqa: E402


def _load_module(name: str, path: Path):
    """Execute *path* as a fresh module, so import-time state reads today's env.

    Registered in ``sys.modules`` for the duration of the exec — dataclass field
    resolution looks its own module up by name — then removed, so the copy the
    rest of the suite imports stays the canonical one.
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    return module


ROOT_VARS = (
    "WORKBENCH_CONFIG_DIR",
    "WORKBENCH_STATE_DIR",
    "WORKBENCH_CACHE_DIR",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Resolve against an empty environment and a throwaway HOME."""
    for var in ROOT_VARS:
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


class TestDefaults:
    def test_config_falls_back_to_dot_config(self, clean_env):
        assert workbench_paths.config_dir() == clean_env / ".config/workbench"

    def test_state_falls_back_to_dot_config(self, clean_env):
        assert workbench_paths.state_dir() == clean_env / ".config/workbench"

    def test_cache_falls_back_to_dot_cache(self, clean_env):
        assert workbench_paths.cache_dir() == clean_env / ".cache/workbench"


class TestXdgRung:
    def test_xdg_config_home_moves_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        assert workbench_paths.config_dir() == tmp_path / "xdg-config/workbench"

    def test_xdg_cache_home_moves_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
        assert workbench_paths.cache_dir() == tmp_path / "xdg-cache/workbench"

    def test_xdg_state_home_does_not_move_state(self, monkeypatch, tmp_path, clean_env):
        # The state root has no XDG rung until #624 phase 4 ships the migration
        # that carries the existing reviews and logs. Honouring the variable now
        # would strand that data behind a path nothing else reads.
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        assert workbench_paths.state_dir() == clean_env / ".config/workbench"

    def test_empty_xdg_var_falls_through_to_the_default(self, monkeypatch, clean_env):
        # An exported-but-empty XDG variable is the same as unset, per the spec.
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert workbench_paths.config_dir() == clean_env / ".config/workbench"


class TestOverrideRung:
    @pytest.mark.parametrize("var,func", [
        ("WORKBENCH_CONFIG_DIR", "config_dir"),
        ("WORKBENCH_STATE_DIR", "state_dir"),
        ("WORKBENCH_CACHE_DIR", "cache_dir"),
    ])
    def test_override_wins_over_the_default(self, monkeypatch, tmp_path, var, func):
        monkeypatch.setenv(var, str(tmp_path / "explicit"))
        assert getattr(workbench_paths, func)() == tmp_path / "explicit"

    def test_override_wins_over_the_xdg_rung(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "explicit"))
        assert workbench_paths.config_dir() == tmp_path / "explicit"

    @pytest.mark.parametrize("var,func,default", [
        ("WORKBENCH_CONFIG_DIR", "config_dir", ".config/workbench"),
        ("WORKBENCH_STATE_DIR", "state_dir", ".config/workbench"),
        ("WORKBENCH_CACHE_DIR", "cache_dir", ".cache/workbench"),
    ])
    def test_an_empty_override_falls_through(self, monkeypatch, clean_env, var, func, default):
        # `export WORKBENCH_STATE_DIR=` in a shell profile leaves the variable
        # present but empty. Reading that as a real override would resolve the
        # root to `/` and write the workbench's data to the filesystem root.
        monkeypatch.setenv(var, "")
        assert getattr(workbench_paths, func)() == clean_env / default


class TestResolvedPerCall:
    def test_a_root_set_after_import_is_still_honoured(self, monkeypatch, tmp_path):
        """The reason the roots are functions rather than module constants."""
        before = workbench_paths.state_dir()
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "moved"))
        assert workbench_paths.state_dir() == tmp_path / "moved" != before


class TestLogsDir:
    def test_bare_logs_dir_is_the_parent_otto_log_globs(self, clean_env):
        assert workbench_paths.logs_dir() == clean_env / ".config/workbench/logs"

    def test_named_logs_dir_nests_under_it(self, clean_env):
        assert workbench_paths.logs_dir("ci-check") == clean_env / ".config/workbench/logs/ci-check"

    def test_logs_follow_the_state_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        assert workbench_paths.logs_dir("retro-scan") == tmp_path / "state/logs/retro-scan"

    @pytest.mark.parametrize("tool", ["/etc", "../escaped", "nested/tool", ".."])
    def test_a_path_like_tool_name_is_rejected(self, tool):
        # Anything but a bare name lands outside the tree otto-log globs over,
        # so the run's trail would simply never be found again.
        with pytest.raises(ValueError):
            workbench_paths.logs_dir(tool)


class TestCacheConsumer:
    def test_bare_cache_dir_is_the_root_itself(self, clean_env):
        assert workbench_paths.cache_dir() == clean_env / ".cache/workbench"

    def test_a_named_consumer_nests_under_it(self, clean_env):
        assert (workbench_paths.cache_dir("vertex-quota")
                == clean_env / ".cache/workbench/vertex-quota")

    def test_a_consumer_subtree_follows_the_cache_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path / "cache"))
        assert workbench_paths.cache_dir("vertex-quota") == tmp_path / "cache/vertex-quota"

    @pytest.mark.parametrize("consumer", ["/etc", "../escaped", "nested/name", ".."])
    def test_a_path_like_consumer_name_is_rejected(self, consumer):
        # The same guard logs_dir enforces: a cache subtree outside the cache
        # root would survive a wipe of the root, which is the one thing a cache
        # must not do.
        with pytest.raises(ValueError):
            workbench_paths.cache_dir(consumer)


class TestConsumers:
    def test_reviews_dir_sits_under_the_state_root(self, monkeypatch, tmp_path):
        """Loaded fresh: REVIEWS_DIR is resolved at import, before this test ran."""
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        review_common = _load_module("review_common_fresh", LIB_DIR / "review_common.py")
        assert review_common.REVIEWS_DIR == tmp_path / "state/reviews"

    def test_workbench_dir_is_an_alias_for_the_state_root(self, monkeypatch, tmp_path):
        import review_common

        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        assert review_common.workbench_dir() == workbench_paths.state_dir()

    def test_mcp_config_sits_under_the_config_root(self, monkeypatch, tmp_path):
        """mcp-tools.json is hand-authored, so it belongs to config, not state.

        Loaded with the two roots pointed at different directories, because the
        defaults make them the same path — under those, a regression back to the
        state root would still pass.
        """
        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        server = _load_module("mcp_server", MCP_SERVER)
        assert server.CONFIG_PATH == tmp_path / "config/mcp-tools.json"


def _add_linked_worktree(main: Path, path: Path, branch: str) -> Path:
    """Attach *path* to *main* as a linked worktree, and return it.

    ``git worktree add`` needs a commit to branch from, and the identity for it
    is passed per-invocation: HOME is a throwaway here, so there is no global
    config to read one from.
    """
    subprocess.run(
        ["git", "-C", str(main),
         "-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "--allow-empty", "-q", "-m", "init", "--no-verify"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-q", "-b", branch, str(path)],
        check=True, capture_output=True,
    )
    return path


class TestWorktreeStateDir:
    def test_it_sits_in_the_worktrees_own_git_dir(self, worktree):
        assert workbench_paths.worktree_state_dir(worktree) == worktree / ".git/workbench"

    def test_a_linked_worktree_gets_its_own(self, worktree, tmp_path):
        """The point of the git dir over the common dir: state per worktree."""
        linked = _add_linked_worktree(worktree, tmp_path / "linked", "feat")
        state = workbench_paths.worktree_state_dir(linked)
        assert state == worktree / ".git/worktrees/linked/workbench"
        assert state != workbench_paths.worktree_state_dir(worktree)

    def test_a_subdirectory_resolves_to_the_same_state(self, worktree):
        """One scoreboard per worktree, wherever inside it the command ran."""
        nested = worktree / "a" / "b"
        nested.mkdir(parents=True)
        assert workbench_paths.worktree_state_dir(nested) == \
            workbench_paths.worktree_state_dir(worktree)

    def test_it_accepts_a_string_root(self, worktree):
        assert workbench_paths.worktree_state_dir(str(worktree)) == worktree / ".git/workbench"

    def test_resolving_does_not_create_it(self, worktree):
        """Callers that only read must not leave a directory behind."""
        workbench_paths.worktree_state_dir(worktree)
        assert not (worktree / ".git/workbench").exists()

    def test_the_git_dir_is_resolved_once_per_worktree(self, worktree, monkeypatch):
        """One `pr` run resolves this for the lock, the trail, and every state
        read and write — but the worktree does not move under it."""
        real = workbench_paths.subprocess.run
        calls = []

        def _counted(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(workbench_paths.subprocess, "run", _counted)
        workbench_paths.worktree_state_dir(worktree)
        workbench_paths.worktree_state_dir(worktree)
        assert len(calls) == 1

    def test_a_directory_outside_a_worktree_raises(self, tmp_path):
        outside = tmp_path / "plain"
        outside.mkdir()
        with pytest.raises(workbench_paths.NotAWorktree):
            workbench_paths.worktree_state_dir(outside)

    def test_a_missing_directory_raises(self, tmp_path):
        with pytest.raises(workbench_paths.NotAWorktree):
            workbench_paths.worktree_state_dir(tmp_path / "gone")

    def test_a_relative_git_dir_raises(self, worktree, monkeypatch):
        """git was asked for an absolute path, so a relative answer is not git.

        Trusting it would hang the worktree's state off whatever directory the
        process happened to be sitting in.
        """
        monkeypatch.setattr(
            workbench_paths.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout=".git\n"),
        )
        with pytest.raises(workbench_paths.NotAWorktree):
            workbench_paths.worktree_state_dir(worktree)

    def test_no_git_on_the_path_raises(self, worktree, monkeypatch):
        def _no_git(*_args, **_kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(workbench_paths.subprocess, "run", _no_git)
        with pytest.raises(workbench_paths.NotAWorktree):
            workbench_paths.worktree_state_dir(worktree)


def _refuse_to_move(*_args, **_kwargs):
    """A shutil.move that must never be reached."""
    raise AssertionError("the copy fallback ran after the target already existed")


class TestLegacyAdoption:
    """A pre-#624 `.workbench/` in the working tree, carried into the git dir."""

    def _legacy(self, worktree: Path) -> Path:
        legacy = worktree / workbench_paths.LEGACY_WORKTREE_STATE_DIRNAME
        legacy.mkdir()
        (legacy / "state.json").write_text('{"version": 1}')
        # The trail and the lock live in there too, so the move has to be whole:
        # anything left behind in the working tree is read by nothing afterwards.
        (legacy / "trail.jsonl").write_text('{"step": "fetch"}\n')
        (legacy / "run.lock").write_text("{}")
        return legacy

    def test_it_moves_the_whole_directory(self, worktree):
        legacy = self._legacy(worktree)
        state = workbench_paths.worktree_state_dir(worktree)
        assert (state / "state.json").read_text() == '{"version": 1}'
        assert (state / "trail.jsonl").read_text() == '{"step": "fetch"}\n'
        assert (state / "run.lock").exists()
        assert not legacy.exists()

    def test_it_happens_once(self, worktree):
        self._legacy(worktree)
        state = workbench_paths.worktree_state_dir(worktree)
        (state / "state.json").write_text('{"version": 2}')
        self._legacy(worktree)
        # The adopted state is the live one now; a `.workbench/` that reappears
        # is a stale leftover and must not overwrite it.
        assert workbench_paths.worktree_state_dir(worktree) == state
        assert (state / "state.json").read_text() == '{"version": 2}'

    def test_a_legacy_file_is_not_adopted(self, worktree):
        """Only a directory is the old layout; a stray file is somebody else's."""
        stray = worktree / workbench_paths.LEGACY_WORKTREE_STATE_DIRNAME
        stray.write_text("not a state dir")
        assert not workbench_paths.worktree_state_dir(worktree).exists()
        assert stray.read_text() == "not a state dir"

    def test_it_falls_back_to_a_copy_across_filesystems(self, worktree, monkeypatch):
        """os.rename refuses a cross-device move; the git dir of a linked
        worktree can well be on another filesystem."""
        def _cross_device(*_args, **_kwargs):
            raise OSError(18, "Invalid cross-device link")

        monkeypatch.setattr(workbench_paths.os, "rename", _cross_device)
        self._legacy(worktree)
        state = workbench_paths.worktree_state_dir(worktree)
        assert (state / "state.json").read_text() == '{"version": 1}'

    def test_a_failed_move_warns_and_starts_fresh(self, worktree, monkeypatch, capsys):
        """The scoreboard is rebuilt by whatever writes it next, so a run must
        not die over one."""
        def _refuse(*_args, **_kwargs):
            raise OSError("nope")

        monkeypatch.setattr(workbench_paths.os, "rename", _refuse)
        monkeypatch.setattr(workbench_paths.shutil, "move", _refuse)
        self._legacy(worktree)
        state = workbench_paths.worktree_state_dir(worktree)
        assert not state.exists()
        assert "starting fresh" in capsys.readouterr().err

    def test_a_race_that_lost_is_not_a_failure(self, worktree, monkeypatch):
        """Two runs can resolve at once. The loser finds the data already there,
        which is the outcome it wanted."""
        target = worktree / ".git" / workbench_paths.WORKTREE_STATE_DIRNAME

        def _rename_then_lose(*_args, **_kwargs):
            target.mkdir(parents=True, exist_ok=True)
            (target / "state.json").write_text('{"version": 9}')
            raise OSError("lost the race")

        monkeypatch.setattr(workbench_paths.os, "rename", _rename_then_lose)
        monkeypatch.setattr(workbench_paths.shutil, "move", _refuse_to_move)
        self._legacy(worktree)
        state = workbench_paths.worktree_state_dir(worktree)
        assert (state / "state.json").read_text() == '{"version": 9}'


class TestTrailDir:
    def test_a_trail_sits_beside_the_state_it_describes(self, worktree):
        assert workbench_paths.trail_dir(worktree, "pr") == \
            workbench_paths.worktree_state_dir(worktree)

    def test_outside_a_worktree_it_falls_back_to_the_tools_logs(self, tmp_path, clean_env):
        """`pr status` from a bare repo still has a trail to write, and it goes
        to the other place otto-log looks."""
        outside = tmp_path / "plain"
        outside.mkdir()
        assert workbench_paths.trail_dir(outside, "pr") == \
            clean_env / ".config/workbench/logs/pr"
