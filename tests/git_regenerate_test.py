"""Tests for the git.regenerate lockfile regeneration library."""

import os
import subprocess
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


# ── Registry invariants ─────────────────────────────────────────────────────


class TestRegistryInvariants:
    """Properties every registry entry must hold, whatever is added to it."""

    def test_find_regenerator_all_entries_have_cmd(self):
        """Every registry entry must carry a non-empty command tuple."""
        for name, entry in regen.LOCKFILE_REGENERATORS.items():
            assert isinstance(entry.cmd, tuple) and len(entry.cmd) > 0, f"{name} has invalid cmd"

    def test_find_regenerator_all_keys_are_basenames(self):
        """Lookup is by basename — a key with a path separator could never match."""
        for name in regen.LOCKFILE_REGENERATORS:
            assert os.path.basename(name) == name, f"{name} is not a bare basename"


# ── Running a regeneration ──────────────────────────────────────────────────


class TestRunRegeneration:
    """Running one rebuild: bare first, mise as the fallback, then staging."""

    def testrun_regeneration_bare_command(self, tmp_path):
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text("old content")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is True
        cmds = [c[0] for c in calls]
        assert ["pnpm", "install"] in cmds
        assert ["git", "add", "pnpm-lock.yaml"] in cmds

    def testrun_regeneration_with_mise(self, tmp_path):
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text("old content")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=True):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is True
        cmds = [c[0] for c in calls]
        assert ["mise", "exec", "--", "pnpm", "install"] in cmds

    def testrun_regeneration_bare_fails_retries_mise(self, tmp_path):
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text("old content")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("cwd")))
            if cmd == ["pnpm", "install"]:
                return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr="command not found")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False), \
             mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is True
        cmds = [c[0] for c in calls]
        assert ["mise", "exec", "--", "pnpm", "install"] in cmds

    def testrun_regeneration_missing_binary_retries_mise(self, tmp_path):
        """A binary absent from PATH raises FileNotFoundError, not exit 127."""
        (tmp_path / "pnpm-lock.yaml").write_text("old content")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("cwd")))
            if cmd == ["pnpm", "install"]:
                raise FileNotFoundError(2, "No such file or directory: 'pnpm'")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False), \
             mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is True
        cmds = [c[0] for c in calls]
        assert ["mise", "exec", "--", "pnpm", "install"] in cmds

    def testrun_regeneration_missing_binary_without_mise_returns_false(self, tmp_path):
        """Missing binary and no mise degrades to a stale file, never a crash."""
        (tmp_path / "pnpm-lock.yaml").write_text("old content")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "pnpm":
                raise FileNotFoundError(2, "No such file or directory: 'pnpm'")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False), \
             mock.patch("shutil.which", return_value=None):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is False

    def testrun_regeneration_not_executable_returns_false(self, tmp_path):
        """A present-but-unexecutable binary raises PermissionError, not 127."""
        (tmp_path / "pnpm-lock.yaml").write_text("old content")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "pnpm":
                raise PermissionError(13, "Permission denied: 'pnpm'")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False), \
             mock.patch("shutil.which", return_value=None):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is False

    def testrun_regeneration_missing_binary_under_mise_returns_false(self, tmp_path):
        """Defensive: a launch failure under mise must not propagate as a traceback.

        detect_mise gates on shutil.which, so this pairing is unreachable in
        production; the test pins run_regeneration's own error handling.
        """
        (tmp_path / "pnpm-lock.yaml").write_text("old content")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "mise":
                raise FileNotFoundError(2, "No such file or directory: 'mise'")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=True), \
             mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is False

    def testrun_regeneration_stage_dir(self, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("go", "mod", "tidy"),
                    stage_dir=True, files=["go.sum"],
                ),
                cwd=str(tmp_path),
            )

        assert result is True
        cmds = [c[0] for c in calls]
        assert ["git", "add", "-u", "."] in cmds

    def testrun_regeneration_failure_returns_false(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if cmd[0] in ("pnpm", "mise"):
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(regen, "detect_mise", return_value=False), \
             mock.patch("shutil.which", return_value=None):
            result = regen.run_regeneration(
                regen.RegenJob(
                    regen_dir=str(tmp_path), cmd=("pnpm", "install"), files=["pnpm-lock.yaml"],
                ),
                cwd=str(tmp_path),
            )

        assert result is False
