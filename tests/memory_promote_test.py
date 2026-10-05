"""Tests for ai/lib/memory/promote.py — promotion scan helpers."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import memory.promote


def test_first_heading_extracts_the_first_heading(tmp_path):
    path = tmp_path / "heading_test.md"
    path.write_text("# My Heading\n\nSome content.\n\n## Sub heading\n")
    assert memory.promote.first_heading(path) == "My Heading"


def test_first_heading_returns_empty_string_for_no_headings(tmp_path):
    path = tmp_path / "no_heading.md"
    path.write_text("Just text\n")
    assert memory.promote.first_heading(path) == ""


def test_run_scan_returns_0_rather_than_exiting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "data"))
    wb = tmp_path / "wb"
    (wb / "ai" / "guidelines" / "rules").mkdir(parents=True)
    (wb / "ai" / "claude" / "agents").mkdir(parents=True)
    (wb / "ai" / "memory").mkdir(parents=True)
    (wb / "bin").mkdir(parents=True)
    assert memory.promote.run_scan(str(tmp_path), str(wb)) == 0
    assert "## Workbench Artifacts" in capsys.readouterr().out


def test_cli_refuses_to_run_without_a_workbench(monkeypatch, capsys):
    """No env var and no checkout to fall back on is a usage error, not a scan of '.'."""
    import cli.promote_scan
    monkeypatch.delenv("OTTO_WORKBENCH", raising=False)
    monkeypatch.setattr(cli.promote_scan.core.workbench_paths, "source_checkout", lambda: None)
    assert cli.promote_scan.main([]) == 2
    assert "--workbench" in capsys.readouterr().err


def test_cli_defaults_to_the_checkout_it_runs_from(monkeypatch, tmp_path):
    import cli.promote_scan
    monkeypatch.delenv("OTTO_WORKBENCH", raising=False)
    seen = {}

    def fake_run_scan(home, workbench, **_):
        seen["wb"] = workbench
        return 0

    monkeypatch.setattr(cli.promote_scan.memory.promote, "run_scan", fake_run_scan)
    monkeypatch.setattr(cli.promote_scan.core.workbench_paths, "source_checkout", lambda: tmp_path)
    cli.promote_scan.main([])
    assert seen["wb"] == str(tmp_path)
