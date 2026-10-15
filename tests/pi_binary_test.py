"""agent.pi_binary: the repo's mise pin is a floor, the managed install the default.

Real subprocesses against fakes on a sandboxed PATH: a managed install laid out
the way pi's installer lays it out, a `mise` that answers from a fixture, and a
`pi` that prints a version. Nothing here reaches the machine's own pi or mise.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.pi_binary
import core.trail
from agent.pi_binary import PiUnresolvable, Source

import conftest
from conftest import init_worktree

PACKAGE = "npm:@earendil-works/pi-coding-agent"

# Captured at import, before any test's autouse guard replaces them.
_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


def _allow_fakes_under(root: Path, monkeypatch) -> None:
    """Let a spawn named `pi` through only when it is one of this test's fakes.

    conftest's `_no_live_backend` refuses every `pi` by name, which the PATH
    fallback's `pi --version` is. The fakes all live under *root*, so anything
    else named `pi` is still refused.
    """
    def check(cmd):
        argv0 = Path(str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd))
        if argv0.name == "pi" and root not in argv0.parents:
            raise AssertionError(f"spawned a pi outside the sandbox: {argv0}")

    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: check(cmd) or _REAL_RUN(cmd, *a, **kw))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **kw: check(cmd) or _REAL_POPEN(cmd, *a, **kw))


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Machine:
    """A sandboxed machine: managed install, repo, mise and PATH, all under tmp_path."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.root = tmp_path / "agent" / "install"
        self.repo = init_worktree(tmp_path / "repo")
        self.mise_ls = tmp_path / "mise-ls.json"
        monkeypatch.setenv(agent.pi_binary.MANAGED_ROOT_ENV, str(self.root))
        # git for the repo lookup, sh for the fakes, and nothing that could be
        # a real pi or mise.
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin")

    def managed(self, version: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "current-version").write_text(f"{version}\n")
        _executable(self.root / "releases" / version / "node_modules" / ".bin" / "pi", "exit 0")
        return str(_executable(self.root.parent / "bin" / "pi", "exit 0"))

    def pin(self, version: str, *, installed: bool = True, source: Path | None = None) -> str:
        """Pin pi in the repo through a fake mise; returns the pinned binary's path."""
        install = self.tmp / "mise" / "installs" / "pi" / version
        binary = _executable(install / "node_modules" / ".bin" / "pi", "exit 0")
        source = source or self.repo / ".mise.toml"
        self.mise_ls.write_text(json.dumps({PACKAGE: [{
            "version": version, "install_path": str(install),
            "source": {"type": "mise.toml", "path": str(source)},
            "installed": installed, "active": True,
        }]}))
        self.mise(which=str(binary))
        return str(binary)

    def mise(self, *, which: str = "", fail: bool = False) -> None:
        if not self.mise_ls.exists():
            self.mise_ls.write_text("{}")
        ls = 'echo "mise ERROR config is broken" >&2; exit 1' if fail else f'cat "{self.mise_ls}"'
        answer = f'echo "{which}"' if which else "exit 1"
        _executable(self.bin / "mise", (
            f'case "$1" in\n  ls) {ls} ;;\n  which) {answer} ;;\n  *) exit 2 ;;\nesac'
        ))

    def path_pi(self, version_output: str) -> str:
        return str(_executable(self.bin / "pi", f'printf "%s\\n" "{version_output}"'))


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.pi_binary, "resolve", conftest._REAL_PI_RESOLVE)
    _allow_fakes_under(tmp_path, monkeypatch)
    agent.pi_binary._resolved.cache_clear()
    yield Machine(tmp_path, monkeypatch)
    agent.pi_binary._resolved.cache_clear()


def _resolve(machine: Machine) -> agent.pi_binary.PiBinary:
    return agent.pi_binary.resolve(str(machine.repo))


class TestTheChoice:
    def test_a_managed_install_newer_than_the_pin_wins(self, machine):
        launcher = machine.managed("1.2.0")
        machine.pin("0.87.1")
        chosen = _resolve(machine)
        assert (chosen.path, chosen.version, chosen.source) == (launcher, "1.2.0", Source.MANAGED)
        assert chosen.pin_version == "0.87.1"
        assert "newer than the repo pin 0.87.1" in chosen.reason

    def test_a_pin_newer_than_the_managed_install_wins(self, machine):
        machine.managed("1.2.0")
        pinned = machine.pin("1.4.0")
        chosen = _resolve(machine)
        assert (chosen.path, chosen.version, chosen.source) == (pinned, "1.4.0", Source.REPO_PIN)
        assert chosen.managed_version == "1.2.0"

    def test_with_no_pin_the_managed_install_runs(self, machine):
        launcher = machine.managed("1.2.0")
        machine.mise()
        chosen = _resolve(machine)
        assert (chosen.path, chosen.source) == (launcher, Source.MANAGED)
        assert chosen.pin_version is None
        assert "mise pins no pi here" in chosen.reason

    def test_with_no_managed_install_and_no_pin_pi_on_path_runs(self, machine):
        on_path = machine.path_pi("1.1.0")
        chosen = _resolve(machine)
        assert (chosen.path, chosen.version, chosen.source) == (on_path, "1.1.0", Source.PATH)
        assert "mise is not installed" in chosen.reason

    def test_a_tie_takes_the_managed_install(self, machine):
        launcher = machine.managed("1.2.0")
        machine.pin("1.2.0")
        assert _resolve(machine).path == launcher

    def test_a_prerelease_sorts_below_its_release(self, machine):
        machine.managed("1.3.0-beta.1")
        pinned = machine.pin("1.3.0")
        assert _resolve(machine).path == pinned

    def test_a_pin_outside_the_repo_is_not_a_floor(self, machine):
        """A global mise pin says nothing about the repo being worked on."""
        launcher = machine.managed("1.2.0")
        machine.pin("9.0.0", source=machine.tmp / "home" / ".config" / "mise" / "config.toml")
        chosen = _resolve(machine)
        assert chosen.path == launcher
        assert "outside the repo" in chosen.reason

    def test_a_losing_pin_need_not_be_installed(self, machine):
        launcher = machine.managed("1.2.0")
        machine.pin("0.87.1", installed=False)
        assert _resolve(machine).path == launcher


