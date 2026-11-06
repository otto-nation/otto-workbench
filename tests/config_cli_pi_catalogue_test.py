"""Tests for `lib/config_cli.py pi-catalogue` — the bounded listing bash syncs through.

The sync path used to run `pi --list-models` unbounded in a command
substitution. This subcommand is how it now shares `catalogue_text`'s
LAUNCH bound, so a hang becomes exit 1 with the reason on stderr and
nothing on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import _REAL_CATALOGUE_TEXT, _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.pi_models
import core.proc

config_cli = _load_lib("config_cli")

LISTING = (
    "provider              model              context  max-out  thinking  images\n"
    "anthropic             claude-opus-5-5    1M       128K     yes       yes\n"
)


def test_pi_catalogue_prints_the_listing(capsys, monkeypatch):
    monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda pi: LISTING)
    assert config_cli.main(["pi-catalogue"]) == 0
    captured = capsys.readouterr()
    assert captured.out == LISTING
    assert captured.err == ""


def test_pi_catalogue_asks_for_pi_on_path(capsys, monkeypatch):
    seen = []

    def catalogue_text(pi):
        seen.append(pi)
        return LISTING

    monkeypatch.setattr(agent.pi_models, "catalogue_text", catalogue_text)
    assert config_cli.main(["pi-catalogue"]) == 0
    assert seen == [("pi",)]
    capsys.readouterr()


def test_pi_catalogue_timeout_exits_1_with_reason_on_stderr(capsys, monkeypatch):
    monkeypatch.setattr(agent.pi_models, "catalogue_text", _REAL_CATALOGUE_TEXT)
    monkeypatch.setattr(core.proc, "run", lambda *_a, **_k: core.proc.CmdResult(
        returncode=core.proc.TIMEOUT_RETURNCODE,
        stderr="timed out after 60s: pi --list-models",
    ))
    agent.pi_models.forget_catalogue()
    try:
        code = config_cli.main(["pi-catalogue"])
    finally:
        agent.pi_models.forget_catalogue()
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "timed out after 60s" in captured.err
    assert "Traceback" not in captured.err
