"""agent.pi_binary spawn: pi starts under a real node, never through a mise shim.

pi is a ``#!/usr/bin/env node`` script. Started by its launcher, ``env node``
finds the first ``node`` on PATH, and where that is a mise shim the shim applies
the trusted mise ``[env]`` of the repo the agent runs in — overriding the
environment the workbench chose for pi. These tests lay out fake nodes, a fake
mise shim and a fake managed install under tmp_path; nothing here runs a real
node, pi or mise.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
import agent.machine_env
import agent.pi_binary
from agent.pi_binary import MANAGED_ROOT_ENV, PiBinary, Source
from ai_backend_test import _recording_popen

ENV_NODE = "#!/usr/bin/env node\n"


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Layout:
    """A home with mise's data dir, a fake mise binary, and directories for PATH."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.data = tmp / "data"
        self.mise = _executable(tmp / "mise-bin" / "mise")
        self.shims = self.data / "mise" / "shims"

    def shim(self, name: str = "node") -> Path:
        """A shim as mise makes one: a symlink in its shims dir to the mise binary."""
        self.shims.mkdir(parents=True, exist_ok=True)
        link = self.shims / name
        link.symlink_to(self.mise)
        return link

    def real(self, directory: str) -> Path:
        return _executable(self.tmp / directory / "node")

    def pi_node(self) -> Path:
        return _executable(self.data / "pi-node" / "current" / "bin" / "node")

    def env(self, *path_dirs, **extra) -> dict[str, str]:
        env = {
            "HOME": str(self.tmp / "home"), "XDG_DATA_HOME": str(self.data),
            "PATH": os.pathsep.join(str(d) for d in path_dirs),
        }
        env.update(extra)
        return env

    def managed(self, version: str = "1.3.0") -> PiBinary:
        root = self.tmp / "agent" / "install"
        release = _executable(root / "releases" / version / "node_modules" / ".bin" / "pi", ENV_NODE)
        launcher = _executable(root.parent / "bin" / "pi")
        return PiBinary(
            str(launcher), version, Source.MANAGED, "test", managed_version=version,
            release=str(release), install_root=str(root),
        )


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path)


def _is_shim(path: str, layout: Layout) -> bool:
    return (
        Path(os.path.realpath(path)).name == "mise"
        or Path(os.path.realpath(path)).parent == layout.shims.resolve()
    )


class TestRealNode:
    def test_pi_s_bundled_node_wins_when_executable(self, layout):
        bundled = layout.pi_node()
        real = layout.real("usr-bin")
        assert agent.pi_binary.real_node(layout.env(real.parent)) == str(bundled)

    def test_a_bundled_node_that_is_not_executable_is_passed_over(self, layout):
        bundled = layout.pi_node()
        bundled.chmod(0o644)
        real = layout.real("usr-bin")
        assert agent.pi_binary.real_node(layout.env(real.parent)) == str(real)

    def test_a_real_node_later_on_path_beats_a_shim_earlier(self, layout):
        shim = layout.shim()
        real = layout.real("homebrew-bin")
        assert agent.pi_binary.real_node(layout.env(shim.parent, real.parent)) == str(real)

    def test_a_link_to_mise_outside_the_shims_dir_is_a_shim(self, layout):
        """Resolved by target: a shim dir mise was told about some other way still counts."""
        elsewhere = layout.tmp / "other-shims"
        elsewhere.mkdir()
        (elsewhere / "node").symlink_to(layout.mise)
        real = layout.real("homebrew-bin")
        assert agent.pi_binary.real_node(layout.env(elsewhere, real.parent)) == str(real)

    def test_a_file_in_mise_data_dir_shims_is_a_shim(self, layout):
        """MISE_DATA_DIR moves the shims dir; a plain file there is still mise's."""
        data = layout.tmp / "custom-mise"
        shim = _executable(data / "shims" / "node")
        real = layout.real("homebrew-bin")
        env = layout.env(shim.parent, real.parent, MISE_DATA_DIR=str(data))
        assert agent.pi_binary.real_node(env) == str(real)

    def test_a_sibling_of_the_shims_dir_is_not_a_shim(self, layout):
        """The directory is compared whole: `shims-extra` is not `shims`."""
        sibling = _executable(layout.data / "mise" / "shims-extra" / "node")
        assert agent.pi_binary.real_node(layout.env(sibling.parent)) == str(sibling)

    def test_only_shims_answers_none(self, layout):
        shim = layout.shim()
        assert agent.pi_binary.real_node(layout.env(shim.parent)) is None

    def test_an_empty_xdg_data_home_means_the_default(self, layout):
        """The launcher's `${XDG_DATA_HOME:-…}` treats empty as unset."""
        home = layout.tmp / "home"
        bundled = _executable(home / ".local" / "share" / "pi-node" / "current" / "bin" / "node")
        env = layout.env(layout.real("usr-bin").parent, XDG_DATA_HOME="")
        assert agent.pi_binary.real_node(env) == str(bundled)


