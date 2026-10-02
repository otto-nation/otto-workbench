"""Tests for agent.rule_prefix — Pi's concatenation of a seeded rules_home."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent.rule_prefix import RulePrefixError, materialize, rule_blob


def _write_rules(home: Path, files: dict[str, str]) -> Path:
    rules = home / "rules"
    rules.mkdir(parents=True)
    for name, body in files.items():
        (rules / name).write_text(body)
    return home


def test_blob_strips_frontmatter_and_skips_path_scoped_and_claude_only(tmp_path):
    home = _write_rules(tmp_path / "arm", {
        "general.md": "---\nharness: [pi, claude]\n---\n# General\nKeep.\n",
        "go.md": "---\npaths:\n  - \"**/*.go\"\n---\n# Go\nSkip me.\n",
        "bash-tool.md": "---\nharness: [claude]\n---\n# Bash tool\nSkip me too.\n",
        "plain.md": "# Plain\nNo frontmatter.\n",
    })
    blob = rule_blob(str(home))
    assert "Keep." in blob
    assert "No frontmatter." in blob
    assert "Skip me." not in blob
    assert "Skip me too." not in blob
    assert "paths:" not in blob
    assert "harness:" not in blob
    assert "<!-- ─── general.md ─── -->" in blob
    assert "<!-- ─── plain.md ─── -->" in blob


def test_empty_paths_list_is_not_a_scope(tmp_path):
    home = _write_rules(tmp_path / "arm", {
        "general.md": "---\npaths: []\n---\n# General\nKeep.\n",
    })
    assert "Keep." in rule_blob(str(home))


def test_missing_or_empty_rules_home_fails_loudly(tmp_path):
    with pytest.raises(RulePrefixError, match="absolute"):
        rule_blob("relative/arm")
    missing = tmp_path / "missing"
    with pytest.raises(RulePrefixError, match="rules/"):
        rule_blob(str(missing))
    empty = tmp_path / "empty"
    (empty / "rules").mkdir(parents=True)
    with pytest.raises(RulePrefixError, match="no .md files"):
        rule_blob(str(empty))


def test_all_filtered_files_fail_loudly_rather_than_injecting_nothing(tmp_path):
    home = _write_rules(tmp_path / "arm", {
        "go.md": "---\npaths:\n  - \"**/*.go\"\n---\n# Go\n",
    })
    with pytest.raises(RulePrefixError, match="reaches Pi"):
        rule_blob(str(home))


def test_materialize_writes_a_file_pi_will_read_as_contents(tmp_path):
    home = _write_rules(tmp_path / "arm", {"general.md": "# General\nBody.\n"})
    path = Path(materialize(str(home))).resolve()
    assert path.is_file()
    assert "Body." in path.read_text()
    assert Path.home().joinpath(".claude") not in path.parents
    assert Path.home().joinpath(".pi") not in path.parents
