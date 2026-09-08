"""Tests for the git.regenerate lockfile regeneration library."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import regenerate as regen


# ── Registry lookup ─────────────────────────────────────────────────────────


class TestFindRegenerator:
    """find_regenerator returns the right command for each known lockfile."""

    @pytest.mark.parametrize("basename,expected_cmd", [
        ("go.sum", ("go", "mod", "tidy")),
        ("pnpm-lock.yaml", ("pnpm", "install", "--lockfile-only")),
        ("package-lock.json", ("npm", "install", "--package-lock-only")),
        ("yarn.lock", ("yarn", "install")),
        ("bun.lock", ("bun", "install")),
        ("bun.lockb", ("bun", "install")),
        ("Cargo.lock", ("cargo", "generate-lockfile")),
        ("uv.lock", ("uv", "lock")),
        ("poetry.lock", ("poetry", "lock", "--no-update")),
        ("composer.lock", ("composer", "update", "--lock")),
        ("Gemfile.lock", ("bundle", "lock")),
    ])
    def test_known_lockfile(self, basename, expected_cmd):
        result = regen.find_regenerator(basename)
        assert result is not None
        assert result.cmd == expected_cmd

    def test_path_prefix_stripped(self):
        result = regen.find_regenerator("packages/web/pnpm-lock.yaml")
        assert result is not None
        assert result.cmd == ("pnpm", "install", "--lockfile-only")

    def test_unknown_file(self):
        assert regen.find_regenerator("main.go") is None
        assert regen.find_regenerator("README.md") is None

    def test_go_sum_stages_dir(self):
        result = regen.find_regenerator("go.sum")
        assert result is not None and result.stage_dir is True

    def test_non_go_does_not_stage_dir(self):
        for name, entry in regen.LOCKFILE_REGENERATORS.items():
            if name != "go.sum":
                assert not entry.stage_dir, f"{name} should not stage_dir"


# ── Mise detection ──────────────────────────────────────────────────────────


class TestDetectMise:
    """detect_mise finds mise config files up to repo root."""

    def test_mise_toml_in_dir(self, tmp_path):
        (tmp_path / "mise.toml").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is True

    def test_dotmise_toml_in_dir(self, tmp_path):
        (tmp_path / ".mise.toml").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is True

    def test_tool_versions_in_dir(self, tmp_path):
        (tmp_path / ".tool-versions").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is True

    def test_config_subdir(self, tmp_path):
        (tmp_path / ".config" / "mise").mkdir(parents=True)
        (tmp_path / ".config" / "mise" / "config.toml").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is True

    def test_parent_directory(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (tmp_path / "mise.toml").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(subdir), str(tmp_path)) is True

    def test_mise_not_installed(self, tmp_path):
        (tmp_path / "mise.toml").touch()
        with mock.patch("shutil.which", return_value=None):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is False

    def test_no_config_file(self, tmp_path):
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(tmp_path), str(tmp_path)) is False

    def test_stops_at_repo_root(self, tmp_path):
        repo = tmp_path / "repo"
        subdir = repo / "pkg" / "sub"
        subdir.mkdir(parents=True)
        # mise.toml is above the repo root — should not be found
        (tmp_path / "mise.toml").touch()
        with mock.patch("shutil.which", return_value="/usr/bin/mise"):
            assert regen.detect_mise(str(subdir), str(repo)) is False


# ── RegenQueue ──────────────────────────────────────────────────────────────


class TestRegenQueue:
    """RegenQueue deduplicates by (directory, command)."""

    def test_deduplication(self, tmp_path):
        queue = regen.RegenQueue()
        cmd = ("go", "mod", "tidy")
        queue.add(tmp_path, "go.sum", cmd, stage_dir=True)
        queue.add(tmp_path, "go.mod", cmd, stage_dir=True)
        jobs = list(queue)
        assert len(jobs) == 1
        assert jobs[0].files == ["go.sum", "go.mod"]

    def test_separate_dirs(self, tmp_path):
        queue = regen.RegenQueue()
        cmd = ("go", "mod", "tidy")
        queue.add(tmp_path / "a", "go.sum", cmd)
        queue.add(tmp_path / "b", "go.sum", cmd)
        assert len(list(queue)) == 2

    def test_unrebuildable(self):
        queue = regen.RegenQueue()
        queue.mark_unrebuildable("proto/gen.go")
        assert queue.unrebuildable == ["proto/gen.go"]
