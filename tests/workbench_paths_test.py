"""Tests for the Python side of the three workbench roots.

Each rung of the chain is exercised in isolation here. That the shell, zsh, and
Python resolvers agree on the *same* rung is asserted separately, in
tests/workbench_roots.bats.
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

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
