"""agent.machine_env: agents inherit the machine's environment, not the repo's mise config.

Real bash against a fake `mise` on PATH that answers `hook-env` with a fixture
script and `trust --show` with a fixture listing, so the machine's own mise
state answers nothing here.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.machine_env
from agent.machine_env import MISE_DIFF_ENV, MISE_IGNORED_ENV, MachineEnvUnavailable

import conftest

# What a repo's `[env]` swapped in, and the shell mise prints to swap it back.
# The echo and the function are the kind of output a real hook-env carries
# (completion stubs); neither may reach the environment listings.
_RESTORE_HOOK = """\
export GOOGLE_CLOUD_PROJECT=machine-proj
unset REPO_ONLY
echo "NOISE=on-stdout"
__completion_stub() { :; }
"""


class FakeMise:
    def __init__(self, root: Path, monkeypatch):
        self.bin = root / "bin"
        self.bin.mkdir()
        self.hook = root / "hook.sh"
        self.trust = root / "trust.txt"
        self.called_from = root / "hook-cwd"
        self.hook.write_text(_RESTORE_HOOK)
        self.trust.write_text("")
        self.script = self.bin / "mise"
        self.answer()
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}{os.environ['PATH']}")

    def answer(self, *, hook_rc: int = 0, trust_rc: int = 0) -> None:
        self.script.write_text(
            "#!/usr/bin/env bash\n"
            'case "$1" in\n'
            f'  hook-env) pwd > "{self.called_from}"; cat "{self.hook}"; exit {hook_rc} ;;\n'
            f'  trust) cat "{self.trust}"; exit {trust_rc} ;;\n'
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def mise(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.machine_env, "base_env", conftest._REAL_BASE_ENV)
    agent.machine_env._restore_delta.cache_clear()
    agent.machine_env.untrusted_dirs.cache_clear()
    yield FakeMise(tmp_path, monkeypatch)
    agent.machine_env._restore_delta.cache_clear()
    agent.machine_env.untrusted_dirs.cache_clear()


def _repo_applied(**extra) -> dict[str, str]:
    """os.environ as a shim leaves it after applying a repo's mise config."""
    return {
        **os.environ, MISE_DIFF_ENV: "record", "GOOGLE_CLOUD_PROJECT": "repo-proj",
        "REPO_ONLY": "x", **extra,
    }


def test_without_a_mise_record_the_environment_is_returned_unchanged(mise):
    mise.answer(hook_rc=1)
    base = {k: v for k, v in _repo_applied().items() if k != MISE_DIFF_ENV}
    assert agent.machine_env.machine_env(base) == base


def test_the_repo_config_is_undone_and_the_process_own_changes_survive(mise):
    base = _repo_applied(ADDED_BY_PR="kept")
    env = agent.machine_env.machine_env(base)
    assert env["GOOGLE_CLOUD_PROJECT"] == "machine-proj"
    assert "REPO_ONLY" not in env
    assert env["ADDED_BY_PR"] == "kept"
    assert env["PATH"] == base["PATH"]
    assert "NOISE" not in env
    assert base["GOOGLE_CLOUD_PROJECT"] == "repo-proj"


def test_the_undo_is_computed_from_home_where_only_global_config_applies(mise, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    agent.machine_env.machine_env(_repo_applied())
    assert Path(mise.called_from.read_text().strip()).resolve() == home.resolve()


def test_a_failing_hook_env_raises_rather_than_passing_the_repo_env_through(mise):
    mise.answer(hook_rc=3)
    with pytest.raises(MachineEnvUnavailable, match="hook-env"):
        agent.machine_env.machine_env(_repo_applied())


def test_a_mise_record_with_no_mise_to_read_it_raises(mise, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    with pytest.raises(MachineEnvUnavailable, match="not on PATH"):
        agent.machine_env.machine_env(_repo_applied())


def test_untrusted_dirs_are_listed_absolute_and_trusted_ones_skipped(mise, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    mise.trust.write_text("~/repo: untrusted\n/abs/pkg: untrusted\n/abs: trusted\n")
    assert agent.machine_env.untrusted_dirs(str(tmp_path)) == (
        str(tmp_path / "repo"), "/abs/pkg",
    )


def test_a_failing_trust_listing_ignores_nothing(mise, tmp_path):
    mise.trust.write_text("/abs/pkg: untrusted\n")
    mise.answer(trust_rc=1)
    assert agent.machine_env.untrusted_dirs(str(tmp_path)) == ()


def test_base_env_appends_untrusted_dirs_after_the_operator_ignores(mise, monkeypatch, tmp_path):
    monkeypatch.delenv(MISE_DIFF_ENV, raising=False)
    monkeypatch.setenv(MISE_IGNORED_ENV, "/operator/own")
    mise.trust.write_text("/wt: untrusted\n")
    env = agent.machine_env.base_env(str(tmp_path))
    assert env[MISE_IGNORED_ENV] == os.pathsep.join(["/operator/own", "/wt"])


def test_an_agent_with_no_env_inherits_the_machine_env(mise, monkeypatch, tmp_path):
    for key, value in _repo_applied().items():
        monkeypatch.setenv(key, value)
    mise.trust.write_text("/wt: untrusted\n")
    env = agent.backend.agent_env(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
    ))
    assert env["GOOGLE_CLOUD_PROJECT"] == "machine-proj"
    assert "REPO_ONLY" not in env
    assert env[MISE_IGNORED_ENV].split(os.pathsep)[-1] == "/wt"


def test_invoke_agent_returns_nonzero_when_the_machine_env_is_unavailable(mise, monkeypatch, tmp_path):
    for key, value in _repo_applied().items():
        monkeypatch.setenv(key, value)
    mise.answer(hook_rc=1)
    monkeypatch.setattr(agent.backend, "_get_module", lambda: type("M", (), {
        "invoke_agent": staticmethod(lambda inv: agent.backend.agent_env(inv) and 0),
    }))
    inv = agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
    )
    assert agent.backend.invoke_agent(inv) == 1


def test_a_caller_supplied_env_is_taken_as_given(mise, tmp_path):
    supplied = {"PATH": "/stub", MISE_DIFF_ENV: "record", "GOOGLE_CLOUD_PROJECT": "chosen"}
    env = agent.backend.agent_env(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"), env=supplied,
    ))
    assert env["GOOGLE_CLOUD_PROJECT"] == "chosen"
    assert not mise.called_from.exists()
