"""Tests for ai/lib/memory/dream.py — signal classification and scan returns."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import core.memory
import memory.dream


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("actually, that's wrong", "correction"),
        ("I prefer tabs over spaces", "preference"),
        ("let's go with option A", "decision"),
        ("you keep forgetting this", "pattern"),
        ("that is a false positive", "review_feedback"),
        ("please read this file for me", None),
        ("I PREFER spaces", "preference"),
    ],
)
def test_classify_signal(text, expected):
    assert memory.dream.classify_signal(text) == expected


def test_list_transcripts_returns_0_rather_than_exiting(tmp_path, capsys):
    dest = tmp_path / ".claude" / "projects" / "p" / "s.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"type":"user","message":{"role":"user","content":"hello"}}\n')
    assert memory.dream.list_transcripts(tmp_path, 30) == 0
    assert str(dest) in capsys.readouterr().out


def test_print_memory_dir_returns_0_rather_than_exiting(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "data"))
    assert memory.dream.print_memory_dir(str(repo)) == 0
    assert capsys.readouterr().out == str(core.memory.memory_dir(str(repo))) + "\n"


def test_run_scan_returns_0_rather_than_exiting(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "data"))
    assert memory.dream.run_scan(tmp_path, 30) == 0
