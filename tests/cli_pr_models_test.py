"""`pr models --json` wiring: exit status, compact JSON, --repo-dir, fail-open catalogue."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_ctx

import cli.pr
import cli.pr_models
import config.workbench_config
from core.tool_parser import EXIT_USAGE


def test_missing_json_exits_2(capsys):
    code = cli.pr_models.cmd_models([], make_ctx())
    assert code == EXIT_USAGE
    assert "pr models needs --json" in capsys.readouterr().err


def test_unreadable_catalogue_exits_0_with_ok_false(capsys):
    code = cli.pr_models.cmd_models(["--json"], make_ctx())
    assert code == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["schema"] == 1
    assert doc["catalogue"]["ok"] is False
    assert doc["catalogue"]["error"]
    assert "\n  " not in out


def test_repo_dir_is_the_project_root(monkeypatch):
    seen = {}

    def fake_choices(project_root=None):
        seen["root"] = project_root
        return _empty_doc()

    monkeypatch.setattr("agent.model_choices.model_choices", fake_choices)
    code = cli.pr_models.cmd_models(
        ["--json"], make_ctx(), original_repo_dir="/checkout")
    assert code == 0
    assert seen["root"] == "/checkout"


def test_config_error_exits_1(monkeypatch, capsys):
    def boom(_project_root=None):
        raise config.workbench_config.ConfigError("bad config")

    monkeypatch.setattr("agent.model_choices.model_choices", boom)
    code = cli.pr_models.cmd_models(["--json"], make_ctx())
    assert code == 1
    assert "bad config" in capsys.readouterr().err


def test_main_prints_json_and_exits_0(capsys):
    rc = cli.pr.main(["models", "--json"], bin_dir=Path("."))
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == 1
    assert "phases" in doc
    assert "tiers" in doc
    assert "enabled" in doc
    assert "catalogue" in doc


def _empty_doc():
    class _Doc:
        def as_json(self):
            return {
                "schema": 1,
                "phases": {},
                "tiers": {},
                "enabled": [],
                "catalogue": {"ok": False, "error": "stub", "providers": []},
            }
    return _Doc()