class TestSpawn:
    def test_a_managed_release_runs_under_the_real_node(self, layout):
        binary = layout.managed()
        shim, real = layout.shim(), layout.real("homebrew-bin")
        env = layout.env(shim.parent, real.parent)
        spawn = binary.spawn(env)
        assert spawn.argv == (str(real), binary.release)
        # PATH is untouched, shims included: the commands pi runs keep the
        # repo's mise toolchain. Only pi's own process skips the shim.
        assert spawn.env["PATH"] == env["PATH"]
        assert spawn.env[MANAGED_ROOT_ENV] == binary.install_root
        assert MANAGED_ROOT_ENV not in env

    def test_pi_s_bundled_node_goes_on_path_as_the_launcher_puts_it(self, layout):
        binary = layout.managed()
        bundled = layout.pi_node()
        env = layout.env(layout.shim().parent)
        spawn = binary.spawn(env)
        assert spawn.argv == (str(bundled), binary.release)
        assert spawn.env["PATH"] == os.pathsep.join([str(bundled.parent), env["PATH"]])

    def test_with_no_real_node_the_launcher_runs_as_before(self, layout):
        binary = layout.managed()
        env = layout.env(layout.shim().parent)
        spawn = binary.spawn(env)
        assert spawn.argv == (binary.path,)
        assert spawn.env == env

    def test_a_non_managed_env_node_script_runs_under_the_real_node(self, layout):
        pinned = _executable(layout.tmp / "mise" / "installs" / "pi" / "bin" / "pi", ENV_NODE)
        binary = PiBinary(str(pinned), "1.4.0", Source.REPO_PIN, "test")
        real = layout.real("homebrew-bin")
        spawn = binary.spawn(layout.env(layout.shim().parent, real.parent))
        assert spawn.argv == (str(real), str(pinned))
        assert MANAGED_ROOT_ENV not in spawn.env

    @pytest.mark.parametrize("shebang", [
        "#!/bin/sh\n", "#!/usr/bin/env -S node --no-warnings\n", "#!/usr/local/bin/node\n",
    ])
    def test_anything_but_an_env_node_script_runs_as_it_is(self, layout, shebang):
        script = _executable(layout.tmp / "path-bin" / "pi", shebang)
        binary = PiBinary(str(script), "1.1.0", Source.PATH, "test")
        spawn = binary.spawn(layout.env(layout.real("homebrew-bin").parent))
        assert spawn.argv == (str(script),)


class TestATrustedRepoCannotReachPi:
    """Regression: a trusted repo's mise `[env]` rewrote pi's environment.

    The launcher's `env node` resolved to a mise shim, and the shim, started in
    the repo, applied the repo's `GOOGLE_CLOUD_PROJECT` over the one the
    workbench passed: every model call then went to a project the identity
    cannot use. A shim can only do that if it is the process started, so the
    spawned argv[0] must be a real node and the environment the workbench
    built must be the one handed over.
    """

    @pytest.fixture(autouse=True)
    def _machine(self, layout, monkeypatch, tmp_path):
        self.binary = layout.managed()
        self.real = layout.real("homebrew-bin")
        self.env = layout.env(
            layout.shim().parent, self.real.parent, GOOGLE_CLOUD_PROJECT="workbench-project",
        )
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        (self.repo / ".mise.toml").write_text('[env]\nGOOGLE_CLOUD_PROJECT = "repo-project"\n')
        monkeypatch.setattr(agent.pi_binary, "resolve", lambda _cwd: self.binary)
        monkeypatch.setattr(agent.machine_env, "base_env", lambda _cwd: dict(self.env))
        monkeypatch.setattr(agent.backend_pi, "REVIEW_EXTENSION", tmp_path / "no-guard.ts")

    def _assert_no_shim(self, argv, env, layout):
        assert not _is_shim(argv[0], layout)
        assert list(argv[:2]) == [str(self.real), self.binary.release]
        assert env["GOOGLE_CLOUD_PROJECT"] == "workbench-project"
        assert env["PATH"] == self.env["PATH"]
        assert env[MANAGED_ROOT_ENV] == self.binary.install_root

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_agent_runs(self, monkeypatch, layout, entry_point):
        seen = {}
        argvs = []
        record = _recording_popen(seen)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: argvs.append(cmd) or record(cmd, **kw))
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(self.repo), session_log=str(self.repo / "s.jsonl"),
            env=dict(self.env),
        ))
        self._assert_no_shim(argvs[0], seen["env"], layout)

    def test_a_prompt(self, monkeypatch, layout):
        seen = []

        def fake_run(cmd, **kw):
            seen.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_pi.prompt("hi", cwd=str(self.repo))
        cmd, kw = seen[0]
        self._assert_no_shim(cmd, kw["env"], layout)