class TestUnreadable:
    def test_an_unreadable_managed_version_is_an_error_naming_its_file(self, machine):
        machine.managed("1.2.0")
        (machine.root / "current-version").write_text("not-a-version\n")
        machine.pin("0.87.1")
        with pytest.raises(PiUnresolvable, match="current-version"):
            _resolve(machine)

    def test_an_unreadable_pin_version_is_an_error_naming_its_file(self, machine):
        machine.managed("1.2.0")
        machine.pin("latest")
        with pytest.raises(PiUnresolvable, match=r"repo pin in .*\.mise\.toml"):
            _resolve(machine)

    def test_a_failing_mise_is_an_error_rather_than_no_pin(self, machine):
        machine.managed("1.2.0")
        machine.mise(fail=True)
        with pytest.raises(PiUnresolvable, match="config is broken"):
            _resolve(machine)

    def test_an_unreadable_path_pi_version_is_an_error(self, machine):
        machine.path_pi("pi: something went wrong")
        with pytest.raises(PiUnresolvable, match="--version"):
            _resolve(machine)

    def test_a_winning_pin_that_is_not_installed_says_how_to_fix_it(self, machine):
        machine.managed("1.2.0")
        machine.pin("1.4.0", installed=False)
        with pytest.raises(PiUnresolvable, match="mise install"):
            _resolve(machine)

    def test_nothing_installed_anywhere_is_an_error(self, machine):
        with pytest.raises(PiUnresolvable, match="no pi on PATH"):
            _resolve(machine)

    def test_a_managed_version_with_no_release_is_an_error(self, machine):
        machine.managed("1.2.0")
        (machine.root / "current-version").write_text("1.3.0\n")
        with pytest.raises(PiUnresolvable, match="not executable"):
            _resolve(machine)


class TestVersionKey:
    def test_numeric_prerelease_identifiers_compare_numerically(self):
        key = agent.pi_binary.version_key
        assert key("1.0.0-rc.9", "t") < key("1.0.0-rc.10", "t") < key("1.0.0", "t")


class TestMiseWhichFails:
    def test_an_oserror_from_mise_which_is_unresolvable(self, machine, monkeypatch):
        machine.pin("0.87.1")
        real = agent.pi_binary.core.proc.run

        def run(cmd, *a, **k):
            if cmd[:2] == ["mise", "which"]:
                raise OSError("gone")
            return real(cmd, *a, **k)

        monkeypatch.setattr(agent.pi_binary.core.proc, "run", run)
        with pytest.raises(PiUnresolvable, match="cannot run mise"):
            _resolve(machine)


class TestOncePerDirectory:
    def test_a_second_call_reuses_the_choice(self, machine):
        machine.managed("1.2.0")
        machine.pin("0.87.1")
        first = _resolve(machine)
        # A changed machine is not re-read inside one run.
        (machine.root / "current-version").write_text("garbage\n")
        assert _resolve(machine) is first


class TestRecordedOnTheTrail:
    def test_the_choice_is_recorded_under_the_running_command(self, machine, monkeypatch):
        recorded = []
        monkeypatch.setattr(core.trail, "record_event", lambda *a, **kw: recorded.append((a, kw)))
        monkeypatch.setenv(core.trail.TRAIL_ROOT_ENV, "abc123")
        machine.managed("1.2.0")
        machine.pin("0.87.1")
        _resolve(machine)
        (args, kwargs), = recorded
        assert args[1] == agent.pi_binary.TRAIL_ACTION
        assert kwargs["data"]["version"] == "1.2.0"
        assert kwargs["data"]["pin_version"] == "0.87.1"
        assert kwargs["data"]["source"] == "managed"
        assert "newer than the repo pin" in kwargs["detail"]

    def test_outside_a_trailed_run_nothing_is_recorded(self, machine, monkeypatch):
        recorded = []
        monkeypatch.setattr(core.trail, "record_event", lambda *a, **kw: recorded.append(a))
        monkeypatch.delenv(core.trail.TRAIL_ROOT_ENV, raising=False)
        machine.managed("1.2.0")
        _resolve(machine)
        assert recorded == []


class TestAnyInstalled:
    def test_a_managed_install_off_path_counts(self, machine):
        machine.managed("1.2.0")
        assert agent.pi_binary.any_installed()

    def test_nothing_installed_is_false(self, machine):
        assert not agent.pi_binary.any_installed()
