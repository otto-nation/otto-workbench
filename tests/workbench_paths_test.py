"""Tests for the Python side of the workbench roots.

Each rung of the chain is exercised in isolation here. That the shell, zsh, and
Python resolvers agree on the *same* rung is asserted separately, in
tests/workbench_roots.bats.
"""

import sys
from pathlib import Path

import pytest

from conftest import exec_fresh

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
MCP_SERVER = REPO_ROOT / "ai" / "claude" / "mcps" / "server.py"
sys.path.insert(0, str(LIB_DIR))

from core import workbench_paths  # noqa: E402


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

    def test_state_falls_back_to_dot_local_state(self, clean_env):
        assert workbench_paths.state_dir() == clean_env / ".local/state/workbench"

    def test_state_no_longer_shares_the_config_default(self, clean_env):
        assert workbench_paths.state_dir() != workbench_paths.config_dir()

    def test_cache_falls_back_to_dot_cache(self, clean_env):
        assert workbench_paths.cache_dir() == clean_env / ".cache/workbench"


class TestXdgRung:
    def test_xdg_config_home_moves_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        assert workbench_paths.config_dir() == tmp_path / "xdg-config/workbench"

    def test_xdg_cache_home_moves_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
        assert workbench_paths.cache_dir() == tmp_path / "xdg-cache/workbench"

    def test_xdg_state_home_moves_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        assert workbench_paths.state_dir() == tmp_path / "xdg-state/workbench"

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
        ("WORKBENCH_STATE_DIR", "state_dir", ".local/state/workbench"),
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


class TestTrailDir:
    def test_defaults_under_the_state_root(self, clean_env):
        assert workbench_paths.trail_dir() == clean_env / ".local/state/workbench/trail"

    def test_follows_the_xdg_state_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert workbench_paths.trail_dir() == tmp_path / "workbench/trail"

    def test_the_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        assert workbench_paths.trail_dir() == tmp_path / "state/trail"

    def test_resolves_per_call(self, tmp_path, monkeypatch):
        """Frozen at import, a monkeypatched state root would never take effect."""
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "one"))
        first = workbench_paths.trail_dir()
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "two"))
        assert workbench_paths.trail_dir() != first


class TestReviewsDir:
    def test_bare_reviews_dir_is_the_parent_every_review_sits_in(self, clean_env):
        assert workbench_paths.reviews_dir() == clean_env / ".local/state/workbench/reviews"

    def test_reviews_follow_the_state_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        assert workbench_paths.reviews_dir() == tmp_path / "state/reviews"


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
        # _subdir's guard: a cache subtree outside the cache root would
        # survive a wipe of the root, which is the one thing a cache must not
        # do.
        with pytest.raises(ValueError):
            workbench_paths.cache_dir(consumer)


class TestConsumers:
    def test_review_paths_follow_a_root_set_after_import(self, monkeypatch, tmp_path):
        """Resolved twice across a moved root, on the module the suite imported.

        A consumer that reads the root at import time answers a fresh-load
        assertion correctly and still ignores the environment for the rest of
        the process, which is exactly what this module used to do — so the
        assertion has to be a second resolve, not a first one.
        """
        from review import paths as review_paths

        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "old"))
        before = review_paths.review_file_path("owner/repo", "42")
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "new"))
        after = review_paths.review_file_path("owner/repo", "42")

        assert before == tmp_path / "old/reviews/repo-42/review.md"
        assert after == tmp_path / "new/reviews/repo-42/review.md"

    def test_the_mcp_server_reads_no_root(self, monkeypatch, tmp_path):
        """MCP discovery is derived from the component layout, not configured.

        It used to read ``mcp-tools.json`` from the config root for extra
        directories to scan. No install ever wrote that file, the workbench's
        own tools come from the layout, and the keys were deleted rather than
        typed into ``config.yml`` — so the server is now the one ai/ module
        that resolves no root at all. Loaded fresh with the roots pointed
        somewhere real, because a reintroduced path would be resolved at import.
        """
        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        server = exec_fresh("mcp_server", MCP_SERVER)

        roots = [tmp_path / "config", tmp_path / "state"]
        paths = [v for v in vars(server).values() if isinstance(v, Path)]
        assert not [p for p in paths if any(p.is_relative_to(root) for root in roots)]


